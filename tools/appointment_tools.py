from __future__ import annotations

import datetime
from typing import Annotated

from livekit.agents import RunContext, function_tool

from config import settings
from logger import logger
from tools.calendar_service import calendar_service
from tools.sms_service import sms_service


def format_slot_phonetically(slot_iso: str) -> str:
    """Convert an ISO datetime string into natural phonetic words for voice synthesis."""
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


@function_tool()
async def check_availability(
    context: RunContext,
    preferred_date: Annotated[str, "Date in YYYY-MM-DD format to check clinic appointment openings"],
) -> str:
    """Check open appointment slots for a given date."""
    try:
        slots = await calendar_service.get_available_slots(preferred_date)
        if not slots:
            return f"I could not find any open slots on that date for {settings.CLINIC_NAME}. Would you like to check the following day?"

        spoken_slots = [format_slot_phonetically(s) for s in slots[:3]]
        return f"We have openings on {spoken_slots[0]}, {spoken_slots[1]}, or {spoken_slots[2]}. Which time suits you best?"
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
    # Prevent user speech from interrupting state mutation mid-request
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
