from __future__ import annotations

from dataclasses import dataclass

from livekit.agents import JobContext


@dataclass(frozen=True)
class BookingRecord:
    patient_name: str
    patient_phone: str
    slot_time: str


@dataclass(frozen=True)
class TransferRecord:
    failover_phone: str
    reason: str | None
    transferred: bool


@dataclass
class CallUserdata:
    """Per-call state shared by the agent, its tools, and the simulation assertions."""

    trace_id: str
    caller_phone: str | None = None
    job_ctx: JobContext | None = None
    last_booking: BookingRecord | None = None
    last_transfer: TransferRecord | None = None
    last_guardrail: str | None = None
