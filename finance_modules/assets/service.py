"""
Fixed Assets Module Service (``finance_modules.assets.service``).

Responsibility
--------------
Orchestrates fixed-asset operations -- acquisitions, depreciation runs,
impairment, revaluation, disposal, and transfers -- by delegating pure
computation to ``finance_engines`` and journal persistence to
``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``FixedAssetService`` is the sole
public entry point for fixed-asset operations.  It composes stateless
engines (``VarianceCalculator``, ``AllocationEngine``) and the kernel
``ModulePostingService``.

Invariants enforced
-------------------
* R7  -- Each public method owns the transaction boundary
          (``commit`` on success, ``rollback`` on failure or exception).
* R14 -- Event type selection is data-driven; no ``if/switch`` on
          event_type inside the posting path.
* L1  -- Account ROLES in profiles; COA resolution deferred to kernel.
* R4  -- Double-entry balance is enforced downstream by ``JournalWriter``.

Failure modes
-------------
* Guard rejection or kernel validation  -> ``ModulePostingResult`` with
  ``is_success == False``; session rolled back.
* Unexpected exception  -> session rolled back, exception re-raised.
* Engine errors (e.g., allocation with no targets)  -> propagate before
  posting attempt.

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying asset IDs, amounts, and depreciation
parameters.  All journal entries feed the kernel audit chain (R11).

Usage::

    service = FixedAssetService(session, role_resolver, clock)
    result = service.record_asset_acquisition(
        asset_id=uuid4(), cost=Decimal("50000.00"),
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Sequence
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
    ModulePostingStatus,
)
from finance_engines.allocation import (
    AllocationEngine,
    AllocationMethod,
    AllocationResult,
    AllocationTarget,
)
from finance_engines.variance import VarianceCalculator, VarianceResult
from finance_modules.assets.models import AssetTransfer, AssetRevaluation
from finance_modules.assets.orm import (
    AssetDisposalModel,
    AssetModel,
    AssetRevaluationModel,
    AssetTransferModel,
    DepreciationComponentModel,
    DepreciationScheduleModel,
)

logger = get_logger("modules.assets.service")


class FixedAssetService:
    """
    Orchestrates fixed-asset operations through engines and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``calculate_depreciation``, ``assess_impairment``,
      etc.) return pure domain objects with no side-effects on the journal.

    Guarantees
    ----------
    * Session is committed only on ``result.is_success``; otherwise rolled back.
    * Engine writes and journal writes share a single transaction
      (``ModulePostingService`` runs with ``auto_commit=False``).
    * Clock is injectable for deterministic testing.

    Non-goals
    ---------
    * Does NOT own account-code resolution (delegated to kernel via ROLES).
    * Does NOT enforce fiscal-period locks directly (kernel ``PeriodService``
      handles R12/R13).

    Engine composition:
    - VarianceCalculator: revaluation variances (book vs fair value)
    - AllocationEngine: impairment allocation across asset components

    Transaction boundary: this service commits on success, rolls back on failure.
    ModulePostingService runs with auto_commit=False so all engine writes
    and journal writes share a single transaction.
    """

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()

        # Kernel posting (auto_commit=False -- we own the boundary)
        self._poster = ModulePostingService(
            session=session,
            role_resolver=role_resolver,
            clock=self._clock,
            auto_commit=False,
        )

        # Stateless engines
        self._variance = VarianceCalculator()
        self._allocation = AllocationEngine()

    # =========================================================================
    # Acquisitions
    # =========================================================================

    def record_asset_acquisition(
        self,
        asset_id: UUID,
        cost: Decimal,
        effective_date: date,
        actor_id: UUID,
        category_id: UUID,
        asset_class: str | None = None,
        useful_life_months: int = 60,
        payment_method: str = "CASH",
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record acquisition of a fixed asset.

        Profile: asset.acquisition (where-clause dispatches by payment_method)
            - CASH -> AssetAcquisitionCash
            - ON_ACCOUNT -> AssetAcquisitionOnAccount
        """
        try:
            logger.info("asset_acquisition_started", extra={
                "asset_id": str(asset_id),
                "cost": str(cost),
                "payment_method": payment_method,
            })

            result = self._poster.post_event(
                event_type="asset.acquisition",
                payload={
                    "cost": str(cost),
                    "asset_class": asset_class,
                    "useful_life_months": useful_life_months,
                    "payment_method": payment_method,
                    "description": description,
                    "asset_id": str(asset_id),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=cost,
                currency=currency,
            )

            if result.is_success:
                orm_asset = AssetModel(
                    id=asset_id,
                    asset_number=str(asset_id),
                    description=description or "",
                    category_id=category_id,
                    acquisition_date=effective_date,
                    in_service_date=effective_date,
                    acquisition_cost=cost,
                    salvage_value=Decimal("0"),
                    useful_life_months=useful_life_months,
                    accumulated_depreciation=Decimal("0"),
                    net_book_value=cost,
                    status="in_service",
                    location_id=None,
                    department_id=None,
                    custodian_id=None,
                    serial_number=None,
                    purchase_order_id=None,
                    vendor_id=None,
                    created_by_id=actor_id,
                )
                self._session.add(orm_asset)
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # CIP Capitalization
    # =========================================================================

    def record_cip_capitalized(
        self,
        asset_id: UUID,
        cost: Decimal,
        effective_date: date,
        actor_id: UUID,
        project_id: str | None = None,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record capitalization of construction-in-progress to a fixed asset.

        Profile: asset.cip_capitalized -> AssetCIPCapitalized
        """
        try:
            logger.info("asset_cip_capitalized_started", extra={
                "asset_id": str(asset_id),
                "cost": str(cost),
                "project_id": project_id,
            })

            result = self._poster.post_event(
                event_type="asset.cip_capitalized",
                payload={
                    "cost": str(cost),
                    "asset_id": str(asset_id),
                    "project_id": project_id,
                    "description": description,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=cost,
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
    # Depreciation
    # =========================================================================

    def record_depreciation(
        self,
        asset_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        period_id: str | None = None,
        depreciation_method: str = "straight_line",
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Record periodic depreciation for a fixed asset.

        Profile: asset.depreciation -> AssetDepreciation
        """
        try:
            logger.info("asset_depreciation_started", extra={
                "asset_id": str(asset_id),
                "amount": str(amount),
                "method": depreciation_method,
            })

            result = self._poster.post_event(
                event_type="asset.depreciation",
                payload={
                    "depreciation_amount": str(amount),
                    "asset_id": str(asset_id),
                    "period_id": period_id,
                    "depreciation_method": depreciation_method,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                orm_schedule = DepreciationScheduleModel(
                    id=uuid4(),
                    asset_id=asset_id,
                    period_date=effective_date,
                    depreciation_amount=amount,
                    accumulated_depreciation=amount,  # incremental; caller tracks cumulative
                    net_book_value=Decimal("0"),  # placeholder; caller tracks actual
                    is_posted=True,
                    created_by_id=actor_id,
                )
                self._session.add(orm_schedule)
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Disposals
    # =========================================================================

    def record_disposal(
        self,
        asset_id: UUID,
        proceeds: Decimal,
        effective_date: date,
        actor_id: UUID,
        original_cost: Decimal | None = None,
        accumulated_depreciation: Decimal | None = None,
        disposal_type: str = "SALE",
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Record disposal of a fixed asset (sale, retirement, or write-off).

        Profile: asset.disposal (where-clause dispatches by disposal_type)
            - SALE -> AssetDisposalGain or AssetDisposalLoss
            - RETIREMENT -> AssetDisposalLoss
            - WRITE_OFF -> AssetImpairment

        Engine: VarianceCalculator used when proceeds differ from book value
                to compute the gain/loss variance.
        """
        try:
            book_value = None

            if original_cost is not None and accumulated_depreciation is not None:
                book_value = original_cost - accumulated_depreciation

            logger.info("asset_disposal_started", extra={
                "asset_id": str(asset_id),
                "proceeds": str(proceeds),
                "disposal_type": disposal_type,
                "book_value": str(book_value) if book_value is not None else None,
            })

            payload: dict = {
                "asset_id": str(asset_id),
                "proceeds": str(proceeds),
                "disposal_type": disposal_type,
            }
            if original_cost is not None:
                payload["original_cost"] = str(original_cost)
            if accumulated_depreciation is not None:
                payload["accumulated_depreciation"] = str(accumulated_depreciation)
            if book_value is not None:
                payload["book_value"] = str(book_value)
                gain_amount = max(Decimal("0"), proceeds - book_value)
                loss_amount = max(Decimal("0"), book_value - proceeds)
                payload["gain_amount"] = str(gain_amount)
                payload["loss_amount"] = str(loss_amount)

            # Amount posted is the greater of proceeds or book value
            posting_amount = max(proceeds, book_value) if book_value is not None else proceeds

            result = self._poster.post_event(
                event_type="asset.disposal",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
            )

            if result.is_success:
                gain_loss = Decimal("0")
                if book_value is not None:
                    gain_loss = proceeds - book_value
                orm_disposal = AssetDisposalModel(
                    id=uuid4(),
                    asset_id=asset_id,
                    disposal_date=effective_date,
                    disposal_type=disposal_type.lower(),
                    proceeds=proceeds,
                    accumulated_depreciation_at_disposal=(
                        accumulated_depreciation or Decimal("0")
                    ),
                    net_book_value_at_disposal=book_value or Decimal("0"),
                    gain_loss=gain_loss,
                    created_by_id=actor_id,
                )
                self._session.add(orm_disposal)
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Impairment (with allocation)
    # =========================================================================

    def record_impairment(
        self,
        asset_id: UUID,
        impairment_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        component_targets: Sequence[AllocationTarget] | None = None,
        allocation_method: AllocationMethod = AllocationMethod.PRORATA,
        currency: str = "USD",
    ) -> tuple[AllocationResult | None, ModulePostingResult]:
        """
        Record impairment of a fixed asset with optional component allocation.

        Engine: AllocationEngine distributes impairment across asset components
                when component_targets are provided.
        Profile: asset.disposal (disposal_type=WRITE_OFF) -> AssetImpairment
        """
        try:
            allocation_result = None

            if component_targets:
                allocation_result = self._allocation.allocate(
                    amount=Money.of(impairment_amount, currency),
                    targets=component_targets,
                    method=allocation_method,
                )

                logger.info("asset_impairment_allocated", extra={
                    "asset_id": str(asset_id),
                    "impairment_amount": str(impairment_amount),
                    "component_count": len(component_targets),
                    "method": allocation_method.value,
                })

            result = self._poster.post_event(
                event_type="asset.disposal",
                payload={
                    "asset_id": str(asset_id),
                    "impairment_amount": str(impairment_amount),
                    "disposal_type": "WRITE_OFF",
                    "proceeds": "0",
                    "has_allocation": allocation_result is not None,
                    "allocation_count": (
                        allocation_result.allocation_count
                        if allocation_result else 0
                    ),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=impairment_amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return allocation_result, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Scrap
    # =========================================================================

    def record_scrap(
        self,
        asset_id: UUID,
        original_cost: Decimal,
        accumulated_depreciation: Decimal,
        effective_date: date,
        actor_id: UUID,
        reason_code: str = "END_OF_LIFE",
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Record scrapping of a fixed asset (no proceeds).

        Profile: asset.scrap -> AssetScrap
        """
        try:
            book_value = original_cost - accumulated_depreciation

            logger.info("asset_scrap_started", extra={
                "asset_id": str(asset_id),
                "book_value": str(book_value),
                "reason_code": reason_code,
            })

            result = self._poster.post_event(
                event_type="asset.scrap",
                payload={
                    "asset_id": str(asset_id),
                    "original_cost": str(original_cost),
                    "accumulated_depreciation": str(accumulated_depreciation),
                    "book_value": str(book_value),
                    "reason_code": reason_code,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=book_value,
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
    # Mass Depreciation
    # =========================================================================

    def run_mass_depreciation(
        self,
        assets: list[dict],
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> list[ModulePostingResult]:
        """
        Run batch depreciation for multiple assets.

        Each asset dict should have 'asset_id' and 'amount' keys.
        Posts individual depreciation entries for each asset.

        Args:
            assets: List of dicts with asset_id and amount.
            effective_date: Accounting effective date.
            actor_id: Actor UUID.
            currency: ISO 4217 currency code.

        Returns:
            List of ModulePostingResult, one per asset.
        """
        results: list[ModulePostingResult] = []
        try:
            for asset_data in assets:
                asset_id = asset_data["asset_id"]
                amount = Decimal(str(asset_data["amount"]))

                result = self._poster.post_event(
                    event_type="asset.mass_depreciation",
                    payload={
                        "asset_id": str(asset_id),
                        "depreciation_amount": str(amount),
                    },
                    effective_date=effective_date,
                    actor_id=actor_id,
                    amount=amount,
                    currency=currency,
                )
                results.append(result)

            if all(r.is_success for r in results):
                self._session.commit()
            else:
                self._session.rollback()
            return results

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Asset Transfer
    # =========================================================================

    def record_asset_transfer(
        self,
        asset_id: UUID,
        from_cost_center: str,
        to_cost_center: str,
        transfer_value: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[AssetTransfer, ModulePostingResult]:
        """
        Record transfer of an asset between cost centers.

        Posts Dr Fixed Asset (new CC) / Cr Fixed Asset (old CC).

        Args:
            asset_id: Asset UUID.
            from_cost_center: Source cost center.
            to_cost_center: Destination cost center.
            transfer_value: Current book value being transferred.
            effective_date: Accounting effective date.
            actor_id: Actor UUID.
            currency: ISO 4217 currency code.

        Returns:
            Tuple of (AssetTransfer, ModulePostingResult).
        """
        try:
            logger.info("asset_transfer_started", extra={
                "asset_id": str(asset_id),
                "from": from_cost_center,
                "to": to_cost_center,
            })

            result = self._poster.post_event(
                event_type="asset.transfer",
                payload={
                    "asset_id": str(asset_id),
                    "from_cost_center": from_cost_center,
                    "to_cost_center": to_cost_center,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=transfer_value,
                currency=currency,
            )

            transfer_record_id = uuid4()
            if result.is_success:
                orm_transfer = AssetTransferModel(
                    id=transfer_record_id,
                    asset_id=asset_id,
                    transfer_date=effective_date,
                    from_cost_center=from_cost_center,
                    to_cost_center=to_cost_center,
                    transferred_by=actor_id,
                    created_by_id=actor_id,
                )
                self._session.add(orm_transfer)
                self._session.commit()
            else:
                self._session.rollback()

            transfer = AssetTransfer(
                id=transfer_record_id,
                asset_id=asset_id,
                transfer_date=effective_date,
                from_cost_center=from_cost_center,
                to_cost_center=to_cost_center,
                transferred_by=actor_id,
            )
            return transfer, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Impairment Testing
    # =========================================================================

    def test_impairment(
        self,
        asset_id: UUID,
        carrying_value: Decimal,
        fair_value: Decimal,
    ) -> Decimal:
        """
        Test an asset for impairment.

        Pure calculation — no posting, no session interaction.

        Args:
            asset_id: Asset UUID.
            carrying_value: Current carrying value (cost - accum depr).
            fair_value: Estimated fair value.

        Returns:
            Impairment loss amount (0 if no impairment).
        """
        from finance_modules.assets.helpers import calculate_impairment_loss
        return calculate_impairment_loss(carrying_value, fair_value)

    # =========================================================================
    # Revaluation
    # =========================================================================

    def record_revaluation(
        self,
        asset_id: UUID,
        old_carrying_value: Decimal,
        new_fair_value: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[AssetRevaluation, ModulePostingResult]:
        """
        Record asset revaluation to fair value (IFRS).

        Posts Dr Fixed Asset / Cr Revaluation Surplus (gain).

        Args:
            asset_id: Asset UUID.
            old_carrying_value: Current carrying value.
            new_fair_value: New fair value.
            effective_date: Accounting effective date.
            actor_id: Actor UUID.
            currency: ISO 4217 currency code.

        Returns:
            Tuple of (AssetRevaluation, ModulePostingResult).
        """
        try:
            surplus = new_fair_value - old_carrying_value

            logger.info("asset_revaluation_started", extra={
                "asset_id": str(asset_id),
                "old_value": str(old_carrying_value),
                "new_value": str(new_fair_value),
                "surplus": str(surplus),
            })

            result = self._poster.post_event(
                event_type="asset.revaluation",
                payload={
                    "asset_id": str(asset_id),
                    "old_carrying_value": str(old_carrying_value),
                    "new_fair_value": str(new_fair_value),
                    "surplus": str(surplus),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=abs(surplus),
                currency=currency,
            )

            reval_record_id = uuid4()
            if result.is_success:
                orm_reval = AssetRevaluationModel(
                    id=reval_record_id,
                    asset_id=asset_id,
                    revaluation_date=effective_date,
                    old_carrying_value=old_carrying_value,
                    new_fair_value=new_fair_value,
                    revaluation_surplus=surplus,
                    created_by_id=actor_id,
                )
                self._session.add(orm_reval)
                self._session.commit()
            else:
                self._session.rollback()

            revaluation = AssetRevaluation(
                id=reval_record_id,
                asset_id=asset_id,
                revaluation_date=effective_date,
                old_carrying_value=old_carrying_value,
                new_fair_value=new_fair_value,
                revaluation_surplus=surplus,
            )
            return revaluation, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Component Depreciation
    # =========================================================================

    def record_component_depreciation(
        self,
        asset_id: UUID,
        component_name: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Record depreciation for a specific asset component.

        Posts Dr Depreciation Expense / Cr Accumulated Depreciation.

        Args:
            asset_id: Asset UUID.
            component_name: Name of the component being depreciated.
            amount: Depreciation amount for this component.
            effective_date: Accounting effective date.
            actor_id: Actor UUID.
            currency: ISO 4217 currency code.

        Returns:
            ModulePostingResult.
        """
        try:
            logger.info("component_depreciation_started", extra={
                "asset_id": str(asset_id),
                "component": component_name,
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="asset.component_depreciation",
                payload={
                    "asset_id": str(asset_id),
                    "component_name": component_name,
                    "depreciation_amount": str(amount),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                orm_component = DepreciationComponentModel(
                    id=uuid4(),
                    asset_id=asset_id,
                    component_name=component_name,
                    cost=amount,
                    useful_life_months=0,  # placeholder; caller tracks actual
                    depreciation_method="straight_line",
                    accumulated_depreciation=amount,
                    created_by_id=actor_id,
                )
                self._session.add(orm_component)
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise
