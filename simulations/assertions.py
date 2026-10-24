from __future__ import annotations

from typing import Any

from livekit.agents import SimulationContext

from session_state import CallUserdata


def first_failure(expectations: dict[str, Any], state: CallUserdata) -> str | None:
    """Compare a scenario's expected outcome against the call's final state. Returns the first mismatch, or None."""
    if expectations.get("expected_transfer"):
        if not state.last_transfer or not state.last_transfer.transferred:
            return "Expected call transfer to human, but transfer_to_human was not executed."

    expected_guardrail = expectations.get("expected_guardrail")
    if expected_guardrail and expected_guardrail not in (state.last_guardrail or ""):
        return f"Expected guardrail intervention '{expected_guardrail}', but got '{state.last_guardrail}'"

    expected = expectations.get("expected_booking")
    if not expected:
        return None

    booking = state.last_booking
    if not booking:
        return "No appointment was actually booked in the calendar backend."

    expected_name = expected.get("patient_name")
    if expected_name and booking.patient_name != expected_name:
        return f"Patient name mismatch: expected '{expected_name}', but got '{booking.patient_name}'"

    expected_phone = expected.get("patient_phone")
    if expected_phone and booking.patient_phone != expected_phone:
        return f"Patient phone mismatch: expected '{expected_phone}', but got '{booking.patient_phone}'"

    expected_date = expected.get("slot_date")
    if expected_date and expected_date not in booking.slot_time:
        return f"Slot date mismatch: expected '{expected_date}' in slot, but got '{booking.slot_time}'"

    return None


async def on_simulation_end(ctx: SimulationContext) -> None:
    try:
        state = ctx.job_context.primary_session.userdata
    except (RuntimeError, ValueError):
        state = None
    if not isinstance(state, CallUserdata):
        state = CallUserdata(trace_id="")

    failure = first_failure(ctx.userdata(), state)
    if failure:
        ctx.fail(reason=failure)
