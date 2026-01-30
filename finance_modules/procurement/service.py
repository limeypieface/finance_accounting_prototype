"""
Procurement Service - Orchestrates procurement operations via engines + kernel.

Thin glue layer that:
1. Calls VarianceCalculator for purchase price variance (PPV)
2. Calls MatchingEngine for PO/receipt/invoice matching
3. Calls LinkGraphService for tracking procurement document links
4. Calls ModulePostingService for journal entry creation

All computation lives in engines. All posting lives in kernel.
This service owns the transaction boundary (R7 compliance).

Usage:
    service = ProcurementService(session, role_resolver, clock)
    result = service.create_purchase_order(
        po_id=uuid4(), vendor_id="V-001",
        lines=[{"item_code": "WIDGET-001", "quantity": "100", "unit_price": "25.00"}],
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.economic_link import (
    ArtifactRef,
    ArtifactType,
    EconomicLink,
    LinkType,
)
from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
    ModulePostingStatus,
)
from finance_engines.variance import VarianceCalculator, VarianceResult
from finance_engines.matching import MatchingEngine

logger = get_logger("modules.procurement.service")


class ProcurementService:
    """
    Orchestrates procurement operations through engines and kernel.

    Engine composition:
    - VarianceCalculator: purchase price variance (PPV) computation
    - MatchingEngine: PO/receipt/invoice matching
    - LinkGraphService: procurement document link tracking

    Transaction boundary: this service commits on success, rolls back on failure.
    ModulePostingService runs with auto_commit=False so all engine writes
    (links, matches) and journal writes share a single transaction.
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

        # Stateful engines (share session for atomicity)
        self._link_graph = LinkGraphService(session)

        # Stateless engines
        self._variance = VarianceCalculator()
        self._matching = MatchingEngine()

    # =========================================================================
    # Purchase Orders
    # =========================================================================

    def create_purchase_order(
        self,
        po_id: UUID,
        vendor_id: str,
        lines: Sequence[dict[str, Any]],
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        cost_center: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Create a purchase order and post encumbrance entry.

        Engine: LinkGraphService to establish PO artifact reference.
        Profile: procurement.po_encumbered -> POEncumbrance
        """
        try:
            # Calculate total PO amount from lines
            total_amount = Decimal("0")
            for line in lines:
                qty = Decimal(str(line.get("quantity", "0")))
                price = Decimal(str(line.get("unit_price", "0")))
                total_amount += qty * price

            logger.info("procurement_po_started", extra={
                "po_id": str(po_id),
                "vendor_id": vendor_id,
                "line_count": len(lines),
                "total_amount": str(total_amount),
            })

            payload: dict[str, Any] = {
                "amount": str(total_amount),
                "vendor_id": vendor_id,
                "po_id": str(po_id),
                "line_count": len(lines),
                "lines": [
                    {
                        "item_code": line.get("item_code", ""),
                        "quantity": str(line.get("quantity", "0")),
                        "unit_price": str(line.get("unit_price", "0")),
                    }
                    for line in lines
                ],
                "cost_center": cost_center,
            }

            result = self._poster.post_event(
                event_type="procurement.po_encumbered",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=total_amount,
                currency=currency,
                description=description or f"PO {po_id} for vendor {vendor_id}",
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
    # Commitments
    # =========================================================================

    def record_commitment(
        self,
        commitment_id: UUID,
        po_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        vendor_id: str | None = None,
        currency: str = "USD",
        cost_center: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a purchase commitment (memo entry).

        Profile: procurement.commitment_recorded -> POCommitment
        """
        try:
            logger.info("procurement_commitment_started", extra={
                "commitment_id": str(commitment_id),
                "po_id": str(po_id),
                "amount": str(amount),
            })

            payload: dict[str, Any] = {
                "amount": str(amount),
                "commitment_id": str(commitment_id),
                "po_id": str(po_id),
                "vendor_id": vendor_id,
                "cost_center": cost_center,
            }

            result = self._poster.post_event(
                event_type="procurement.commitment_recorded",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description or f"Commitment for PO {po_id}",
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    def relieve_commitment(
        self,
        relief_id: UUID,
        commitment_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        cost_center: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Relieve a purchase commitment (memo reversal).

        Profile: procurement.commitment_relieved -> POCommitmentRelief
        """
        try:
            logger.info("procurement_commitment_relief_started", extra={
                "relief_id": str(relief_id),
                "commitment_id": str(commitment_id),
                "amount": str(amount),
            })

            payload: dict[str, Any] = {
                "amount": str(amount),
                "relief_id": str(relief_id),
                "commitment_id": str(commitment_id),
                "cost_center": cost_center,
            }

            result = self._poster.post_event(
                event_type="procurement.commitment_relieved",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description or f"Commitment relief for {commitment_id}",
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
    # Goods Receipt
    # =========================================================================

    def receive_goods(
        self,
        receipt_id: UUID,
        po_id: UUID,
        lines: Sequence[dict[str, Any]],
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        warehouse: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Receive goods against a purchase order and relieve encumbrance.

        Engine: LinkGraphService to create FULFILLED_BY link (PO -> Receipt).
        Profile: procurement.po_relief -> POEncumbranceRelief
        """
        try:
            # Calculate total receipt amount from lines
            total_amount = Decimal("0")
            for line in lines:
                qty = Decimal(str(line.get("quantity", "0")))
                price = Decimal(str(line.get("unit_price", "0")))
                total_amount += qty * price

            logger.info("procurement_receive_started", extra={
                "receipt_id": str(receipt_id),
                "po_id": str(po_id),
                "line_count": len(lines),
                "total_amount": str(total_amount),
            })

            # Engine: create FULFILLED_BY link (PO -> Receipt)
            po_ref = ArtifactRef.purchase_order(po_id)
            receipt_ref = ArtifactRef.receipt(receipt_id)
            link = EconomicLink.create(
                link_id=uuid4(),
                link_type=LinkType.FULFILLED_BY,
                parent_ref=po_ref,
                child_ref=receipt_ref,
                creating_event_id=receipt_id,
                created_at=datetime.utcnow(),
                metadata={
                    "po_id": str(po_id),
                    "receipt_id": str(receipt_id),
                    "total_amount": str(total_amount),
                    "currency": currency,
                },
            )
            self._link_graph.establish_link(link, allow_duplicate=True)

            logger.info("procurement_receive_link_created", extra={
                "po_id": str(po_id),
                "receipt_id": str(receipt_id),
            })

            payload: dict[str, Any] = {
                "amount": str(total_amount),
                "po_id": str(po_id),
                "receipt_id": str(receipt_id),
                "line_count": len(lines),
                "lines": [
                    {
                        "item_code": line.get("item_code", ""),
                        "quantity": str(line.get("quantity", "0")),
                        "unit_price": str(line.get("unit_price", "0")),
                    }
                    for line in lines
                ],
                "warehouse": warehouse,
            }

            result = self._poster.post_event(
                event_type="procurement.po_relief",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=total_amount,
                currency=currency,
                description=description or f"Receipt {receipt_id} against PO {po_id}",
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
    # Price Variance
    # =========================================================================

    def record_price_variance(
        self,
        po_id: UUID,
        invoice_id: UUID,
        variance_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        po_unit_price: Decimal | None = None,
        invoice_unit_price: Decimal | None = None,
        quantity: Decimal | None = None,
        currency: str = "USD",
        cost_center: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record purchase price variance between PO and invoice.

        Engine: VarianceCalculator.price_variance() for PPV computation.
        Engine: LinkGraphService to create FULFILLED_BY link (PO -> Invoice).
        Profile: procurement.po_relief -> POEncumbranceRelief
                 (variance posted as encumbrance relief adjustment)
        """
        try:
            logger.info("procurement_ppv_started", extra={
                "po_id": str(po_id),
                "invoice_id": str(invoice_id),
                "variance_amount": str(variance_amount),
            })

            # Engine: compute variance if unit prices provided
            variance_result: VarianceResult | None = None
            if po_unit_price is not None and invoice_unit_price is not None and quantity is not None:
                variance_result = self._variance.price_variance(
                    expected_price=Money.of(po_unit_price, currency),
                    actual_price=Money.of(invoice_unit_price, currency),
                    quantity=quantity,
                )
                logger.info("procurement_ppv_computed", extra={
                    "computed_variance": str(variance_result.variance.amount),
                    "is_favorable": variance_result.is_favorable,
                })

            # Engine: create FULFILLED_BY link (PO -> Invoice)
            po_ref = ArtifactRef.purchase_order(po_id)
            invoice_ref = ArtifactRef.invoice(invoice_id)
            link = EconomicLink.create(
                link_id=uuid4(),
                link_type=LinkType.FULFILLED_BY,
                parent_ref=po_ref,
                child_ref=invoice_ref,
                creating_event_id=invoice_id,
                created_at=datetime.utcnow(),
                metadata={
                    "po_id": str(po_id),
                    "invoice_id": str(invoice_id),
                    "variance_amount": str(variance_amount),
                    "currency": currency,
                },
            )
            self._link_graph.establish_link(link, allow_duplicate=True)

            payload: dict[str, Any] = {
                "amount": str(abs(variance_amount)),
                "po_id": str(po_id),
                "invoice_id": str(invoice_id),
                "variance_amount": str(variance_amount),
                "is_favorable": variance_result.is_favorable if variance_result else variance_amount < 0,
                "cost_center": cost_center,
            }
            if po_unit_price is not None:
                payload["po_unit_price"] = str(po_unit_price)
            if invoice_unit_price is not None:
                payload["invoice_unit_price"] = str(invoice_unit_price)
            if quantity is not None:
                payload["quantity"] = str(quantity)

            result = self._poster.post_event(
                event_type="procurement.po_relief",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=abs(variance_amount),
                currency=currency,
                description=description or f"PPV for PO {po_id} / Invoice {invoice_id}",
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise
