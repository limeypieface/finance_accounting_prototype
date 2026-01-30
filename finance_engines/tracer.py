"""
Engine Invocation Tracer — emits FINANCE_ENGINE_TRACE.

Provides a decorator that wraps pure engine invocations with structured
trace logging. The trace captures:
- trace_id, event_id (from LogContext)
- engine_name, engine_version
- input_fingerprint (hash of canonicalized inputs)
- caller information

Usage:
    from finance_engines.tracer import traced_engine

    @traced_engine("variance", "1.0")
    def calculate_variance(standard_cost, actual_cost, quantity):
        ...

The decorator is lightweight and does not affect engine purity — it only
reads from LogContext (set by the caller) and emits a log record.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import time
from typing import Any, Callable

# Engine tracer uses its own logger namespace to avoid importing kernel logging.
# In production this logger is configured by the application root.
_logger = logging.getLogger("finance_kernel.engines.tracer")


def _canonicalize(value: Any) -> str:
    """Produce a stable string representation of a value for fingerprinting."""
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
