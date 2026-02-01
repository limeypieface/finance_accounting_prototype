"""
LogCapture -- in-process structured log capture for audit traceability.

Responsibility:
    Collects all structured log records emitted by the ``finance_kernel``
    logger hierarchy during a posting operation.  Implements the
    ``LogQueryPort`` protocol for ``TraceSelector`` to populate audit
    timelines with decision-level detail.

Architecture position:
    Kernel > Services -- read-side infrastructure.
    Attached to the ``finance_kernel`` logger hierarchy during posting
    operations.  Does not persist data -- purely in-memory capture.

Invariants enforced:
    None directly.  LogCapture is an observability tool that assists
    auditors in reviewing the decision journal for R11, L5, P15, and
    other invariant enforcement.

Failure modes:
    - JSON parse failure during ``emit()``: Falls back to building a
      dict directly from the ``LogRecord`` attributes.
    - Query on empty capture: Returns empty list (not an error).

Audit relevance:
    LogCapture is the bridge between structured logging and the audit
    trace system.  Every decision logged during posting (profile
    selection, guard evaluation, role resolution, balance validation)
    is captured and queryable by correlation_id, event_id, or trace_id.

Usage::

    capture = LogCapture()
    capture.install()

    try:
        result = coordinator.interpret_and_post(...)
    finally:
        capture.uninstall()

    # Pass captured records to TraceSelector
    selector = TraceSelector(session, log_query=capture)
    bundle = selector.trace_by_event_id(event_id)

No new database columns. No external log infrastructure needed.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from finance_kernel.logging_config import StructuredFormatter

_LOGGER_PREFIX = "finance_kernel"


class LogCapture(logging.Handler):
    """
    In-process log handler that captures structured log records.

    Contract:
        Implements the ``LogQueryPort`` protocol from trace_selector.py:
        - ``query_by_correlation_id(correlation_id)`` -> list[dict]
        - ``query_by_event_id(event_id)`` -> list[dict]
        - ``query_by_trace_id(trace_id)`` -> list[dict]

    Guarantees:
        - Records are stored as dicts with at minimum: ``ts``, ``message``,
          and all structured extra fields from the log call.
        - The ``records`` property returns a read-only copy (no mutation
          of internal state by callers).

    Non-goals:
        - Does NOT persist records to a database or file.
        - Does NOT filter or transform records (captures all levels
          at or above the configured level).
    """

    def __init__(self, level: int = logging.DEBUG):
        super().__init__(level)
        self._records: list[dict] = []
        self._formatter = StructuredFormatter()

    def emit(self, record: logging.LogRecord) -> None:
        """Capture a log record as a structured dict."""
        try:
            # Parse the JSON formatted output to get a clean dict
            formatted = self._formatter.format(record)
            entry = json.loads(formatted)
            self._records.append(entry)
        except Exception:
            # Fallback: build dict from record attributes directly
            entry = self._build_entry_from_record(record)
            self._records.append(entry)

    def _build_entry_from_record(self, record: logging.LogRecord) -> dict:
        """Build a dict from a LogRecord when JSON parsing fails."""
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Extract extra fields
        _stdlib_keys = {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process", "message",
            "taskName",
        }
        for key, val in vars(record).items():
            if key not in _stdlib_keys and key not in entry:
                entry[key] = self._serialize(val)

        return entry

    @staticmethod
    def _serialize(val: Any) -> Any:
        """Serialize a value for storage."""
        if isinstance(val, UUID):
            return str(val)
        if isinstance(val, datetime):
            return val.isoformat()
        try:
            from decimal import Decimal
            if isinstance(val, Decimal):
                return str(val)
        except ImportError:
            pass
        return val

    # -----------------------------------------------------------------------
    # Install / Uninstall
    # -----------------------------------------------------------------------

    def install(self) -> "LogCapture":
        """
        Attach this handler to the finance_kernel logger hierarchy.

        Returns self for chaining.
        """
        logger = logging.getLogger(_LOGGER_PREFIX)
        logger.addHandler(self)
        # Ensure the logger is at least at our level
        if logger.level > self.level or logger.level == logging.NOTSET:
            logger.setLevel(self.level)
        return self

    def uninstall(self) -> None:
        """Remove this handler from the finance_kernel logger hierarchy."""
        logger = logging.getLogger(_LOGGER_PREFIX)
        logger.removeHandler(self)

    # -----------------------------------------------------------------------
    # LogQueryPort protocol implementation
    # -----------------------------------------------------------------------

    def query_by_correlation_id(self, correlation_id: str) -> list[dict]:
        """Return all records matching the given correlation_id.

        Preconditions:
            - ``correlation_id`` is a non-empty string (typically a UUID).
            - This handler has been ``install()``ed before the records
              were emitted (records emitted before install are not captured).

        Postconditions:
            - Every returned dict has ``correlation_id`` matching the input.
        """
        return [
            r for r in self._records
            if r.get("correlation_id") == correlation_id
        ]

    def query_by_event_id(self, event_id: str) -> list[dict]:
        """Return all records matching the given event_id.

        Preconditions:
            - ``event_id`` is a non-empty string (typically a UUID).

        Postconditions:
            - Every returned dict has ``event_id`` or ``source_event_id``
              matching the input.
        """
        return [
            r for r in self._records
            if r.get("event_id") == event_id
            or r.get("source_event_id") == event_id
        ]

    def query_by_trace_id(self, trace_id: str) -> list[dict]:
        """Return all records matching the given trace_id.

        Preconditions:
            - ``trace_id`` is a non-empty string (typically a UUID).

        Postconditions:
            - Every returned dict has ``trace_id`` matching the input.
        """
        return [
            r for r in self._records
            if r.get("trace_id") == trace_id
        ]

    # -----------------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------------

    @property
    def records(self) -> list[dict]:
        """All captured records (read-only copy)."""
        return list(self._records)

    def clear(self) -> None:
        """Discard all captured records."""
        self._records.clear()

    def __len__(self) -> int:
        return len(self._records)

    def __enter__(self) -> "LogCapture":
        """Context manager: install on entry."""
        return self.install()

    def __exit__(self, *exc: Any) -> None:
        """Context manager: uninstall on exit."""
        self.uninstall()
