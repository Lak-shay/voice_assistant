from __future__ import annotations

import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from logger import logger
from tools.calendar_service import CalendarService


def clean_test_reservations() -> None:
    cs = CalendarService()
    cs.clear_reservations()

    service = cs._get_google_service()
    if service and settings.CALENDAR_BACKEND == "google":
        cal_id = settings.CALENDAR_ID or "primary"
        try:
            events_res = service.events().list(calendarId=cal_id, singleEvents=True).execute()
            items = events_res.get("items", [])
            deleted_count = 0
            for item in items:
                summary = item.get("summary") or ""
                if summary.startswith("Appointment:"):
                    try:
                        service.events().delete(calendarId=cal_id, eventId=item["id"]).execute()
                        deleted_count += 1
                    except Exception as err:
                        logger.debug("Failed to delete event %s: %s", item.get("id"), err)
            logger.info("Cleaned %d test appointment events from Google Calendar.", deleted_count)
        except Exception as exc:
            logger.error("Failed to query Google Calendar for cleanup: %s", exc)


if __name__ == "__main__":
    clean_test_reservations()
