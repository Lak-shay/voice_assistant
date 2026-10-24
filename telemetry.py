import asyncio
from typing import Any

from langfuse import Langfuse, propagate_attributes
from livekit.agents.telemetry import set_tracer_provider
from opentelemetry import trace as trace_api
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider

from config import settings
from logger import logger


def _langfuse_trace_id(trace_id: str) -> str:
    """Deterministic Langfuse trace id for a call, so scores can attach to the root trace without a lookup."""
    return Langfuse.create_trace_id(seed=trace_id)


def _export_all_spans(span: ReadableSpan) -> bool:
    # Langfuse's default filter keeps only its own and GenAI spans, which would drop LiveKit's
    # session, turn, STT and TTS spans that share this provider.
    return True


class TelemetryManager:
    """One OTel TracerProvider shared by the Langfuse client and LiveKit, exported by Langfuse's span processor."""

    def __init__(self) -> None:
        self._client: Langfuse | None = None
        self._active_traces: dict[str, Any] = {}

    def get_client(self) -> Langfuse | None:
        if self._client is None and settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
            provider = TracerProvider(
                resource=Resource.create({"service.name": "clinic-receptionist", "langfuse.environment": settings.APP_ENV})
            )
            try:
                self._client = Langfuse(
                    public_key=settings.LANGFUSE_PUBLIC_KEY,
                    secret_key=settings.LANGFUSE_SECRET_KEY,
                    host=settings.LANGFUSE_BASE_URL,
                    environment=settings.APP_ENV,
                    tracer_provider=provider,
                    should_export_span=_export_all_spans,
                    additional_headers={"x-langfuse-ingestion-version": "4"},
                )
                set_tracer_provider(provider)
            except Exception as exc:
                logger.warning("Failed to initialize Langfuse tracing: %s", exc)
        return self._client

    def create_session_trace(self, trace_id: str, room_sid: str | None = None) -> Any | None:
        """Open the root `inbound_call` observation for a call."""
        client = self.get_client()
        if not client:
            return None

        metadata = {"clinic_name": settings.CLINIC_NAME}
        if room_sid:
            metadata["room_sid"] = room_sid

        try:
            with propagate_attributes(trace_name="inbound_call", session_id=trace_id, metadata=metadata):
                trace = client.start_observation(
                    name="inbound_call",
                    trace_context={"trace_id": _langfuse_trace_id(trace_id)},
                )
        except Exception as exc:
            logger.warning("Failed to create Langfuse trace: %s", exc)
            return None
        self._active_traces[trace_id] = trace
        return trace

    def otel_parent_span(self, trace_id: str) -> trace_api.Span | None:
        """The call's root observation as an OTel span, for parenting LiveKit's spans under it.

        Returned as a non-recording span carrying the root's ids, so spans started while it is
        current join the root's trace without touching the Langfuse wrapper's private span.
        """
        root = self._active_traces.get(trace_id)
        if root is None:
            return None
        try:
            span_context = trace_api.SpanContext(
                trace_id=int(root.trace_id, 16),
                span_id=int(root.id, 16),
                is_remote=False,
                trace_flags=trace_api.TraceFlags(trace_api.TraceFlags.SAMPLED),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning("Root observation has no usable OTel ids: %s", exc)
            return None
        return trace_api.NonRecordingSpan(span_context)

    async def flush(self, trace_id: str | None = None) -> None:
        if trace_id:
            trace = self._active_traces.pop(trace_id, None)
            if trace:
                try:
                    trace.end()
                except Exception as exc:
                    logger.warning("Error ending root trace observation: %s", exc)

        client = self.get_client()
        if client:
            try:
                # Flushes the shared provider too, so LiveKit's spans go out with the root.
                await asyncio.to_thread(client.flush)
            except Exception as exc:
                logger.warning("Langfuse telemetry flush error: %s", exc)

    def record_guardrail_event(self, trace_id: str, event_type: str, details: dict[str, Any] | None = None) -> None:
        client = self.get_client()
        if not client:
            return
        try:
            client.create_score(
                name="guardrail_intervention",
                value=1.0,
                trace_id=_langfuse_trace_id(trace_id),
                comment=f"{event_type}: {details or {}}",
            )
        except Exception as exc:
            logger.warning("Failed to record guardrail score: %s", exc)


telemetry = TelemetryManager()
