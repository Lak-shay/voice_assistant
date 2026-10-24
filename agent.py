from __future__ import annotations

import asyncio
import contextlib
import sys

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    inference,
    llm,
    room_io,
)
from livekit.agents.llm import StopResponse
from livekit.agents.worker import JobExecutorType
from livekit.plugins import silero
from opentelemetry import trace as trace_api

from clinic_time import now_in_clinic
from config import settings
from guardrails.input_guardrails import check_input_guardrails
from guardrails.output_guardrails import mask_secrets_transform
from logger import bind_call_context, logger, standalone_logging
from prompts import build_instructions, greeting_instruction
from session_state import CallUserdata
from simulations.assertions import on_simulation_end
from startup import run_startup_checks
from telemetry import telemetry
from tools.appointment_tools import (
    book_appointment,
    check_availability,
    send_confirmation_sms,
    transfer_to_human,
)
from tools.phone import get_sip_caller_phone


def _evaluate_guardrails(user_text: str, userdata: CallUserdata) -> str | None:
    """Returns the scripted reply if the input guardrails block this turn, else None."""
    if not settings.GUARDRAILS_ENABLED or not user_text:
        return None

    result = check_input_guardrails(
        user_text,
        check_emergency=settings.GUARDRAILS_EMERGENCY_TRIAGE_ENABLED,
        check_injection=settings.GUARDRAILS_INJECTION_DEFENSE_ENABLED,
        check_medical_advice=settings.GUARDRAILS_MEDICAL_ADVICE_ENABLED,
    )
    if not (result.is_blocked and result.response_text):
        return None

    userdata.last_guardrail = result.reason
    logger.info("Input guardrail triggered: reason=%s", result.reason)
    telemetry.record_guardrail_event(
        userdata.trace_id,
        "input_guardrail_blocked",
        {"reason": result.reason, "user_text": user_text[:80]},
    )
    return result.response_text


class ClinicReceptionistAgent(Agent):
    def __init__(self, caller_phone: str | None = None) -> None:
        super().__init__(instructions=build_instructions(now_in_clinic(), caller_phone))

    async def on_enter(self) -> None:
        await self.session.generate_reply(instructions=greeting_instruction())

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        reply = _evaluate_guardrails(new_message.text_content or "", self.session.userdata)
        if reply:
            self.session.say(reply, allow_interruptions=False)
            raise StopResponse()


async def _guarded_text_input_cb(sess: AgentSession[CallUserdata], ev: room_io.TextInputEvent) -> None:
    # Same shape as livekit's default text_input_cb (which also uses the private _claim_user_turn),
    # with the input guardrails applied before the LLM sees the text.
    async with sess._claim_user_turn():
        await sess.interrupt()
        reply = _evaluate_guardrails(ev.text, sess.userdata)
        if reply:
            sess.say(reply, allow_interruptions=False)
            return
        sess.generate_reply(user_input=ev.text)


def _prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=settings.VAD_MIN_SPEECH_DURATION,
        min_silence_duration=settings.VAD_MIN_SILENCE_DURATION,
    )


server = AgentServer(
    ws_url=settings.LIVEKIT_URL,
    api_key=settings.LIVEKIT_API_KEY,
    api_secret=settings.LIVEKIT_API_SECRET,
    num_idle_processes=settings.NUM_IDLE_PROCESSES,
    job_executor_type=JobExecutorType.PROCESS,
    initialize_process_timeout=settings.PROCESS_INIT_TIMEOUT,
    setup_fnc=_prewarm,
)


async def _wait_for_caller(ctx: JobContext) -> rtc.RemoteParticipant | None:
    try:
        participant = await asyncio.wait_for(ctx.wait_for_participant(), timeout=settings.PARTICIPANT_WAIT_TIMEOUT)
    except asyncio.TimeoutError:
        logger.debug("Wait for participant timed out; proceeding with room check")
        return None
    except Exception as exc:
        logger.warning("Error waiting for participant: %s", exc)
        return None
    logger.info("Participant joined: identity=%s, kind=%s", participant.identity, participant.kind)
    return participant


async def _resolve_room_sid(room: rtc.Room) -> str:
    # rtc.Room.sid is an awaitable property; the server assigns it shortly after connect.
    try:
        return await asyncio.wait_for(room.sid, timeout=settings.ROOM_SID_TIMEOUT)
    except Exception as exc:
        logger.debug("Room sid unavailable: %s", exc)
        return ""


def _build_session(ctx: JobContext, trace_id: str, caller_phone: str | None) -> AgentSession[CallUserdata]:
    credentials = {
        "api_key": settings.LIVEKIT_API_KEY or None,
        "api_secret": settings.LIVEKIT_API_SECRET or None,
    }
    tts_text_transforms: list = ["filter_markdown", "filter_emoji"]
    if settings.GUARDRAILS_ENABLED and settings.GUARDRAILS_TTS_SANITIZER_ENABLED:
        tts_text_transforms.append(mask_secrets_transform)

    return AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=inference.STT(f"{settings.STT_PROVIDER}/{settings.STT_MODEL}", **credentials),
        llm=inference.LLM(f"{settings.LLM_PROVIDER}/{settings.LLM_MODEL}", **credentials),
        tts=inference.TTS(f"{settings.TTS_PROVIDER}/{settings.TTS_MODEL}", voice=settings.TTS_VOICE_ID, **credentials),
        tts_text_transforms=tts_text_transforms,
        tools=[check_availability, book_appointment, send_confirmation_sms, transfer_to_human],
        userdata=CallUserdata(trace_id=trace_id, caller_phone=caller_phone, job_ctx=ctx),
    )


@server.rtc_session(agent_name="clinic-receptionist", on_simulation_end=on_simulation_end)
async def entrypoint(ctx: JobContext) -> None:
    trace_id = ctx.room.name
    logger.info("Inbound call received. Connecting to room...")
    await ctx.connect()

    sim = ctx.simulation_context()
    participant = None if sim else await _wait_for_caller(ctx)
    participant_id = participant.identity if participant else None
    room_sid = await _resolve_room_sid(ctx.room)
    bind_call_context(room_sid=room_sid, participant_id=participant_id)
    logger.info("Connected to room (sid=%s). Initializing pipeline...", room_sid)

    telemetry.create_session_trace(trace_id=trace_id, room_sid=room_sid)

    async def on_shutdown() -> None:
        logger.info("Call ended. Flushing telemetry...")
        await telemetry.flush(trace_id)

    ctx.add_shutdown_callback(on_shutdown)

    @ctx.room.on("participant_connected")
    def on_participant_connected(remote_p: rtc.RemoteParticipant) -> None:
        logger.debug(
            "Participant connected: identity=%s, kind=%s, attributes=%s",
            remote_p.identity, remote_p.kind, remote_p.attributes,
        )

    @ctx.room.on("participant_attributes_changed")
    def on_attributes_changed(changed_attributes: dict[str, str], remote_p: rtc.RemoteParticipant) -> None:
        logger.debug("Participant attributes updated: changed=%s, all=%s", changed_attributes, remote_p.attributes)

    caller_phone = get_sip_caller_phone(ctx.room, participant)
    if sim and "caller_phone" in sim.userdata():
        caller_phone = sim.userdata()["caller_phone"]
    logger.info("Caller phone: %s", caller_phone or "not provided by caller ID")

    session = _build_session(ctx, trace_id, caller_phone)
    root_span = telemetry.otel_parent_span(trace_id)
    # Session tasks copy the current OTel context when they are created, so starting the session
    # while the root is current makes every turn, LLM, TTS and tool span a child of inbound_call.
    with trace_api.use_span(root_span, end_on_exit=False) if root_span else contextlib.nullcontext():
        await session.start(
            agent=ClinicReceptionistAgent(caller_phone=caller_phone),
            room=ctx.room,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(),
                text_input=room_io.TextInputOptions(text_input_cb=_guarded_text_input_cb),
            ),
        )


if __name__ == "__main__":
    with standalone_logging():
        if not asyncio.run(run_startup_checks()):
            logger.error("Startup readiness verification failed; aborting worker.")
            sys.exit(1)

    cli.run_app(server)
