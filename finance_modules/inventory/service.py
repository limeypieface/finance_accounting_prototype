"""
Inventory Module Service - Orchestrates inventory operations via engines + kernel.

Thin glue layer that:
1. Calls ValuationLayer for cost lot management (create/consume)
2. Calls VarianceCalculator for price/cost variances
3. Calls ModulePostingService for journal entry creation

All computation lives in engines. All posting lives in kernel.
This service owns the transaction boundary (R7 compliance).

Usage:
    service = InventoryService(session, role_resolver, clock)
    result = service.receive_inventory(
        receipt_id=uuid4(), item_id="WIDGET-001",
        quantity=Decimal("100"), unit_cost=Decimal("25.00"),
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Sequence
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType
from finance_kernel.domain.values import Money, Quantity
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
    ModulePostingStatus,
)
from finance_services.valuation_service import ValuationLayer
from finance_engines.valuation import ConsumptionResult, CostMethod
from finance_engines.variance import VarianceCalculator, VarianceResult
from finance_modules.inventory.helpers import (
    classify_abc as _classify_abc,
    calculate_reorder_point as _calculate_reorder_point,
    calculate_eoq as _calculate_eoq,
)
from finance_modules.inventory.models import (
    ABCClassification,
    CycleCount,
    ItemValue,
    ReorderPoint,
)

logger = get_logger("modules.inventory.service")


class InventoryService:
    """
    Orchestrates inventory operations through engines and kernel.

    Engine composition:
    - ValuationLayer: cost lot creation and FIFO/LIFO consumption
    - VarianceCalculator: price and standard cost variances

    Transaction boundary: this service commits on success, rolls back on failure.
    ModulePostingService runs with auto_commit=False so all engine writes
    (cost lots, links) and journal writes share a single transaction.
    """

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()

        # Kernel posting (auto_commit=False — we own the boundary)
        self._poster = ModulePostingService(
            session=session,
            role_resolver=role_resolver,
            clock=self._clock,
            auto_commit=False,
        )

        # Stateful engines (share session for atomicity)
        self._link_graph = LinkGraphService(session)
        self._valuation = ValuationLayer(session, self._link_graph)

        # Stateless engines
        self._variance = VarianceCalculator()

    # =========================================================================
    # Receipts
    # =========================================================================

    def receive_inventory(
        self,
        receipt_id: UUID,
        item_id: str,
        quantity: Decimal,
        unit_cost: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        po_number: str | None = None,
        warehouse: str | None = None,
    ) -> ModulePostingResult:
        """
        Receive inventory: create cost lot + post journal entry.

        Engine: ValuationLayer.create_lot() for FIFO/LIFO tracking.
        Profile: inventory.receipt -> InventoryReceipt
        """
        total_cost = quantity * unit_cost
        try:
            # Engine: create cost lot
            lot = self._valuation.create_lot(
                lot_id=uuid4(),
                source_ref=ArtifactRef.receipt(receipt_id),
                item_id=item_id,
                quantity=Quantity.of(quantity, "EA"),
                total_cost=Money.of(total_cost, currency),
                lot_date=effective_date,
                creating_event_id=receipt_id,
                location_id=warehouse,
            )

            logger.info("inventory_receive_lot_created", extra={
                "lot_id": str(lot.lot_id),
                "item_id": item_id,
                "unit_cost": str(lot.unit_cost.amount),
            })

            # Kernel: post journal entry
            result = self._poster.post_event(
                event_type="inventory.receipt",
                payload={
                    "quantity": int(quantity) if quantity == int(quantity) else str(quantity),
                    "unit_cost": str(unit_cost),
                    "item_code": item_id,
                    "po_number": po_number,
                    "warehouse": warehouse,
                    "lot_id": str(lot.lot_id),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(total_cost)),
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

    def receive_with_variance(
        self,
        receipt_id: UUID,
        item_id: str,
        quantity: Decimal,
        actual_unit_cost: Decimal,
        standard_unit_cost: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        po_number: str | None = None,
        warehouse: str | None = None,
    ) -> tuple[VarianceResult, ModulePostingResult]:
        """
        Receive inventory with price variance tracking.

        Engine: VarianceCalculator.price_variance() for PPV computation.
        Engine: ValuationLayer.create_lot() at standard cost.
        Profile: inventory.receipt_with_variance -> InventoryReceiptWithVariance
        """
        try:
            # Engine: compute purchase price variance
            variance_result = self._variance.price_variance(
                expected_price=Money.of(standard_unit_cost, currency),
                actual_price=Money.of(actual_unit_cost, currency),
                quantity=quantity,
            )

            # Engine: create lot at standard cost (variance posted separately)
            standard_total = standard_unit_cost * quantity
            lot = self._valuation.create_lot(
                lot_id=uuid4(),
                source_ref=ArtifactRef.receipt(receipt_id),
                item_id=item_id,
                quantity=Quantity.of(quantity, "EA"),
                total_cost=Money.of(standard_total, currency),
                lot_date=effective_date,
                creating_event_id=receipt_id,
                location_id=warehouse,
            )

            logger.info("inventory_receive_with_variance", extra={
                "lot_id": str(lot.lot_id),
                "item_id": item_id,
                "variance_amount": str(variance_result.variance.amount),
                "is_favorable": variance_result.is_favorable,
            })

            # Kernel: post as inventory.receipt with has_variance flag
            # (where-clause dispatch selects InventoryReceiptWithVariance profile)
            actual_total = actual_unit_cost * quantity
            result = self._poster.post_event(
                event_type="inventory.receipt",
                payload={
                    "quantity": int(quantity) if quantity == int(quantity) else str(quantity),
                    "unit_cost": str(actual_unit_cost),
                    "standard_unit_cost": str(standard_unit_cost),
                    "standard_total": str(standard_total),
                    "item_code": item_id,
                    "po_number": po_number,
                    "warehouse": warehouse,
                    "lot_id": str(lot.lot_id),
                    "variance_amount": str(variance_result.variance.amount),
                    "is_favorable": variance_result.is_favorable,
                    "has_variance": True,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(actual_total)),
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return variance_result, result

        except Exception:
            self._session.rollback()
            raise

    def receive_from_production(
        self,
        receipt_id: UUID,
        item_id: str,
        quantity: Decimal,
        unit_cost: Decimal,
        work_order_id: UUID,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        warehouse: str | None = None,
    ) -> ModulePostingResult:
        """
        Receive finished goods from production.

        Engine: ValuationLayer.create_lot() to track FG cost lot.
        Profile: inventory.receipt_from_production -> InventoryReceiptFromProduction
        """
        total_cost = quantity * unit_cost
        try:
            lot = self._valuation.create_lot(
                lot_id=uuid4(),
                source_ref=ArtifactRef.receipt(receipt_id),
                item_id=item_id,
                quantity=Quantity.of(quantity, "EA"),
                total_cost=Money.of(total_cost, currency),
                lot_date=effective_date,
                creating_event_id=receipt_id,
                location_id=warehouse,
            )

            result = self._poster.post_event(
                event_type="inventory.receipt_from_production",
                payload={
                    "quantity": int(quantity) if quantity == int(quantity) else str(quantity),
                    "unit_cost": str(unit_cost),
                    "item_code": item_id,
                    "work_order_id": str(work_order_id),
                    "warehouse": warehouse,
                    "lot_id": str(lot.lot_id),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(total_cost)),
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

    def receive_transfer(
        self,
        transfer_id: UUID,
        item_id: str,
        quantity: Decimal,
        unit_cost: Decimal,
        to_location: str,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Receive inventory from an inter-location transfer.

        No engine call — the transfer-out side consumed lots.
        Profile: inventory.transfer_in -> InventoryTransferIn
        """
        total_cost = quantity * unit_cost
        try:
            result = self._poster.post_event(
                event_type="inventory.transfer_in",
                payload={
                    "quantity": int(quantity) if quantity == int(quantity) else str(quantity),
                    "unit_cost": str(unit_cost),
                    "item_code": item_id,
                    "warehouse": to_location,
                    "transfer_id": str(transfer_id),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(total_cost)),
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
    # Issues
    # =========================================================================

    def issue_sale(
        self,
        issue_id: UUID,
        item_id: str,
        quantity: Decimal,
        effective_date: date,
        actor_id: UUID,
        costing_method: str = "fifo",
        currency: str = "USD",
        location_id: str | None = None,
    ) -> tuple[ConsumptionResult, ModulePostingResult]:
        """
        Issue inventory for a sale (COGS recognition).

        Engine: ValuationLayer.consume_fifo/lifo() determines cost.
        Profile: inventory.issue (issue_type=SALE) -> InventoryIssueSale
        """
        return self._issue_inventory(
            issue_id=issue_id,
            item_id=item_id,
            quantity=quantity,
            effective_date=effective_date,
            actor_id=actor_id,
            issue_type="SALE",
            costing_method=costing_method,
            currency=currency,
            location_id=location_id,
        )

    def issue_production(
        self,
        issue_id: UUID,
        item_id: str,
        quantity: Decimal,
        work_order_id: UUID,
        effective_date: date,
        actor_id: UUID,
        costing_method: str = "fifo",
        currency: str = "USD",
        location_id: str | None = None,
    ) -> tuple[ConsumptionResult, ModulePostingResult]:
        """
        Issue inventory for production (WIP material consumption).

        Engine: ValuationLayer.consume_fifo/lifo() determines cost.
        Profile: inventory.issue (issue_type=PRODUCTION) -> InventoryIssueProduction
        """
        return self._issue_inventory(
            issue_id=issue_id,
            item_id=item_id,
            quantity=quantity,
            effective_date=effective_date,
            actor_id=actor_id,
            issue_type="PRODUCTION",
            costing_method=costing_method,
            currency=currency,
            location_id=location_id,
            work_order_id=work_order_id,
        )

    def issue_scrap(
        self,
        issue_id: UUID,
        item_id: str,
        quantity: Decimal,
        reason_code: str,
        effective_date: date,
        actor_id: UUID,
        costing_method: str = "fifo",
        currency: str = "USD",
        location_id: str | None = None,
    ) -> tuple[ConsumptionResult, ModulePostingResult]:
        """
        Issue inventory as scrap/write-off.

        Engine: ValuationLayer.consume_fifo/lifo() determines cost.
        Profile: inventory.issue (issue_type=SCRAP) -> InventoryIssueScrap
        """
        return self._issue_inventory(
            issue_id=issue_id,
            item_id=item_id,
            quantity=quantity,
            effective_date=effective_date,
            actor_id=actor_id,
            issue_type="SCRAP",
            costing_method=costing_method,
            currency=currency,
            location_id=location_id,
            reason_code=reason_code,
        )

    def issue_transfer(
        self,
        issue_id: UUID,
        item_id: str,
        quantity: Decimal,
        from_location: str,
        to_location: str,
        effective_date: date,
        actor_id: UUID,
        costing_method: str = "fifo",
        currency: str = "USD",
    ) -> tuple[ConsumptionResult, ModulePostingResult]:
        """
        Issue inventory for inter-location transfer.

        Engine: ValuationLayer.consume_fifo/lifo() determines cost from source.
        Profile: inventory.issue (issue_type=TRANSFER) -> InventoryIssueTransfer
        """
        return self._issue_inventory(
            issue_id=issue_id,
            item_id=item_id,
            quantity=quantity,
            effective_date=effective_date,
            actor_id=actor_id,
            issue_type="TRANSFER",
            costing_method=costing_method,
            currency=currency,
            location_id=from_location,
            to_location=to_location,
        )

    # =========================================================================
    # Adjustments
    # =========================================================================

    def adjust_inventory(
        self,
        adjustment_id: UUID,
        item_id: str,
        quantity_change: Decimal,
        value_change: Decimal,
        reason_code: str,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        warehouse: str | None = None,
    ) -> ModulePostingResult:
        """
        Adjust inventory quantity/value (cycle count, physical count, etc.).

        No engine call — adjustments are direct postings.
        Profile: inventory.adjustment -> InventoryAdjustmentPositive or Negative
        (where-clause dispatch based on quantity_change sign)
        """
        try:
            result = self._poster.post_event(
                event_type="inventory.adjustment",
                payload={
                    "quantity_change": str(quantity_change),
                    "value_change": str(value_change),
                    "reason_code": reason_code,
                    "item_code": item_id,
                    "warehouse": warehouse,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=abs(value_change),
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

    def revalue_inventory(
        self,
        item_id: str,
        old_value: Decimal,
        new_value: Decimal,
        quantity: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[VarianceResult, ModulePostingResult]:
        """
        Revalue inventory (standard cost change, LCM adjustment, etc.).

        Engine: VarianceCalculator.standard_cost_variance() for revaluation difference.
        Profile: inventory.revaluation -> InventoryRevaluation
        """
        try:
            # Engine: compute revaluation variance
            variance_result = self._variance.standard_cost_variance(
                standard_cost=Money.of(old_value, currency),
                actual_cost=Money.of(new_value, currency),
                quantity=quantity,
            )

            logger.info("inventory_revalue_variance", extra={
                "item_id": item_id,
                "old_value": str(old_value),
                "new_value": str(new_value),
                "variance": str(variance_result.variance.amount),
            })

            revaluation_amount = abs(new_value - old_value)
            result = self._poster.post_event(
                event_type="inventory.revaluation",
                payload={
                    "item_code": item_id,
                    "old_value": str(old_value),
                    "new_value": str(new_value),
                    "quantity": str(quantity),
                    "variance_amount": str(variance_result.variance.amount),
                    "is_favorable": variance_result.is_favorable,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=revaluation_amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return variance_result, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Cycle Count
    # =========================================================================

    def record_cycle_count(
        self,
        count_id: UUID,
        item_id: str,
        expected_quantity: Decimal,
        actual_quantity: Decimal,
        unit_cost: Decimal,
        effective_date: date,
        actor_id: UUID,
        location_id: str | None = None,
        currency: str = "USD",
        notes: str = "",
    ) -> ModulePostingResult:
        """
        Record a cycle count and post the adjustment for any variance.

        Engine: VarianceCalculator for count variance computation.
        Profile: inventory.cycle_count -> InventoryCycleCountPositive or Negative
        (where-clause dispatch based on variance sign)
        """
        try:
            variance_qty = actual_quantity - expected_quantity
            variance_amount = variance_qty * unit_cost

            if variance_qty == 0:
                logger.info("inventory_cycle_count_no_variance", extra={
                    "item_id": item_id, "quantity": str(actual_quantity),
                })
                # No posting needed when count matches
                return ModulePostingResult(
                    status=ModulePostingStatus.POSTED,
                    event_id=uuid4(),
                    journal_entry_ids=(),
                    profile_name="InventoryCycleCountZero",
                )

            # Engine: compute variance
            variance_result = self._variance.price_variance(
                expected_price=Money.of(expected_quantity * unit_cost, currency),
                actual_price=Money.of(actual_quantity * unit_cost, currency),
                quantity=Decimal("1"),
            )

            logger.info("inventory_cycle_count_variance", extra={
                "item_id": item_id,
                "expected": str(expected_quantity),
                "actual": str(actual_quantity),
                "variance_qty": str(variance_qty),
                "variance_amount": str(variance_amount),
            })

            result = self._poster.post_event(
                event_type="inventory.cycle_count",
                payload={
                    "item_id": item_id,
                    "expected_quantity": str(expected_quantity),
                    "actual_quantity": str(actual_quantity),
                    "variance_quantity": str(variance_qty),
                    "amount": str(abs(variance_amount)),
                    "unit_cost": str(unit_cost),
                    "location_id": location_id,
                    "notes": notes,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=abs(variance_amount),
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
    # ABC Classification (no posting)
    # =========================================================================

    def classify_abc(
        self,
        items: Sequence[ItemValue],
        a_pct: Decimal = Decimal("80"),
        b_pct: Decimal = Decimal("15"),
    ) -> dict[str, str]:
        """
        Classify items into A/B/C categories by cumulative annual value.

        No posting — delegates to helpers.py pure function.
        """
        result = _classify_abc(items, a_pct, b_pct)
        logger.info("inventory_abc_classified", extra={
            "item_count": len(items),
            "a_count": sum(1 for v in result.values() if v == "A"),
            "b_count": sum(1 for v in result.values() if v == "B"),
            "c_count": sum(1 for v in result.values() if v == "C"),
        })
        return result

    # =========================================================================
    # Reorder Point (no posting)
    # =========================================================================

    def calculate_reorder_point(
        self,
        item_id: str,
        avg_daily_usage: Decimal,
        lead_time_days: int,
        safety_stock: Decimal,
        annual_demand: Decimal | None = None,
        order_cost: Decimal | None = None,
        holding_cost: Decimal | None = None,
        location_id: str | None = None,
    ) -> ReorderPoint:
        """
        Calculate reorder point and optionally EOQ.

        No posting — delegates to helpers.py pure functions.
        """
        rop = _calculate_reorder_point(avg_daily_usage, lead_time_days, safety_stock)

        eoq = Decimal("0")
        if annual_demand and order_cost and holding_cost:
            eoq = _calculate_eoq(annual_demand, order_cost, holding_cost)

        return ReorderPoint(
            item_id=item_id,
            location_id=location_id,
            reorder_point=rop,
            safety_stock=safety_stock,
            eoq=eoq,
            avg_daily_usage=avg_daily_usage,
            lead_time_days=lead_time_days,
        )

    # =========================================================================
    # Inter-Warehouse Transfer
    # =========================================================================

    def record_inter_warehouse_transfer(
        self,
        transfer_id: UUID,
        item_id: str,
        from_location: str,
        to_location: str,
        quantity: Decimal,
        unit_cost: Decimal,
        effective_date: date,
        actor_id: UUID,
        costing_method: str = "fifo",
        currency: str = "USD",
    ) -> tuple[ConsumptionResult, ModulePostingResult]:
        """
        Transfer inventory between warehouses (consumes from source).

        Engine: ValuationLayer.consume_fifo/lifo() for source lot consumption.
        Profile: inventory.warehouse_transfer -> InventoryWarehouseTransferOut/In
        Posts the outbound side; caller should call receive_transfer for inbound.
        """
        return self._issue_inventory(
            issue_id=transfer_id,
            item_id=item_id,
            quantity=quantity,
            effective_date=effective_date,
            actor_id=actor_id,
            issue_type="TRANSFER",
            costing_method=costing_method,
            currency=currency,
            location_id=from_location,
            to_location=to_location,
        )

    # =========================================================================
    # Shelf-Life Write-Off
    # =========================================================================

    def record_shelf_life_write_off(
        self,
        write_off_id: UUID,
        item_id: str,
        quantity: Decimal,
        effective_date: date,
        actor_id: UUID,
        location_id: str | None = None,
        reason: str = "EXPIRED",
        costing_method: str = "fifo",
        currency: str = "USD",
    ) -> tuple[ConsumptionResult, ModulePostingResult]:
        """
        Write off expired inventory.

        Engine: ValuationLayer.consume_fifo/lifo() determines write-off cost.
        Profile: inventory.issue (issue_type=SCRAP) -> InventoryIssueScrap
        """
        return self._issue_inventory(
            issue_id=write_off_id,
            item_id=item_id,
            quantity=quantity,
            effective_date=effective_date,
            actor_id=actor_id,
            issue_type="SCRAP",
            costing_method=costing_method,
            currency=currency,
            location_id=location_id,
            reason_code=reason,
        )

    # =========================================================================
    # Internal
    # =========================================================================

    def _issue_inventory(
        self,
        issue_id: UUID,
        item_id: str,
        quantity: Decimal,
        effective_date: date,
        actor_id: UUID,
        issue_type: str,
        costing_method: str,
        currency: str,
        location_id: str | None = None,
        work_order_id: UUID | None = None,
        reason_code: str | None = None,
        to_location: str | None = None,
    ) -> tuple[ConsumptionResult, ModulePostingResult]:
        """
        Internal method for all inventory issue operations.

        Engine: ValuationLayer.consume_fifo/lifo() determines the cost of goods issued.
        """
        try:
            # Engine: consume cost lots
            consuming_ref = ArtifactRef(ArtifactType.SHIPMENT, issue_id)
            qty = Quantity.of(quantity, "EA")

            if costing_method == "lifo":
                consumption = self._valuation.consume_lifo(
                    consuming_ref=consuming_ref,
                    item_id=item_id,
                    quantity=qty,
                    creating_event_id=issue_id,
                    location_id=location_id,
                )
            else:
                consumption = self._valuation.consume_fifo(
                    consuming_ref=consuming_ref,
                    item_id=item_id,
                    quantity=qty,
                    creating_event_id=issue_id,
                    location_id=location_id,
                )

            logger.info("inventory_issue_consumed", extra={
                "issue_id": str(issue_id),
                "item_id": item_id,
                "issue_type": issue_type,
                "layers_consumed": consumption.layer_count,
                "total_cost": str(consumption.total_cost.amount),
                "cost_method": consumption.cost_method.value,
            })

            # Build payload with engine-computed cost
            payload: dict = {
                "quantity": int(quantity) if quantity == int(quantity) else str(quantity),
                "item_code": item_id,
                "issue_type": issue_type,
                "cost": str(consumption.total_cost.amount),
                "unit_cost": str(consumption.average_unit_cost.amount),
                "layers_consumed": consumption.layer_count,
                "cost_method": consumption.cost_method.value,
            }
            if work_order_id:
                payload["work_order_id"] = str(work_order_id)
            if reason_code:
                payload["reason_code"] = reason_code
            if location_id:
                payload["from_location"] = location_id
            if to_location:
                payload["to_location"] = to_location

            # Kernel: post with engine-computed amount
            result = self._poster.post_event(
                event_type="inventory.issue",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=consumption.total_cost.amount,
                currency=consumption.total_cost.currency.code,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return consumption, result

        except Exception:
            self._session.rollback()
            raise
