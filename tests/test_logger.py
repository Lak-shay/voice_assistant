import contextvars
import io
import logging

from logger import CallContextFilter, bind_call_context, get_logger, standalone_logging


def _filtered_record() -> logging.LogRecord:
    record = logging.LogRecord("voice_assistant", logging.INFO, __file__, 1, "msg", None, None)
    CallContextFilter().filter(record)
    return record


def test_records_carry_bound_call_context():
    def in_call() -> logging.LogRecord:
        bind_call_context(room_sid="RM_123", participant_id="sip_+15551234567")
        return _filtered_record()

    record = contextvars.copy_context().run(in_call)
    assert record.room_sid == "RM_123"
    assert record.participant_id == "sip_+15551234567"


def test_records_outside_a_call_get_no_call_fields():
    record = contextvars.copy_context().run(_filtered_record)
    assert not hasattr(record, "room_sid")
    assert not hasattr(record, "participant_id")


def test_app_logs_propagate_to_root_handlers():
    """LiveKit installs its non-blocking queue handler on the root logger, so app loggers must propagate to it."""
    captured: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    root = logging.getLogger()
    handler = Capture()
    root.addHandler(handler)
    previous_level = root.level
    root.setLevel(logging.INFO)
    try:
        def in_call() -> None:
            bind_call_context(room_sid="RM_9")
            get_logger("voice_assistant.test").info("booked")

        contextvars.copy_context().run(in_call)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)

    assert [r.getMessage() for r in captured] == ["booked"]
    assert captured[0].room_sid == "RM_9"
    assert not get_logger("voice_assistant.test").handlers


def test_standalone_logging_is_removed_on_exit(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr("logger.sys.stdout", stream)
    root = logging.getLogger()
    handlers_before = list(root.handlers)

    with standalone_logging():
        get_logger("voice_assistant.startup").info("checks passed")

    assert "[INFO] voice_assistant.startup: checks passed" in stream.getvalue()
    assert root.handlers == handlers_before
