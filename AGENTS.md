# Real-Time Voice Agent Engineering Directives

## 1. Development Environment & Agent Workflow
- **LiveKit Agent Skill Standard:** Adhere strictly to the installed `livekit-agents` skills standard (`.agents/skills/` or `SKILL.md`). Follow architectural constraints: task handoffs, small conversational contexts, and synthetic test verification.
- **Zero Hallucinations (MCP Mandatory):** Do not guess or recall SDK methods, plugin parameters, or class signatures from memory. Always query the active `livekit-docs`, `langfuse-docs`, and `telnyx-docs` MCP servers (or fetch `https://developers.telnyx.com/llms.txt`) before writing or modifying pipeline scripts.
- **Test-Driven Verification:** Write and run asynchronous mock unit tests (`pytest tests/`) against all calendar, messaging, and business tools before launching worker processes (`python agent.py dev`).

## 2. Concurrency & Event Loop Integrity (Zero Blocking)
- The main `asyncio` event loop directly manages real-time WebRTC audio streams.
- NEVER execute blocking synchronous calls (`time.sleep()`, synchronous `requests`, `urllib`, blocking `telnyx` SDK calls, or synchronous DB clients).
- Wrap all synchronous I/O or legacy blocking APIs inside `asyncio.to_thread(...)`.
- Prefer streaming async primitives over bulk in-memory batch collections.

## 3. Session Correlation & Tracing Architecture (Root trace_id)
- **Root Identifier:** Every call session MUST establish a single root `trace_id` using the unique LiveKit room name (`trace_id = ctx.room.name`).
- **Telemetry Propagation (Langfuse):**
  - Initialize the root observation at session start via Langfuse v4 (`client.start_observation(name="inbound_call", trace_context={"trace_id": trace_id_hex}, ...)`) with client-side attribute propagation (`propagate_attributes(trace_name="inbound_call", session_id=trace_id, ...)`).
  - All subsequent turns, speech synthesis events, and tool executions must nest under this root trace using child observations/generations.
- **Log Correlation:** Every standard library or `structlog` output line MUST prefix the root identifier (`[%(trace_id)s]`) so concurrent calls can be filtered independently in stdout.

## 4. Real-Time Logging & Diagnostic Rules
- **Decoupled Queue-Backed Output:** All application logging MUST use `logging.handlers.QueueHandler` on the main thread pushing to an in-memory queue, paired with a background `logging.handlers.QueueListener` worker thread dispatching to `sys.stdout`. Direct synchronous stream writes (`StreamHandler(sys.stdout)`) and file-writing handlers (`FileHandler`) on the event loop thread are strictly prohibited because they acquire thread locks and execute synchronous I/O.
- **Centralized Logger Architecture:** All pipeline modules, tools, and startup scripts MUST import the shared non-blocking logger from `logger.py` (`get_logger`) rather than attaching ad-hoc handlers.
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

## 7. Telephony & Messaging Directives (Telnyx / SIP / SMS)
- **Official Docs Verification:** Consult `telnyx-docs` or check `https://developers.telnyx.com/llms.txt` prior to generating endpoints, SIP trunk options, or outbound SMS payloads. Never invent REST parameter keys.
- **Async REST Dispatch:** All Telnyx operations (e.g., dispatching post-call confirmation texts via `/v2/messages`) must use asynchronous clients (`aiohttp.ClientSession`) or run via `asyncio.to_thread(...)` to ensure the audio loop is never stalled.
- **E.164 Number Sanitization:** All phone numbers must be formatted to standard E.164 (`+1XXXXXXXXXX`) before making external API requests to Telnyx.
- **Failover SIP Routing:** Telnyx SIP trunks routing calls to LiveKit must define a fallback PSTN transfer URI to route to front-desk staff in the event of an SFU or worker timeout.

## 8. Configuration & Environment Invariants (Zero-Redeploy Policy)
- **Externalize Configurable Parameters:** Any setting, timeout, threshold, buffer size, or external endpoint (including Telnyx API keys and carrier numbers) MUST be externalized into environment variables and loaded via `config.py` (`pydantic-settings`).
- **Never Hardcode Operational Constants:** Hardcoding operational timeouts or thresholds directly in source code is strictly prohibited.
- **Environment Template Parity:** Whenever a configurable field is added or updated in `config.py`, it MUST simultaneously be mirrored across all environment templates: `.env.example`, `.env.dev`, and `.env.prod`.
- **Safe Defaults in Code:** Code models (`BaseSettings`) may provide sensible fallback defaults, but operational values must remain fully overridable via environment variables.

## 9. Code Commenting Discipline (Minimal & Essential Only)
- Comments MUST only be placed where strictly essential (e.g., explaining non-obvious algorithms, asynchronous concurrency pitfalls, or third-party protocol quirks).
- NEVER add redundant, conversational, or self-evident comments (e.g., `# import modules`, `# define function`, `# return result`).
- Function signatures and clean type annotations should be self-documenting. Keep docstrings concise and focused on parameter contracts and exception behaviors.

<!-- antislop:start -->
## antislop
For UI, copy, people, mobile layout, or code comments work, load the antislop skill for the task:
- Core filter, always on: `antislop`
Before starting, ask the user when antislop applies: during the work, or after it is done.
<!-- antislop:end -->