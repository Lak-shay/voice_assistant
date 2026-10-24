from __future__ import annotations

import datetime

from dateutil import parser


def _ordinal_suffix(day: int) -> str:
    if 11 <= (day % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _month_day(d: datetime.date) -> str:
    return f"{d.strftime('%B')} {d.day}{_ordinal_suffix(d.day)}"


def format_date_phonetically(date_str: str) -> str:
    """'2026-09-10' -> 'September 10th'. Returns the input unchanged if it cannot be parsed."""
    try:
        return _month_day(parser.parse(date_str.strip()).date())
    except (ValueError, OverflowError, parser.ParserError):
        return date_str


def format_weekday_date(d: datetime.date) -> str:
    """date(2026, 9, 12) -> 'Saturday, September 12th'."""
    return f"{d.strftime('%A')}, {_month_day(d)}"


def format_slot_phonetically(slot_iso: str) -> str:
    """'2026-09-10T14:30:00' -> 'September 10th at 2 30 PM'. Returns the input unchanged if it cannot be parsed."""
    try:
        dt = parser.parse(slot_iso.strip())
    except (ValueError, OverflowError, parser.ParserError):
        return slot_iso
    hour = dt.strftime("%I").lstrip("0")
    am_pm = dt.strftime("%p")
    time_str = f"{hour} {am_pm}" if dt.minute == 0 else f"{hour} {dt.strftime('%M')} {am_pm}"
    return f"{_month_day(dt.date())} at {time_str}"
