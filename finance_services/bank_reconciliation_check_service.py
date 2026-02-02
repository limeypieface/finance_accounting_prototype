"""
BankReconciliationCheckService -- Service wrapper for bank reconciliation checks.

Composes BankReconciliationChecker (pure engine) with clock injection
and configurable thresholds.

Architecture: finance_services -- imperative shell.
    The service receives a pre-built BankReconContext and delegates to
    the pure engine for analysis.  Context building (ORM queries) is
    the caller's responsibility, as cash ORM models reside in
    finance_modules which this layer may not import.

Invariants enforced:
    BR-1 through BR-4 via BankReconciliationChecker.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.logging_config import get_logger

from finance_engines.reconciliation.bank_checker import BankReconciliationChecker
from finance_engines.reconciliation.bank_recon_types import (
    BankReconCheckResult,
    BankReconContext,
)

logger = get_logger("services.bank_reconciliation_check")


class BankReconciliationCheckService:
    """Service that runs bank reconciliation checks on pre-built contexts.

    Contract:
        - ``check()`` runs all BR checks on a single BankReconContext.
        - ``check_multiple()`` runs checks on multiple contexts.

    Non-goals:
        - Does NOT build contexts from ORM (caller provides BankReconContext).
        - Does NOT persist check results (caller decides).
        - Does NOT modify any data (read-only).
    """

    def __init__(
        self,
        clock: Clock | None = None,
        checker: BankReconciliationChecker | None = None,
        stale_threshold_days: int = 30,
        variance_tolerance: Decimal = Decimal("0.01"),
    ) -> None:
        self._clock = clock or SystemClock()
        self._checker = checker or BankReconciliationChecker()
        self._stale_threshold_days = stale_threshold_days
        self._variance_tolerance = variance_tolerance

    def check(
        self,
        context: BankReconContext,
    ) -> BankReconCheckResult:
        """Run all bank reconciliation checks on the given context.

        If ``context.as_of_date`` is the default (2000-01-01), the current
        clock date is substituted.

        Args:
            context: Pre-built bank reconciliation context.

        Returns:
            BankReconCheckResult with findings and status.
        """
        # Substitute default date with clock
        effective_context = context
        if context.as_of_date == date(2000, 1, 1):
            effective_context = BankReconContext(
                bank_account_id=context.bank_account_id,
                statements=context.statements,
                reconciliation_status=context.reconciliation_status,
                reconciliation_variance=context.reconciliation_variance,
                as_of_date=self._clock.now().date(),
            )

        return self._checker.run_all_checks(
            context=effective_context,
            stale_threshold_days=self._stale_threshold_days,
            variance_tolerance=self._variance_tolerance,
        )

    def check_multiple(
        self,
        contexts: list[BankReconContext],
    ) -> list[BankReconCheckResult]:
        """Run bank reconciliation checks on multiple contexts.

        Args:
            contexts: List of pre-built bank reconciliation contexts.

        Returns:
            List of BankReconCheckResult, one per context.
        """
        return [self.check(ctx) for ctx in contexts]
