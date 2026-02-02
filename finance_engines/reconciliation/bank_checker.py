"""
BankReconciliationChecker -- Pure engine for bank reconciliation analysis.

Detects stale unmatched lines, cross-statement balance discontinuities,
duplicate GL matches, and unexplained variance on completed reconciliations.

Architecture: finance_engines -- pure calculation, zero I/O, zero DB access.
All inputs are frozen dataclasses populated by the service layer.

Invariants enforced:
    BR-1  Timely matching
    BR-2  Balance continuity
    BR-3  Match uniqueness
    BR-4  Variance accountability
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import UUID

from finance_kernel.logging_config import get_logger
from finance_engines.tracer import traced_engine

from finance_engines.reconciliation.lifecycle_types import (
    CheckSeverity,
    ReconciliationFinding,
)
from finance_engines.reconciliation.bank_recon_types import (
    BankReconCheckResult,
    BankReconContext,
)

logger = get_logger("engines.reconciliation.bank_checker")

# Default thresholds
_DEFAULT_STALE_THRESHOLD_DAYS = 30
_DEFAULT_VARIANCE_TOLERANCE = Decimal("0.01")


class BankReconciliationChecker:
    """Pure engine for bank reconciliation checks.

    All methods receive a fully populated BankReconContext and return
    tuples of ReconciliationFinding. No I/O, no database access.

    Usage:
        checker = BankReconciliationChecker()
        result = checker.run_all_checks(context)
    """

    # -----------------------------------------------------------------
    # BR-1: Stale unmatched lines
    # -----------------------------------------------------------------

    @traced_engine(
        "bank_reconciliation", "1.0",
        fingerprint_fields=("context",),
    )
    def check_stale_unmatched(
        self,
        context: BankReconContext,
        stale_threshold_days: int = _DEFAULT_STALE_THRESHOLD_DAYS,
    ) -> tuple[ReconciliationFinding, ...]:
        """BR-1: Flag statement lines unmatched beyond threshold.

        Only checks lines with status "unmatched". Lines that are
        "excluded", "suggested", or "matched" are skipped.
        """
        findings: list[ReconciliationFinding] = []

        for stmt in context.statements:
            for line in stmt.lines:
                if line.status != "unmatched":
                    continue

                days_old = (context.as_of_date - line.transaction_date).days
                if days_old > stale_threshold_days:
                    findings.append(ReconciliationFinding(
                        code="STALE_UNMATCHED_LINE",
                        severity=CheckSeverity.WARNING,
                        message=(
                            f"Statement line {line.line_id} from "
                            f"{line.transaction_date} has been unmatched "
                            f"for {days_old} days (threshold: "
                            f"{stale_threshold_days})"
                        ),
                        details={
                            "line_id": str(line.line_id),
                            "statement_id": str(stmt.statement_id),
                            "transaction_date": str(line.transaction_date),
                            "amount": str(line.amount),
                            "days_unmatched": days_old,
                            "threshold_days": stale_threshold_days,
                            "description": line.description,
                        },
                    ))

        return tuple(findings)

    # -----------------------------------------------------------------
    # BR-2: Cross-statement balance continuity
    # -----------------------------------------------------------------

    @traced_engine(
        "bank_reconciliation", "1.0",
        fingerprint_fields=("context",),
    )
    def check_balance_continuity(
        self,
        context: BankReconContext,
    ) -> tuple[ReconciliationFinding, ...]:
        """BR-2: Consecutive statements must have continuous balances.

        For each pair of consecutive statements (sorted by date),
        statement[N].closing_balance must equal statement[N+1].opening_balance.
        """
        findings: list[ReconciliationFinding] = []

        if len(context.statements) < 2:
            return ()

        # Sort by statement_date for safety
        sorted_stmts = sorted(context.statements, key=lambda s: s.statement_date)

        for i in range(len(sorted_stmts) - 1):
            current = sorted_stmts[i]
            next_stmt = sorted_stmts[i + 1]

            if current.closing_balance != next_stmt.opening_balance:
                gap = next_stmt.opening_balance - current.closing_balance
                findings.append(ReconciliationFinding(
                    code="BALANCE_DISCONTINUITY",
                    severity=CheckSeverity.ERROR,
                    message=(
                        f"Balance gap between statement "
                        f"{current.statement_date} (closing: "
                        f"{current.closing_balance}) and "
                        f"{next_stmt.statement_date} (opening: "
                        f"{next_stmt.opening_balance}), "
                        f"difference: {gap}"
                    ),
                    details={
                        "prior_statement_id": str(current.statement_id),
                        "prior_statement_date": str(current.statement_date),
                        "prior_closing_balance": str(current.closing_balance),
                        "next_statement_id": str(next_stmt.statement_id),
                        "next_statement_date": str(next_stmt.statement_date),
                        "next_opening_balance": str(next_stmt.opening_balance),
                        "gap": str(gap),
                    },
                ))

        return tuple(findings)

    # -----------------------------------------------------------------
    # BR-3: Duplicate GL match
    # -----------------------------------------------------------------

    @traced_engine(
        "bank_reconciliation", "1.0",
        fingerprint_fields=("context",),
    )
    def check_duplicate_gl_matches(
        self,
        context: BankReconContext,
    ) -> tuple[ReconciliationFinding, ...]:
        """BR-3: Same journal_line_id must not be matched to multiple statement lines.

        Collects all matched_journal_line_ids across all statement lines.
        If any GL line ID appears more than once, it is flagged.
        """
        findings: list[ReconciliationFinding] = []

        # Map: journal_line_id -> list of (statement_id, line_id) that reference it
        gl_usage: dict[UUID, list[tuple[UUID, UUID]]] = defaultdict(list)

        for stmt in context.statements:
            for line in stmt.lines:
                for gl_id in line.matched_journal_line_ids:
                    gl_usage[gl_id].append((stmt.statement_id, line.line_id))

        for gl_id, usages in gl_usage.items():
            if len(usages) <= 1:
                continue

            line_ids = [str(u[1]) for u in usages]
            stmt_ids = [str(u[0]) for u in usages]
            findings.append(ReconciliationFinding(
                code="DUPLICATE_GL_MATCH",
                severity=CheckSeverity.ERROR,
                message=(
                    f"Journal line {gl_id} is matched to "
                    f"{len(usages)} statement lines: "
                    f"{', '.join(line_ids)}"
                ),
                details={
                    "journal_line_id": str(gl_id),
                    "matched_to_count": len(usages),
                    "statement_line_ids": line_ids,
                    "statement_ids": stmt_ids,
                },
            ))

        return tuple(findings)

    # -----------------------------------------------------------------
    # BR-4: Unexplained variance on completed reconciliation
    # -----------------------------------------------------------------

    @traced_engine(
        "bank_reconciliation", "1.0",
        fingerprint_fields=("context",),
    )
    def check_unexplained_variance(
        self,
        context: BankReconContext,
        tolerance: Decimal = _DEFAULT_VARIANCE_TOLERANCE,
    ) -> tuple[ReconciliationFinding, ...]:
        """BR-4: Completed reconciliations must not have unexplained variance.

        Only fires when reconciliation_status is "completed" and
        the absolute variance exceeds the tolerance.
        """
        if context.reconciliation_status != "completed":
            return ()

        if context.reconciliation_variance is None:
            return ()

        if abs(context.reconciliation_variance) <= tolerance:
            return ()

        return (
            ReconciliationFinding(
                code="UNEXPLAINED_VARIANCE",
                severity=CheckSeverity.WARNING,
                message=(
                    f"Completed reconciliation for bank account "
                    f"{context.bank_account_id} has unexplained "
                    f"variance of {context.reconciliation_variance} "
                    f"(tolerance: {tolerance})"
                ),
                details={
                    "bank_account_id": str(context.bank_account_id),
                    "variance": str(context.reconciliation_variance),
                    "tolerance": str(tolerance),
                    "reconciliation_status": context.reconciliation_status,
                },
            ),
        )

    # -----------------------------------------------------------------
    # Orchestrator: run all checks
    # -----------------------------------------------------------------

    @traced_engine(
        "bank_reconciliation", "1.0",
        fingerprint_fields=("context",),
    )
    def run_all_checks(
        self,
        context: BankReconContext,
        stale_threshold_days: int = _DEFAULT_STALE_THRESHOLD_DAYS,
        variance_tolerance: Decimal = _DEFAULT_VARIANCE_TOLERANCE,
    ) -> BankReconCheckResult:
        """Run all 4 check categories and return aggregated result."""
        all_findings: list[ReconciliationFinding] = []
        checks: list[str] = []

        # BR-1
        all_findings.extend(self.check_stale_unmatched(
            context=context,
            stale_threshold_days=stale_threshold_days,
        ))
        checks.append("BR-1:stale_unmatched")

        # BR-2
        all_findings.extend(self.check_balance_continuity(context=context))
        checks.append("BR-2:balance_continuity")

        # BR-3
        all_findings.extend(self.check_duplicate_gl_matches(context=context))
        checks.append("BR-3:duplicate_gl_match")

        # BR-4
        all_findings.extend(self.check_unexplained_variance(
            context=context,
            tolerance=variance_tolerance,
        ))
        checks.append("BR-4:unexplained_variance")

        return BankReconCheckResult.from_findings(
            bank_account_id=context.bank_account_id,
            findings=tuple(all_findings),
            statements_checked=context.statement_count,
            lines_checked=context.total_lines,
            checks_performed=tuple(checks),
            as_of_date=context.as_of_date,
        )
