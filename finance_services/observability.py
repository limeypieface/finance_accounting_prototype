"""
Observability hooks for reconciliation and matching.

Emits structured log events for metrics and dashboards:
- Match hit-rate: match_suggested (suggestion count), match_accepted (applied).
- Guard failures: guard_failure (with guard_type for aggregation).
- Optional: duration_ms for time-to-clear and latency.

All events use a consistent ``observability_event`` field and stable extra
fields so log aggregators (e.g. Datadog, Splunk) can parse and build metrics.

Usage:
    from finance_services.observability import (
        log_match_suggested,
        log_match_accepted,
        log_guard_failure,
    )
    log_match_suggested(context="bank", suggestion_count=5, statement_line_id=str(id))
    log_match_accepted(context="payment", duration_ms=12.5, invoice_ref=str(ref))
    log_guard_failure(guard_type="DOCUMENT_ALREADY_MATCHED", document_ref=str(ref))
"""

from __future__ import annotations

from typing import Any

from finance_kernel.logging_config import get_logger

logger = get_logger("services.observability")

# Standard event names for filtering in log pipelines
EVENT_MATCH_SUGGESTED = "match_suggested"
EVENT_MATCH_ACCEPTED = "match_accepted"
EVENT_GUARD_FAILURE = "guard_failure"


def log_match_suggested(
    *,
    context: str,
    suggestion_count: int,
    statement_line_id: str | None = None,
    target_id: str | None = None,
    candidate_count: int | None = None,
    duration_ms: float | None = None,
    **extra: Any,
) -> None:
    """
    Log when a match-suggestion run completed (e.g. find_matches, find_bank_match_suggestions).

    Use for match hit-rate numerator: how many suggestions were produced.
    """
    payload: dict[str, Any] = {
        "observability_event": EVENT_MATCH_SUGGESTED,
        "context": context,
        "suggestion_count": suggestion_count,
        **extra,
    }
    if statement_line_id is not None:
        payload["statement_line_id"] = statement_line_id
    if target_id is not None:
        payload["target_id"] = target_id
    if candidate_count is not None:
        payload["candidate_count"] = candidate_count
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 2)
    logger.info("reconciliation_match_suggested", extra=payload)


def log_match_accepted(
    *,
    context: str,
    duration_ms: float | None = None,
    invoice_ref: str | None = None,
    payment_ref: str | None = None,
    statement_line_id: str | None = None,
    match_type: str | None = None,
    **extra: Any,
) -> None:
    """
    Log when a match or payment application was successfully applied.

    Use for match hit-rate denominator and time-to-clear.
    """
    payload: dict[str, Any] = {
        "observability_event": EVENT_MATCH_ACCEPTED,
        "context": context,
        **extra,
    }
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 2)
    if invoice_ref is not None:
        payload["invoice_ref"] = invoice_ref
    if payment_ref is not None:
        payload["payment_ref"] = payment_ref
    if statement_line_id is not None:
        payload["statement_line_id"] = statement_line_id
    if match_type is not None:
        payload["match_type"] = match_type
    logger.info("reconciliation_match_accepted", extra=payload)


def log_guard_failure(
    *,
    guard_type: str,
    exc_code: str | None = None,
    document_ref: str | None = None,
    statement_line_id: str | None = None,
    variance_type: str | None = None,
    **extra: Any,
) -> None:
    """
    Log when a reconciliation guard rejected an operation.

    guard_type should be one of: DOCUMENT_ALREADY_MATCHED, OVERAPPLICATION,
    MATCH_VARIANCE_EXCEEDED, BANK_RECONCILIATION_ERROR (or exception code).
    """
    payload: dict[str, Any] = {
        "observability_event": EVENT_GUARD_FAILURE,
        "guard_type": guard_type,
        **extra,
    }
    if exc_code is not None:
        payload["exc_code"] = exc_code
    if document_ref is not None:
        payload["document_ref"] = document_ref
    if statement_line_id is not None:
        payload["statement_line_id"] = statement_line_id
    if variance_type is not None:
        payload["variance_type"] = variance_type
    logger.warning("reconciliation_guard_failure", extra=payload)
