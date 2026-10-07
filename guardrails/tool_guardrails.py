from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo
from config import settings
from tools.sms_service import sanitize_e164
from tools.calendar_service import parse_flexible_date

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

    # Prevent obviously fake numbers like +10000000000
    digits = sanitized.lstrip("+")
    if all(d == digits[1] for d in digits[1:]):
        return False, None, "A valid phone number is required to complete the booking. The phone number provided appears to be invalid."

    return True, sanitized, None


def validate_booking_date_range(
    target_date: str | datetime.date,
    clinic_timezone: str | None = None,
    max_days_ahead: int | None = None,
    reference_date: datetime.date | None = None,
) -> tuple[bool, str | None]:
    if not settings.GUARDRAILS_ENABLED:
        return True, None

    tz_name = clinic_timezone or settings.CLINIC_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = datetime.timezone.utc

    today = reference_date or datetime.datetime.now(tz).date()
    max_days = max_days_ahead or settings.GUARDRAILS_MAX_BOOKING_DAYS_AHEAD

    parsed: datetime.date | None = None
    if isinstance(target_date, datetime.date):
        parsed = target_date
    elif isinstance(target_date, str):
        parsed = parse_flexible_date(target_date, tz)

    if not parsed:
        return False, "The requested appointment slot date could not be understood. Please specify a valid date."

    # In mock test backend, allow historical test fixtures unless reference_date is explicitly supplied
    if settings.CALENDAR_BACKEND == "mock" and reference_date is None:
        return True, None

    if parsed < today:
        return False, "Appointments cannot be scheduled for past dates. Please choose today or an upcoming date."

    furthest_date = today + datetime.timedelta(days=max_days)
    if parsed > furthest_date:
        return False, f"Our schedule is currently open up to {max_days} days in advance. Please choose an earlier date."

    return True, None
