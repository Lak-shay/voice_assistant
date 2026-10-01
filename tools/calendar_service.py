from __future__ import annotations

import datetime
from typing import Any
from config import settings


class CalendarService:
    def __init__(self) -> None:
        self._in_memory_bookings: set[str] = set()

    async def get_available_slots(self, date_str: str) -> list[str]:
        try:
            target_date = datetime.date.fromisoformat(date_str.strip())
        except ValueError:
            return []

        # Generate standard clinic hours (9:00 AM to 5:00 PM, 30-min increments)
        slots: list[str] = []
        for hour in range(9, 17):
            for minute in (0, 30):
                slot_iso = f"{target_date.isoformat()}T{hour:02d}:{minute:02d}:00"
                if slot_iso not in self._in_memory_bookings:
                    slots.append(slot_iso)

        return slots[:6]

    async def book_slot(
        self,
        slot_time: str,
        patient_name: str,
        patient_phone: str,
    ) -> dict[str, Any]:
        normalized_slot = slot_time.strip()
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


calendar_service = CalendarService()
