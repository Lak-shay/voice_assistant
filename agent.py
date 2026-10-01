from __future__ import annotations

import asyncio
import sys

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


class ClinicReceptionistAgent(Agent):
    def __init__(self) -> None:
        instructions = (
            f"You are the friendly, professional voice receptionist for {settings.CLINIC_NAME}. "
            "Your role is to answer questions and schedule appointments over the phone. "
            "Follow these dialogue rules strictly: "
            "1. Speak in 1 to 2 short conversational sentences per turn. "
            "2. Never output markdown characters like asterisks, hashes, bullet points, or raw web URLs. "
            "3. Say all dates, times, and phone numbers phonetically in words. "
            "4. When a caller wants to book, check availability first to offer open slots, "
            "then ask for their name and phone number before calling book_appointment. "
            "5. Once booked, offer to text them the confirmation."
        )
        super().__init__(instructions=instructions)

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions=f"Warmly greet the caller on behalf of {settings.CLINIC_NAME} in one short sentence and ask how you can help them."
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
        )
    )
