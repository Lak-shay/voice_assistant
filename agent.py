from __future__ import annotations

import asyncio
import datetime
import sys
from zoneinfo import ZoneInfo

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
    inference,
    room_io,
)
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
                "When the customer agrees to an open slot, ask only for their full name. "
                "Before calling book_appointment, confirm only the chosen appointment date and time with the customer "
                "(for example: 'Just to confirm, would you like me to book your appointment for [date and time]?'). "
                f"When calling book_appointment and send_confirmation_sms, pass {caller_phone} as the patient_phone. "
            )
        else:
            intake_instruction = (
                "No phone number was received from caller ID. "
                "When the customer agrees to an open slot, you must ask for both their full name and their phone number. "
                "Before calling book_appointment, you must explicitly confirm the chosen appointment date, time, and phone number with the customer "
                "(for example: 'Just to confirm, would you like me to book your appointment for [date and time] with phone number [phone number spoken phonetically in digits]?'). "
                "When calling book_appointment and send_confirmation_sms, pass their provided phone number as the patient_phone. "
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
            "   c. Once they choose a period, offer the specific open times in that period. "
            "   d. If the customer rejects an offered slot (for example, saying 'No I do not want that slot'), politely reply by asking: 'When would you like the appointment?' "
            "   e. If the customer asks 'When is it available?' or provides an updated date and time, check availability around their original requested date and times and offer the nearest options. "
            f"   f. {intake_instruction}"
            "   g. Only call book_appointment after the customer confirms. "
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
    if caller_phone:
        logger.info("[%s] Detected Telnyx SIP caller phone: %s", trace_id, caller_phone)
    else:
        logger.info("[%s] No SIP caller phone detected from caller ID (Playground/WebRTC session)", trace_id)

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

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            ws_url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
            agent_name="clinic-receptionist",
        )
    )
