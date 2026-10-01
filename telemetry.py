from __future__ import annotations

import asyncio
from typing import Any

from langfuse import Langfuse

from config import settings
from logger import logger


class TelemetryManager:
    def __init__(self) -> None:
        self._client: Langfuse | None = None
        self._active_traces: dict[str, Any] = {}

    def get_client(self) -> Langfuse | None:
        if self._client is None and settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
            try:
                self._client = Langfuse(
                    public_key=settings.LANGFUSE_PUBLIC_KEY,
                    secret_key=settings.LANGFUSE_SECRET_KEY,
                    host=settings.LANGFUSE_BASE_URL,
                )
            except Exception as exc:
                logger.warning("Failed to initialize Langfuse client: %s", exc)
                self._client = None
        return self._client

    def create_session_trace(self, room_name: str, participant_id: str | None = None) -> Any | None:
        client = self.get_client()
        if not client:
            return None

        trace_id = room_name
        try:
            trace = client.trace(
                id=trace_id,
                name="inbound_call",
                session_id=trace_id,
                user_id=participant_id or "caller",
                metadata={
                    "environment": settings.APP_ENV,
                    "room_name": room_name,
                    "clinic_name": settings.CLINIC_NAME,
                },
            )
            self._active_traces[trace_id] = trace
            return trace
        except Exception as exc:
            logger.warning("[%s] Failed to create Langfuse trace: %s", trace_id, exc)
            return None

    def start_span(self, trace_id: str, span_name: str, input_data: Any = None) -> Any | None:
        trace = self._active_traces.get(trace_id)
        if not trace:
            return None
        try:
            return trace.span(name=span_name, input=input_data)
        except Exception as exc:
            logger.warning("[%s] Error starting span %s: %s", trace_id, span_name, exc)
            return None

    def end_span(self, span: Any | None, output_data: Any = None) -> None:
        if not span:
            return
        try:
            span.end(output=output_data)
        except Exception as exc:
            logger.warning("Error ending span: %s", exc)

    async def flush(self, trace_id: str | None = None) -> None:
        if trace_id:
            self._active_traces.pop(trace_id, None)

        client = self.get_client()
        if not client:
            return

        # Flush background worker buffer in separate thread to prevent event loop stalls
        try:
            await asyncio.to_thread(client.flush)
        except Exception as exc:
            logger.warning("Langfuse telemetry flush error: %s", exc)


telemetry = TelemetryManager()
