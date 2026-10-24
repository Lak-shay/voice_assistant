import asyncio
import base64
from typing import Any

from langfuse import Langfuse, propagate_attributes
from livekit.agents.telemetry import set_tracer_provider
from opentelemetry import trace as trace_api
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from config import settings
from logger import logger


def _langfuse_trace_id(trace_id: str) -> str:
    """Deterministic Langfuse trace id for a call, so scores can attach to the root trace without a lookup."""
    return Langfuse.create_trace_id(seed=trace_id)


class TelemetryManager:
    def __init__(self) -> None:
        self._client: Langfuse | None = None
        self._active_traces: dict[str, Any] = {}
        self._trace_provider: TracerProvider | None = None

    def get_client(self) -> Langfuse | None:
        if self._client is None and settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
            try:
                self._client = Langfuse(
                    public_key=settings.LANGFUSE_PUBLIC_KEY,
                    secret_key=settings.LANGFUSE_SECRET_KEY,
                    host=settings.LANGFUSE_BASE_URL,
                    environment=settings.APP_ENV,
                )
            except Exception as exc:
                logger.warning("Failed to initialize Langfuse client: %s", exc)
        return self._client

    def _get_trace_provider(self) -> TracerProvider:
        if self._trace_provider is None:
            auth = base64.b64encode(f"{settings.LANGFUSE_PUBLIC_KEY}:{settings.LANGFUSE_SECRET_KEY}".encode()).decode()
            exporter = OTLPSpanExporter(
                endpoint=f"{settings.LANGFUSE_BASE_URL.rstrip('/')}/api/public/otel/v1/traces",
                headers={"Authorization": f"Basic {auth}", "x-langfuse-ingestion-version": "4"},
            )
            resource = Resource.create({
                "deployment.environment": settings.APP_ENV,
                "deployment.environment.name": settings.APP_ENV,
                "langfuse.environment": settings.APP_ENV,
                "service.name": "clinic-receptionist",
            })
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            self._trace_provider = provider
        return self._trace_provider

    def create_session_trace(
        self,
        trace_id: str,
        room_sid: str | None = None,
        participant_id: str | None = None,
    ) -> Any | None:
        """Open the root `inbound_call` observation for a call and route LiveKit's OTel spans to Langfuse."""
        client = self.get_client()
        if not client:
            return None

        metadata: dict[str, Any] = {"room_name": trace_id, "clinic_name": settings.CLINIC_NAME}
        if room_sid:
            metadata["room_sid"] = room_sid

        try:
            with propagate_attributes(
                trace_name="inbound_call",
                session_id=trace_id,
                user_id=participant_id or "caller",
                environment=settings.APP_ENV,
                metadata=metadata,
            ):
                trace = client.start_observation(
                    name="inbound_call",
                    trace_context={"trace_id": _langfuse_trace_id(trace_id)},
                    metadata=metadata,
                )
        except Exception as exc:
            logger.warning("Failed to create Langfuse trace: %s", exc)
            return None
        self._active_traces[trace_id] = trace

        try:
            set_tracer_provider(
                self._get_trace_provider(),
                metadata={
                    "langfuse.session.id": trace_id,
                    "langfuse.environment": settings.APP_ENV,
                    "deployment.environment": settings.APP_ENV,
                },
            )
        except Exception as exc:
            logger.warning("Failed to attach OpenTelemetry tracer provider: %s", exc)

        return trace

    def otel_parent_span(self, trace_id: str) -> trace_api.Span | None:
        """The call's root observation as an OTel span, for parenting LiveKit's spans under it.

        LiveKit exports through its own TracerProvider, so the root is passed as a non-recording
        span carrying the root's ids; spans started while it is current join the root's trace.
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

        if self._trace_provider:
            try:
                await asyncio.to_thread(self._trace_provider.force_flush)
            except Exception as exc:
                logger.warning("Error flushing tracer provider: %s", exc)

        client = self.get_client()
        if client:
            try:
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
