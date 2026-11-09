from __future__ import annotations

from collections.abc import AsyncIterable
import datetime
import re

from dateutil import parser

from text_stream import rewrite_whole_words

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty"}


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


def _number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] if ones == 0 else f"{_TENS[tens]} {_ONES[ones]}"


def _clock_words(hour: int, minute: int) -> str:
    """(14, 5) -> 'two oh five'. The hour is spoken on a twelve-hour clock without AM or PM."""
    spoken_hour = _number_words(hour % 12 or 12)
    if minute == 0:
        return spoken_hour
    if minute < 10:
        return f"{spoken_hour} oh {_ONES[minute]}"
    return f"{spoken_hour} {_number_words(minute)}"


def format_time_phonetically(hour: int, minute: int = 0) -> str:
    """(14, 30) -> 'two thirty PM', (11, 0) -> 'eleven AM'."""
    return f"{_clock_words(hour, minute)} {'PM' if hour >= 12 else 'AM'}"


def format_slot_phonetically(slot_iso: str) -> str:
    """'2026-09-10T14:30:00' -> 'September 10th at two thirty PM'. Returns the input unchanged if it cannot be parsed."""
    try:
        dt = parser.parse(slot_iso.strip())
    except (ValueError, OverflowError, parser.ParserError):
        return slot_iso
    return f"{_month_day(dt.date())} at {format_time_phonetically(dt.hour, dt.minute)}"


_CLOCK_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)(?:\s*([AaPp])\.?\s*[Mm]\b)?")
_TRAILING_CLOCK_TIME = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\s*\Z")
_DIGIT_ORDINAL = re.compile(r"\b([1-9]|[12]\d|3[01])(?:st|nd|rd|th)\b", re.I)

_ORDINAL_ONES = (
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
    "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth"
).split()
_ORDINAL_TENS = {2: "twentieth", 3: "thirtieth"}


def _ordinal_words(n: int) -> str:
    if n < 20:
        return _ORDINAL_ONES[n]
    tens, ones = divmod(n, 10)
    return _ORDINAL_TENS[tens] if ones == 0 else f"{_TENS[tens]} {_ORDINAL_ONES[ones]}"


def _speak_clock_time(match: re.Match[str]) -> str:
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3)
    if meridiem:
        return f"{_clock_words(hour, minute)} {meridiem.upper()}M"
    if hour == 0 or hour > 12:
        return format_time_phonetically(hour, minute)
    return _clock_words(hour, minute)


def speak_clock_times(text: str) -> str:
    """'11:00 AM' -> 'eleven AM', '2:05pm' -> 'two oh five PM', '14:30' -> 'two thirty PM'."""
    return _CLOCK_TIME.sub(_speak_clock_time, text)


def speak_ordinals(text: str) -> str:
    """'September 10th' -> 'September tenth', '21st' -> 'twenty first'."""
    return _DIGIT_ORDINAL.sub(lambda m: _ordinal_words(int(m.group(1))), text)


def _speak_numbers(text: str) -> str:
    return speak_ordinals(speak_clock_times(text))


async def spoken_times_transform(text: AsyncIterable[str]) -> AsyncIterable[str]:
    """Streaming tts_text_transform that rewrites digit clock times and day ordinals into words.

    Tool results keep digit ordinals ('September 10th') so the LLM can pass slots back in a form
    the slot parser reads; they are spoken as words only here. A clock time is held until the
    next word arrives, because its AM or PM can come in a later LLM chunk.
    """
    async for out in rewrite_whole_words(text, _speak_numbers, hold_back=_TRAILING_CLOCK_TIME):
        yield out
