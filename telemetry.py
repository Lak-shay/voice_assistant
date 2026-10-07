import asyncio
import base64
import os
from typing import Any

from langfuse import Langfuse, propagate_attributes
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from livekit.agents.telemetry import set_tracer_provider

from config import settings
from logger import logger


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
                self._client = None
        return self._client

    def create_session_trace(
        self,
        room_name: str,
        participant_id: str | None = None,
    ) -> Any | None:
        client = self.get_client()
        if not client:
            return None

        trace_id_hex = Langfuse.create_trace_id(seed=room_name)
        try:
            with propagate_attributes(
                trace_name="inbound_call",
                session_id=room_name,
                user_id=participant_id or "caller",
                environment=settings.APP_ENV,
                metadata={
                    "room_name": room_name,
                    "clinic_name": settings.CLINIC_NAME,
                },
            ):
                trace = client.start_observation(
                    name="inbound_call",
                    trace_context={"trace_id": trace_id_hex},
                    metadata={
                        "room_name": room_name,
                        "clinic_name": settings.CLINIC_NAME,
                    },
                )
            self._active_traces[room_name] = trace

            if settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
                try:
                    if self._trace_provider is None:
                        auth = base64.b64encode(f"{settings.LANGFUSE_PUBLIC_KEY}:{settings.LANGFUSE_SECRET_KEY}".encode()).decode()
                        base_url = settings.LANGFUSE_BASE_URL.rstrip("/")
                        endpoint = f"{base_url}/api/public/otel/v1/traces"
                        headers = {
                            "Authorization": f"Basic {auth}",
                            "x-langfuse-ingestion-version": "4",
                        }

                        resource = Resource.create({
                            "deployment.environment": settings.APP_ENV,
                            "deployment.environment.name": settings.APP_ENV,
                            "langfuse.environment": settings.APP_ENV,
                            "service.name": "clinic-receptionist",
                        })
                        provider = TracerProvider(resource=resource)
                        exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
                        provider.add_span_processor(BatchSpanProcessor(exporter))
                        self._trace_provider = provider

                    set_tracer_provider(
                        self._trace_provider,
                        metadata={
                            "langfuse.session.id": room_name,
                            "langfuse.environment": settings.APP_ENV,
                            "deployment.environment": settings.APP_ENV,
                        },
                    )
                except Exception as otel_exc:
                    logger.warning("[%s] Failed to attach OpenTelemetry tracer provider: %s", room_name, otel_exc)

            return trace
        except Exception as exc:
            logger.warning("[%s] Failed to create Langfuse trace: %s", room_name, exc)
            return None

    async def flush(self, trace_id: str | None = None) -> None:
        if trace_id:
            trace = self._active_traces.pop(trace_id, None)
            if trace:
                try:
                    trace.end()
                except Exception as exc:
                    logger.warning("[%s] Error ending root trace observation: %s", trace_id, exc)

        if self._trace_provider:
            try:
                await asyncio.to_thread(self._trace_provider.force_flush)
            except Exception as exc:
                logger.warning("Error flushing tracer provider: %s", exc)

        client = self.get_client()
        if not client:
            return

        # Flush background worker buffer in separate thread to prevent event loop stalls
        try:
            await asyncio.to_thread(client.flush)
        except Exception as exc:
            logger.warning("Langfuse telemetry flush error: %s", exc)

    def record_guardrail_event(self, trace_id: str, event_type: str, details: dict[str, Any] | None = None) -> None:
        client = self.get_client()
        if not client:
            return
        try:
            client.score(
                name="guardrail_intervention",
                value=1.0,
                comment=f"{event_type}: {details or {}}",
            )
        except Exception as exc:
            logger.debug("[%s] Telemetry guardrail score notice: %s", trace_id, exc)


telemetry = TelemetryManager()
