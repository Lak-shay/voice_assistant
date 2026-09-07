# Real-Time Voice Agent Engineering Directives

## 1. Development Environment & Agent Workflow
- **LiveKit Agent Skill Standard:** Adhere strictly to the installed `livekit-agents` skills standard (`.agents/skills/` or `SKILL.md`). Follow architectural constraints: task handoffs, small conversational contexts, and synthetic test verification.
- **Zero Hallucinations (MCP Mandatory):** Do not guess or recall SDK methods, plugin parameters, or class signatures from memory. Always query the active `livekit-docs` and `langfuse-docs` MCP servers before writing or modifying pipeline scripts.
- **Test-Driven Verification:** Write and run asynchronous mock unit tests (`pytest tests/`) against all calendar and business tools before launching worker processes (`python agent.py dev`).

## 2. Concurrency & Event Loop Integrity (Zero Blocking)
- The main `asyncio` event loop directly manages real-time WebRTC audio streams.
- NEVER execute blocking synchronous calls (`time.sleep()`, synchronous `requests`, `urllib`, or synchronous DB clients).
- Wrap all synchronous I/O or legacy blocking APIs inside `asyncio.to_thread(...)`.
- Prefer streaming async primitives over bulk in-memory batch collections.

## 3. Session Correlation & Tracing Architecture (Root trace_id)
- **Root Identifier:** Every call session MUST establish a single root `trace_id` using the unique LiveKit room name (`trace_id = ctx.room.name`).
- **Telemetry Propagation (Langfuse):**
  - Initialize the root trace at session start via `langfuse.trace(id=trace_id, name="inbound_call", session_id=trace_id, ...)`.
  - All subsequent turns, speech synthesis events, and tool executions must nest under this root trace using child spans/generations.
- **Log Correlation:** Every standard library or `structlog` output line MUST prefix the root identifier (`[%(trace_id)s]`) so concurrent calls can be filtered independently in stdout.

## 4. Real-Time Logging & Diagnostic Rules
- **Non-Blocking Output:** All logging must write exclusively to `sys.stdout`. NEVER attach synchronous file-writing handlers (`FileHandler`) that trigger blocking disk I/O inside the WebRTC event loop.
- **Structured Fields:** Include `trace_id`, `room_sid`, and `participant_id` in log records where available.
- **Log Level Discipline:**
  - `DEBUG`: Raw audio frame events, VAD silence thresholds, and interim partial transcripts.
  - `INFO`: Room connection lifecycle (`ctx.connect`), finalized transcripts, tool invocation starts/completions.
  - `WARNING`: Handled tool errors, retry attempts, or low-confidence transcription fallbacks.
  - `ERROR`: Telephony SIP drops, unhandled external API failures, or WebRTC ICE timeouts.

## 5. Voice UX & Dialogue Constraints
- Responses MUST strictly be 1–2 short conversational sentences per turn.
- **Formatting Filter:** Never emit Markdown formatting syntax (no `**bold**`, `*italics*`, markdown bullets, code blocks, or raw web URLs). Cartesia TTS will verbalize markdown characters literally.
- **Phonetic Output:** Format numbers, dates, times, and phone numbers phonetically for the speech synthesis model (e.g., "September eighth at two thirty PM" instead of "09/08 2:30pm").

## 6. Tool Calling Rules (`llm.FunctionContext`)
- Keep active tool sets minimal (3–5 single-responsibility tools per conversational phase).
- Inherit from `livekit.agents.llm.FunctionContext` and decorate methods with `@llm.ai_callable()`.
- Annotate all tool arguments using `typing.Annotated` with explicit parameter descriptions so the LLM infers correct types.
- Tools must catch exceptions internally and return plain-English error strings rather than throwing unhandled exceptions that crash the WebRTC worker.