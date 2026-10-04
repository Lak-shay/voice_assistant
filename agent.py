from __future__ import annotations

import asyncio
import datetime
import sys
from zoneinfo import ZoneInfo

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    SimulationContext,
    cli,
    inference,
    room_io,
)
from livekit.agents.worker import JobExecutorType
from livekit.plugins import silero

from config import settings
from logger import logger
from startup import run_startup_checks
from telemetry import telemetry
from tools.appointment_tools import (
    book_appointment,
    check_availability,
    get_sip_caller_phone,
    send_confirmation_sms,
)


def get_current_clinic_time() -> tuple[str, str]:
    try:
        clinic_tz = ZoneInfo(settings.CLINIC_TIMEZONE)
    except Exception:
        clinic_tz = datetime.timezone.utc
    now = datetime.datetime.now(clinic_tz)
    today_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%I:%M %p").lstrip("0")
    return today_str, time_str


class ClinicReceptionistAgent(Agent):
    def __init__(self, caller_phone: str | None = None) -> None:
        today_str, time_str = get_current_clinic_time()
        if caller_phone:
            intake_instruction = (
                f"The caller's phone number is already available via caller ID as {caller_phone}. "
                "When the customer agrees to an open slot, ask for their full name first. "
                "Once they provide their name, confirm the chosen appointment date and time with them. "
                f"Only after they confirm, call book_appointment passing their name and patient_phone={caller_phone}. "
                "Never call send_confirmation_sms in parallel with book_appointment; only call send_confirmation_sms in a subsequent turn after book_appointment returns success."
            )
        else:
            intake_instruction = (
                "No phone number was received from caller ID. "
                "When the customer agrees to an open slot, ask for their full name and phone number. "
                "Once provided, confirm the chosen appointment date, time, and phone number with them. "
                "Only after they confirm, call book_appointment with their details. "
                "Never call send_confirmation_sms in parallel with book_appointment; only call send_confirmation_sms in a subsequent turn after book_appointment returns success."
            )

        instructions = (
            f"You are the friendly, professional voice receptionist for {settings.CLINIC_NAME}. "
            f"Today's date is {today_str}. The current clinic time is {time_str} in timezone {settings.CLINIC_TIMEZONE}. "
            "Use this live date and time to accurately resolve relative dates like today, tomorrow, this afternoon, or next Monday. "
            "Follow these dialogue rules strictly: "
            "1. Speak in 1 to 2 short conversational sentences per turn. "
            "2. Never output markdown characters like asterisks, hashes, bullet points, or raw web URLs. "
            "3. In spoken responses to the caller, say all dates, times, and phone numbers phonetically in words. When calling tools, pass dates in standard YYYY-MM-DD format and pass slot_time in ISO format YYYY-MM-DDTHH:MM:SS. "
            "4. Follow the appointment booking flow: "
            "   a. When the customer provides their preferred date and time, call check_availability for that date and time. "
            "   b. If their exact requested time is unavailable but other slots exist on that day, offer only the periods (morning, afternoon, or evening) that actually have open slots, and ask which period they prefer. Never ask for periods that have no openings. "
            "   c. Once they choose a period, offer the specific open times in that period. Note that slots at 4 PM and later are classified as evening. If a caller asks for after 4 PM or late in the day, check evening slots or specify preferred_time. "
            "   d. If the customer rejects an offered slot (for example, saying 'No I do not want that slot'), politely reply by asking: 'When would you like the appointment?' "
            "   e. Whenever the customer asks 'When is it available?' or proposes any new date or time (including fallback choices like 10:30 AM), always call check_availability for that specific date and time before answering. Never assert a time is unavailable without checking it with check_availability first. "
            f"   f. {intake_instruction}"
            "   g. Only call book_appointment after the customer explicitly confirms the restated date and time. "
            "   h. If book_appointment returns that a slot was just taken or unavailable, apologize and explain that another caller just booked that exact slot. Retain their already-collected name and phone number in memory without asking for them or asking to re-confirm them again. Immediately offer the remaining open slots on that day, and ask them to choose another slot. When they choose an alternative slot, simply ask to confirm the new slot time (e.g. 'Just to confirm, would you like to book October twenty-fifth at ten thirty AM?'), and upon their yes, immediately call book_appointment with their retained name and phone number. "
            "5. Once booked, offer to send a confirmation text message."
        )
        super().__init__(instructions=instructions)

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions=(
                f"Greet the caller on behalf of {settings.CLINIC_NAME} in one short sentence by saying: "
                f"'Welcome to {settings.CLINIC_NAME}. How can I help you?'"
            )
        )


server = AgentServer(
    num_idle_processes=settings.NUM_IDLE_PROCESSES,
    job_executor_type=JobExecutorType.PROCESS,
    initialize_process_timeout=30.0,
)


async def on_simulation_end(ctx: SimulationContext) -> None:
    expected = ctx.userdata().get("expected_booking")
    if not expected:
        return

    session = getattr(ctx.job_context, "primary_session", None)
    userdata = getattr(session, "userdata", {}) if session else {}
    last_booking = userdata.get("last_booking") if isinstance(userdata, dict) else None

    if not last_booking:
        ctx.fail(reason="No appointment was actually booked in the calendar backend.")
        return

    if not last_booking.get("success"):
        ctx.fail(reason="Calendar backend booking was recorded as unsuccessful.")
        return

    expected_name = expected.get("patient_name")
    if expected_name and last_booking.get("patient_name") != expected_name:
        ctx.fail(
            reason=f"Patient name mismatch: expected '{expected_name}', but got '{last_booking.get('patient_name')}'"
        )
        return

    expected_phone = expected.get("patient_phone")
    if expected_phone and last_booking.get("patient_phone") != expected_phone:
        ctx.fail(
            reason=f"Patient phone mismatch: expected '{expected_phone}', but got '{last_booking.get('patient_phone')}'"
        )
        return

    expected_date = expected.get("slot_date")
    if expected_date and expected_date not in str(last_booking.get("slot_time", "")):
        ctx.fail(
            reason=f"Slot date mismatch: expected '{expected_date}' in slot, but got '{last_booking.get('slot_time')}'"
        )
        return


@server.rtc_session(agent_name="clinic-receptionist", on_simulation_end=on_simulation_end)
async def entrypoint(ctx: JobContext) -> None:
    trace_id = ctx.room.name
    logger.info("[%s] Inbound call received. Connecting to room...", trace_id)
    await ctx.connect()
    logger.info("[%s] Connected to room. Initializing pipeline...", trace_id)

    telemetry.create_session_trace(trace_id)

    async def on_shutdown() -> None:
        logger.info("[%s] Call ended. Flushing telemetry...", trace_id)
        await telemetry.flush(trace_id)

    ctx.add_shutdown_callback(on_shutdown)

    @ctx.room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        logger.debug(
            "[%s] Participant connected: identity=%s, kind=%s, attributes=%s",
            trace_id,
            getattr(participant, "identity", None),
            getattr(participant, "kind", None),
            getattr(participant, "attributes", None),
        )

    @ctx.room.on("participant_attributes_changed")
    def on_attributes_changed(changed_attributes: dict[str, str], participant: rtc.RemoteParticipant) -> None:
        logger.debug(
            "[%s] Participant attributes updated: changed=%s, all=%s",
            trace_id,
            changed_attributes,
            getattr(participant, "attributes", None),
        )

    caller_phone = get_sip_caller_phone(ctx.room)
    sim = ctx.simulation_context() if hasattr(ctx, "simulation_context") else None
    if sim and "caller_phone" in sim.userdata():
        caller_phone = sim.userdata().get("caller_phone")
    if caller_phone:
        logger.info("[%s] Detected caller phone: %s", trace_id, caller_phone)
    else:
        logger.info("[%s] No caller phone detected from caller ID", trace_id)

    vad = silero.VAD.load(
        min_speech_duration=settings.VAD_MIN_SPEECH_DURATION,
        min_silence_duration=settings.VAD_MIN_SILENCE_DURATION,
    )

    stt = inference.STT(
        f"{settings.STT_PROVIDER}/{settings.STT_MODEL}",
        api_key=settings.LIVEKIT_API_KEY or None,
        api_secret=settings.LIVEKIT_API_SECRET or None,
    )
    llm = inference.LLM(
        f"{settings.LLM_PROVIDER}/{settings.LLM_MODEL}",
        api_key=settings.LIVEKIT_API_KEY or None,
        api_secret=settings.LIVEKIT_API_SECRET or None,
    )
    tts = inference.TTS(
        f"{settings.TTS_PROVIDER}/{settings.TTS_MODEL}",
        voice=settings.TTS_VOICE_ID,
        api_key=settings.LIVEKIT_API_KEY or None,
        api_secret=settings.LIVEKIT_API_SECRET or None,
    )

    session = AgentSession(
        vad=vad,
        stt=stt,
        llm=llm,
        tts=tts,
        tools=[check_availability, book_appointment, send_confirmation_sms],
        userdata={
            "room": ctx.room,
            "trace_id": trace_id,
            "caller_phone": caller_phone,
        },
    )

    await session.start(
        agent=ClinicReceptionistAgent(caller_phone=caller_phone),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(),
        ),
    )



if __name__ == "__main__":
    is_ready = asyncio.run(run_startup_checks())
    if not is_ready:
        logger.error("Startup readiness verification failed; aborting worker.")
        sys.exit(1)

    cli.run_app(server)
