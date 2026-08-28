"""Structured logging with run and stage context.

Agents log through here instead of calling ``print``, so every line can be
attributed to a run and a pipeline stage and later streamed to the frontend.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from core.config import get_settings

_EMPTY: dict[str, Any] = {}

# The default is never mutated: bind() always sets a freshly built dict.
_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default=_EMPTY)

_configured = False

# Attributes the stdlib puts on every record; anything else was added by us and
# belongs in the structured payload.
_STANDARD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "thread", "threadName", "taskName",
    }
)  # fmt: skip


def current_context() -> dict[str, Any]:
    return dict(_context.get())


def current_run_id() -> str | None:
    return _context.get().get("run_id")


@contextmanager
def bind(**fields: Any) -> Iterator[None]:
    """Attach fields to every log record emitted inside this block."""
    merged = {**_context.get(), **{k: v for k, v in fields.items() if v is not None}}
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _context.get()
        record.run_id = ctx.get("run_id", "-")
        record.stage = ctx.get("stage", "-")
        for key, value in ctx.items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        run_id = getattr(record, "run_id", "-")
        stage = getattr(record, "stage", "-")
        prefix = f"{record.levelname:<7} [{_short(run_id)}/{stage}]"
        text = f"{prefix} {record.getMessage()}"
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _short(run_id: str) -> str:
    return run_id[:8] if run_id and run_id != "-" else "-"


def configure_logging(*, force: bool = False) -> None:
    """Install the root handler. Safe to call more than once."""
    global _configured
    if _configured and not force:
        return

    settings = get_settings()

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ContextFilter())
    handler.setFormatter(JsonFormatter() if settings.log_json else HumanFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # These are chatty at INFO. fontTools in particular logs a line per glyph
    # while a PDF is written, which would otherwise fill a run's event log.
    for noisy in (
        "httpx",
        "httpcore",
        "urllib3",
        "google_genai",
        "openai",
        "fontTools",
        "PIL",
        "asyncio",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def add_handler(handler: logging.Handler) -> None:
    """Attach an extra sink, used by the server to stream logs to clients."""
    configure_logging()
    handler.addFilter(_ContextFilter())
    logging.getLogger().addHandler(handler)


def remove_handler(handler: logging.Handler) -> None:
    logging.getLogger().removeHandler(handler)
