"""Application logging on the standard library.

Inside a LiveKit worker the CLI owns the handlers: in each job process the root logger
gets a queue handler whose background thread ships records to the parent process, where
another thread formats them (colored in dev, JSON in prod). The event loop therefore
never blocks on log I/O, and app loggers only need to propagate to the root.

On the way through the parent, LiveKit tags every job record with `pid`, `job_id` and
`room` (the room name, which is the call's trace_id), so call correlation needs nothing
from us. `CallContextFilter` adds the two fields LiveKit does not know, `room_sid` and
`participant_id`, from ContextVars set once per call with `bind_call_context`.
"""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from config import settings

room_sid_var: ContextVar[str | None] = ContextVar("room_sid", default=None)
participant_id_var: ContextVar[str | None] = ContextVar("participant_id", default=None)


def bind_call_context(room_sid: str | None = None, participant_id: str | None = None) -> None:
    """Set the call fields for the current task and the tasks it spawns. None leaves a field unchanged."""
    if room_sid is not None:
        room_sid_var.set(room_sid)
    if participant_id is not None:
        participant_id_var.set(participant_id)


class CallContextFilter(logging.Filter):
    """Copies the call ContextVars onto each record. Runs on the emitting thread, so it sees the caller's context."""

    def filter(self, record: logging.LogRecord) -> bool:
        room_sid = room_sid_var.get()
        participant_id = participant_id_var.get()
        if room_sid and not hasattr(record, "room_sid"):
            record.room_sid = room_sid
        if participant_id and not hasattr(record, "participant_id"):
            record.participant_id = participant_id
        return True


def get_logger(name: str = "voice_assistant") -> logging.Logger:
    """A standard logger that tags records with the call context and propagates to the handlers LiveKit installs."""
    log = logging.getLogger(name)
    if not any(isinstance(f, CallContextFilter) for f in log.filters):
        log.addFilter(CallContextFilter())
    return log


def _resolve_level(name: str) -> int:
    level = logging.getLevelName((name or "").strip().upper())
    return level if isinstance(level, int) else logging.INFO


@contextmanager
def standalone_logging() -> Iterator[None]:
    """Temporary stdout handler for code that runs outside a LiveKit worker (startup checks, scripts).

    Removed on exit so it does not duplicate the handler `cli.run_app` installs afterwards.
    Only for pre-worker code: it writes synchronously on the calling thread.
    """
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(_resolve_level(settings.LOG_LEVEL))
    try:
        yield
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


logger = get_logger("voice_assistant")
startup_logger = get_logger("voice_assistant.startup")
