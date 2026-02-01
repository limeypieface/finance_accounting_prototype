"""
finance_services.subledger_period_service -- Subledger period close service (SL-G6).

Responsibility:
    Orchestrate subledger period close with reconciliation enforcement.
    Compare SL aggregate balances against GL control account balances,
    persist failure reports on mismatch, and mark SL periods as CLOSED
    on success.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    Lives in finance_services/ (not kernel) because it coordinates across
    concrete subledger services, selectors, and the reconciler.
    Called by PeriodCloseOrchestrator during the subledger close phase.

Invariants enforced:
    - SL-G4 (snapshot isolation): uses the caller's session for all
      queries, ensuring a consistent point-in-time view.
    - SL-G6 (close-time enforcement): when enforce_on_close=True and
      reconciliation fails, the GL close is blocked and a
      ReconciliationFailureReport is persisted for audit.
    - SL-G3 (per-currency reconciliation): currency-specific balance
      comparison via SubledgerReconciler.validate_period_close.

Failure modes:
    - Reconciliation blocking violations: period remains OPEN and a
      ReconciliationFailureReportModel is persisted.
    - Unresolvable control account role: period is closed without
      enforcement (logged as warning for audit follow-up).
    - Already-closed period: returns existing status row (idempotent).

Audit relevance:
    - ReconciliationFailureReportModel captures GL balance, SL balance,
      delta, and checked_at for every failed reconciliation.
    - Successful closes are logged with GL/SL balance details.
    - get_close_status() provides period-end status reporting for all
      subledger types.

Key behaviors:
    - close_subledger_period(): Reconciles SL vs GL, creates failure report
      on mismatch, marks SL period as CLOSED on success.
    - is_subledger_closed(): Queries SubledgerPeriodStatusModel.
    - are_all_subledgers_closed(): Checks all contract-defined subledgers.
    - get_close_status(): Returns status dict for all subledger types.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock
from finance_kernel.domain.subledger_control import (
    SubledgerControlContract,
    SubledgerControlRegistry,
    SubledgerReconciler,
    SubledgerType,
)
from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger
from finance_kernel.models.subledger import (
    ReconciliationFailureReportModel,
    SubledgerPeriodStatus,
    SubledgerPeriodStatusModel,
)
from finance_kernel.selectors.ledger_selector import LedgerSelector
from finance_kernel.selectors.subledger_selector import SubledgerSelector
from finance_kernel.services.journal_writer import RoleResolver

logger = get_logger("services.subledger_period")


class SubledgerPeriodService:
    """Orchestrates subledger period close with reconciliation enforcement.

    Contract:
        Receives all dependencies via constructor injection.  Uses the
        caller's session for all queries (SL-G4 snapshot isolation).
    Guarantees:
        - ``close_subledger_period`` is idempotent: calling it on an
          already-closed period returns the existing status row.
        - Blocking violations prevent close and persist a failure report.
        - Non-blocking warnings are logged but do not prevent close.
        - ``are_all_subledgers_closed`` evaluates only subledgers with
          enforce_on_close=True.
    Non-goals:
        - Does not manage the GL period close; that is the
          PeriodCloseOrchestrator's responsibility.
        - Does not initiate corrective actions for reconciliation failures.
    """

    def __init__(
        self,
        session: Session,
        clock: Clock,
        registry: SubledgerControlRegistry,
        role_resolver: RoleResolver,
    ) -> None:
        self._session = session
        self._clock = clock
        self._registry = registry
        self._role_resolver = role_resolver
        self._sl_selector = SubledgerSelector(session)
        self._gl_selector = LedgerSelector(session)
        self._reconciler = SubledgerReconciler()

    def close_subledger_period(
        self,
        subledger_type: SubledgerType,
        period_code: str,
        period_end_date: date,
        actor_id: UUID | None = None,
    ) -> SubledgerPeriodStatusModel:
        """Close a subledger period with reconciliation enforcement.

        Preconditions:
            subledger_type is a valid SubledgerType enum value.
            period_code references an existing fiscal period.
            period_end_date is the accounting end date for balance queries.

        Postconditions:
            Returns SubledgerPeriodStatusModel with status CLOSED (if
            reconciliation passed or was not enforced) or OPEN (if
            reconciliation failed with blocking violations and a
            ReconciliationFailureReportModel was persisted).

        SL-G6: If enforce_on_close=True and reconciliation fails, the
        period remains OPEN and a ReconciliationFailureReport is persisted.

        Args:
            subledger_type: Which subledger to close.
            period_code: Period code (F17: from FiscalPeriod).
            period_end_date: End date for balance queries.
            actor_id: Who is closing (for audit trail).

        Returns:
            The SubledgerPeriodStatusModel (CLOSED or OPEN with failure report).
        """
        now = self._clock.now()
        contract = self._registry.get(subledger_type)

        # Get or create period status row
        status_row = self._get_or_create_status(subledger_type, period_code, now)

        if status_row.status == SubledgerPeriodStatus.CLOSED.value:
            logger.info(
                "subledger_period_already_closed",
                extra={
                    "subledger_type": subledger_type.value,
                    "period_code": period_code,
                },
            )
            return status_row

        # Mark as reconciling
        status_row.status = SubledgerPeriodStatus.RECONCILING.value

        # If no contract or enforce_on_close is False, close immediately
        if contract is None or not contract.enforce_on_close:
            status_row.status = SubledgerPeriodStatus.CLOSED.value
            status_row.closed_at = now
            status_row.closed_by = actor_id
            self._session.flush()
            logger.info(
                "subledger_period_closed",
                extra={
                    "subledger_type": subledger_type.value,
                    "period_code": period_code,
                    "enforcement": "skipped",
                },
            )
            return status_row

        # Resolve GL control account
        try:
            control_account_id, _code = self._role_resolver.resolve(
                contract.control_account_role, "GL", 0,
            )
        except Exception as exc:
            logger.warning(
                "subledger_period_close_role_unresolvable",
                extra={
                    "subledger_type": subledger_type.value,
                    "period_code": period_code,
                    "role": contract.control_account_role,
                    "error": str(exc),
                },
            )
            # Cannot resolve role — close without enforcement
            status_row.status = SubledgerPeriodStatus.CLOSED.value
            status_row.closed_at = now
            status_row.closed_by = actor_id
            self._session.flush()
            return status_row

        # Determine currency from contract binding
        currency = contract.binding.currency

        # Get GL control balance
        gl_balances = self._gl_selector.account_balance(
            account_id=control_account_id,
            as_of_date=period_end_date,
            currency=currency,
        )
        raw_gl_balance = gl_balances[0].balance if gl_balances else Decimal("0")

        # Normalize GL balance to SL convention
        if not contract.binding.is_debit_normal:
            gl_economic = -raw_gl_balance
        else:
            gl_economic = raw_gl_balance
        gl_balance = Money.of(gl_economic, currency)

        # Get SL aggregate balance
        sl_balance = self._sl_selector.get_aggregate_balance(
            subledger_type=subledger_type,
            as_of_date=period_end_date,
            currency=currency,
        )

        # Run period close reconciliation
        violations = self._reconciler.validate_period_close(
            contract=contract,
            subledger_balance=sl_balance,
            control_account_balance=gl_balance,
            period_end_date=period_end_date,
            checked_at=now,
        )

        blocking = [v for v in violations if v.blocking]

        if blocking:
            # INVARIANT [SL-G6]: blocking violations prevent close; persist failure report.
            delta = sl_balance - gl_balance
            report = ReconciliationFailureReportModel(
                id=uuid4(),
                subledger_type=subledger_type.value,
                period_code=period_code,
                gl_control_balance=gl_balance.amount,
                sl_aggregate_balance=sl_balance.amount,
                delta_amount=delta.amount,
                currency=currency,
                checked_at=now,
            )
            self._session.add(report)
            self._session.flush()

            status_row.status = SubledgerPeriodStatus.OPEN.value
            status_row.reconciliation_report_id = report.id
            self._session.flush()

            logger.warning(
                "subledger_period_close_blocked",
                extra={
                    "subledger_type": subledger_type.value,
                    "period_code": period_code,
                    "gl_balance": str(gl_balance.amount),
                    "sl_balance": str(sl_balance.amount),
                    "delta": str(delta.amount),
                    "report_id": str(report.id),
                    "violation_count": len(blocking),
                },
            )
            return status_row

        # Non-blocking warnings — log and close
        for v in violations:
            logger.info(
                "subledger_period_close_warning",
                extra={
                    "subledger_type": subledger_type.value,
                    "period_code": period_code,
                    "message": v.message,
                },
            )

        # Close successfully
        status_row.status = SubledgerPeriodStatus.CLOSED.value
        status_row.closed_at = now
        status_row.closed_by = actor_id
        self._session.flush()

        logger.info(
            "subledger_period_closed",
            extra={
                "subledger_type": subledger_type.value,
                "period_code": period_code,
                "gl_balance": str(gl_balance.amount),
                "sl_balance": str(sl_balance.amount),
                "enforcement": "passed",
            },
        )
        return status_row

    def is_subledger_closed(
        self,
        subledger_type: SubledgerType,
        period_code: str,
    ) -> bool:
        """Check if a specific subledger period is closed."""
        row = self._session.execute(
            select(SubledgerPeriodStatusModel).where(
                SubledgerPeriodStatusModel.subledger_type == subledger_type.value,
                SubledgerPeriodStatusModel.period_code == period_code,
            )
        ).scalar_one_or_none()

        return row is not None and row.status == SubledgerPeriodStatus.CLOSED.value

    def are_all_subledgers_closed(self, period_code: str) -> bool:
        """Check if all contract-defined subledgers are closed for a period.

        This is what the ALL_SUBLEDGERS_CLOSED guard evaluates.
        Returns True only if every subledger with enforce_on_close=True
        has a CLOSED status row for the given period.
        """
        for contract in self._registry.get_all():
            if not contract.enforce_on_close:
                continue
            if not self.is_subledger_closed(contract.subledger_type, period_code):
                return False
        return True

    def get_close_status(self, period_code: str) -> dict[str, str]:
        """Get close status for all subledger types for a period.

        Returns dict of subledger_type.value → status string.
        """
        result: dict[str, str] = {}
        for contract in self._registry.get_all():
            sl_type = contract.subledger_type
            row = self._session.execute(
                select(SubledgerPeriodStatusModel).where(
                    SubledgerPeriodStatusModel.subledger_type == sl_type.value,
                    SubledgerPeriodStatusModel.period_code == period_code,
                )
            ).scalar_one_or_none()

            if row is None:
                result[sl_type.value] = SubledgerPeriodStatus.OPEN.value
            else:
                result[sl_type.value] = row.status
        return result

    def _get_or_create_status(
        self,
        subledger_type: SubledgerType,
        period_code: str,
        now: datetime,
    ) -> SubledgerPeriodStatusModel:
        """Get or create SubledgerPeriodStatusModel row."""
        row = self._session.execute(
            select(SubledgerPeriodStatusModel).where(
                SubledgerPeriodStatusModel.subledger_type == subledger_type.value,
                SubledgerPeriodStatusModel.period_code == period_code,
            )
        ).scalar_one_or_none()

        if row is None:
            row = SubledgerPeriodStatusModel(
                id=uuid4(),
                subledger_type=subledger_type.value,
                period_code=period_code,
                status=SubledgerPeriodStatus.OPEN.value,
            )
            self._session.add(row)
            self._session.flush()

        return row
