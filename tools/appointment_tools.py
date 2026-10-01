from __future__ import annotations

import datetime
from typing import Annotated

from livekit.agents import RunContext, function_tool

from config import settings
from logger import logger
from tools.calendar_service import calendar_service
from tools.sms_service import sms_service


def format_date_phonetically(date_str: str) -> str:
    try:
        dt = datetime.date.fromisoformat(date_str.strip())
        day_suffix = "th"
        if dt.day in (1, 21, 31):
            day_suffix = "st"
        elif dt.day in (2, 22):
            day_suffix = "nd"
        elif dt.day in (3, 23):
            day_suffix = "rd"
        month_name = dt.strftime("%B")
        return f"{month_name} {dt.day}{day_suffix}"
    except Exception:
        return date_str


def format_slot_phonetically(slot_iso: str) -> str:
    try:
        dt = datetime.datetime.fromisoformat(slot_iso)
        day_suffix = "th"
        if dt.day in (1, 21, 31):
            day_suffix = "st"
        elif dt.day in (2, 22):
            day_suffix = "nd"
        elif dt.day in (3, 23):
            day_suffix = "rd"

        month_name = dt.strftime("%B")
        hour = dt.strftime("%I").lstrip("0")
        minute = dt.strftime("%M")
        am_pm = dt.strftime("%p")

        time_str = f"{hour} {minute} {am_pm}" if minute != "00" else f"{hour} {am_pm}"
        return f"{month_name} {dt.day}{day_suffix} at {time_str}"
    except Exception:
        return slot_iso


def normalize_time_str(time_str: str) -> str | None:
    cleaned = time_str.strip().upper()
    for fmt in ("%I:%M %p", "%I %p", "%H:%M:%S", "%H:%M", "%I:%M%p", "%I%p"):
        try:
            t = datetime.datetime.strptime(cleaned, fmt).time()
            return f"{t.hour:02d}:{t.minute:02d}:00"
        except ValueError:
            continue
    return None


@function_tool()
async def check_availability(
    context: RunContext,
    preferred_date: Annotated[str, "Date in YYYY-MM-DD format to check clinic appointment openings"],
    preferred_time: Annotated[str | None, "Optional specific time (e.g. 10:00 AM, 2:00 PM, 14:00) requested by the caller"] = None,
    preferred_period: Annotated[str | None, "Optional period of the day: 'morning', 'afternoon', or 'evening'"] = None,
) -> str:
    """Check open appointment slots for a given date, specific time, or time of day."""
    try:
        slots_by_period = await calendar_service.get_slots_by_period(preferred_date)
        all_day_slots = [s for sublist in slots_by_period.values() for s in sublist]
        available_periods = [p for p in ("morning", "afternoon", "evening") if slots_by_period.get(p)]
        phonetic_date = format_date_phonetically(preferred_date)

        if preferred_time:
            norm_time = normalize_time_str(preferred_time)
            if norm_time:
                target_slot = f"{preferred_date.strip()}T{norm_time}"
                if target_slot in all_day_slots:
                    return f"{format_slot_phonetically(target_slot)} is open. Would you like me to book that for you?"

            if available_periods:
                if len(available_periods) == 1:
                    period_name = available_periods[0]
                    return f"That exact time is unavailable on {phonetic_date}, but we have openings in the {period_name}. Would you like {period_name}?"
                if len(available_periods) == 2:
                    return f"That exact time is unavailable on {phonetic_date}, but we have openings in the {available_periods[0]} and {available_periods[1]}. Would you prefer {available_periods[0]} or {available_periods[1]}?"
                return f"That exact time is unavailable on {phonetic_date}, but we have openings in the morning, afternoon, and evening. Which period works best for you?"

            nearby = await calendar_service.find_nearby_available_slots(preferred_date)
            if nearby:
                next_date, next_slots = nearby[0]
                next_date_phonetic = format_date_phonetically(next_date)
                next_periods = [p for p in ("morning", "afternoon", "evening") if any(calendar_service.classify_period(s) == p for s in next_slots)]
                return f"We are fully booked on {phonetic_date}. The closest openings around that time are on {next_date_phonetic} in the {' and '.join(next_periods)}. Would you like to check that day?"
            return f"I could not find any open slots around {phonetic_date} for {settings.CLINIC_NAME}. Would you like to check another week?"

        if preferred_period:
            period_key = preferred_period.lower().strip()
            period_slots = slots_by_period.get(period_key, [])
            if period_slots:
                spoken = [format_slot_phonetically(s) for s in period_slots[:3]]
                if len(spoken) == 1:
                    return f"In the {period_key} on {phonetic_date}, we have an opening on {spoken[0]}. Does that time suit you?"
                if len(spoken) == 2:
                    return f"In the {period_key} on {phonetic_date}, we have openings on {spoken[0]} or {spoken[1]}. Which time suits you best?"
                return f"In the {period_key} on {phonetic_date}, we have openings on {spoken[0]}, {spoken[1]}, or {spoken[2]}. Which time suits you best?"

            if available_periods:
                return f"We have no {period_key} openings on {phonetic_date}, but we have slots in the {' and '.join(available_periods)}. Would either of those work for you?"

        if not available_periods:
            nearby = await calendar_service.find_nearby_available_slots(preferred_date)
            if nearby:
                next_date, next_slots = nearby[0]
                next_date_phonetic = format_date_phonetically(next_date)
                next_periods = [p for p in ("morning", "afternoon", "evening") if any(calendar_service.classify_period(s) == p for s in next_slots)]
                return f"I could not find any open slots on that date for {settings.CLINIC_NAME}. The closest openings are on {next_date_phonetic} in the {' and '.join(next_periods)}. Would you like to check that day?"
            return f"I could not find any open slots on that date for {settings.CLINIC_NAME}. Would you like to check another day?"

        if len(available_periods) == 1:
            period_name = available_periods[0]
            period_slots = slots_by_period[period_name]
            spoken = [format_slot_phonetically(s) for s in period_slots[:3]]
            if len(spoken) == 1:
                return f"We have an opening on {spoken[0]}. Does that time suit you?"
            if len(spoken) == 2:
                return f"We have openings on {spoken[0]} or {spoken[1]}. Which time suits you best?"
            return f"We have openings on {spoken[0]}, {spoken[1]}, or {spoken[2]}. Which time suits you best?"

        if len(available_periods) == 2:
            return f"On {phonetic_date}, we have openings in the {available_periods[0]} and {available_periods[1]}. Would you prefer {available_periods[0]} or {available_periods[1]}?"

        return f"On {phonetic_date}, we have openings in the morning, afternoon, and evening. Which period do you prefer?"

    except Exception as exc:
        logger.warning("Error checking availability: %s", exc)
        return "I had trouble checking the schedule just now. Could you please repeat your preferred date?"


@function_tool()
async def book_appointment(
    context: RunContext,
    patient_name: Annotated[str, "Full name of the patient"],
    patient_phone: Annotated[str, "Patient phone number for appointment contact"],
    slot_time: Annotated[str, "Selected ISO timestamp or date time slot for booking"],
) -> str:
    """Book a confirmed appointment slot for the patient."""
    context.disallow_interruptions()

    try:
        res = await calendar_service.book_slot(slot_time, patient_name, patient_phone)
        if not res.get("success"):
            return str(res.get("error", "That slot is unavailable. Please choose another time."))

        phonetic_time = format_slot_phonetically(slot_time)
        return f"You are all set, {patient_name}. Your appointment is confirmed for {phonetic_time}."
    except Exception as exc:
        logger.warning("Error booking appointment: %s", exc)
        return "I was unable to finalize the booking due to a temporary glitch. Let us try once more."


@function_tool()
async def send_confirmation_sms(
    context: RunContext,
    patient_phone: Annotated[str, "Phone number to receive the confirmation text"],
    appointment_summary: Annotated[str, "Plain text summary of the appointment date and time"],
) -> str:
    """Send an SMS confirmation to the patient."""
    try:
        message = f"Hello from {settings.CLINIC_NAME}. Your visit is confirmed for {appointment_summary}."
        res = await sms_service.send_confirmation(patient_phone, message)
        if res.get("success"):
            return "I have sent a confirmation text message to your phone."
        return "I booked your appointment, but our text service could not deliver the confirmation message."
    except Exception as exc:
        logger.warning("Error sending confirmation SMS: %s", exc)
        return "Your appointment is confirmed, though I could not send the text message at this moment."
