"""
EngineTypes -- Pure frozen DTOs for engine dispatch tracing.

Responsibility:
    Defines the immutable data structures for engine invocation auditing:
    ``EngineTraceRecord`` (per-engine) and ``EngineDispatchResult`` (aggregate).

Architecture position:
    Kernel > Domain -- pure functional core, zero I/O.
    Used by EngineDispatcher (finance_services/) and
    InterpretationCoordinator (finance_kernel/services/).

Invariants enforced:
    (none directly -- supports audit trail for engine invocations)

Failure modes:
    (none -- pure value objects)

Audit relevance:
    EngineTraceRecord captures engine name, version, input fingerprint,
    duration, and success/failure for every engine invocation.  Auditors
    use this to verify that the correct engine versions were invoked and
    that results are reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EngineTraceRecord:
    """
    Audit record for a single engine invocation.

    Contract:
        One record per engine call.  ``success=False`` requires ``error``
        to carry a human-readable description.

    Guarantees:
        Frozen dataclass -- immutable after construction.
    """

    engine_name: str
    engine_version: str
    input_fingerprint: str
    duration_ms: float
    parameters_used: dict[str, Any]
    success: bool
    error: str | None = None


@dataclass(frozen=True)
class EngineDispatchResult:
    """
    Aggregate result of dispatching all required engines for a policy.

    Contract:
        ``all_succeeded`` is ``True`` only if every engine trace reports
        ``success=True``.

    Guarantees:
        - Frozen dataclass -- immutable after construction.
        - ``errors`` collects all engine-level error messages.
    """

    engine_outputs: dict[str, Any]
    traces: tuple[EngineTraceRecord, ...]
    all_succeeded: bool
    errors: tuple[str, ...]
