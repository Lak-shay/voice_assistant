from __future__ import annotations

import datetime

from clinic_time import now_in_clinic, parse_flexible_date
from config import settings
from tools.phone import sanitize_e164

INVALID_NAME_VALUES = {
    "anonymous", "unknown", "none", "n/a", "na", "caller", "test", "patient", "user", "someone", "nobody"
}


def validate_patient_name(name: str | None) -> str | None:
    """Returns an error message for the caller, or None if the name is acceptable."""
    if not name or not name.strip():
        return "A full patient name is required to book an appointment."

    clean = name.strip()
    if len(clean) < 2:
        return "Please provide a valid first and last name."

    if clean.isdigit():
        return "A patient name cannot consist of numbers."

    if clean.lower() in INVALID_NAME_VALUES:
        return "Could you please provide your actual full name?"

    return None


def validate_patient_phone(phone: str | None) -> tuple[str, None] | tuple[None, str]:
    """Returns (E.164 number, None) if valid, else (None, error message for the caller)."""
    if not phone or not phone.strip():
        return None, "A valid phone number is required to complete the booking. Please provide your phone number."

    sanitized = sanitize_e164(phone)
    if not sanitized:
        return None, "A valid phone number is required to complete the booking. The phone number format could not be verified."

    # Rejects placeholder numbers like 000-000-0000. Checks the 10-digit national number so the country code is ignored.
    if len(set(sanitized[-10:])) == 1:
        return None, "A valid phone number is required to complete the booking. The phone number provided appears to be invalid."

    return sanitized, None


def validate_booking_date_range(
    target_date: str | datetime.date,
    max_days_ahead: int | None = None,
    reference_date: datetime.date | None = None,
) -> str | None:
    """Returns an error message for the caller, or None if the date is bookable."""
    if not settings.GUARDRAILS_ENABLED:
        return None

    today = reference_date or now_in_clinic().date()
    max_days = settings.GUARDRAILS_MAX_BOOKING_DAYS_AHEAD if max_days_ahead is None else max_days_ahead
    parsed = target_date if isinstance(target_date, datetime.date) else parse_flexible_date(target_date, base_date=today)

    if not parsed:
        return "The requested appointment slot date could not be understood. Please specify a valid date."

    if parsed < today:
        return "Appointments cannot be scheduled for past dates. Please choose today or an upcoming date."

    if parsed > today + datetime.timedelta(days=max_days):
        return f"Our schedule is currently open up to {max_days} days in advance. Please choose an earlier date."

    return None
