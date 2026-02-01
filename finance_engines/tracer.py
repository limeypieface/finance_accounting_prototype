"""
finance_engines.tracer -- Engine invocation tracer emitting FINANCE_ENGINE_TRACE.

Responsibility:
    Provide a lightweight decorator (``@traced_engine``) that wraps pure
    engine invocations with structured trace logging.  The trace captures
    engine_name, engine_version, input_fingerprint (deterministic SHA-256
    hash of selected inputs), and duration_ms.

Architecture position:
    Engines -- infrastructure support for the pure calculation layer.
    Does NOT introduce I/O into engines; emits a log record only.
    Uses its own logger namespace (``finance_kernel.engines.tracer``)
    to avoid importing kernel logging infrastructure.

Invariants enforced:
    - R6 (replay safety): fingerprint computation is deterministic --
      _canonicalize produces stable string representations of values;
      dict keys are sorted; the hash is SHA-256 truncated to 16 hex chars.
    - Engine purity: the decorator only reads kwargs and emits a log
      record; it does not mutate inputs or inject side effects.

Failure modes:
    - If fingerprint_fields reference kwargs that are not present, the
      missing field is recorded as "null" (safe default).
    - _canonicalize falls back to ``str(value)`` for unknown types,
      which may produce non-deterministic output for custom objects
      without stable __str__.

Audit relevance:
    Every engine invocation produces a FINANCE_ENGINE_TRACE log record
    that is consumed by the EngineDispatcher and persisted in
    EngineTraceRecord for post-hoc audit.  The input_fingerprint allows
    deterministic replay verification.

Usage:
    from finance_engines.tracer import traced_engine

    @traced_engine("variance", "1.0")
    def calculate_variance(standard_cost, actual_cost, quantity):
        ...

The decorator is lightweight and does not affect engine purity -- it only
reads from LogContext (set by the caller) and emits a log record.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

# Engine tracer uses its own logger namespace to avoid importing kernel logging.
# In production this logger is configured by the application root.
_logger = logging.getLogger("finance_kernel.engines.tracer")


def _canonicalize(value: Any) -> str:
    """Produce a stable string representation of a value for fingerprinting.

    Preconditions:
        value is any Python object.

    Postconditions:
        Returns a deterministic string for None, int, float, str, dict
        (sorted keys), list/tuple (order-preserved).  Unknown types fall
        back to ``str(value)``.

    Raises:
        Nothing -- always returns a string.
    """
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        items = sorted(value.items())
        return "{" + ",".join(f"{k}:{_canonicalize(v)}" for k, v in items) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonicalize(v) for v in value) + "]"
    return str(value)


def compute_input_fingerprint(
    fingerprint_fields: tuple[str, ...],
    kwargs: dict[str, Any],
) -> str:
    """Compute a deterministic SHA-256 fingerprint of selected input fields.

    Preconditions:
        fingerprint_fields is a tuple of string field names.
        kwargs is the keyword arguments dict from the engine invocation.

    Postconditions:
        Returns a 16-character hex string (SHA-256 prefix) computed from
        the canonicalized values of the specified fields.  Missing fields
        are recorded as "null".  The result is deterministic for identical
        inputs.

    Raises:
        Nothing -- always returns a string.

    Only the fields listed in fingerprint_fields are included. Missing
    fields are recorded as "null". The result is a hex digest prefix (16 chars).
    """
    parts: list[str] = []
    for field in fingerprint_fields:
        val = kwargs.get(field)
        parts.append(f"{field}={_canonicalize(val)}")
    canonical = "|".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def traced_engine(
    engine_name: str,
    engine_version: str,
    fingerprint_fields: tuple[str, ...] = (),
) -> Callable:
    """Decorator that emits FINANCE_ENGINE_TRACE for pure engine invocations.

    Args:
        engine_name: Engine identifier (e.g., "variance").
        engine_version: Engine version (e.g., "1.0").
        fingerprint_fields: Keyword argument names to include in
            the input fingerprint hash.

    Returns:
        Decorator function.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Compute input fingerprint from kwargs
            fp = ""
            if fingerprint_fields:
                fp = compute_input_fingerprint(fingerprint_fields, kwargs)

            t0 = time.monotonic()
            result = func(*args, **kwargs)
            duration_ms = round((time.monotonic() - t0) * 1000, 2)

            _logger.info(
                "FINANCE_ENGINE_TRACE",
                extra={
                    "trace_type": "FINANCE_ENGINE_TRACE",
                    "engine_name": engine_name,
                    "engine_version": engine_version,
                    "input_fingerprint": fp,
                    "duration_ms": duration_ms,
                    "function": func.__qualname__,
                },
            )
            return result

        return wrapper

    return decorator
