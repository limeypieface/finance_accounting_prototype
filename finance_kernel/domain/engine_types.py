"""
Engine dispatch types — pure frozen dataclasses.

These DTOs are used by both the EngineDispatcher (in finance_services/)
and the InterpretationCoordinator (in finance_kernel/services/).
They live in finance_kernel/domain/ because they have no external
dependencies and are needed by kernel-internal code.

The EngineDispatcher class itself lives in finance_services/ because
it depends on finance_config (CompiledPolicy) and finance_engines (tracer).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EngineTraceRecord:
    """Audit record for a single engine invocation."""

    engine_name: str
    engine_version: str
    input_fingerprint: str
    duration_ms: float
    parameters_used: dict[str, Any]
    success: bool
    error: str | None = None


@dataclass(frozen=True)
class EngineDispatchResult:
    """Result of dispatching all required engines for a policy."""

    engine_outputs: dict[str, Any]
    traces: tuple[EngineTraceRecord, ...]
    all_succeeded: bool
    errors: tuple[str, ...]
