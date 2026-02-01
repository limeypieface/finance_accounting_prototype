"""
Lease Accounting Module Service (``finance_modules.lease.service``).

Responsibility
--------------
Orchestrates ASC 842 lease accounting operations -- lease commencement,
ROU asset and liability recognition, payment recording, amortization
schedule generation, lease modifications, remeasurement, early termination,
and lease classification -- by delegating pure computation to
``calculations.py`` and journal persistence to
``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``LeaseAccountingService`` is the
sole public entry point for lease operations.  It composes pure calculation
functions (``present_value``, ``build_amortization_schedule``,
``classify_lease_type``, ``remeasure_liability``, ``calculate_rou_adjustment``)
and the kernel ``ModulePostingService``.

Invariants enforced
-------------------
* R7  -- Each public method owns the transaction boundary
          (``commit`` on success, ``rollback`` on failure or exception).
* R14 -- Event type selection is data-driven; no ``if/switch`` on
          event_type inside the posting path.
* L1  -- Account ROLES in profiles; COA resolution deferred to kernel.
* All monetary calculations use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Guard rejection or kernel validation  -> ``ModulePostingResult`` with
  ``is_success == False``; session rolled back.
* Unexpected exception  -> session rolled back, exception re-raised.
* Classification errors (e.g., missing lease term)  -> propagate from
  ``classify_lease_type`` before posting attempt.

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying lease IDs, PV amounts, and classification
outcomes.  All journal entries feed the kernel audit chain (R11).
ASC 842 compliance requires full traceability of classification decisions
and amortization schedules.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
    ModulePostingStatus,
)
from finance_modules.lease.calculations import (
    build_amortization_schedule,
    calculate_rou_adjustment,
    classify_lease_type,
    present_value,
    remeasure_liability,
)
from finance_modules.lease.models import (
    AmortizationScheduleLine,
    Lease,
    LeaseClassification,
    LeaseLiability,
    LeaseModification,
    LeasePayment,
    LeaseStatus,
    ROUAsset,
)
from finance_modules.lease.orm import (
    LeaseLiabilityModel,
    LeaseModel,
    LeaseModificationModel,
    LeasePaymentModel,
    ROUAssetModel,
)

logger = get_logger("modules.lease.service")


class LeaseAccountingService:
    """
    Orchestrates ASC 842 lease accounting through calculations and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``build_amortization_schedule``,
      ``classify_lease_type``, etc.) return pure domain objects with no
      side-effects on the journal.

    Guarantees
    ----------
    * Session is committed only on ``result.is_success``; otherwise rolled back.
    * Engine writes and journal writes share a single transaction
      (``ModulePostingService`` runs with ``auto_commit=False``).
    * Clock is injectable for deterministic testing.
    * All PV and amortization calculations use ``Decimal`` -- NEVER ``float``.

    Non-goals
    ---------
    * Does NOT own account-code resolution (delegated to kernel via ROLES).
    * Does NOT enforce fiscal-period locks directly (kernel ``PeriodService``
      handles R12/R13).
    * Does NOT persist lease domain models -- only journal entries are
      persisted.
    """

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()

        self._poster = ModulePostingService(
            session=session,
            role_resolver=role_resolver,
            clock=self._clock,
            auto_commit=False,
        )

    # =========================================================================
    # Classification
    # =========================================================================

    def classify_lease(
        self,
        lease_term_months: int,
        economic_life_months: int,
        monthly_payment: Decimal,
        discount_rate: Decimal,
        fair_value: Decimal,
        transfer_ownership: bool = False,
        purchase_option: bool = False,
        specialized_asset: bool = False,
    ) -> LeaseClassification:
        """
        Classify a lease per ASC 842-10-25-2.

        Pure calculation — no posting.
        """
        monthly_rate = discount_rate / Decimal("12")
        pv_payments = present_value(monthly_payment, monthly_rate, lease_term_months)

        result = classify_lease_type(
            lease_term_months=lease_term_months,
            economic_life_months=economic_life_months,
            pv_payments=pv_payments,
            fair_value=fair_value,
            transfer_ownership=transfer_ownership,
            purchase_option=purchase_option,
            specialized_asset=specialized_asset,
        )

        classification = LeaseClassification(result)
        logger.info("lease_classified", extra={
            "classification": classification.value,
            "lease_term_months": lease_term_months,
            "pv_payments": str(pv_payments),
            "fair_value": str(fair_value),
        })
        return classification

    # =========================================================================
    # Initial Recognition
    # =========================================================================

    def record_initial_recognition(
        self,
        lease_id: UUID,
        classification: LeaseClassification,
        monthly_payment: Decimal,
        discount_rate: Decimal,
        lease_term_months: int,
        commencement_date: date,
        actor_id: UUID,
        lessee_id: UUID | None = None,
        currency: str = "USD",
    ) -> tuple[ROUAsset, LeaseLiability, ModulePostingResult]:
        """
        Record initial recognition of lease (ROU asset + liability).

        Posts via lease.finance_initial or lease.operating_initial.
        """
        monthly_rate = discount_rate / Decimal("12")
        liability_value = present_value(monthly_payment, monthly_rate, lease_term_months)

        event_type = (
            "lease.finance_initial"
            if classification == LeaseClassification.FINANCE
            else "lease.operating_initial"
        )

        rou = ROUAsset(
            id=uuid4(),
            lease_id=lease_id,
            initial_value=liability_value,
            carrying_value=liability_value,
            commencement_date=commencement_date,
        )

        liability = LeaseLiability(
            id=uuid4(),
            lease_id=lease_id,
            initial_value=liability_value,
            current_balance=liability_value,
            commencement_date=commencement_date,
        )

        logger.info("lease_initial_recognition", extra={
            "lease_id": str(lease_id),
            "classification": classification.value,
            "liability_value": str(liability_value),
        })

        try:
            result = self._poster.post_event(
                event_type=event_type,
                payload={
                    "lease_id": str(lease_id),
                    "classification": classification.value,
                    "liability_value": str(liability_value),
                },
                effective_date=commencement_date,
                actor_id=actor_id,
                amount=liability_value,
                currency=currency,
            )

            if result.is_success:
                end_date = commencement_date + timedelta(days=lease_term_months * 30)
                orm_lease = LeaseModel(
                    id=lease_id,
                    lease_number=str(lease_id)[:20],
                    lessee_id=lessee_id or actor_id,
                    lessor_name="",
                    commencement_date=commencement_date,
                    end_date=end_date,
                    classification=classification.value,
                    status=LeaseStatus.ACTIVE.value,
                    monthly_payment=monthly_payment,
                    discount_rate=discount_rate,
                    currency=currency,
                    created_by_id=actor_id,
                )
                self._session.add(orm_lease)
                orm_rou = ROUAssetModel.from_dto(rou, created_by_id=actor_id)
                self._session.add(orm_rou)
                orm_liability = LeaseLiabilityModel.from_dto(liability, created_by_id=actor_id)
                self._session.add(orm_liability)
                self._session.commit()
            else:
                self._session.rollback()
            return rou, liability, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Amortization Schedule
    # =========================================================================

    def generate_amortization_schedule(
        self,
        principal: Decimal,
        monthly_payment: Decimal,
        discount_rate: Decimal,
        num_periods: int,
        start_date: date,
    ) -> tuple[AmortizationScheduleLine, ...]:
        """
        Generate amortization schedule for a lease.

        Pure calculation — no posting.
        """
        monthly_rate = discount_rate / Decimal("12")
        schedule_data = build_amortization_schedule(
            principal=principal,
            rate_per_period=monthly_rate,
            payment=monthly_payment,
            num_periods=num_periods,
            start_date=start_date,
        )

        schedule = tuple(
            AmortizationScheduleLine(
                period=line["period"],
                payment_date=line["payment_date"],
                payment=line["payment"],
                interest=line["interest"],
                principal=line["principal"],
                balance=line["balance"],
            )
            for line in schedule_data
        )

        logger.info("lease_schedule_generated", extra={
            "periods": num_periods,
            "principal": str(principal),
        })
        return schedule

    # =========================================================================
    # Periodic Payment
    # =========================================================================

    def record_periodic_payment(
        self,
        lease_id: UUID,
        payment_amount: Decimal,
        payment_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Record a periodic lease payment.

        Posts via lease.payment_made (Dr Lease Liability / Cr Cash).
        """
        logger.info("lease_payment_recorded", extra={
            "lease_id": str(lease_id),
            "amount": str(payment_amount),
        })

        try:
            result = self._poster.post_event(
                event_type="lease.payment_made",
                payload={
                    "lease_id": str(lease_id),
                    "payment_date": payment_date.isoformat(),
                },
                effective_date=payment_date,
                actor_id=actor_id,
                amount=payment_amount,
                currency=currency,
            )

            if result.is_success:
                orm_payment = LeasePaymentModel(
                    id=uuid4(),
                    lease_id=lease_id,
                    payment_date=payment_date,
                    amount=payment_amount,
                    principal_portion=payment_amount,
                    interest_portion=Decimal("0"),
                    payment_number=0,
                    created_by_id=actor_id,
                )
                self._session.add(orm_payment)
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Interest Accrual
    # =========================================================================

    def accrue_interest(
        self,
        lease_id: UUID,
        interest_amount: Decimal,
        period_end: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Accrue interest on lease liability.

        Posts via lease.interest_accrued (Dr Lease Interest / Cr Lease Liability).
        """
        logger.info("lease_interest_accrued", extra={
            "lease_id": str(lease_id),
            "interest_amount": str(interest_amount),
        })

        try:
            result = self._poster.post_event(
                event_type="lease.interest_accrued",
                payload={
                    "lease_id": str(lease_id),
                    "period_end": period_end.isoformat(),
                },
                effective_date=period_end,
                actor_id=actor_id,
                amount=interest_amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Amortization
    # =========================================================================

    def record_amortization(
        self,
        lease_id: UUID,
        amortization_amount: Decimal,
        classification: LeaseClassification,
        period_end: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Record ROU asset amortization.

        Posts via lease.amortization_finance or lease.amortization_operating.
        """
        event_type = (
            "lease.amortization_finance"
            if classification == LeaseClassification.FINANCE
            else "lease.amortization_operating"
        )

        logger.info("lease_amortization_recorded", extra={
            "lease_id": str(lease_id),
            "amount": str(amortization_amount),
            "classification": classification.value,
        })

        try:
            result = self._poster.post_event(
                event_type=event_type,
                payload={
                    "lease_id": str(lease_id),
                    "period_end": period_end.isoformat(),
                    "classification": classification.value,
                },
                effective_date=period_end,
                actor_id=actor_id,
                amount=amortization_amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Modification
    # =========================================================================

    def modify_lease(
        self,
        lease_id: UUID,
        modification_date: date,
        remeasurement_amount: Decimal,
        actor_id: UUID,
        description: str = "",
        currency: str = "USD",
    ) -> tuple[LeaseModification, ModulePostingResult]:
        """
        Record a lease modification with liability remeasurement.

        Posts via lease.modified (Dr ROU Asset / Cr Lease Liability).
        """
        modification = LeaseModification(
            id=uuid4(),
            lease_id=lease_id,
            modification_date=modification_date,
            description=description,
            remeasurement_amount=remeasurement_amount,
            actor_id=actor_id,
        )

        posting_amount = abs(remeasurement_amount)

        logger.info("lease_modification", extra={
            "lease_id": str(lease_id),
            "remeasurement_amount": str(remeasurement_amount),
        })

        try:
            result = self._poster.post_event(
                event_type="lease.modified",
                payload={
                    "lease_id": str(lease_id),
                    "description": description,
                    "remeasurement_amount": str(remeasurement_amount),
                },
                effective_date=modification_date,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
            )

            if result.is_success:
                orm_mod = LeaseModificationModel.from_dto(modification, created_by_id=actor_id)
                self._session.add(orm_mod)
                self._session.commit()
            else:
                self._session.rollback()
            return modification, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Early Termination
    # =========================================================================

    def terminate_early(
        self,
        lease_id: UUID,
        termination_date: date,
        remaining_liability: Decimal,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Early lease termination — derecognize ROU and liability.

        Posts via lease.terminated_early.
        """
        logger.info("lease_early_termination", extra={
            "lease_id": str(lease_id),
            "remaining_liability": str(remaining_liability),
        })

        try:
            result = self._poster.post_event(
                event_type="lease.terminated_early",
                payload={
                    "lease_id": str(lease_id),
                    "termination_date": termination_date.isoformat(),
                },
                effective_date=termination_date,
                actor_id=actor_id,
                amount=remaining_liability,
                currency=currency,
            )

            if result.is_success:
                orm_lease = self._session.get(LeaseModel, lease_id)
                if orm_lease is not None:
                    orm_lease.status = LeaseStatus.TERMINATED.value
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Queries
    # =========================================================================

    def get_lease_portfolio(
        self,
        leases: list[Lease],
    ) -> dict:
        """Query lease portfolio summary."""
        finance_count = sum(1 for l in leases if l.classification == LeaseClassification.FINANCE)
        operating_count = sum(1 for l in leases if l.classification == LeaseClassification.OPERATING)
        return {
            "total_leases": len(leases),
            "finance_leases": finance_count,
            "operating_leases": operating_count,
        }

    def get_disclosure_data(
        self,
        leases: list[Lease],
        rou_assets: list[ROUAsset],
        liabilities: list[LeaseLiability],
    ) -> dict:
        """ASC 842 disclosure data."""
        total_rou = sum(a.carrying_value for a in rou_assets)
        total_liability = sum(l.current_balance for l in liabilities)
        return {
            "total_rou_assets": str(total_rou),
            "total_lease_liabilities": str(total_liability),
            "lease_count": len(leases),
        }
