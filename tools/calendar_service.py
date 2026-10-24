from __future__ import annotations

import asyncio
import datetime
import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from clinic_time import clinic_tz, parse_flexible_date, parse_flexible_slot
from config import settings
from logger import logger

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    GOOGLE_CLIENT_AVAILABLE = True
except ImportError:
    GOOGLE_CLIENT_AVAILABLE = False

PERIODS = ("morning", "afternoon", "evening")

SLOT_TAKEN_ERROR = "This slot was just taken. Please select another open time."
TEMPORARY_BOOKING_ERROR = "A temporary booking error occurred. Please try again."

BusyInterval = tuple[datetime.datetime, datetime.datetime]


def classify_period(slot_iso: str) -> str:
    try:
        hour = datetime.datetime.fromisoformat(slot_iso).hour
    except ValueError:
        return "afternoon"
    if hour < settings.AFTERNOON_START_HOUR:
        return "morning"
    if hour < settings.EVENING_START_HOUR:
        return "afternoon"
    return "evening"


def _overlaps(start: datetime.datetime, end: datetime.datetime, busy: list[BusyInterval]) -> bool:
    return any(start < b_end and end > b_start for b_start, b_end in busy)


class CalendarService:
    """Slot availability and booking. SQLite is the source of truth for reservations
    (its primary key on slot_time arbitrates concurrent bookings across worker
    processes); Google Calendar is mirrored when CALENDAR_BACKEND is 'google'."""

    GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, db_path: str | None = None) -> None:
        self._google_service: Any = None
        self._google_init_attempted = False
        self._google_init_lock = threading.Lock()
        self._db_path = db_path or settings.BOOKINGS_DB_PATH
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=settings.BOOKINGS_DB_TIMEOUT)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS booked_slots (
                        slot_time TEXT PRIMARY KEY,
                        patient_name TEXT,
                        patient_phone TEXT,
                        event_id TEXT,
                        created_at REAL
                    )
                    """
                )
        except sqlite3.Error as exc:
            logger.error("Failed to initialize booking database at %s: %s", self._db_path, exc)

    @property
    def calendar_id(self) -> str:
        return settings.CALENDAR_ID or "primary"

    def clear_reservations(self) -> None:
        """Delete every reservation and its mirrored Google event. Blocking; for test and simulation resets only."""
        try:
            with self._connect() as conn:
                event_ids = [row[0] for row in conn.execute("SELECT event_id FROM booked_slots WHERE event_id IS NOT NULL")]
                conn.execute("DELETE FROM booked_slots")
        except sqlite3.Error as exc:
            logger.error("Failed to clear reservations: %s", exc)
            return

        service = self.get_google_service()
        if not service:
            return
        for eid in event_ids:
            try:
                service.events().delete(calendarId=self.calendar_id, eventId=eid).execute()
            except Exception as exc:
                logger.debug("Failed to delete Google event %s: %s", eid, exc)

    def _get_db_booked_slots(self, date_iso: str) -> set[str]:
        try:
            with self._connect() as conn:
                cursor = conn.execute("SELECT slot_time FROM booked_slots WHERE slot_time LIKE ?", (f"{date_iso}%",))
                return {row[0] for row in cursor}
        except sqlite3.Error as exc:
            logger.warning("Failed to query booked slots for %s: %s", date_iso, exc)
            return set()

    def get_google_service(self) -> Any:
        """Lazily build the Google Calendar client. Blocking (reads credentials); call via asyncio.to_thread."""
        with self._google_init_lock:
            if self._google_service is not None or self._google_init_attempted:
                return self._google_service
            self._google_init_attempted = True

            if not GOOGLE_CLIENT_AVAILABLE or settings.CALENDAR_BACKEND != "google":
                return None

            api_key_or_path = (settings.CALENDAR_API_KEY or "").strip()
            if not api_key_or_path:
                logger.warning("CALENDAR_BACKEND is 'google' but CALENDAR_API_KEY is not configured.")
                return None

            # Credential files are kept out of the image; deployed workers receive them as mounted secrets.
            if not os.path.exists(api_key_or_path):
                mounted = os.path.join(settings.SECRETS_MOUNT_DIR, os.path.basename(api_key_or_path))
                if os.path.exists(mounted):
                    api_key_or_path = mounted

            try:
                if os.path.exists(api_key_or_path):
                    credentials = service_account.Credentials.from_service_account_file(
                        api_key_or_path, scopes=self.GOOGLE_SCOPES
                    )
                else:
                    credentials = service_account.Credentials.from_service_account_info(
                        json.loads(api_key_or_path), scopes=self.GOOGLE_SCOPES
                    )
                self._google_service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
                logger.info("Google Calendar API client initialized.")
            except Exception as exc:
                logger.error("Failed to initialize Google Calendar API client: %s", exc)
            return self._google_service

    def _clinic_day_bounds(self, target_date: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
        tz = clinic_tz()
        return (
            datetime.datetime.combine(target_date, datetime.time(settings.CLINIC_OPEN_HOUR), tzinfo=tz),
            datetime.datetime.combine(target_date, datetime.time(settings.CLINIC_CLOSE_HOUR), tzinfo=tz),
        )

    def _sync_query_google_busy(self, time_min: datetime.datetime, time_max: datetime.datetime) -> list[BusyInterval]:
        """Blocking freebusy query. Returns [] when Google is not configured or the query fails."""
        service = self.get_google_service()
        if not service:
            return []

        cal_id = self.calendar_id
        body = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "timeZone": settings.CLINIC_TIMEZONE,
            "items": [{"id": cal_id}],
        }
        try:
            res = service.freebusy().query(body=body).execute()
        except Exception as exc:
            logger.error("Failed to query Google Calendar freebusy: %s", exc)
            return []

        calendars = res.get("calendars", {})
        cal_data = calendars.get(cal_id)
        # Google may key the response by the resolved calendar address rather than "primary".
        if not cal_data and len(calendars) == 1:
            cal_data = next(iter(calendars.values()))
        if not cal_data:
            return []
        if "errors" in cal_data:
            logger.error("Google Calendar freebusy returned errors for calendar %s: %s", cal_id, cal_data["errors"])

        tz = clinic_tz()
        busy: list[BusyInterval] = []
        for item in cal_data.get("busy", []):
            if item.get("start") and item.get("end"):
                b_start = datetime.datetime.fromisoformat(item["start"])
                b_end = datetime.datetime.fromisoformat(item["end"])
                busy.append((
                    b_start if b_start.tzinfo else b_start.replace(tzinfo=tz),
                    b_end if b_end.tzinfo else b_end.replace(tzinfo=tz),
                ))
        return busy

    async def get_all_slots_for_date(self, date_str: str) -> list[str]:
        target_date = parse_flexible_date(date_str)
        if not target_date:
            return []

        day_start, day_end = self._clinic_day_bounds(target_date)
        db_task = asyncio.to_thread(self._get_db_booked_slots, target_date.isoformat())
        if settings.CALENDAR_BACKEND == "google":
            db_booked, google_busy = await asyncio.gather(
                db_task, asyncio.to_thread(self._sync_query_google_busy, day_start, day_end)
            )
        else:
            db_booked, google_busy = await db_task, []

        now = datetime.datetime.now(day_start.tzinfo)
        step = datetime.timedelta(minutes=settings.SLOT_INTERVAL_MINUTES)
        duration = datetime.timedelta(minutes=settings.APPOINTMENT_DEFAULT_DURATION_MINUTES)
        slots: list[str] = []
        slot_dt = day_start
        while slot_dt < day_end:
            slot_iso = slot_dt.strftime("%Y-%m-%dT%H:%M:00")
            if slot_dt > now and slot_iso not in db_booked and not _overlaps(slot_dt, slot_dt + duration, google_busy):
                slots.append(slot_iso)
            slot_dt += step
        return slots

    async def get_slots_by_period(self, date_str: str) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {p: [] for p in PERIODS}
        for slot in await self.get_all_slots_for_date(date_str):
            grouped[classify_period(slot)].append(slot)
        return grouped

    async def find_next_available_day(self, date_str: str) -> tuple[str, list[str]] | None:
        """First day with openings within NEARBY_SEARCH_DAYS after date_str, as (YYYY-MM-DD, slots)."""
        target_date = parse_flexible_date(date_str)
        if not target_date:
            return None

        dates = [(target_date + datetime.timedelta(days=offset)).isoformat() for offset in range(1, settings.NEARBY_SEARCH_DAYS + 1)]
        results = await asyncio.gather(*(self.get_all_slots_for_date(d) for d in dates))
        return next(((d, slots) for d, slots in zip(dates, results) if slots), None)

    def _rollback_slot(self, normalized_slot: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM booked_slots WHERE slot_time = ?", (normalized_slot,))
        except sqlite3.Error as exc:
            logger.error("Failed to roll back slot reservation %s: %s", normalized_slot, exc)

    def _sync_google_book(
        self,
        service: Any,
        normalized_slot: str,
        patient_name: str,
        patient_phone: str,
        slot_dt: datetime.datetime,
        slot_end: datetime.datetime,
    ) -> dict[str, Any]:
        try:
            if _overlaps(slot_dt, slot_end, self._sync_query_google_busy(slot_dt, slot_end)):
                self._rollback_slot(normalized_slot)
                return {"success": False, "error": SLOT_TAKEN_ERROR}

            event_body = {
                "summary": f"Appointment: {patient_name}",
                "description": f"Booked via Voice Receptionist for {patient_name}. Contact: {patient_phone}",
                "start": {"dateTime": slot_dt.isoformat(), "timeZone": settings.CLINIC_TIMEZONE},
                "end": {"dateTime": slot_end.isoformat(), "timeZone": settings.CLINIC_TIMEZONE},
            }
            event_id = service.events().insert(calendarId=self.calendar_id, body=event_body).execute().get("id")
            with self._connect() as conn:
                conn.execute("UPDATE booked_slots SET event_id = ? WHERE slot_time = ?", (event_id, normalized_slot))
        except Exception as exc:
            logger.error("Google Calendar booking error: %s", exc)
            self._rollback_slot(normalized_slot)
            return {"success": False, "error": TEMPORARY_BOOKING_ERROR}

        return {
            "success": True,
            "slot_time": normalized_slot,
            "event_id": event_id,
            "patient_name": patient_name,
            "patient_phone": patient_phone,
            "clinic_name": settings.CLINIC_NAME,
        }

    def _sync_check_and_book_slot(
        self,
        slot_dt: datetime.datetime,
        patient_name: str,
        patient_phone: str,
    ) -> dict[str, Any]:
        normalized_slot = slot_dt.strftime("%Y-%m-%dT%H:%M:00")
        slot_end = slot_dt + datetime.timedelta(minutes=settings.APPOINTMENT_DEFAULT_DURATION_MINUTES)

        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO booked_slots (slot_time, patient_name, patient_phone, created_at) VALUES (?, ?, ?, ?)",
                    (normalized_slot, patient_name, patient_phone, time.time()),
                )
        except sqlite3.IntegrityError:
            return {"success": False, "error": SLOT_TAKEN_ERROR}
        except sqlite3.Error as exc:
            logger.error("Failed to reserve slot %s: %s", normalized_slot, exc)
            return {"success": False, "error": TEMPORARY_BOOKING_ERROR}

        service = self.get_google_service()
        if service:
            return self._sync_google_book(service, normalized_slot, patient_name, patient_phone, slot_dt, slot_end)

        return {
            "success": True,
            "slot_time": normalized_slot,
            "patient_name": patient_name,
            "patient_phone": patient_phone,
            "clinic_name": settings.CLINIC_NAME,
        }

    async def book_slot(self, slot_time: str, patient_name: str, patient_phone: str) -> dict[str, Any]:
        parsed_dt = parse_flexible_slot(slot_time)
        if not parsed_dt:
            logger.warning("Failed to parse slot_time '%s' for booking.", slot_time)
            return {
                "success": False,
                "error": "The appointment time could not be understood. Please specify a clear date and time.",
            }
        return await asyncio.to_thread(
            self._sync_check_and_book_slot, parsed_dt, patient_name.strip(), patient_phone.strip()
        )


calendar_service = CalendarService()
