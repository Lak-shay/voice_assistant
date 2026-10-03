from __future__ import annotations

import asyncio
import datetime
import json
import os
import threading
from typing import Any
from zoneinfo import ZoneInfo

from config import settings
from logger import logger

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GOOGLE_CLIENT_AVAILABLE = True
except ImportError:
    GOOGLE_CLIENT_AVAILABLE = False


class CalendarService:
    GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self) -> None:
        self._in_memory_bookings: set[str] = set()
        self._write_lock = threading.Lock()
        self._google_service: Any = None
        self._google_init_attempted = False

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

    def _sync_query_google_busy(self, target_date: datetime.date) -> list[tuple[datetime.datetime, datetime.datetime]]:
        service = self._get_google_service()
        if not service:
            return []

        tz = self._get_timezone()
        time_min = datetime.datetime.combine(target_date, datetime.time(9, 0), tzinfo=tz).isoformat()
        time_max = datetime.datetime.combine(target_date, datetime.time(17, 30), tzinfo=tz).isoformat()
        cal_id = settings.CALENDAR_ID or "primary"

        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": settings.CLINIC_TIMEZONE,
            "items": [{"id": cal_id}],
        }
        res = service.freebusy().query(body=body).execute()
        calendars = res.get("calendars", {})
        cal_data = calendars.get(cal_id)
        if not cal_data and len(calendars) == 1:
            cal_data = next(iter(calendars.values()))
        busy_items = cal_data.get("busy", []) if cal_data else []

        parsed_busy: list[tuple[datetime.datetime, datetime.datetime]] = []
        for item in busy_items:
            start_str = item.get("start")
            end_str = item.get("end")
            if start_str and end_str:
                parsed_busy.append((
                    datetime.datetime.fromisoformat(start_str),
                    datetime.datetime.fromisoformat(end_str),
                ))
        return parsed_busy

    async def get_all_slots_for_date(self, date_str: str) -> list[str]:
        try:
            target_date = datetime.date.fromisoformat(date_str.strip())
        except ValueError:
            return []

        tz = self._get_timezone()
        google_busy: list[tuple[datetime.datetime, datetime.datetime]] = []
        if settings.CALENDAR_BACKEND == "google":
            google_busy = await asyncio.to_thread(self._sync_query_google_busy, target_date)

        slots: list[str] = []
        duration = settings.APPOINTMENT_DEFAULT_DURATION_MINUTES
        for hour in range(9, 18):
            for minute in (0, 30):
                slot_dt = datetime.datetime.combine(target_date, datetime.time(hour, minute), tzinfo=tz)
                slot_end = slot_dt + datetime.timedelta(minutes=duration)
                slot_iso = f"{target_date.isoformat()}T{hour:02d}:{minute:02d}:00"

                if slot_iso in self._in_memory_bookings:
                    continue

                is_busy = False
                for b_start, b_end in google_busy:
                    if slot_dt < b_end and slot_end > b_start:
                        is_busy = True
                        break

                if not is_busy:
                    slots.append(slot_iso)

        return slots

    async def get_available_slots(self, date_str: str) -> list[str]:
        all_slots = await self.get_all_slots_for_date(date_str)
        return all_slots[:6]

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
        try:
            target_date = datetime.date.fromisoformat(date_str.strip())
        except ValueError:
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
    ) -> dict[str, Any]:
        with self._write_lock:
            tz = self._get_timezone()
            slot_dt = datetime.datetime.fromisoformat(normalized_slot)
            if slot_dt.tzinfo is None:
                slot_dt = slot_dt.replace(tzinfo=tz)
            slot_end = slot_dt + datetime.timedelta(minutes=settings.APPOINTMENT_DEFAULT_DURATION_MINUTES)

            service = self._get_google_service()
            if service:
                cal_id = settings.CALENDAR_ID or "primary"
                time_min = slot_dt.isoformat()
                time_max = slot_end.isoformat()

                try:
                    fb_res = service.freebusy().query(
                        body={
                            "timeMin": time_min,
                            "timeMax": time_max,
                            "timeZone": settings.CLINIC_TIMEZONE,
                            "items": [{"id": cal_id}],
                        }
                    ).execute()
                    calendars = fb_res.get("calendars", {})
                    cal_data = calendars.get(cal_id)
                    if not cal_data and len(calendars) == 1:
                        cal_data = next(iter(calendars.values()))
                    busy_list = cal_data.get("busy", []) if cal_data else []

                    for b in busy_list:
                        b_start = datetime.datetime.fromisoformat(b["start"])
                        b_end = datetime.datetime.fromisoformat(b["end"])
                        if b_start.tzinfo is None:
                            b_start = b_start.replace(tzinfo=tz)
                        if b_end.tzinfo is None:
                            b_end = b_end.replace(tzinfo=tz)
                        if slot_dt < b_end and slot_end > b_start:
                            return {
                                "success": False,
                                "error": "This slot was just taken. Please select another open time.",
                            }

                    event_body = {
                        "summary": f"Appointment: {patient_name}",
                        "description": f"Booked via Voice Receptionist for {patient_name}. Contact: {patient_phone}",
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
                    self._in_memory_bookings.add(normalized_slot)
                    return {
                        "success": True,
                        "slot_time": normalized_slot,
                        "event_id": created_event.get("id"),
                        "patient_name": patient_name.strip(),
                        "patient_phone": patient_phone.strip(),
                        "clinic_name": settings.CLINIC_NAME,
                    }
                except HttpError as http_err:
                    logger.error("Google Calendar API HttpError: %s", http_err)
                    return {
                        "success": False,
                        "error": "A temporary calendar error occurred. Please try again.",
                    }
                except Exception as exc:
                    logger.error("Unexpected error during Google Calendar booking: %s", exc)
                    return {
                        "success": False,
                        "error": "A temporary booking error occurred. Please try again.",
                    }

            # Local / mock fallback mode
            if normalized_slot in self._in_memory_bookings:
                return {
                    "success": False,
                    "error": "This slot was just taken. Please select another open time.",
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
        raw = slot_time.strip()
        normalized_slot = raw.replace(" ", "T")
        if len(normalized_slot) == 16:
            normalized_slot = f"{normalized_slot}:00"

        return await asyncio.to_thread(
            self._sync_check_and_book_slot,
            normalized_slot,
            patient_name,
            patient_phone,
        )


calendar_service = CalendarService()
