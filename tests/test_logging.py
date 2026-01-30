"""Tests for the structured logging system (finance_kernel/logging_config.py)."""

import json
import logging
from io import StringIO
from uuid import uuid4

import pytest

from finance_kernel.logging_config import (
    LogContext,
    StructuredFormatter,
    configure_logging,
    get_logger,
    reset_logging,
)


@pytest.fixture(autouse=True)
def _clean_logging():
    """Reset logging state between tests."""
    reset_logging()
    LogContext.clear()
    yield
    LogContext.clear()
    reset_logging()


def _make_handler() -> tuple[logging.Handler, StringIO]:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter())
    return handler, stream


def _parse_log(stream: StringIO) -> dict:
    """Parse the first JSON log line from a stream."""
    line = stream.getvalue().strip().split("\n")[0]
    return json.loads(line)


def _parse_all_logs(stream: StringIO) -> list[dict]:
    """Parse all JSON log lines from a stream."""
    lines = stream.getvalue().strip().split("\n")
    return [json.loads(line) for line in lines if line]


# ---------------------------------------------------------------------------
# StructuredFormatter tests
# ---------------------------------------------------------------------------


class TestStructuredFormatter:
    """Tests for JSON log output format."""

    def test_basic_json_output(self):
        handler, stream = _make_handler()
        configure_logging(handler=handler)
        logger = get_logger("test")
        logger.info("hello")

        record = _parse_log(stream)
        assert record["level"] == "INFO"
        assert record["message"] == "hello"
        assert record["logger"] == "finance_kernel.test"
        assert "ts" in record

    def test_extra_fields_included(self):
        handler, stream = _make_handler()
        configure_logging(handler=handler)
        logger = get_logger("test")
        logger.info("posted", extra={"seq": 42, "status": "posted"})

        record = _parse_log(stream)
        assert record["seq"] == 42
        assert record["status"] == "posted"

    def test_context_fields_included(self):
        handler, stream = _make_handler()
        configure_logging(handler=handler)
        logger = get_logger("test")
        LogContext.set(correlation_id="abc-123", event_id="evt-456")
        logger.info("test_msg")

        record = _parse_log(stream)
        assert record["correlation_id"] == "abc-123"
        assert record["event_id"] == "evt-456"

    def test_exception_fields(self):
        handler, stream = _make_handler()
        configure_logging(handler=handler)
        logger = get_logger("test")
        try:
            raise ValueError("boom")
        except ValueError:
            logger.error("failed", exc_info=True)

        record = _parse_log(stream)
        assert record["exc_type"] == "ValueError"
        assert record["exc_message"] == "boom"
        assert "traceback" in record

    def test_finance_exception_code_extracted(self):
        """Finance kernel exceptions carry .code attribute."""
        handler, stream = _make_handler()
        configure_logging(handler=handler)
        logger = get_logger("test")
        from finance_kernel.exceptions import ClosedPeriodError

        try:
            raise ClosedPeriodError("2026-01", "2026-01-15")
        except ClosedPeriodError:
            logger.error("period_error", exc_info=True)

        record = _parse_log(stream)
        assert record["exc_code"] == "CLOSED_PERIOD"
        assert record["exc_type"] == "ClosedPeriodError"
        assert record["exc_period_code"] == "2026-01"
        assert record["exc_effective_date"] == "2026-01-15"

    def test_no_context_fields_when_empty(self):
        handler, stream = _make_handler()
        configure_logging(handler=handler)
        logger = get_logger("test")
        logger.info("bare_message")

        record = _parse_log(stream)
        assert "correlation_id" not in record
        assert "event_id" not in record

    def test_uuid_serialized(self):
        handler, stream = _make_handler()
        configure_logging(handler=handler)
        logger = get_logger("test")
        uid = uuid4()
        logger.info("with_uuid", extra={"entry_id": uid})

        record = _parse_log(stream)
        assert record["entry_id"] == str(uid)

    def test_valid_json_every_line(self):
        handler, stream = _make_handler()
        configure_logging(handler=handler)
        logger = get_logger("test")
        logger.info("first")
        logger.warning("second", extra={"k": "v"})
        logger.debug("third")

        logs = _parse_all_logs(stream)
        # debug should not appear at INFO level, but configure_logging was called with default INFO
        # actually we set level=INFO by default, so debug won't appear
        assert len(logs) == 2
        for record in logs:
            assert "ts" in record
            assert "level" in record
            assert "logger" in record
            assert "message" in record


# ---------------------------------------------------------------------------
# LogContext tests
# ---------------------------------------------------------------------------


class TestLogContext:
    """Tests for context propagation."""

    def test_set_and_get(self):
        LogContext.set(correlation_id="x", event_id="y")
        ctx = LogContext.get_all()
        assert ctx == {"correlation_id": "x", "event_id": "y"}

    def test_clear(self):
        LogContext.set(correlation_id="x")
        LogContext.clear()
        assert LogContext.get_all() == {}

    def test_bind_context_manager(self):
        LogContext.set(correlation_id="outer")
        with LogContext.bind(correlation_id="inner"):
            assert LogContext.get_all()["correlation_id"] == "inner"
        assert LogContext.get_all()["correlation_id"] == "outer"

    def test_bind_restores_none(self):
        """bind() restores to None if there was no previous value."""
        assert "correlation_id" not in LogContext.get_all()
        with LogContext.bind(correlation_id="temp"):
            assert LogContext.get_all()["correlation_id"] == "temp"
        assert "correlation_id" not in LogContext.get_all()

    def test_additive_set(self):
        LogContext.set(correlation_id="a")
        LogContext.set(event_id="b")
        ctx = LogContext.get_all()
        assert ctx["correlation_id"] == "a"
        assert ctx["event_id"] == "b"

    def test_all_fields(self):
        LogContext.set(
            correlation_id="c",
            event_id="e",
            actor_id="a",
            producer="p",
            entry_id="n",
            trace_id="t",
        )
        ctx = LogContext.get_all()
        assert len(ctx) == 6
        assert ctx["correlation_id"] == "c"
        assert ctx["trace_id"] == "t"


# ---------------------------------------------------------------------------
# configure_logging tests
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    """Tests for initialization."""

    def test_idempotent(self):
        h1, _ = _make_handler()
        configure_logging(handler=h1)
        h2, _ = _make_handler()
        configure_logging(handler=h2)  # second call is no-op
        root = logging.getLogger("finance_kernel")
        assert len(root.handlers) == 1

    def test_get_logger_returns_child(self):
        logger = get_logger("services.posting")
        assert logger.name == "finance_kernel.services.posting"

    def test_logger_hierarchy(self):
        """Child loggers inherit the finance_kernel root config."""
        handler, stream = _make_handler()
        configure_logging(handler=handler, level=logging.DEBUG)
        child = get_logger("deep.nested.module")
        child.debug("hierarchy_test")

        record = _parse_log(stream)
        assert record["message"] == "hierarchy_test"
        assert record["logger"] == "finance_kernel.deep.nested.module"
