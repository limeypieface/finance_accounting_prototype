"""
finance_services._close_types -- Period Close DTOs for the close orchestrator.

Responsibility:
    Define frozen dataclasses for the period close lifecycle: run status,
    close roles with authority hierarchy, close exceptions (blocking/warning),
    health check results, phase results, close runs, certificates, and
    the top-level PeriodCloseResult.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    These types live in finance_services/ because the orchestrator that
    produces and consumes them lives here.  The dependency direction is:
        finance_modules -> finance_services (may import these types)
        finance_services -> finance_kernel (types have no kernel dependency)

Invariants enforced:
    - R6 (replay safety): all DTOs are frozen dataclasses (immutable).
    - Close authority hierarchy: CloseRole.has_authority enforces a
      strict ordering (AUDITOR < PREPARER < REVIEWER < APPROVER).

Failure modes:
    - KeyError from CloseRole.has_authority if an unknown role is passed
      (should not happen since CloseRole is a closed enum).

Audit relevance:
    CloseCertificate is the immutable attestation of a completed period
    close.  It records the ledger_hash (R24), trial balance snapshot,
    subledgers closed, adjustments and closing entries posted, and the
    approver identity.  The certificate is persisted as an audit event
    payload.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


class CloseRunStatus(Enum):
    """Period close run lifecycle."""
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CloseRole(str, Enum):
    """Roles for period close operations."""
    AUDITOR = "auditor"
    PREPARER = "preparer"
    REVIEWER = "reviewer"
    APPROVER = "approver"

    def has_authority(self, required: "CloseRole") -> bool:
        """Check if this role has at least the authority of the required role."""
        hierarchy = {
            CloseRole.AUDITOR: 0,
            CloseRole.PREPARER: 1,
            CloseRole.REVIEWER: 2,
            CloseRole.APPROVER: 3,
        }
        return hierarchy[self] >= hierarchy[required]


@dataclass(frozen=True)
class CloseException:
    """Structured investigation item for close diagnostics."""
    category: str
    subledger_type: str | None
    entity_id: str | None
    account_code: str | None
    amount: Decimal
    currency: str
    description: str
    severity: str  # "blocking" or "warning"


@dataclass(frozen=True)
class HealthCheckResult:
    """Pre-close diagnostic result."""
    period_code: str
    run_at: datetime
    sl_reconciliation: dict[str, dict[str, Any]] = field(default_factory=dict)
    suspense_balances: list[dict[str, Any]] = field(default_factory=list)
    trial_balance_ok: bool = True
    total_debits: Decimal = Decimal("0")
    total_credits: Decimal = Decimal("0")
    period_entry_count: int = 0
    period_rejection_count: int = 0
    blocking_issues: list[CloseException] = field(default_factory=list)
    warnings: list[CloseException] = field(default_factory=list)

    @property
    def can_proceed(self) -> bool:
        """True if zero blocking issues."""
        return len(self.blocking_issues) == 0


@dataclass(frozen=True)
class ClosePhaseResult:
    """Result of executing a single close phase."""
    phase: int
    phase_name: str
    success: bool
    executed_by: UUID
    guard: str | None = None
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    exceptions: tuple[CloseException, ...] = ()


@dataclass(frozen=True)
class PeriodCloseRun:
    """Auditable record of a period close attempt."""
    id: UUID
    period_code: str
    fiscal_year: int
    is_year_end: bool
    status: CloseRunStatus
    current_phase: int
    correlation_id: str
    started_at: datetime
    started_by: UUID
    completed_at: datetime | None = None
    phase_actors: dict[int, UUID] = field(default_factory=dict)
    ledger_hash: str | None = None
    certificate_id: UUID | None = None


@dataclass(frozen=True)
class CloseCertificate:
    """Immutable attestation of period close. Persisted as audit event payload."""
    id: UUID
    period_code: str
    closed_at: datetime
    closed_by: UUID
    approved_by: UUID | None
    correlation_id: str
    ledger_hash: str
    trial_balance_debits: Decimal
    trial_balance_credits: Decimal
    subledgers_closed: tuple[str, ...]
    adjustments_posted: int
    closing_entries_posted: int
    phases_completed: int
    phases_skipped: int
    audit_event_id: UUID | None = None


@dataclass(frozen=True)
class PeriodCloseResult:
    """Return type from close_period_full()."""
    period_code: str
    status: CloseRunStatus
    correlation_id: str
    phases_completed: int
    phases_total: int
    phase_results: tuple[ClosePhaseResult, ...]
    started_at: datetime
    completed_at: datetime | None = None
    certificate: CloseCertificate | None = None
    message: str = ""
