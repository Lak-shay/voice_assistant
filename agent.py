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
    llm,
    room_io,
)
from livekit.agents.llm import StopResponse
from livekit.agents.worker import JobExecutorType
from livekit.plugins import silero

from config import settings
from guardrails.input_guardrails import check_input_guardrails
from guardrails.output_guardrails import mask_secrets_transform
from logger import logger
from startup import run_startup_checks
from telemetry import telemetry
from tools.appointment_tools import (
    book_appointment,
    check_availability,
    get_sip_caller_phone,
    send_confirmation_sms,
    transfer_to_human,
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
                "When the customer agrees to an open slot, do not confirm the slot or call book_appointment yet. Ask for their full name first: 'May I have your full name, please?'. "
                "Never use placeholders like 'User' or 'Caller' for patient_name. "
                "Once they provide their full name, confirm the chosen appointment date and time with them and immediately call book_appointment passing their name and patient_phone={caller_phone}. "
                "Never call send_confirmation_sms in parallel with book_appointment; only call send_confirmation_sms in a subsequent turn after book_appointment returns success."
            )
        else:
            intake_instruction = (
                "No phone number was received from caller ID. "
                "When the customer agrees to an open slot, ask for their full name and phone number. "
                "Once they provide their name and phone number, you MUST read back their phone number in spoken digits and confirm the appointment date and time before booking (for example: 'Thank you. Just to confirm, your phone number is 5 5 5, 9 8 7, 6 5 4 3, and you would like Friday, October 9th at 2:30 PM?'). "
                "Only after the caller explicitly confirms this readback, call book_appointment with their details. "
                "Never call send_confirmation_sms in parallel with book_appointment; only call send_confirmation_sms in a subsequent turn after book_appointment returns success."
            )

        instructions = (
            f"You are the friendly, professional voice receptionist for {settings.CLINIC_NAME}. "
            f"Today's date is {today_str}. The current clinic time is {time_str} in timezone {settings.CLINIC_TIMEZONE}. "
            "Use this live date and time to accurately resolve relative dates like today, tomorrow, this afternoon, or next Monday. "
            "Follow these dialogue rules strictly: "
            "1. Speak in 1 to 2 short conversational sentences per turn. "
            "2. Never output markdown characters like asterisks, hashes, bullet points, or raw web URLs. "
            "3. In spoken responses to the caller, say all dates, times, and phone numbers phonetically in words. When calling check_availability, you can pass relative phrases directly (like 'tomorrow', 'next Thursday', 'this Friday', 'next Monday') or dates in YYYY-MM-DD format. Always verify that the calendar date returned by check_availability matches the requested day of the week before speaking the date or booking. When calling book_appointment, pass slot_time in ISO format YYYY-MM-DDTHH:MM:SS. "
            "4. Follow the appointment booking flow: "
            "   a. When the customer provides their preferred date and time or period, call check_availability for that date and period or time immediately. Always prioritize and check the caller's stated first-choice period before checking or proposing other periods. Never pivot to another period (such as afternoon) when the caller specifically asks for morning. "
            "   b. If their exact requested time is unavailable but other slots exist on that day, offer only the periods (morning, afternoon, or evening) that actually have open slots, and ask which period they prefer. Never ask for periods that have no openings. "
            "   c. Once they choose a period, offer the specific open times in that period. Note that slots at 4 PM and later are classified as evening. If a caller asks for after 4 PM or late in the day, check evening slots or specify preferred_time. "
            "   d. If the customer rejects an offered slot (for example, saying 'No I do not want that slot'), politely reply by asking: 'When would you like the appointment?' "
            "   e. Whenever the customer asks 'When is it available?', asks for the closest slot, reiterates or renews a preference (such as asking for morning again), asks about cancellations, or requests any new date or time (including fallback choices like 10:30 AM), you MUST ALWAYS make a fresh call to check_availability with the requested date and period before answering. NEVER rely on cached or previous turn responses, and NEVER claim a period or cancellations are unavailable without executing a fresh check_availability call on that turn. If a fresh check finds no openings in that period, state that clearly and only then offer alternative open periods or dates. "
            f"   f. {intake_instruction}"
            "   g. Only call book_appointment after the customer explicitly confirms. If caller ID is absent, ensure you read back their phone number for confirmation before booking. If caller ID is present, make sure you collect their actual full name before booking and never use placeholder names like 'User'. "
            "   h. If book_appointment returns that a slot was just taken or unavailable, apologize and explain that another caller just booked that exact slot. Retain their already-collected name and phone number in memory without asking for them or asking to re-confirm them again. Immediately offer the remaining open slots on that day, and ask them to choose another slot. When they choose an alternative slot, simply ask to confirm the new slot time (e.g. 'Just to confirm, would you like to book October twenty-fifth at ten thirty AM?'), and upon their yes, immediately call book_appointment with their retained name and phone number. "
            "5. Once booked, offer to send a confirmation text message. "
            "6. If the caller asks to speak to a person, receptionist, front desk staff, or human, or has an inquiry that cannot be handled by appointment scheduling, politely acknowledge and call transfer_to_human. "
            "7. Never offer, promise, or imply future notifications, callbacks, or waitlists (for example, never say 'I will let you know if a slot opens up' or 'I will put you on a waitlist'). We do not have a waitlist or proactive notification system. If a caller asks to be notified of openings or cancellations, state that proactive notifications and waitlists are unavailable, and offer to check another date, pick from currently available periods, or transfer to the front desk. "
            "8. For cancellation inquiries, live schedule updates are checked dynamically via check_availability. Never claim that cancellations are unavailable or that a separate cancellation list exists; always perform a live check_availability call for the requested date and period before answering."
        )
        super().__init__(instructions=instructions)

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions=(
                f"Greet the caller on behalf of {settings.CLINIC_NAME} in one short sentence by saying: "
                f"'Welcome to {settings.CLINIC_NAME}. How can I help you?'"
            )
        )

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        user_text = getattr(new_message, "text_content", "") or ""
        if not user_text and hasattr(new_message, "content"):
            if isinstance(new_message.content, list):
                user_text = " ".join(str(c) for c in new_message.content)
            else:
                user_text = str(new_message.content)

        session = getattr(self, "session", None)
        trace_id = session.userdata.get("trace_id", "") if session and isinstance(getattr(session, "userdata", None), dict) else ""
        is_blocked, response_text = _evaluate_guardrails(user_text, session, trace_id)
        if is_blocked and response_text and session:
            session.say(response_text, allow_interruptions=False)
            raise StopResponse()


server = AgentServer(
    num_idle_processes=settings.NUM_IDLE_PROCESSES,
    job_executor_type=JobExecutorType.PROCESS,
    initialize_process_timeout=30.0,
)


async def on_simulation_end(ctx: SimulationContext) -> None:
    session = getattr(ctx.job_context, "primary_session", None)
    userdata = getattr(session, "userdata", {}) if session else {}

    expected_transfer = ctx.userdata().get("expected_transfer")
    if expected_transfer:
        last_transfer = userdata.get("last_transfer") if isinstance(userdata, dict) else None
        if not last_transfer or not last_transfer.get("transferred"):
            ctx.fail(reason="Expected call transfer to human, but transfer_to_human was not executed.")
            return

    expected_guardrail = ctx.userdata().get("expected_guardrail")
    if expected_guardrail:
        last_guardrail = userdata.get("last_guardrail") if isinstance(userdata, dict) else None
        if not last_guardrail or expected_guardrail not in str(last_guardrail):
            ctx.fail(
                reason=f"Expected guardrail intervention '{expected_guardrail}', but got '{last_guardrail}'"
            )
            return

    expected = ctx.userdata().get("expected_booking")
    if not expected:
        return

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


def _evaluate_guardrails(
    user_text: str,
    session: AgentSession | None,
    trace_id: str = "",
) -> tuple[bool, str | None]:
    if not settings.GUARDRAILS_ENABLED or not user_text:
        return False, None

    guardrail_res = check_input_guardrails(
        user_text,
        check_emergency=settings.GUARDRAILS_EMERGENCY_TRIAGE_ENABLED,
        check_injection=settings.GUARDRAILS_INJECTION_DEFENSE_ENABLED,
    )
    if guardrail_res.is_blocked and guardrail_res.response_text:
        if session and hasattr(session, "userdata") and isinstance(session.userdata, dict):
            session.userdata["last_guardrail"] = guardrail_res.reason
        logger.info(
            "[%s] Input guardrail triggered: reason=%s",
            trace_id,
            guardrail_res.reason,
        )
        telemetry.record_guardrail_event(
            trace_id,
            "input_guardrail_blocked",
            {"reason": guardrail_res.reason, "user_text": user_text[:80]},
        )
        return True, guardrail_res.response_text
    return False, None


async def _custom_text_input_cb(sess: AgentSession, ev: room_io.TextInputEvent) -> None:
    async with sess._claim_user_turn():
        await sess.interrupt()
        trace_id = sess.userdata.get("trace_id", "") if hasattr(sess, "userdata") and isinstance(sess.userdata, dict) else ""
        is_blocked, response_text = _evaluate_guardrails(ev.text, sess, trace_id)
        if is_blocked and response_text:
            sess.generate_reply(
                instructions=f"Immediately speak this exact message to the user and nothing else: {response_text}"
            )
            return
        sess.generate_reply(user_input=ev.text)


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
    llm_model = inference.LLM(
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
        llm=llm_model,
        tts=tts,
        tts_text_transforms=[
            "filter_markdown",
            "filter_emoji",
            mask_secrets_transform,
        ] if settings.GUARDRAILS_ENABLED and settings.GUARDRAILS_TTS_SANITIZER_ENABLED else [
            "filter_markdown",
            "filter_emoji",
        ],
        tools=[check_availability, book_appointment, send_confirmation_sms, transfer_to_human],
        userdata={
            "room": ctx.room,
            "trace_id": trace_id,
            "caller_phone": caller_phone,
            "job_ctx": ctx,
        },
    )

    orig_generate_reply = session.generate_reply

    def guarded_generate_reply(*args, **kwargs):
        user_input = kwargs.get("user_input")
        if user_input:
            user_text = user_input if isinstance(user_input, str) else getattr(user_input, "text_content", "") or ""
            is_blocked, response_text = _evaluate_guardrails(user_text, session, trace_id)
            if is_blocked and response_text:
                kwargs.pop("user_input", None)
                kwargs["instructions"] = f"Immediately speak this exact message to the user and nothing else: {response_text}"
        return orig_generate_reply(*args, **kwargs)

    session.generate_reply = guarded_generate_reply

    await session.start(
        agent=ClinicReceptionistAgent(caller_phone=caller_phone),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(),
            text_input=room_io.TextInputOptions(text_input_cb=_custom_text_input_cb),
        ),
    )



if __name__ == "__main__":
    is_ready = asyncio.run(run_startup_checks())
    if not is_ready:
        logger.error("Startup readiness verification failed; aborting worker.")
        sys.exit(1)

    cli.run_app(server)
