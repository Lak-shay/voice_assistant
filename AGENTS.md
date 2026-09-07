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

## 3. Voice UX & Dialogue Constraints
- Responses MUST strictly be 1–2 short conversational sentences per turn.
- **Formatting Filter:** Never emit Markdown formatting syntax (no `**bold**`, `*italics*`, markdown bullets, code blocks, or raw web URLs). Cartesia TTS will verbalize markdown characters literally.
- **Phonetic Output:** Format numbers, dates, times, and phone numbers phonetically for the speech synthesis model (e.g., "September eighth at two thirty PM" instead of "09/08 2:30pm").

## 4. Tool Calling Rules (`llm.FunctionContext`)
- Keep active tool sets minimal (3–5 single-responsibility tools per conversational phase).
- Inherit from `livekit.agents.llm.FunctionContext` and decorate methods with `@llm.ai_callable()`.
- Annotate all tool arguments using `typing.Annotated` with explicit parameter descriptions so the LLM infers correct types.
- Tools must handle exceptions internally and return plain-English error strings rather than throwing unhandled exceptions that crash the WebRTC worker.

## 5. Observability & Tracing (Langfuse)
- Instrument all LLM generations, tool invocations, and session turns asynchronously via Langfuse.
- Do not introduce synchronous logging or middleware that adds frame-level audio latency.