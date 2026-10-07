from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo
from dateutil import parser

from config import settings
from logger import logger

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GOOGLE_CLIENT_AVAILABLE = True
except ImportError:
    GOOGLE_CLIENT_AVAILABLE = False


def parse_flexible_date(date_str: str, clinic_tz: datetime.tzinfo) -> datetime.date | None:
    """Robustly parse natural language, relative, or formatted date strings into a date object."""
    if not date_str:
        return None
    raw = date_str.strip().lower()
    base_date = datetime.datetime.now(clinic_tz).date()

    if raw in ("today", "now"):
        return base_date
    if raw == "tomorrow":
        return base_date + datetime.timedelta(days=1)
    if raw == "yesterday":
        return base_date - datetime.timedelta(days=1)

    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    for day_name, day_num in weekdays.items():
        if day_name in raw:
            days_ahead = (day_num - base_date.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return base_date + datetime.timedelta(days=days_ahead)

    try:
        parsed = parser.parse(date_str, default=datetime.datetime.combine(base_date, datetime.time(0, 0)))
        return parsed.date()
    except (ValueError, OverflowError, parser.ParserError):
        return None


def parse_flexible_slot(
    slot_str: str,
    clinic_tz: datetime.tzinfo,
    default_date: datetime.date | None = None,
) -> datetime.datetime | None:
    """Robustly parse natural language, relative, or formatted appointment slot strings."""
    if not slot_str:
        return None
    raw = slot_str.strip()

    # Reject date-only strings without any time specification
    if not re.search(r"(\d{1,2}:\d{2}|\b\d{1,2}\s*(?:am|pm)\b)", raw, re.I):
        return None

    if default_date is None:
        default_date = datetime.datetime.now(clinic_tz).date()

    # If slot string specifies a relative day (e.g. 'tomorrow at 10 AM', 'Monday at 2 PM')
    rel_match = re.search(r"\b(today|tomorrow|yesterday|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", raw, re.I)
    if rel_match:
        rel_date = parse_flexible_date(rel_match.group(1), clinic_tz)
        if rel_date:
            default_date = rel_date

    default_dt = datetime.datetime.combine(default_date, datetime.time(0, 0))
    try:
        parsed = parser.parse(raw, default=default_dt, fuzzy=True)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=clinic_tz)
        else:
            parsed = parsed.astimezone(clinic_tz)
        return parsed.replace(second=0, microsecond=0)
    except (ValueError, OverflowError, parser.ParserError):
        return None


class CalendarService:
    GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, db_path: str | None = None) -> None:
        self._in_memory_bookings: set[str] = set()
        self._write_lock = threading.Lock()
        self._google_service: Any = None
        self._google_init_attempted = False
        self._db_path = db_path or os.path.join(tempfile.gettempdir(), "voice_assistant_bookings.db")
        self._init_db()

    def _init_db(self) -> None:
        try:
            with sqlite3.connect(self._db_path, timeout=10.0) as conn:
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
                conn.commit()
        except Exception as exc:
            logger.error("Failed to initialize booking database at %s: %s", self._db_path, exc)

    def clear_reservations(self) -> None:
        self._in_memory_bookings.clear()
        try:
            with sqlite3.connect(self._db_path, timeout=10.0) as conn:
                cursor = conn.execute("SELECT event_id FROM booked_slots WHERE event_id IS NOT NULL")
                event_ids = [row[0] for row in cursor.fetchall()]
                conn.execute("DELETE FROM booked_slots")
                conn.commit()

            service = self._get_google_service()
            if service and event_ids:
                cal_id = settings.CALENDAR_ID or "primary"
                for eid in event_ids:
                    try:
                        service.events().delete(calendarId=cal_id, eventId=eid).execute()
                    except Exception:
                        pass
        except Exception as exc:
            logger.error("Failed to clear reservations: %s", exc)

    def _get_db_booked_slots(self, date_str: str) -> set[str]:
        try:
            with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                cursor = conn.execute("SELECT slot_time FROM booked_slots WHERE slot_time LIKE ?", (f"{date_str}%",))
                return {row[0] for row in cursor.fetchall()}
        except Exception as exc:
            logger.debug("Failed to query DB booked slots: %s", exc)
            return set()

    def _get_timezone(self) -> datetime.tzinfo:
        try:
            return ZoneInfo(settings.CLINIC_TIMEZONE)
        except Exception:
            return datetime.timezone.utc

    def _get_google_service(self) -> Any:
        if self._google_service is not None:
            return self._google_service
        if self._google_init_attempted:
            return None

        self._google_init_attempted = True
        if not GOOGLE_CLIENT_AVAILABLE or settings.CALENDAR_BACKEND != "google":
            return None

        api_key_or_path = (settings.CALENDAR_API_KEY or "").strip()
        if not api_key_or_path:
            logger.warning("CALENDAR_BACKEND is 'google' but CALENDAR_API_KEY is not configured.")
            return None

        try:
            if os.path.exists(api_key_or_path):
                credentials = service_account.Credentials.from_service_account_file(
                    api_key_or_path, scopes=self.GOOGLE_SCOPES
                )
            else:
                key_dict = json.loads(api_key_or_path)
                credentials = service_account.Credentials.from_service_account_info(
                    key_dict, scopes=self.GOOGLE_SCOPES
                )

            self._google_service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
            logger.info("Google Calendar API client successfully initialized.")
            return self._google_service
        except Exception as exc:
            logger.error("Failed to initialize Google Calendar API client: %s", exc)
            return None

    def classify_period(self, slot_iso: str) -> str:
        try:
            dt = datetime.datetime.fromisoformat(slot_iso)
            if dt.hour < 12:
                return "morning"
            if 12 <= dt.hour < 16:
                return "afternoon"
            return "evening"
        except Exception:
            return "afternoon"

    def _sync_query_google_busy(
        self,
        target_date: datetime.date | None = None,
        time_min: str | None = None,
        time_max: str | None = None,
    ) -> list[tuple[datetime.datetime, datetime.datetime]]:
        service = self._get_google_service()
        if not service:
            return []

        tz = self._get_timezone()
        if time_min is None or time_max is None:
            if target_date is None:
                return []
            time_min = datetime.datetime.combine(target_date, datetime.time(9, 0), tzinfo=tz).isoformat()
            time_max = datetime.datetime.combine(target_date, datetime.time(18, 0), tzinfo=tz).isoformat()

        cal_id = settings.CALENDAR_ID or "primary"
        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": settings.CLINIC_TIMEZONE,
            "items": [{"id": cal_id}],
        }
        try:
            res = service.freebusy().query(body=body).execute()
            calendars = res.get("calendars", {})
            cal_data = calendars.get(cal_id)
            if not cal_data and len(calendars) == 1:
                cal_data = next(iter(calendars.values()))
            if cal_data and "errors" in cal_data:
                logger.error("Google Calendar freebusy returned errors for calendar %s: %s", cal_id, cal_data["errors"])
            busy_items = cal_data.get("busy", []) if cal_data else []

            parsed_busy: list[tuple[datetime.datetime, datetime.datetime]] = []
            for item in busy_items:
                start_str = item.get("start")
                end_str = item.get("end")
                if start_str and end_str:
                    b_start = datetime.datetime.fromisoformat(start_str)
                    b_end = datetime.datetime.fromisoformat(end_str)
                    if b_start.tzinfo is None:
                        b_start = b_start.replace(tzinfo=tz)
                    if b_end.tzinfo is None:
                        b_end = b_end.replace(tzinfo=tz)
                    parsed_busy.append((b_start, b_end))
            return parsed_busy
        except Exception as exc:
            logger.error("Failed to query Google Calendar freebusy: %s", exc)
            return []

    async def get_all_slots_for_date(self, date_str: str) -> list[str]:
        tz = self._get_timezone()
        target_date = parse_flexible_date(date_str, tz)
        if not target_date:
            return []

        google_busy: list[tuple[datetime.datetime, datetime.datetime]] = []
        if settings.CALENDAR_BACKEND == "google":
            google_busy = await asyncio.to_thread(self._sync_query_google_busy, target_date)

        slots: list[str] = []
        now = datetime.datetime.now(tz)
        db_booked = self._get_db_booked_slots(target_date.isoformat())
        duration = settings.APPOINTMENT_DEFAULT_DURATION_MINUTES
        for hour in range(9, 18):
            for minute in (0, 30):
                slot_dt = datetime.datetime.combine(target_date, datetime.time(hour, minute), tzinfo=tz)
                if target_date == now.date() and slot_dt <= now:
                    continue

                slot_end = slot_dt + datetime.timedelta(minutes=duration)
                slot_iso = f"{target_date.isoformat()}T{hour:02d}:{minute:02d}:00"

                if slot_iso in self._in_memory_bookings or slot_iso in db_booked:
                    continue

                is_busy = False
                for b_start, b_end in google_busy:
                    if slot_dt < b_end and slot_end > b_start:
                        is_busy = True
                        break

                if not is_busy:
                    slots.append(slot_iso)

        return slots

    async def get_slots_by_period(self, date_str: str) -> dict[str, list[str]]:
        all_slots = await self.get_all_slots_for_date(date_str)
        grouped: dict[str, list[str]] = {
            "morning": [],
            "afternoon": [],
            "evening": [],
        }
        for s in all_slots:
            period = self.classify_period(s)
            grouped[period].append(s)
        return grouped

    async def find_nearby_available_slots(self, date_str: str, max_days: int = 3) -> list[tuple[str, list[str]]]:
        tz = self._get_timezone()
        target_date = parse_flexible_date(date_str, tz)
        if not target_date:
            return []

        nearby: list[tuple[str, list[str]]] = []
        for offset in range(1, max_days + 1):
            next_date = target_date + datetime.timedelta(days=offset)
            next_date_str = next_date.isoformat()
            slots = await self.get_all_slots_for_date(next_date_str)
            if slots:
                nearby.append((next_date_str, slots))
        return nearby

    def _sync_check_and_book_slot(
        self,
        normalized_slot: str,
        patient_name: str,
        patient_phone: str,
        slot_dt: datetime.datetime | None = None,
    ) -> dict[str, Any]:
        with self._write_lock:
            tz = self._get_timezone()
            if slot_dt is None:
                slot_dt = datetime.datetime.fromisoformat(normalized_slot)
            if slot_dt.tzinfo is None:
                slot_dt = slot_dt.replace(tzinfo=tz)
            slot_end = slot_dt + datetime.timedelta(minutes=settings.APPOINTMENT_DEFAULT_DURATION_MINUTES)

            try:
                with sqlite3.connect(self._db_path, timeout=15.0) as conn:
                    conn.execute(
                        "INSERT INTO booked_slots (slot_time, patient_name, patient_phone, created_at) VALUES (?, ?, ?, ?)",
                        (normalized_slot, patient_name.strip(), patient_phone.strip(), time.time()),
                    )
                    conn.commit()
            except sqlite3.IntegrityError:
                return {
                    "success": False,
                    "error": "This slot was just taken. Please select another open time.",
                }
            except Exception as db_exc:
                logger.error("Failed to insert slot reservation in DB: %s", db_exc)

            service = self._get_google_service()
            if service:
                cal_id = settings.CALENDAR_ID or "primary"
                time_min = slot_dt.isoformat()
                time_max = slot_end.isoformat()

                try:
                    busy_list = self._sync_query_google_busy(time_min=time_min, time_max=time_max)
                    if any(slot_dt < b_end and slot_end > b_start for b_start, b_end in busy_list):
                        with sqlite3.connect(self._db_path, timeout=10.0) as conn:
                            conn.execute("DELETE FROM booked_slots WHERE slot_time = ?", (normalized_slot,))
                            conn.commit()
                        return {
                            "success": False,
                            "error": "This slot was just taken. Please select another open time.",
                        }

                    event_body = {
                        "summary": f"Appointment: {patient_name.strip()}",
                        "description": f"Booked via Voice Receptionist for {patient_name.strip()}. Contact: {patient_phone.strip()}",
                        "start": {
                            "dateTime": time_min,
                            "timeZone": settings.CLINIC_TIMEZONE,
                        },
                        "end": {
                            "dateTime": time_max,
                            "timeZone": settings.CLINIC_TIMEZONE,
                        },
                    }
                    created_event = service.events().insert(calendarId=cal_id, body=event_body).execute()
                    event_id = created_event.get("id")
                    with sqlite3.connect(self._db_path, timeout=10.0) as conn:
                        conn.execute("UPDATE booked_slots SET event_id = ? WHERE slot_time = ?", (event_id, normalized_slot))
                        conn.commit()

                    self._in_memory_bookings.add(normalized_slot)
                    return {
                        "success": True,
                        "slot_time": normalized_slot,
                        "event_id": event_id,
                        "patient_name": patient_name.strip(),
                        "patient_phone": patient_phone.strip(),
                        "clinic_name": settings.CLINIC_NAME,
                    }
                except HttpError as http_err:
                    logger.error("Google Calendar API HttpError: %s", http_err)
                    with sqlite3.connect(self._db_path, timeout=10.0) as conn:
                        conn.execute("DELETE FROM booked_slots WHERE slot_time = ?", (normalized_slot,))
                        conn.commit()
                    return {
                        "success": False,
                        "error": "A temporary calendar error occurred. Please try again.",
                    }
                except Exception as exc:
                    logger.error("Unexpected error during Google Calendar booking: %s", exc)
                    with sqlite3.connect(self._db_path, timeout=10.0) as conn:
                        conn.execute("DELETE FROM booked_slots WHERE slot_time = ?", (normalized_slot,))
                        conn.commit()
                    return {
                        "success": False,
                        "error": "A temporary booking error occurred. Please try again.",
                    }

            self._in_memory_bookings.add(normalized_slot)
            return {
                "success": True,
                "slot_time": normalized_slot,
                "patient_name": patient_name.strip(),
                "patient_phone": patient_phone.strip(),
                "clinic_name": settings.CLINIC_NAME,
            }

    async def book_slot(
        self,
        slot_time: str,
        patient_name: str,
        patient_phone: str,
    ) -> dict[str, Any]:
        tz = self._get_timezone()
        parsed_dt = parse_flexible_slot(slot_time, tz)
        if not parsed_dt:
            logger.warning("Failed to parse slot_time '%s' for booking.", slot_time)
            return {
                "success": False,
                "error": "The appointment time could not be understood. Please specify a clear date and time.",
            }

        normalized_slot = parsed_dt.strftime("%Y-%m-%dT%H:%M:00")
        return await asyncio.to_thread(
            self._sync_check_and_book_slot,
            normalized_slot,
            patient_name,
            patient_phone,
            parsed_dt,
        )


calendar_service = CalendarService()

