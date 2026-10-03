from __future__ import annotations

import asyncio
import datetime
import sys
from zoneinfo import ZoneInfo

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
    def __init__(self) -> None:
        today_str, time_str = get_current_clinic_time()
        instructions = (
            f"You are the friendly, professional voice receptionist for {settings.CLINIC_NAME}. "
            f"Today's date is {today_str}. The current clinic time is {time_str} in timezone {settings.CLINIC_TIMEZONE}. "
            "Use this live date and time to accurately resolve relative dates like today, tomorrow, this afternoon, or next Monday. "
            "Follow these dialogue rules strictly: "
            "1. Speak in 1 to 2 short conversational sentences per turn. "
            "2. Never output markdown characters like asterisks, hashes, bullet points, or raw web URLs. "
            "3. In spoken responses to the caller, say all dates, times, and phone numbers phonetically in words. When calling tools, pass dates in standard YYYY-MM-DD format. "
            "4. Follow the appointment booking flow: "
            "   a. When the customer provides their preferred date and time, call check_availability for that date and time. "
            "   b. If their exact requested time is unavailable but other slots exist on that day, offer only the periods (morning, afternoon, or evening) that actually have open slots, and ask which period they prefer. Never ask for periods that have no openings. "
            "   c. Once they choose a period, offer the specific open times in that period. "
            "   d. If the customer rejects an offered slot (for example, saying 'No I do not want that slot'), politely reply by asking: 'When would you like the appointment?' "
            "   e. If the customer asks 'When is it available?' or provides an updated date and time, check availability around their original requested date and times and offer the nearest options. "
            "   f. When the customer agrees to an open slot, ask for their full name and phone number. "
            "   g. Before calling book_appointment, you must explicitly confirm the chosen appointment date and time with the customer (for example: 'Just to confirm, would you like me to book your appointment for [date and time]?'). Only call book_appointment after the customer confirms. "
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
    )

    await session.start(
        agent=ClinicReceptionistAgent(),
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
