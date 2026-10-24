import contextvars
import logging

from logger import CallContextFilter, bind_call_context


def _filtered_record() -> logging.LogRecord:
    record = logging.LogRecord("voice_assistant", logging.INFO, __file__, 1, "msg", None, None)
    CallContextFilter(default_trace_id="system").filter(record)
    return record


def test_records_carry_bound_call_context():
    def in_call() -> logging.LogRecord:
        bind_call_context(trace_id="room_abc", room_sid="RM_123", participant_id="sip_+15551234567")
        return _filtered_record()

    record = contextvars.copy_context().run(in_call)
    assert record.trace_id == "room_abc"
    assert record.room_sid == "RM_123"
    assert record.participant_id == "sip_+15551234567"


def test_records_outside_a_call_use_default_trace_id():
    record = contextvars.copy_context().run(_filtered_record)
    assert record.trace_id == "system"
    assert record.room_sid is None
