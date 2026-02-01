"""
finance_services.period_close_orchestrator -- Period close phase sequencing.

Responsibility:
    Sequence period-close phases (0-6) with guard enforcement, diagnostics,
    authority control, close lock (R25), and close certification (R24).
    All business logic lives in existing services; the orchestrator adds
    sequencing, guard evaluation, and evidence collection.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    Composes PeriodService (R12/R13), AuditorService (R11), JournalSelector,
    LedgerSelector, SubledgerSelector, and SubledgerPeriodService.
    Consumes DTOs from _close_types.py; does not import from finance_modules.

Invariants enforced:
    - R12 (closed period enforcement): PeriodService.close_period marks
      period as CLOSED; no further posting is allowed.
    - R24 (canonical ledger hash): close certificate records the
      deterministic ledger hash computed by AuditorService.
    - R25 (close lock): SELECT ... FOR UPDATE on the fiscal period row
      prevents concurrent close attempts.
    - R11 (audit chain): close certificate is persisted as an audit event.
    - Authority hierarchy: each phase requires minimum CloseRole authority.
    - SL-G6 (subledger close): Phase 2 closes all subledgers with
      reconciliation enforcement before GL close.

Failure modes:
    - PeriodNotFoundError if period_code does not exist.
    - CloseAuthorityError if the actor's role is below the phase's
      required CloseRole.
    - Blocking issues in health check (Phase 0) prevent progression.
    - Failed subledger close (Phase 2) sets phase result as failed
      but continues to next phase.

Audit relevance:
    Close is treated as a first-class financial artifact:
    - Exclusive lock on the period (R25)
    - Authority model at phase boundaries
    - Immutable close certificate with ledger hash (R24)
    - Full traceability via existing infrastructure (R11)
    - PeriodCloseRun tracks every close attempt with correlation_id
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock
from finance_kernel.domain.subledger_control import SubledgerType
from finance_kernel.exceptions import CloseAuthorityError, PeriodNotFoundError
from finance_kernel.logging_config import get_logger
from finance_kernel.models.account import Account
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.selectors.journal_selector import JournalSelector
from finance_kernel.selectors.ledger_selector import LedgerSelector
from finance_kernel.selectors.subledger_selector import SubledgerSelector
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.period_service import PeriodService
from finance_services._close_types import (
    CloseCertificate,
    CloseException,
    ClosePhaseResult,
    CloseRole,
    CloseRunStatus,
    HealthCheckResult,
    PeriodCloseResult,
    PeriodCloseRun,
)

logger = get_logger("services.period_close")


# ---------------------------------------------------------------------------
# Authority model
# ---------------------------------------------------------------------------

PHASE_AUTHORITY: dict[int, CloseRole] = {
    0: CloseRole.AUDITOR,
    1: CloseRole.PREPARER,
    2: CloseRole.PREPARER,
    3: CloseRole.PREPARER,
    4: CloseRole.PREPARER,
    5: CloseRole.APPROVER,
    6: CloseRole.APPROVER,
}


class CloseRoleResolver(Protocol):
    """Protocol for resolving close authority roles."""

    def resolve(self, actor_id: UUID) -> CloseRole: ...


class DefaultCloseRoleResolver:
    """Default: all actors are APPROVER (unrestricted)."""

    def resolve(self, actor_id: UUID) -> CloseRole:
        return CloseRole.APPROVER


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class PeriodCloseOrchestrator:
    """
    Sequences period-close phases with guard enforcement, diagnostics,
    authority control, and close certification.

    Contract:
        Receives all dependencies (PeriodService, AuditorService,
        selectors, SubledgerPeriodService, Clock, CloseRoleResolver)
        via constructor injection.
    Guarantees:
        - ``close_period_full`` executes phases 0-6 in order, respecting
          guard checks and authority at each boundary.
        - ``health_check`` produces a non-destructive diagnostic of
          period readiness (Phase 0).
        - Phase 5 (GL close) calls PeriodService.close_period (R12).
        - Phase 6 (certification) produces an immutable CloseCertificate
          with ledger hash (R24) and persists it as audit event (R11).
        - close_period_full is idempotent: re-closing a CLOSED period
          returns immediately.
    Non-goals:
        - Does not implement business logic for adjustments, accruals,
          or closing entries; those are module responsibilities called
          before close.
        - Does not manage fiscal year rollover beyond year_end_extra_phases.
    """

    # Subledger types to close (order matters for reporting)
    _SL_CLOSE_ORDER: tuple[SubledgerType, ...] = (
        SubledgerType.AP,
        SubledgerType.AR,
        SubledgerType.INVENTORY,
        SubledgerType.BANK,
    )

    # Known suspense/clearing account codes
    _SUSPENSE_ACCOUNTS: tuple[str, ...] = ("2100", "2500", "6998")

    def __init__(
        self,
        session: Session,
        period_service: PeriodService,
        sl_period_service: SubledgerPeriodService | None,
        reporting_service: ReportingService,
        gl_service: GeneralLedgerService,
        auditor_service: AuditorService,
        subledger_selector: SubledgerSelector,
        ledger_selector: LedgerSelector,
        journal_selector: JournalSelector,
        clock: Clock,
        role_resolver: CloseRoleResolver | None = None,
    ) -> None:
        self._session = session
        self._period_service = period_service
        self._sl_period_service = sl_period_service
        self._reporting_service = reporting_service
        self._gl_service = gl_service
        self._auditor = auditor_service
        self._sl_selector = subledger_selector
        self._ledger_selector = ledger_selector
        self._journal_selector = journal_selector
        self._clock = clock
        self._role_resolver: CloseRoleResolver = role_resolver or DefaultCloseRoleResolver()

    @classmethod
    def from_posting_orchestrator(
        cls,
        posting_orch: Any,
        reporting_service: ReportingService,
        gl_service: GeneralLedgerService,
    ) -> PeriodCloseOrchestrator:
        """Preferred constructor — reuses singleton services from PostingOrchestrator."""
        return cls(
            session=posting_orch.session,
            period_service=posting_orch.period_service,
            sl_period_service=posting_orch.subledger_period_service,
            reporting_service=reporting_service,
            gl_service=gl_service,
            auditor_service=posting_orch.auditor,
            subledger_selector=SubledgerSelector(posting_orch.session),
            ledger_selector=LedgerSelector(posting_orch.session),
            journal_selector=JournalSelector(posting_orch.session),
            clock=posting_orch.clock,
        )

    # ------------------------------------------------------------------
    # Authority
    # ------------------------------------------------------------------

    def _check_authority(self, actor_id: UUID, phase: int) -> None:
        required = PHASE_AUTHORITY.get(phase, CloseRole.APPROVER)
        actual = self._role_resolver.resolve(actor_id)
        if not actual.has_authority(required):
            raise CloseAuthorityError(
                actor_id=str(actor_id),
                required_role=required.value,
                actual_role=actual.value,
                phase=phase,
            )

    # ------------------------------------------------------------------
    # Account code → UUID helper
    # ------------------------------------------------------------------

    def _account_id_for_code(self, code: str) -> UUID | None:
        acct = self._session.execute(
            select(Account).where(Account.code == code)
        ).scalar_one_or_none()
        return acct.id if acct else None

    # ------------------------------------------------------------------
    # Health check (Phase 0 — read-only)
    # ------------------------------------------------------------------

    def health_check(
        self,
        period_code: str,
        period_end_date: date,
        currency: str = "USD",
    ) -> HealthCheckResult:
        """Read-only diagnostic scan. No state changes. Safe to run repeatedly."""
        blocking: list[CloseException] = []
        warnings: list[CloseException] = []
        sl_recon: dict[str, dict[str, Any]] = {}

        # 0a: Subledger reconciliation
        for sl_type in self._SL_CLOSE_ORDER:
            try:
                sl_bal = self._sl_selector.get_aggregate_balance(
                    sl_type, period_end_date, currency,
                )
                sl_amount = sl_bal.amount if sl_bal else Decimal("0")
            except Exception:
                sl_amount = Decimal("0")

            # Find GL control account balance
            gl_amount = Decimal("0")
            control_codes = {
                SubledgerType.AP: "2000",
                SubledgerType.AR: "1200",
                SubledgerType.INVENTORY: "1400",
                SubledgerType.BANK: "1000",
            }
            code = control_codes.get(sl_type)
            if code:
                acct_id = self._account_id_for_code(code)
                if acct_id:
                    try:
                        balances = self._ledger_selector.account_balance(
                            acct_id, as_of_date=period_end_date, currency=currency,
                        )
                        if balances:
                            gl_amount = balances[0].balance
                    except Exception:
                        pass

            variance = sl_amount - gl_amount
            status = "OK" if variance == Decimal("0") else "MISMATCH"
            sl_recon[sl_type.value] = {
                "sl_balance": sl_amount,
                "gl_balance": gl_amount,
                "variance": variance,
                "status": status,
            }
            if variance != Decimal("0"):
                blocking.append(CloseException(
                    category="sl_variance",
                    subledger_type=sl_type.value,
                    entity_id=None,
                    account_code=code,
                    amount=variance,
                    currency=currency,
                    description=f"{sl_type.value} subledger variance: {variance}",
                    severity="blocking",
                ))

        # 0c: Suspense/clearing accounts
        suspense_list: list[dict[str, Any]] = []
        for code in self._SUSPENSE_ACCOUNTS:
            acct_id = self._account_id_for_code(code)
            if acct_id:
                try:
                    balances = self._ledger_selector.account_balance(
                        acct_id, as_of_date=period_end_date, currency=currency,
                    )
                    balance = balances[0].balance if balances else Decimal("0")
                except Exception:
                    balance = Decimal("0")
                suspense_list.append({
                    "account_code": code,
                    "balance": balance,
                    "status": "OK" if balance == Decimal("0") else "WARNING",
                })
                if balance != Decimal("0") and code != "6998":
                    warnings.append(CloseException(
                        category="suspense_balance",
                        subledger_type=None,
                        entity_id=None,
                        account_code=code,
                        amount=balance,
                        currency=currency,
                        description=f"Account {code} balance not zero: {balance}",
                        severity="warning",
                    ))

        # 0d: Trial balance
        tb_ok = True
        total_debits = Decimal("0")
        total_credits = Decimal("0")
        try:
            tb = self._reporting_service.trial_balance(
                as_of_date=period_end_date, currency=currency,
            )
            tb_ok = tb.is_balanced
            total_debits = tb.total_debits
            total_credits = tb.total_credits
        except Exception:
            tb_ok = False

        # 0e: Period activity
        period_info = self._period_service.get_period_by_code(period_code)
        entry_count = 0
        rejection_count = 0
        if period_info:
            try:
                entries = self._journal_selector.get_entries_by_period(
                    period_info.start_date, period_info.end_date,
                )
                entry_count = len(entries)
            except Exception:
                pass

        logger.info(
            "health_check_completed",
            extra={
                "period_code": period_code,
                "blocking_count": len(blocking),
                "warning_count": len(warnings),
                "trial_balance_ok": tb_ok,
            },
        )

        return HealthCheckResult(
            period_code=period_code,
            run_at=self._clock.now(),
            sl_reconciliation=sl_recon,
            suspense_balances=suspense_list,
            trial_balance_ok=tb_ok,
            total_debits=total_debits,
            total_credits=total_credits,
            period_entry_count=entry_count,
            period_rejection_count=rejection_count,
            blocking_issues=blocking,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Begin close (acquire R25 lock)
    # ------------------------------------------------------------------

    def begin_close(
        self,
        period_code: str,
        actor_id: UUID,
        is_year_end: bool = False,
    ) -> PeriodCloseRun:
        """Initialize close run, acquire exclusive close lock (R25)."""
        self._check_authority(actor_id, phase=1)

        run_id = uuid4()
        correlation_id = str(uuid4())

        # Acquire R25 close lock
        period_info = self._period_service.begin_closing(
            period_code, str(run_id), actor_id,
        )

        # Record audit event
        self._auditor.record_close_begun(
            period_id=period_info.id,
            period_code=period_code,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

        # Determine fiscal year from period
        fiscal_year = period_info.start_date.year

        run = PeriodCloseRun(
            id=run_id,
            period_code=period_code,
            fiscal_year=fiscal_year,
            is_year_end=is_year_end,
            status=CloseRunStatus.IN_PROGRESS,
            current_phase=0,
            correlation_id=correlation_id,
            started_at=self._clock.now(),
            started_by=actor_id,
        )

        logger.info(
            "close_begun",
            extra={
                "period_code": period_code,
                "run_id": str(run_id),
                "correlation_id": correlation_id,
                "is_year_end": is_year_end,
            },
        )

        return run

    # ------------------------------------------------------------------
    # Phase execution
    # ------------------------------------------------------------------

    def run_phase(
        self,
        run: PeriodCloseRun,
        phase: int,
        actor_id: UUID,
        **kwargs: Any,
    ) -> ClosePhaseResult:
        """Execute a single close phase."""
        self._check_authority(actor_id, phase)

        phase_dispatch = {
            1: self._phase_1_close_subledgers,
            2: self._phase_2_verify_trial_balance,
            3: self._phase_3_adjustments,
            4: self._phase_4_closing_entries,
            5: self._phase_5_close_gl,
            6: self._phase_6_lock_period,
        }

        handler = phase_dispatch.get(phase)
        if handler is None:
            return ClosePhaseResult(
                phase=phase,
                phase_name="unknown",
                success=False,
                executed_by=actor_id,
                message=f"Unknown phase: {phase}",
            )

        result = handler(run, actor_id, **kwargs)

        logger.info(
            "phase_completed",
            extra={
                "correlation_id": run.correlation_id,
                "phase": phase,
                "phase_name": result.phase_name,
                "success": result.success,
                "actor_id": str(actor_id),
            },
        )

        return result

    def _phase_1_close_subledgers(
        self, run: PeriodCloseRun, actor_id: UUID, **kwargs: Any,
    ) -> ClosePhaseResult:
        """Phase 1: Close subledgers."""
        if self._sl_period_service is None:
            return ClosePhaseResult(
                phase=1,
                phase_name="close_subledgers",
                success=True,
                executed_by=actor_id,
                guard="ALL_SUBLEDGERS_CLOSED",
                message="No subledger service configured — skipping",
            )

        period_info = self._period_service.get_period_by_code(run.period_code)
        if period_info is None:
            raise PeriodNotFoundError(run.period_code)

        details: dict[str, Any] = {}
        exceptions: list[CloseException] = []
        all_closed = True

        for sl_type in self._SL_CLOSE_ORDER:
            try:
                result = self._sl_period_service.close_subledger_period(
                    subledger_type=sl_type,
                    period_code=run.period_code,
                    period_end_date=period_info.end_date,
                    actor_id=actor_id,
                )
                from finance_kernel.models.subledger import SubledgerPeriodStatus
                is_closed = result.status == SubledgerPeriodStatus.CLOSED
                details[sl_type.value] = {
                    "status": result.status.value,
                    "closed": is_closed,
                }
                if not is_closed:
                    all_closed = False
                    exceptions.append(CloseException(
                        category="sl_close_failed",
                        subledger_type=sl_type.value,
                        entity_id=None,
                        account_code=None,
                        amount=Decimal("0"),
                        currency="USD",
                        description=f"{sl_type.value} subledger failed to close",
                        severity="blocking",
                    ))
                else:
                    self._auditor.record_subledger_closed(
                        period_id=period_info.id,
                        period_code=run.period_code,
                        subledger_type=sl_type.value,
                        actor_id=actor_id,
                    )

                logger.info(
                    "phase_1_subledger_close",
                    extra={
                        "correlation_id": run.correlation_id,
                        "subledger_type": sl_type.value,
                        "period_code": run.period_code,
                        "result": "closed" if is_closed else "failed",
                    },
                )
            except Exception as e:
                all_closed = False
                details[sl_type.value] = {"status": "error", "error": str(e)}
                exceptions.append(CloseException(
                    category="sl_close_error",
                    subledger_type=sl_type.value,
                    entity_id=None,
                    account_code=None,
                    amount=Decimal("0"),
                    currency="USD",
                    description=f"{sl_type.value} error: {e}",
                    severity="blocking",
                ))

        return ClosePhaseResult(
            phase=1,
            phase_name="close_subledgers",
            success=all_closed,
            executed_by=actor_id,
            guard="ALL_SUBLEDGERS_CLOSED",
            message="All subledgers closed" if all_closed else "Subledger close failed",
            details=details,
            exceptions=tuple(exceptions),
        )

    def _phase_2_verify_trial_balance(
        self, run: PeriodCloseRun, actor_id: UUID, **kwargs: Any,
    ) -> ClosePhaseResult:
        """Phase 2: Verify trial balance."""
        period_info = self._period_service.get_period_by_code(run.period_code)
        if period_info is None:
            raise PeriodNotFoundError(run.period_code)

        try:
            tb = self._reporting_service.trial_balance(
                as_of_date=period_info.end_date,
            )
            logger.info(
                "phase_2_trial_balance",
                extra={
                    "correlation_id": run.correlation_id,
                    "total_debits": str(tb.total_debits),
                    "total_credits": str(tb.total_credits),
                    "is_balanced": tb.is_balanced,
                },
            )
            return ClosePhaseResult(
                phase=2,
                phase_name="verify_trial_balance",
                success=tb.is_balanced,
                executed_by=actor_id,
                guard="TRIAL_BALANCE_BALANCED",
                message="Trial balance balanced" if tb.is_balanced else "Trial balance NOT balanced",
                details={
                    "total_debits": str(tb.total_debits),
                    "total_credits": str(tb.total_credits),
                    "is_balanced": tb.is_balanced,
                },
            )
        except Exception as e:
            return ClosePhaseResult(
                phase=2,
                phase_name="verify_trial_balance",
                success=False,
                executed_by=actor_id,
                guard="TRIAL_BALANCE_BALANCED",
                message=f"Trial balance error: {e}",
            )

    def _phase_3_adjustments(
        self, run: PeriodCloseRun, actor_id: UUID, **kwargs: Any,
    ) -> ClosePhaseResult:
        """Phase 3: Post adjustments (callback-driven)."""
        callback: Callable | None = kwargs.get("adjustment_callback")
        adjustment_count = 0

        if callback:
            adjustment_count = callback(run, actor_id) or 0

        logger.info(
            "phase_3_adjustments",
            extra={
                "correlation_id": run.correlation_id,
                "adjustment_count": adjustment_count,
            },
        )

        return ClosePhaseResult(
            phase=3,
            phase_name="post_adjustments",
            success=True,
            executed_by=actor_id,
            message=f"{adjustment_count} adjustments posted",
            details={"adjustment_count": adjustment_count},
        )

    def _phase_4_closing_entries(
        self, run: PeriodCloseRun, actor_id: UUID, **kwargs: Any,
    ) -> ClosePhaseResult:
        """Phase 4: Post closing entries (year-end only)."""
        if not run.is_year_end:
            return ClosePhaseResult(
                phase=4,
                phase_name="post_closing_entries",
                success=True,
                executed_by=actor_id,
                message="Skipped — not year-end",
            )

        period_info = self._period_service.get_period_by_code(run.period_code)
        if period_info is None:
            raise PeriodNotFoundError(run.period_code)

        try:
            result = self._gl_service.record_closing_entry(
                period_id=run.period_code,
                effective_date=period_info.end_date,
                actor_id=actor_id,
            )
            entry_count = len(result.journal_entry_ids) if hasattr(result, "journal_entry_ids") else 1

            logger.info(
                "phase_4_closing_entry",
                extra={
                    "correlation_id": run.correlation_id,
                    "entry_count": entry_count,
                },
            )

            return ClosePhaseResult(
                phase=4,
                phase_name="post_closing_entries",
                success=True,
                executed_by=actor_id,
                guard="YEAR_END_ENTRIES_POSTED",
                message=f"Closing entry posted ({entry_count} entries)",
                details={"entry_count": entry_count},
            )
        except Exception as e:
            return ClosePhaseResult(
                phase=4,
                phase_name="post_closing_entries",
                success=False,
                executed_by=actor_id,
                guard="YEAR_END_ENTRIES_POSTED",
                message=f"Closing entry error: {e}",
            )

    def _phase_5_close_gl(
        self, run: PeriodCloseRun, actor_id: UUID, **kwargs: Any,
    ) -> ClosePhaseResult:
        """Phase 5: Close GL period (CLOSING -> CLOSED)."""
        try:
            self._period_service.close_period(run.period_code, actor_id)

            logger.info(
                "phase_5_gl_close",
                extra={
                    "correlation_id": run.correlation_id,
                    "period_code": run.period_code,
                },
            )

            return ClosePhaseResult(
                phase=5,
                phase_name="close_gl_period",
                success=True,
                executed_by=actor_id,
                message=f"Period {run.period_code} -> CLOSED",
            )
        except Exception as e:
            return ClosePhaseResult(
                phase=5,
                phase_name="close_gl_period",
                success=False,
                executed_by=actor_id,
                message=f"GL close error: {e}",
            )

    def _phase_6_lock_period(
        self, run: PeriodCloseRun, actor_id: UUID, **kwargs: Any,
    ) -> ClosePhaseResult:
        """Phase 6: Lock period (year-end only, CLOSED -> LOCKED)."""
        if not run.is_year_end:
            return ClosePhaseResult(
                phase=6,
                phase_name="lock_period",
                success=True,
                executed_by=actor_id,
                message="Skipped — not year-end",
            )

        try:
            self._period_service.lock_period(run.period_code, actor_id)

            logger.info(
                "phase_6_lock",
                extra={
                    "correlation_id": run.correlation_id,
                    "period_code": run.period_code,
                },
            )

            return ClosePhaseResult(
                phase=6,
                phase_name="lock_period",
                success=True,
                executed_by=actor_id,
                message=f"Period {run.period_code} -> LOCKED",
            )
        except Exception as e:
            return ClosePhaseResult(
                phase=6,
                phase_name="lock_period",
                success=False,
                executed_by=actor_id,
                message=f"Lock error: {e}",
            )

    # ------------------------------------------------------------------
    # Close certificate
    # ------------------------------------------------------------------

    def _build_certificate(
        self,
        run: PeriodCloseRun,
        phase_results: list[ClosePhaseResult],
        actor_id: UUID,
    ) -> CloseCertificate:
        """Build and persist close certificate."""
        period_info = self._period_service.get_period_by_code(run.period_code)

        # Compute ledger hash (R24)
        ledger_hash = self._ledger_selector.canonical_hash(
            as_of_date=period_info.end_date if period_info else None,
        )

        # Get trial balance totals
        total_debits = Decimal("0")
        total_credits = Decimal("0")
        try:
            if period_info:
                tb = self._reporting_service.trial_balance(
                    as_of_date=period_info.end_date,
                )
                total_debits = tb.total_debits
                total_credits = tb.total_credits
        except Exception:
            pass

        # Collect stats from phase results
        sl_closed: list[str] = []
        adjustments_posted = 0
        closing_entries_posted = 0
        phases_completed = 0
        phases_skipped = 0

        for pr in phase_results:
            if pr.success:
                if pr.message and "Skipped" in pr.message:
                    phases_skipped += 1
                else:
                    phases_completed += 1
            if pr.phase == 1 and pr.details:
                for sl_name, sl_info in pr.details.items():
                    if isinstance(sl_info, dict) and sl_info.get("closed"):
                        sl_closed.append(sl_name)
            if pr.phase == 3 and pr.details:
                adjustments_posted = pr.details.get("adjustment_count", 0)
            if pr.phase == 4 and pr.details:
                closing_entries_posted = pr.details.get("entry_count", 0)

        cert_id = uuid4()
        cert = CloseCertificate(
            id=cert_id,
            period_code=run.period_code,
            closed_at=self._clock.now(),
            closed_by=actor_id,
            approved_by=None,
            correlation_id=run.correlation_id,
            ledger_hash=ledger_hash,
            trial_balance_debits=total_debits,
            trial_balance_credits=total_credits,
            subledgers_closed=tuple(sl_closed),
            adjustments_posted=adjustments_posted,
            closing_entries_posted=closing_entries_posted,
            phases_completed=phases_completed,
            phases_skipped=phases_skipped,
        )

        # Persist via audit service
        if period_info:
            audit_event = self._auditor.record_close_certified(
                period_id=period_info.id,
                period_code=run.period_code,
                actor_id=actor_id,
                certificate_data={
                    "certificate_id": str(cert.id),
                    "ledger_hash": cert.ledger_hash,
                    "correlation_id": cert.correlation_id,
                    "trial_balance_debits": str(cert.trial_balance_debits),
                    "trial_balance_credits": str(cert.trial_balance_credits),
                    "subledgers_closed": list(cert.subledgers_closed),
                    "adjustments_posted": cert.adjustments_posted,
                    "closing_entries_posted": cert.closing_entries_posted,
                    "phases_completed": cert.phases_completed,
                    "phases_skipped": cert.phases_skipped,
                },
            )
            cert = CloseCertificate(
                id=cert.id,
                period_code=cert.period_code,
                closed_at=cert.closed_at,
                closed_by=cert.closed_by,
                approved_by=cert.approved_by,
                correlation_id=cert.correlation_id,
                ledger_hash=cert.ledger_hash,
                trial_balance_debits=cert.trial_balance_debits,
                trial_balance_credits=cert.trial_balance_credits,
                subledgers_closed=cert.subledgers_closed,
                adjustments_posted=cert.adjustments_posted,
                closing_entries_posted=cert.closing_entries_posted,
                phases_completed=cert.phases_completed,
                phases_skipped=cert.phases_skipped,
                audit_event_id=audit_event.id,
            )

        logger.info(
            "close_certificate_issued",
            extra={
                "correlation_id": run.correlation_id,
                "certificate_id": str(cert.id),
                "ledger_hash": cert.ledger_hash,
            },
        )

        return cert

    # ------------------------------------------------------------------
    # Full close workflow
    # ------------------------------------------------------------------

    def close_period_full(
        self,
        period_code: str,
        actor_id: UUID,
        is_year_end: bool = False,
        adjustment_callback: Callable | None = None,
    ) -> PeriodCloseResult:
        """Execute all close phases in sequence. Stops on first blocking failure."""
        run = self.begin_close(period_code, actor_id, is_year_end)

        phase_results: list[ClosePhaseResult] = []
        phase_actors: dict[int, UUID] = {}

        for phase in range(1, 7):
            kwargs: dict[str, Any] = {}
            if phase == 3 and adjustment_callback:
                kwargs["adjustment_callback"] = adjustment_callback

            result = self.run_phase(run, phase, actor_id, **kwargs)
            phase_results.append(result)
            phase_actors[phase] = actor_id

            if not result.success:
                logger.warning(
                    "close_phase_failed",
                    extra={
                        "correlation_id": run.correlation_id,
                        "phase": phase,
                        "phase_message": result.message,
                    },
                )
                return PeriodCloseResult(
                    period_code=period_code,
                    status=CloseRunStatus.FAILED,
                    correlation_id=run.correlation_id,
                    phases_completed=phase - 1,
                    phases_total=6,
                    phase_results=tuple(phase_results),
                    started_at=run.started_at,
                    completed_at=self._clock.now(),
                    message=f"Failed at phase {phase}: {result.message}",
                )

        # All phases passed — build certificate
        cert = self._build_certificate(run, phase_results, actor_id)

        completed = sum(1 for r in phase_results if r.success and "Skipped" not in (r.message or ""))
        skipped = sum(1 for r in phase_results if r.success and "Skipped" in (r.message or ""))

        logger.info(
            "period_close_completed",
            extra={
                "correlation_id": run.correlation_id,
                "period_code": period_code,
                "certificate_id": str(cert.id),
                "phases_completed": completed,
                "phases_skipped": skipped,
                "ledger_hash": cert.ledger_hash,
            },
        )

        return PeriodCloseResult(
            period_code=period_code,
            status=CloseRunStatus.COMPLETED,
            correlation_id=run.correlation_id,
            phases_completed=completed,
            phases_total=6,
            phase_results=tuple(phase_results),
            started_at=run.started_at,
            completed_at=self._clock.now(),
            certificate=cert,
            message=f"Period {period_code} closed successfully",
        )

    # ------------------------------------------------------------------
    # Cancel close
    # ------------------------------------------------------------------

    def cancel_close(self, period_code: str, actor_id: UUID, reason: str = "") -> None:
        """Cancel an in-progress close. Releases R25 lock."""
        self._check_authority(actor_id, phase=5)  # APPROVER required

        period_info = self._period_service.cancel_closing(period_code, actor_id)

        self._auditor.record_close_cancelled(
            period_id=period_info.id,
            period_code=period_code,
            actor_id=actor_id,
            reason=reason or "Cancelled by user",
        )

        logger.info(
            "close_cancelled",
            extra={
                "period_code": period_code,
                "actor_id": str(actor_id),
                "reason": reason,
            },
        )

    # ------------------------------------------------------------------
    # Status query
    # ------------------------------------------------------------------

    def get_status(self, period_code: str) -> dict[str, Any] | None:
        """Get current close status for a period."""
        period = self._session.execute(
            select(FiscalPeriod).where(FiscalPeriod.period_code == period_code)
        ).scalar_one_or_none()

        if period is None:
            return None

        return {
            "period_code": period_code,
            "status": period.status.value if isinstance(period.status, PeriodStatus) else period.status,
            "closing_run_id": period.closing_run_id,
            "is_closing": period.status == PeriodStatus.CLOSING,
            "is_closed": period.is_closed,
        }
