from __future__ import annotations

import datetime

from clinic_time import now_in_clinic, parse_flexible_date
from config import settings
from tools.phone import sanitize_e164

INVALID_NAME_VALUES = {
    "anonymous", "unknown", "none", "n/a", "na", "caller", "test", "patient", "user", "someone", "nobody"
}


def validate_patient_name(name: str | None) -> tuple[bool, str | None]:
    if not name or not name.strip():
        return False, "A full patient name is required to book an appointment."

    clean = name.strip()
    if len(clean) < 2:
        return False, "Please provide a valid first and last name."

    if clean.isdigit():
        return False, "A patient name cannot consist of numbers."

    if clean.lower() in INVALID_NAME_VALUES:
        return False, "Could you please provide your actual full name?"

    return True, None


def validate_patient_phone(phone: str | None) -> tuple[bool, str | None, str | None]:
    if not phone or not phone.strip():
        return False, None, "A valid phone number is required to complete the booking. Please provide your phone number."

    sanitized = sanitize_e164(phone)
    if not sanitized:
        return False, None, "A valid phone number is required to complete the booking. The phone number format could not be verified."

    # Rejects placeholder numbers like 000-000-0000. Checks the 10-digit national number so the country code is ignored.
    if len(set(sanitized[-10:])) == 1:
        return False, None, "A valid phone number is required to complete the booking. The phone number provided appears to be invalid."

    return True, sanitized, None


def validate_booking_date_range(
    target_date: str | datetime.date,
    max_days_ahead: int | None = None,
    reference_date: datetime.date | None = None,
) -> tuple[bool, str | None]:
    if not settings.GUARDRAILS_ENABLED:
        return True, None

    today = reference_date or now_in_clinic().date()
    max_days = settings.GUARDRAILS_MAX_BOOKING_DAYS_AHEAD if max_days_ahead is None else max_days_ahead
    parsed = target_date if isinstance(target_date, datetime.date) else parse_flexible_date(target_date, base_date=today)

    if not parsed:
        return False, "The requested appointment slot date could not be understood. Please specify a valid date."

    if parsed < today:
        return False, "Appointments cannot be scheduled for past dates. Please choose today or an upcoming date."

    if parsed > today + datetime.timedelta(days=max_days):
        return False, f"Our schedule is currently open up to {max_days} days in advance. Please choose an earlier date."

    return True, None
