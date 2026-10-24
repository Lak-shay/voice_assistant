from __future__ import annotations

from typing import Annotated

from dateutil import parser
from livekit import rtc
from livekit.agents import JobContext, RunContext, SimulationContext, function_tool
from pydantic import Field

from clinic_time import parse_flexible_date
from config import settings
from guardrails.tool_guardrails import (
    validate_booking_date_range,
    validate_patient_name,
    validate_patient_phone,
)
from logger import logger
from session_state import BookingRecord, CallUserdata, TransferRecord
from tools.calendar_service import PERIODS, calendar_service, classify_period
from tools.phone import sanitize_e164
from tools.sms_service import sms_service
from tools.speech_format import format_date_phonetically, format_slot_phonetically, format_weekday_date

TRANSFERRING_REPLY = "I am transferring you to our front desk now. Please hold while I connect your call."
TRANSFER_FAILED_REPLY = "I was unable to transfer your call right now. Please call our clinic directly."


def _format_spoken_slot_choices(spoken: list[str], prefix: str = "We have ") -> str:
    if len(spoken) == 1:
        return f"{prefix}an opening on {spoken[0]}. Does that time suit you?"
    if len(spoken) == 2:
        return f"{prefix}openings on {spoken[0]} or {spoken[1]}. Which time suits you best?"
    return f"{prefix}openings on {spoken[0]}, {spoken[1]}, or {spoken[2]}. Which time suits you best?"


def _describe_periods(periods: list[str]) -> str:
    if len(periods) == 1:
        return f"in the {periods[0]}. Would you like {periods[0]}?"
    if len(periods) == 2:
        return f"in the {periods[0]} and {periods[1]}. Would you prefer {periods[0]} or {periods[1]}?"
    return f"in the {', '.join(periods[:-1])}, and {periods[-1]}. Which period works best for you?"


def _active_periods(slots: list[str]) -> list[str]:
    present = {classify_period(s) for s in slots}
    return [p for p in PERIODS if p in present]


def normalize_time_str(time_str: str) -> str | None:
    """'2 PM' / '14:00' / '10' -> 'HH:MM:00'. A bare hour of 9-11 is read as AM, anything else as PM."""
    if not time_str:
        return None
    cleaned = time_str.strip().upper()
    if cleaned.isdigit():
        h = int(cleaned)
        if 1 <= h <= 12:
            cleaned = f"{h} {'AM' if 9 <= h <= 11 else 'PM'}"
    try:
        t = parser.parse(cleaned).time()
    except (ValueError, OverflowError, parser.ParserError):
        return None
    return f"{t.hour:02d}:{t.minute:02d}:00"


async def _fully_booked_reply(date_str: str, spoken_date: str) -> str:
    nearby = await calendar_service.find_next_available_day(date_str)
    if not nearby:
        return f"We are fully booked on {spoken_date}. Would you like to check another week?"
    next_date, next_slots = nearby
    return (
        f"We are fully booked on {spoken_date}. The closest openings are on "
        f"{format_date_phonetically(next_date)} in the {' and '.join(_active_periods(next_slots))}. "
        "Would you like to check that day?"
    )


@function_tool()
async def check_availability(
    context: RunContext[CallUserdata],
    preferred_date: Annotated[
        str,
        Field(description="Date or relative phrase for the appointment (e.g. 'next Thursday', 'tomorrow', 'this Friday', 'next Monday', or YYYY-MM-DD). Pass relative phrases directly."),
    ],
    preferred_time: Annotated[
        str | None,
        Field(default=None, description="Optional specific time (e.g. '10:00 AM', '2:00 PM', '14:00') requested by the caller"),
    ] = None,
    preferred_period: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                f"Optional period of the day: 'morning' (before {settings.AFTERNOON_START_HOUR}:00), "
                f"'afternoon' ({settings.AFTERNOON_START_HOUR}:00 to {settings.EVENING_START_HOUR}:00), or "
                f"'evening' ({settings.EVENING_START_HOUR}:00 and later, including late afternoon)"
            ),
        ),
    ] = None,
) -> str:
    """Check open appointment slots for a given date, specific time, or time of day."""
    try:
        target_date = parse_flexible_date(preferred_date)
        if not target_date:
            return "I did not catch that date. Which day would you like to come in?"

        is_valid_date, date_err = validate_booking_date_range(target_date)
        if not is_valid_date:
            return date_err or "Please provide a valid date."

        date_str = target_date.isoformat()
        spoken_date = format_weekday_date(target_date)
        slots_by_period = await calendar_service.get_slots_by_period(date_str)
        available_periods = [p for p in PERIODS if slots_by_period[p]]

        if not available_periods:
            return await _fully_booked_reply(date_str, spoken_date)

        if preferred_time:
            norm_time = normalize_time_str(preferred_time)
            target_slot = f"{date_str}T{norm_time}" if norm_time else None
            if target_slot and any(target_slot in slots for slots in slots_by_period.values()):
                return f"{format_slot_phonetically(target_slot)} is open. Would you like me to book that for you?"
            return f"That exact time is unavailable on {spoken_date}, but we have openings {_describe_periods(available_periods)}"

        if preferred_period:
            period_key = preferred_period.lower().strip()
            period_slots = slots_by_period.get(period_key, [])
            if period_slots:
                spoken = [format_slot_phonetically(s) for s in period_slots[:3]]
                return _format_spoken_slot_choices(spoken, prefix=f"In the {period_key} on {spoken_date}, we have ")
            return (
                f"We have no {period_key} openings on {spoken_date}, but we have slots in the "
                f"{' and '.join(available_periods)}. Would either of those work for you?"
            )

        if len(available_periods) == 1:
            spoken = [format_slot_phonetically(s) for s in slots_by_period[available_periods[0]][:3]]
            return _format_spoken_slot_choices(spoken)
        return f"On {spoken_date}, we have openings {_describe_periods(available_periods)}"
    except Exception as exc:
        logger.warning("Error checking availability: %s", exc)
        return "I had trouble checking the schedule just now. Could you please repeat your preferred date?"


@function_tool()
async def book_appointment(
    context: RunContext[CallUserdata],
    patient_name: Annotated[
        str,
        Field(description="Full name of the patient"),
    ],
    patient_phone: Annotated[
        str,
        Field(description="Patient phone number for appointment contact (e.g. +1XXXXXXXXXX)"),
    ],
    slot_time: Annotated[
        str,
        Field(description="Selected appointment slot date and time (e.g. '2026-10-05T10:00:00' or 'October 5th at 10:00 AM')"),
    ],
) -> str:
    """Book a confirmed appointment slot for the patient."""
    context.disallow_interruptions()

    try:
        valid_name, name_err = validate_patient_name(patient_name)
        if not valid_name:
            return name_err or "A full patient name is required to complete the booking."

        valid_phone, clean_phone, phone_err = validate_patient_phone(patient_phone)
        if not valid_phone or not clean_phone:
            return phone_err or "A valid phone number is required to complete the booking."

        is_valid_date, date_err = validate_booking_date_range(slot_time)
        if not is_valid_date:
            return date_err or "Please choose an upcoming appointment date."

        clean_name = patient_name.strip()
        res = await calendar_service.book_slot(slot_time, clean_name, clean_phone)
        if not res.get("success"):
            return str(res.get("error", "That slot is unavailable. Please choose another time."))

        booked_slot = res.get("slot_time", slot_time)
        context.userdata.last_booking = BookingRecord(
            patient_name=clean_name,
            patient_phone=clean_phone,
            slot_time=booked_slot,
        )
        logger.info("Booked appointment slot %s", booked_slot)
        return f"You are all set, {clean_name}. Your appointment is confirmed for {format_slot_phonetically(booked_slot)}."
    except Exception as exc:
        logger.warning("Error booking appointment: %s", exc)
        return "I was unable to finalize the booking due to a temporary glitch. Let us try once more."


@function_tool()
async def send_confirmation_sms(context: RunContext[CallUserdata]) -> str:
    """Text the patient a confirmation of the appointment booked on this call, to the phone number used for that booking."""
    booking = context.userdata.last_booking
    if booking is None:
        return "I can only send a confirmation text after an appointment has been booked on this call."

    try:
        message = (
            f"Hello from {settings.CLINIC_NAME}. "
            f"Your visit is confirmed for {format_slot_phonetically(booking.slot_time)}."
        )
        res = await sms_service.send_confirmation(booking.patient_phone, message)
        if res.get("success"):
            return "You will receive the confirmation message on your phone shortly."
        return "I booked your appointment, but our text service could not deliver the confirmation message."
    except Exception as exc:
        logger.warning("Error sending confirmation SMS: %s", exc)
        return "Your appointment is confirmed, though I could not send the text message at this moment."


def _find_caller(room: rtc.Room) -> rtc.RemoteParticipant | None:
    participants = list(room.remote_participants.values())
    sip = next((p for p in participants if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP), None)
    return sip or (participants[0] if participants else None)


def _is_simulation(job_ctx: JobContext) -> bool:
    return isinstance(job_ctx.simulation_context(), SimulationContext)


@function_tool()
async def transfer_to_human(
    context: RunContext[CallUserdata],
    reason: Annotated[
        str | None,
        Field(default=None, description="Optional brief reason for transferring the call to front desk staff"),
    ] = None,
) -> str:
    """Transfer the caller to a human receptionist or front desk staff."""
    context.disallow_interruptions()
    failover_phone = sanitize_e164(settings.CLINIC_FAILOVER_PHONE)
    if not failover_phone:
        logger.warning("CLINIC_FAILOVER_PHONE is not configured or invalid: %s", settings.CLINIC_FAILOVER_PHONE)
        return "I am unable to transfer your call because the front desk phone number is not configured."

    def record(transferred: bool) -> None:
        context.userdata.last_transfer = TransferRecord(failover_phone=failover_phone, reason=reason, transferred=transferred)

    job_ctx = context.userdata.job_ctx
    # Simulated calls have no SIP leg to transfer, so the intent is recorded for the simulation assertions.
    if job_ctx is None or _is_simulation(job_ctx):
        logger.info("Simulated transfer to front desk %s (reason: %s)", failover_phone, reason)
        record(True)
        return TRANSFERRING_REPLY

    caller = _find_caller(job_ctx.room)
    if caller is None:
        logger.warning("No remote caller participant found to transfer.")
        record(False)
        return TRANSFER_FAILED_REPLY

    logger.info("Transferring participant %s to %s (reason: %s)", caller.identity, failover_phone, reason)
    try:
        await job_ctx.transfer_sip_participant(caller.identity, f"tel:{failover_phone}", play_dialtone=False)
    except Exception as exc:
        logger.error("SIP transfer to %s failed: %s", failover_phone, exc)
        record(False)
        return TRANSFER_FAILED_REPLY

    record(True)
    return TRANSFERRING_REPLY
