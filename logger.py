"""Non-blocking logging.

Records are pushed onto an in-memory queue by a QueueHandler on the calling thread;
a QueueListener thread does the formatting and the write to sys.stdout, so the
asyncio event loop never blocks on stream I/O.

Per-call correlation fields (trace_id, room_sid, participant_id) live in
ContextVars. `bind_call_context` sets them once in the job entrypoint and every
task spawned from it inherits them, so call sites never pass the trace_id by hand.
"""

import atexit
import logging
import logging.handlers
import queue
import sys
from contextvars import ContextVar
from typing import Optional

from config import settings

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
room_sid_var: ContextVar[str | None] = ContextVar("room_sid", default=None)
participant_id_var: ContextVar[str | None] = ContextVar("participant_id", default=None)


def bind_call_context(
    trace_id: str | None = None,
    room_sid: str | None = None,
    participant_id: str | None = None,
) -> None:
    """Set the correlation fields for the current task and the tasks it spawns. None leaves a field unchanged."""
    if trace_id is not None:
        trace_id_var.set(trace_id)
    if room_sid is not None:
        room_sid_var.set(room_sid)
    if participant_id is not None:
        participant_id_var.set(participant_id)


class CallContextFilter(logging.Filter):
    """Copies the call ContextVars onto each record. Runs on the emitting thread, before the record is queued."""

    def __init__(self, default_trace_id: str = "system"):
        super().__init__()
        self.default_trace_id = default_trace_id

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = trace_id_var.get() or self.default_trace_id
        record.room_sid = room_sid_var.get()
        record.participant_id = participant_id_var.get()
        return True


class NonBlockingLogManager:
    _instance: Optional["NonBlockingLogManager"] = None

    def __init__(self, level: int):
        self.log_queue: queue.SimpleQueue = queue.SimpleQueue()

        self.stdout_handler = logging.StreamHandler(sys.stdout)
        self.stdout_handler.setLevel(level)
        self.stdout_handler.setFormatter(logging.Formatter("[%(trace_id)s] [%(levelname)s] %(message)s"))

        self.listener = logging.handlers.QueueListener(
            self.log_queue,
            self.stdout_handler,
            respect_handler_level=True,
        )
        self.listener.start()
        self._stopped = False
        atexit.register(self.stop)

    @classmethod
    def get_instance(cls, level: int) -> "NonBlockingLogManager":
        if cls._instance is None:
            cls._instance = cls(level=level)
        return cls._instance

    def stop(self) -> None:
        """Flushes remaining records and stops the listener thread. Safe to call more than once."""
        if not self._stopped:
            self._stopped = True
            self.listener.stop()


def _resolve_level(name: str) -> int:
    level = logging.getLevelName(name.strip().upper())
    return level if isinstance(level, int) else logging.INFO


def get_logger(name: str = "voice_assistant", default_trace_id: str = "system") -> logging.Logger:
    level = _resolve_level(settings.LOG_LEVEL)
    manager = NonBlockingLogManager.get_instance(level)
    log = logging.getLogger(name)
    log.setLevel(level)

    if not any(isinstance(h, logging.handlers.QueueHandler) for h in log.handlers):
        handler = logging.handlers.QueueHandler(manager.log_queue)
        handler.addFilter(CallContextFilter(default_trace_id=default_trace_id))
        log.addHandler(handler)
        log.propagate = False

    return log


logger = get_logger("voice_assistant", default_trace_id="system")
startup_logger = get_logger("voice_assistant.startup", default_trace_id="startup")
