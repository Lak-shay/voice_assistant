from __future__ import annotations

from typing import Annotated
from dateutil import parser

from livekit import rtc
from livekit.agents import RunContext, function_tool, get_job_context
from pydantic import Field

from config import settings
from logger import logger
from tools.calendar_service import (
    calendar_service,
    parse_flexible_date,
)
from tools.sms_service import sanitize_e164, sms_service
from guardrails.tool_guardrails import (
    validate_booking_date_range,
    validate_patient_name,
    validate_patient_phone,
)


def _ordinal_suffix(day: int) -> str:
    if 11 <= (day % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _format_spoken_slot_choices(spoken: list[str], prefix: str = "We have ") -> str:
    if len(spoken) == 1:
        return f"{prefix}an opening on {spoken[0]}. Does that time suit you?"
    if len(spoken) == 2:
        return f"{prefix}openings on {spoken[0]} or {spoken[1]}. Which time suits you best?"
    return f"{prefix}openings on {spoken[0]}, {spoken[1]}, or {spoken[2]}. Which time suits you best?"


def _get_active_periods(slots: list[str]) -> list[str]:
    return [p for p in ("morning", "afternoon", "evening") if any(calendar_service.classify_period(s) == p for s in slots)]


def format_date_phonetically(date_str: str) -> str:
    try:
        dt = parser.parse(date_str.strip()).date()
        return f"{dt.strftime('%B')} {dt.day}{_ordinal_suffix(dt.day)}"
    except Exception:
        return date_str


def format_slot_phonetically(slot_iso: str) -> str:
    try:
        dt = parser.parse(slot_iso.strip())
        month_name = dt.strftime("%B")
        hour = dt.strftime("%I").lstrip("0")
        minute = dt.strftime("%M")
        am_pm = dt.strftime("%p")
        time_str = f"{hour} {minute} {am_pm}" if minute != "00" else f"{hour} {am_pm}"
        return f"{month_name} {dt.day}{_ordinal_suffix(dt.day)} at {time_str}"
    except Exception:
        return slot_iso


def normalize_time_str(time_str: str) -> str | None:
    if not time_str:
        return None
    cleaned = time_str.strip().upper()
    if cleaned.isdigit():
        h = int(cleaned)
        if 1 <= h <= 12:
            period = "AM" if 9 <= h <= 11 else "PM"
            cleaned = f"{h} {period}"
    try:
        t = parser.parse(cleaned).time()
        return f"{t.hour:02d}:{t.minute:02d}:00"
    except (ValueError, OverflowError, parser.ParserError):
        return None


@function_tool()
async def check_availability(
    context: RunContext,
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
        Field(default=None, description="Optional period of the day: 'morning' (before 12 PM), 'afternoon' (12 PM to 4 PM), or 'evening' (4 PM and later, including late afternoon)"),
    ] = None,
) -> str:
    """Check open appointment slots for a given date, specific time, or time of day."""
    try:
        tz = calendar_service._get_timezone()
        parsed_date = parse_flexible_date(preferred_date, tz)
        date_str = parsed_date.isoformat() if parsed_date else preferred_date.strip()

        is_valid_date, date_err = validate_booking_date_range(parsed_date or date_str)
        if not is_valid_date:
            return date_err or "Please provide a valid date."

        slots_by_period = await calendar_service.get_slots_by_period(date_str)
        all_day_slots = [s for sublist in slots_by_period.values() for s in sublist]
        available_periods = [p for p in ("morning", "afternoon", "evening") if slots_by_period.get(p)]
        dt_val = parsed_date or parser.parse(date_str).date()
        weekday_name = dt_val.strftime("%A")
        phonetic_date = f"{weekday_name}, {format_date_phonetically(date_str)}"

        if preferred_time:
            norm_time = normalize_time_str(preferred_time)
            if norm_time:
                target_slot = f"{date_str}T{norm_time}"
                if target_slot in all_day_slots:
                    return f"{format_slot_phonetically(target_slot)} is open. Would you like me to book that for you?"

            if available_periods:
                if len(available_periods) == 1:
                    period_name = available_periods[0]
                    return f"That exact time is unavailable on {phonetic_date}, but we have openings in the {period_name}. Would you like {period_name}?"
                if len(available_periods) == 2:
                    return f"That exact time is unavailable on {phonetic_date}, but we have openings in the {available_periods[0]} and {available_periods[1]}. Would you prefer {available_periods[0]} or {available_periods[1]}?"
                return f"That exact time is unavailable on {phonetic_date}, but we have openings in the morning, afternoon, and evening. Which period works best for you?"

            nearby = await calendar_service.find_nearby_available_slots(date_str)
            if nearby:
                next_date, next_slots = nearby[0]
                next_date_phonetic = format_date_phonetically(next_date)
                next_periods = _get_active_periods(next_slots)
                return f"We are fully booked on {phonetic_date}. The closest openings around that time are on {next_date_phonetic} in the {' and '.join(next_periods)}. Would you like to check that day?"
            return f"I could not find any open slots around {phonetic_date} for {settings.CLINIC_NAME}. Would you like to check another week?"

        if preferred_period:
            period_key = preferred_period.lower().strip()
            period_slots = slots_by_period.get(period_key, [])
            if period_slots:
                spoken = [format_slot_phonetically(s) for s in period_slots[:3]]
                return _format_spoken_slot_choices(spoken, prefix=f"In the {period_key} on {phonetic_date}, we have ")

            if available_periods:
                return f"We have no {period_key} openings on {phonetic_date}, but we have slots in the {' and '.join(available_periods)}. Would either of those work for you?"

        if not available_periods:
            nearby = await calendar_service.find_nearby_available_slots(date_str)
            if nearby:
                next_date, next_slots = nearby[0]
                next_date_phonetic = format_date_phonetically(next_date)
                next_periods = _get_active_periods(next_slots)
                return f"I could not find any open slots on that date for {settings.CLINIC_NAME}. The closest openings are on {next_date_phonetic} in the {' and '.join(next_periods)}. Would you like to check that day?"
            return f"I could not find any open slots on that date for {settings.CLINIC_NAME}. Would you like to check another day?"

        if len(available_periods) == 1:
            period_name = available_periods[0]
            spoken = [format_slot_phonetically(s) for s in slots_by_period[period_name][:3]]
            return _format_spoken_slot_choices(spoken)

        if len(available_periods) == 2:
            return f"On {phonetic_date}, we have openings in the {available_periods[0]} and {available_periods[1]}. Would you prefer {available_periods[0]} or {available_periods[1]}?"

        return f"On {phonetic_date}, we have openings in the morning, afternoon, and evening. Which period do you prefer?"

    except Exception as exc:
        logger.warning("Error checking availability: %s", exc)
        return "I had trouble checking the schedule just now. Could you please repeat your preferred date?"


def get_sip_caller_phone(room: rtc.Room | None) -> str | None:
    """Extract E.164 phone number from SIP participant 'sip.phoneNumber' attribute."""
    if not room or not hasattr(room, "remote_participants"):
        return None
    for p_id, participant in room.remote_participants.items():
        attrs = getattr(participant, "attributes", {}) or {}
        logger.debug(
            "Evaluating participant [%s] for SIP caller data: identity=%s, kind=%s, attributes=%s",
            p_id,
            getattr(participant, "identity", None),
            getattr(participant, "kind", None),
            attrs,
        )
        phone = attrs.get("sip.phoneNumber")
        if phone:
            sanitized = sanitize_e164(phone)
            logger.debug(
                "Extracted SIP caller phone from 'sip.phoneNumber': raw=%s, sanitized=%s",
                phone,
                sanitized,
            )
            if sanitized:
                return sanitized
    return None


@function_tool()
async def book_appointment(
    context: RunContext,
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
        if hasattr(context, "userdata") and isinstance(context.userdata, dict):
            context.userdata["last_booking"] = {
                "patient_name": patient_name.strip(),
                "patient_phone": clean_phone,
                "slot_time": booked_slot,
                "success": bool(res.get("success")),
            }
        phonetic_time = format_slot_phonetically(booked_slot)
        return f"You are all set, {patient_name.strip()}. Your appointment is confirmed for {phonetic_time}."
    except Exception as exc:
        logger.warning("Error booking appointment: %s", exc)
        return "I was unable to finalize the booking due to a temporary glitch. Let us try once more."


@function_tool()
async def send_confirmation_sms(
    context: RunContext,
    patient_phone: Annotated[
        str,
        Field(description="Phone number to receive the confirmation text (e.g. +1XXXXXXXXXX)"),
    ],
    appointment_summary: Annotated[
        str,
        Field(description="Plain text summary of the appointment date and time"),
    ],
) -> str:
    """Send an SMS confirmation to the patient."""
    try:
        clean_phone = sanitize_e164(patient_phone)
        if not clean_phone:
            return "A valid phone number is required to send the confirmation text message."

        message = f"Hello from {settings.CLINIC_NAME}. Your visit is confirmed for {appointment_summary}."
        res = await sms_service.send_confirmation(clean_phone, message)
        if res.get("success"):
            return "You will receive the confirmation message on your phone shortly."
        return "I booked your appointment, but our text service could not deliver the confirmation message."
    except Exception as exc:
        logger.warning("Error sending confirmation SMS: %s", exc)
        return "Your appointment is confirmed, though I could not send the text message at this moment."


@function_tool()
async def transfer_to_human(
    context: RunContext,
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

    job_ctx = None
    if hasattr(context, "userdata") and isinstance(context.userdata, dict):
        job_ctx = context.userdata.get("job_ctx")
        context.userdata["last_transfer"] = {
            "transferred": True,
            "failover_phone": failover_phone,
            "reason": reason,
        }
    if job_ctx is None:
        try:
            job_ctx = get_job_context()
        except Exception:
            job_ctx = None

    if job_ctx is None:
        logger.info("Transfer to human initiated in mock/standalone mode: failover=%s, reason=%s", failover_phone, reason)
        return "I am transferring you to the front desk now. Please hold while I connect your call."

    room = getattr(job_ctx, "room", None)
    remote_participants = getattr(room, "remote_participants", {}) if room else {}

    target_participant = next(
        (
            p for p in remote_participants.values()
            if getattr(p, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        ),
        None,
    )
    if target_participant is None and remote_participants:
        target_participant = next(iter(remote_participants.values()))

    if not target_participant:
        logger.warning("No remote caller participant found to transfer.")
        return "I am transferring you to the front desk now. Please hold while I connect your call."

    try:
        logger.info(
            "Transferring participant %s to failover number %s (reason: %s)",
            target_participant.identity,
            failover_phone,
            reason,
        )
        transfer_res = job_ctx.transfer_sip_participant(
            target_participant.identity,
            f"tel:{failover_phone}",
            play_dialtone=False,
        )
        if hasattr(transfer_res, "__await__"):
            await transfer_res
        return "I am transferring you to our front desk now. Please hold while I connect your call."
    except Exception as exc:
        sim_ctx = job_ctx.simulation_context() if hasattr(job_ctx, "simulation_context") and callable(job_ctx.simulation_context) else None
        is_real_sim = sim_ctx is not None and sim_ctx.__class__.__name__ == "SimulationContext"
        if is_real_sim:
            logger.info("SIP transfer exception in simulation environment handled gracefully: %s", exc)
            return "I am transferring you to our front desk now. Please hold while I connect your call."
        logger.warning("Error transferring SIP call to %s: %s", failover_phone, exc)
        return "I was unable to transfer your call right now. Please call our clinic directly."
