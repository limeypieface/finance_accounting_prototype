"""Structured JSON logging for the finance kernel."""

__all__ = [
    "StructuredFormatter",
    "LogContext",
    "get_logger",
    "configure_logging",
    "reset_logging",
]

import json
import logging
import threading
import time
from contextvars import ContextVar
from datetime import UTC, datetime, timezone
from typing import Any
from uuid import UUID

# ---------------------------------------------------------------------------
# Context propagation
# ---------------------------------------------------------------------------


class LogContext:
    """Thread-safe / async-safe context holder for request-scoped log fields."""

    _correlation_id: ContextVar[str | None] = ContextVar(
        "log_correlation_id", default=None
    )
    _event_id: ContextVar[str | None] = ContextVar(
        "log_event_id", default=None
    )
    _actor_id: ContextVar[str | None] = ContextVar(
        "log_actor_id", default=None
    )
    _producer: ContextVar[str | None] = ContextVar(
        "log_producer", default=None
    )
    _entry_id: ContextVar[str | None] = ContextVar(
        "log_entry_id", default=None
    )
    _trace_id: ContextVar[str | None] = ContextVar(
        "log_trace_id", default=None
    )

    _FIELD_NAMES = (
        "correlation_id",
        "event_id",
        "actor_id",
        "producer",
        "entry_id",
        "trace_id",
    )

    @classmethod
    def set(
        cls,
        *,
        correlation_id: str | None = None,
        event_id: str | None = None,
        actor_id: str | None = None,
        producer: str | None = None,
        entry_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Set context fields. Only non-None values are updated."""
        if correlation_id is not None:
            cls._correlation_id.set(correlation_id)
        if event_id is not None:
            cls._event_id.set(event_id)
        if actor_id is not None:
            cls._actor_id.set(actor_id)
        if producer is not None:
            cls._producer.set(producer)
        if entry_id is not None:
            cls._entry_id.set(entry_id)
        if trace_id is not None:
            cls._trace_id.set(trace_id)

    @classmethod
    def get_all(cls) -> dict[str, str]:
        """Return all non-None context fields as a dict."""
        ctx: dict[str, str] = {}
        for name in cls._FIELD_NAMES:
            val = getattr(cls, f"_{name}").get()
            if val is not None:
                ctx[name] = val
        return ctx

    @classmethod
    def clear(cls) -> None:
        """Reset all context fields to None."""
        for name in cls._FIELD_NAMES:
            getattr(cls, f"_{name}").set(None)

    @classmethod
    def bind(cls, **kwargs: str | None) -> "_LogContextManager":
        """Context manager that sets fields on entry and restores on exit."""
        return _LogContextManager(**kwargs)


class _LogContextManager:
    """Context manager for LogContext.bind()."""

    def __init__(self, **kwargs: str | None):
        self._kwargs = kwargs
        self._tokens: dict[str, Any] = {}

    def __enter__(self) -> "LogContext":
        for key, val in self._kwargs.items():
            if val is not None:
                var = getattr(LogContext, f"_{key}", None)
                if var is not None:
                    self._tokens[key] = var.set(val)
        return LogContext

    def __exit__(self, *exc: Any) -> None:
        for key, token in self._tokens.items():
            var = getattr(LogContext, f"_{key}", None)
            if var is not None:
                var.reset(token)


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------

_STDLIB_KEYS: frozenset[str] = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | {"message", "taskName"}


class _JSONEncoder(json.JSONEncoder):
    """Handle UUID, datetime, Decimal in log payloads."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        try:
            from decimal import Decimal

            if isinstance(obj, Decimal):
                return str(obj)
        except ImportError:
            pass
        return super().default(obj)


class StructuredFormatter(logging.Formatter):
    """Formats each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        # Mandatory envelope
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(
                record.created, tz=UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge context fields
        payload.update(LogContext.get_all())

        # Merge structured extra data (skip stdlib internal keys)
        for key, val in vars(record).items():
            if key not in _STDLIB_KEYS and key not in payload:
                payload[key] = val

        # Exception info
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            payload["exc_type"] = type(exc).__name__
            payload["exc_message"] = str(exc)
            if hasattr(exc, "code"):
                payload["exc_code"] = exc.code
            # Include structured fields from FinanceKernelError subclasses
            for k, v in vars(exc).items():
                if not k.startswith("_") and k not in ("args", "code"):
                    payload[f"exc_{k}"] = v
            payload["traceback"] = self.formatException(record.exc_info)

        return json.dumps(payload, cls=_JSONEncoder, default=str)


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------

_LOGGER_PREFIX = "finance_kernel"


def get_logger(name: str) -> logging.Logger:
    """Get a logger under the finance_kernel namespace."""
    return logging.getLogger(f"{_LOGGER_PREFIX}.{name}")


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

_configured = False
_lock = threading.Lock()


def configure_logging(
    *,
    level: int = logging.INFO,
    stream: Any = None,
    handler: logging.Handler | None = None,
) -> None:
    """Configure the finance_kernel logger hierarchy (idempotent)."""
    global _configured
    with _lock:
        if _configured:
            return
        _configured = True

    root_logger = logging.getLogger(_LOGGER_PREFIX)
    root_logger.setLevel(level)
    root_logger.propagate = False

    if handler is not None:
        h = handler
    else:
        import sys

        h = logging.StreamHandler(stream or sys.stderr)

    h.setFormatter(StructuredFormatter())
    root_logger.addHandler(h)


def reset_logging() -> None:
    """Reset logging configuration. FOR TESTING ONLY."""
    global _configured
    with _lock:
        _configured = False
    logger = logging.getLogger(_LOGGER_PREFIX)
    logger.handlers.clear()
    logger.setLevel(logging.WARNING)
