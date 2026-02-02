"""
Bank reconciliation check domain types -- GAP-BRC.

Pure frozen dataclasses for bank reconciliation analysis.
Used by BankReconciliationChecker (pure engine) and
BankReconciliationCheckService (imperative shell).

Architecture: finance_engines/reconciliation -- pure domain, zero I/O.

Invariants supported:
    BR-1 through BR-4 (timely matching, balance continuity,
    match uniqueness, variance accountability).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from finance_engines.reconciliation.lifecycle_types import (
    CheckSeverity,
    CheckStatus,
    ReconciliationFinding,
)


# =============================================================================
# Input types (populated by service, consumed by engine)
# =============================================================================


@dataclass(frozen=True)
class BankReconLine:
    """One statement line with its match state.

    The service layer populates these from DB queries.  The engine
    receives them as immutable inputs.
    """

    line_id: UUID
    transaction_date: date
    amount: Decimal
    description: str
    status: str  # "unmatched" | "matched" | "excluded" | "suggested"
    matched_journal_line_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class BankReconStatement:
    """One bank statement with its lines and matches -- engine input."""

    statement_id: UUID
    bank_account_id: UUID
    statement_date: date
    opening_balance: Decimal
    closing_balance: Decimal
    currency: str
    lines: tuple[BankReconLine, ...] = ()

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def unmatched_count(self) -> int:
        return sum(1 for ln in self.lines if ln.status == "unmatched")

    @property
    def matched_count(self) -> int:
        return sum(1 for ln in self.lines if ln.status == "matched")


@dataclass(frozen=True)
class BankReconContext:
    """Complete input for bank reconciliation checks.

    ``statements`` must be ordered by statement_date (ascending).
    """

    bank_account_id: UUID
    statements: tuple[BankReconStatement, ...] = ()
    reconciliation_status: str | None = None  # "draft" | "completed" | None
    reconciliation_variance: Decimal | None = None
    as_of_date: date = date(2000, 1, 1)

    @property
    def statement_count(self) -> int:
        return len(self.statements)

    @property
    def total_lines(self) -> int:
        return sum(s.line_count for s in self.statements)


# =============================================================================
# Output type
# =============================================================================


@dataclass(frozen=True)
class BankReconCheckResult:
    """Complete result of bank reconciliation check.

    Aggregates all findings from all bank check categories.
    ``status`` is derived from the highest-severity finding.
    """

    bank_account_id: UUID
    status: CheckStatus
    findings: tuple[ReconciliationFinding, ...] = ()
    statements_checked: int = 0
    lines_checked: int = 0
    checks_performed: tuple[str, ...] = ()
    as_of_date: date | None = None

    @property
    def is_clean(self) -> bool:
        """True if no findings of any severity."""
        return len(self.findings) == 0

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == CheckSeverity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == CheckSeverity.WARNING)

    @classmethod
    def from_findings(
        cls,
        bank_account_id: UUID,
        findings: tuple[ReconciliationFinding, ...],
        statements_checked: int,
        lines_checked: int,
        checks_performed: tuple[str, ...],
        as_of_date: date,
    ) -> BankReconCheckResult:
        """Factory that derives status from findings."""
        has_error = any(f.severity == CheckSeverity.ERROR for f in findings)
        has_warning = any(f.severity == CheckSeverity.WARNING for f in findings)

        if has_error:
            status = CheckStatus.FAILED
        elif has_warning:
            status = CheckStatus.WARNING
        else:
            status = CheckStatus.PASSED

        return cls(
            bank_account_id=bank_account_id,
            status=status,
            findings=findings,
            statements_checked=statements_checked,
            lines_checked=lines_checked,
            checks_performed=checks_performed,
            as_of_date=as_of_date,
        )
