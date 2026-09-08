"""Asynchronous Non-Blocking Logging System

Utilizes Python's standard library QueueHandler and QueueListener to guarantee
zero blocking I/O on the main asyncio event loop thread. Formatting and writing
to sys.stdout are executed exclusively on an isolated background worker thread.
"""

import atexit
import logging
import logging.handlers
import queue
import sys
from typing import Optional


class TraceIdFilter(logging.Filter):
    """Ensures every log record contains a trace_id field for log correlation."""

    def __init__(self, default_trace_id: str = "system"):
        super().__init__()
        self.default_trace_id = default_trace_id

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = self.default_trace_id
        return True


class NonBlockingLogManager:
    """Manages QueueHandler and QueueListener lifecycle."""

    _instance: Optional["NonBlockingLogManager"] = None

    def __init__(self, level: int = logging.INFO):
        self.log_queue: queue.SimpleQueue = queue.SimpleQueue()

        self.stdout_handler = logging.StreamHandler(sys.stdout)
        self.formatter = logging.Formatter(
            "[%(trace_id)s] [%(levelname)s] %(message)s"
        )
        self.stdout_handler.setFormatter(self.formatter)

        self.listener = logging.handlers.QueueListener(
            self.log_queue,
            self.stdout_handler,
            respect_handler_level=True,
        )
        self.listener.start()
        atexit.register(self.stop)

    @classmethod
    def get_instance(cls, level: int = logging.INFO) -> "NonBlockingLogManager":
        if cls._instance is None:
            cls._instance = cls(level=level)
        return cls._instance

    def stop(self) -> None:
        """Flushes remaining records and terminates the background listener thread."""
        if hasattr(self, "listener") and self.listener:
            self.listener.stop()


def get_logger(name: str = "voice_assistant", default_trace_id: str = "system") -> logging.Logger:
    """Returns a configured non-blocking logger for the given name."""
    manager = NonBlockingLogManager.get_instance()
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)

    # Avoid duplicate handlers if called multiple times
    if not any(isinstance(h, logging.handlers.QueueHandler) for h in log.handlers):
        handler = logging.handlers.QueueHandler(manager.log_queue)
        handler.addFilter(TraceIdFilter(default_trace_id=default_trace_id))
        log.addHandler(handler)
        log.propagate = False

    return log


logger = get_logger("voice_assistant", default_trace_id="system")
startup_logger = get_logger("voice_assistant.startup", default_trace_id="startup")
