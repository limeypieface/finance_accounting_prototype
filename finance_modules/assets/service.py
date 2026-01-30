"""
Fixed Assets Module Service - Orchestrates asset operations via engines + kernel.

Thin glue layer that:
1. Calls VarianceCalculator for revaluation variances
2. Calls AllocationEngine for impairment allocation
3. Calls ModulePostingService for journal entry creation

All computation lives in engines. All posting lives in kernel.
This service owns the transaction boundary (R7 compliance).

Usage:
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
from uuid import UUID

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

logger = get_logger("modules.assets.service")


class FixedAssetService:
    """
    Orchestrates fixed-asset operations through engines and kernel.

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
