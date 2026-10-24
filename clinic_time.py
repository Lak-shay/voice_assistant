from __future__ import annotations

import datetime
import functools
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import parser

from config import settings
from logger import logger

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


@functools.lru_cache(maxsize=8)
def _resolve_tz(name: str) -> datetime.tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown CLINIC_TIMEZONE %r; falling back to UTC", name)
        return datetime.timezone.utc


def clinic_tz() -> datetime.tzinfo:
    return _resolve_tz(settings.CLINIC_TIMEZONE)


def now_in_clinic() -> datetime.datetime:
    return datetime.datetime.now(clinic_tz())


def parse_flexible_date(
    date_str: str,
    tz: datetime.tzinfo | None = None,
    base_date: datetime.date | None = None,
) -> datetime.date | None:
    """Parse 'today', 'tomorrow', '[next] <weekday>', or any dateutil-parsable date. Returns None if unparsable."""
    if not date_str:
        return None
    raw = date_str.strip().lower()
    if base_date is None:
        base_date = datetime.datetime.now(tz or clinic_tz()).date()

    if raw in ("today", "now"):
        return base_date
    if raw == "tomorrow":
        return base_date + datetime.timedelta(days=1)
    if raw == "yesterday":
        return base_date - datetime.timedelta(days=1)

    is_next = "next" in raw
    for day_name, day_num in WEEKDAYS.items():
        if day_name in raw:
            days_ahead = (day_num - base_date.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            elif is_next and day_num > base_date.weekday():
                days_ahead += 7
            return base_date + datetime.timedelta(days=days_ahead)

    try:
        return parser.parse(date_str, default=datetime.datetime.combine(base_date, datetime.time(0, 0))).date()
    except (ValueError, OverflowError, parser.ParserError):
        return None


_TIME_PATTERN = re.compile(r"(\d{1,2}:\d{2}|\b\d{1,2}\s*(?:am|pm)\b)", re.I)
_RELATIVE_DAY_PATTERN = re.compile(
    r"\b(today|tomorrow|yesterday|(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    re.I,
)


def parse_flexible_slot(
    slot_str: str,
    tz: datetime.tzinfo | None = None,
    default_date: datetime.date | None = None,
) -> datetime.datetime | None:
    """Parse an appointment slot such as '2026-10-05T10:00:00' or 'next Thursday at 10 AM' into an aware datetime.

    Returns None for unparsable input and for date-only strings with no time component.
    """
    if not slot_str:
        return None
    raw = slot_str.strip()
    if not _TIME_PATTERN.search(raw):
        return None

    tz = tz or clinic_tz()
    if default_date is None:
        default_date = datetime.datetime.now(tz).date()

    rel_match = _RELATIVE_DAY_PATTERN.search(raw)
    if rel_match:
        default_date = parse_flexible_date(rel_match.group(1), tz, base_date=default_date) or default_date

    try:
        parsed = parser.parse(raw, default=datetime.datetime.combine(default_date, datetime.time(0, 0)), fuzzy=True)
    except (ValueError, OverflowError, parser.ParserError):
        return None
    parsed = parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
    return parsed.replace(second=0, microsecond=0)
