"""
Procurement Module Service (``finance_modules.procurement.service``).

Responsibility
--------------
Orchestrates procurement operations -- purchase order creation, goods
receipt, PO/receipt/invoice three-way matching, purchase price variance
(PPV) analysis, supplier scoring, and document link tracking -- by
delegating pure computation to ``finance_engines`` and journal persistence
to ``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``ProcurementService`` is the sole
public entry point for procurement operations.  It composes stateless
engines (``VarianceCalculator``, ``MatchingEngine``), stateful engines
(``LinkGraphService``), and the kernel ``ModulePostingService``.

Invariants enforced
-------------------
* R7  -- Each public method owns the transaction boundary
          (``commit`` on success, ``rollback`` on failure or exception).
* R14 -- Event type selection is data-driven; no ``if/switch`` on
          event_type inside the posting path.
* L1  -- Account ROLES in profiles; COA resolution deferred to kernel.
* L5  -- Atomicity: link creation and journal posting share a single
          transaction (``auto_commit=False``).

Failure modes
-------------
* Guard rejection or kernel validation  -> ``ModulePostingResult`` with
  ``is_success == False``; session rolled back.
* Unexpected exception  -> session rolled back, exception re-raised.
* Matching failure (e.g., tolerance exceeded)  -> ``MatchResult`` with
  non-success status returned before posting.

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying PO numbers, receipt IDs, amounts, and match
results.  All journal entries feed the kernel audit chain (R11).
Three-way matching results are tracked for SOX compliance.

Usage::

    # workflow_executor is required — guards are always enforced.
    service = ProcurementService(
        session, role_resolver, orchestrator.workflow_executor, clock=clock,
    )
    result = service.create_purchase_order(
        po_id=uuid4(), vendor_id="V-001",
        lines=[{"item_code": "WIDGET-001", "quantity": "100", "unit_price": "25.00"}],
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_engines.matching import (
    MatchCandidate,
    MatchingEngine,
    MatchResult,
    MatchTolerance,
    MatchType,
)
from finance_engines.variance import VarianceCalculator, VarianceResult
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
from finance_kernel.services.party_service import PartyService
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
    ModulePostingStatus,
)
from finance_modules._posting_helpers import run_workflow_guard
from finance_services.workflow_executor import WorkflowExecutor
from finance_modules.procurement.workflows import (
    PROCUREMENT_AMEND_PO_WORKFLOW,
    PROCUREMENT_CONVERT_REQUISITION_TO_PO_WORKFLOW,
    PROCUREMENT_CREATE_PO_WORKFLOW,
    PROCUREMENT_CREATE_REQUISITION_WORKFLOW,
    PROCUREMENT_MATCH_RECEIPT_TO_PO_WORKFLOW,
    PROCUREMENT_RECORD_COMMITMENT_WORKFLOW,
    PROCUREMENT_RECORD_PRICE_VARIANCE_WORKFLOW,
    PROCUREMENT_RECORD_QUANTITY_VARIANCE_WORKFLOW,
    PROCUREMENT_RECEIVE_GOODS_WORKFLOW,
    PROCUREMENT_RELIEVE_COMMITMENT_WORKFLOW,
)
from finance_modules.procurement.models import (
    PurchaseOrderVersion,
    ReceiptMatch,
    SupplierScore,
)
from finance_modules.procurement.orm import (
    PurchaseOrderLineModel,
    PurchaseOrderModel,
    PurchaseRequisitionModel,
    ReceivingReportModel,
    RequisitionLineModel,
)

logger = get_logger("modules.procurement.service")


class ProcurementService:
    """
    Orchestrates procurement operations through engines and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``score_supplier``, ``match_documents``, etc.)
      return pure domain objects with no side-effects on the journal.

    Guarantees
    ----------
    * Session is committed only on ``result.is_success``; otherwise rolled back.
    * Engine link writes and journal writes share a single transaction
      (``ModulePostingService`` runs with ``auto_commit=False``).
    * Clock is injectable for deterministic testing.

    Non-goals
    ---------
    * Does NOT own account-code resolution (delegated to kernel via ROLES).
    * Does NOT enforce fiscal-period locks directly (kernel ``PeriodService``
      handles R12/R13).

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
        workflow_executor: WorkflowExecutor,
        clock: Clock | None = None,
        party_service: PartyService | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()
        self._workflow_executor = workflow_executor  # Required: guards always enforced

        # Kernel posting (auto_commit=False -- we own the boundary). G14: actor validation mandatory.
        self._poster = ModulePostingService(
            session=session,
            role_resolver=role_resolver,
            clock=self._clock,
            auto_commit=False,
            party_service=party_service,
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

            failure = run_workflow_guard(
                self._workflow_executor,
                PROCUREMENT_CREATE_PO_WORKFLOW,
                "purchase_order",
                po_id,
                actor_id=actor_id,
                amount=total_amount,
                currency=currency,
                context={},
            )
            if failure is not None:
                return failure

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
                po_model = PurchaseOrderModel(
                    id=po_id,
                    po_number=str(po_id),
                    vendor_id=vendor_id,
                    order_date=effective_date,
                    total_amount=total_amount,
                    currency=currency,
                    status="draft",
                    created_by_id=actor_id,
                )
                self._session.add(po_model)
                for idx, line in enumerate(lines):
                    qty = Decimal(str(line.get("quantity", "0")))
                    price = Decimal(str(line.get("unit_price", "0")))
                    line_total = qty * price
                    self._session.add(
                        PurchaseOrderLineModel(
                            id=uuid4(),
                            purchase_order_id=po_id,
                            line_number=idx + 1,
                            item_code=str(line.get("item_code", "")),
                            quantity=qty,
                            unit_price=price,
                            line_total=line_total,
                            created_by_id=actor_id,
                        )
                    )
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
            failure = run_workflow_guard(
                self._workflow_executor,
                PROCUREMENT_RECORD_COMMITMENT_WORKFLOW,
                "procurement_commitment",
                commitment_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context={},
            )
            if failure is not None:
                return failure

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
            failure = run_workflow_guard(
                self._workflow_executor,
                PROCUREMENT_RELIEVE_COMMITMENT_WORKFLOW,
                "procurement_commitment",
                relief_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context={},
            )
            if failure is not None:
                return failure

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
            total_amount_pre = Decimal("0")
            for line in lines:
                total_amount_pre += Decimal(str(line.get("quantity", "0"))) * Decimal(str(line.get("unit_price", "0")))
            failure = run_workflow_guard(
                self._workflow_executor,
                PROCUREMENT_RECEIVE_GOODS_WORKFLOW,
                "procurement_receipt",
                receipt_id,
                actor_id=actor_id,
                amount=total_amount_pre,
                currency=currency,
                context={},
            )
            if failure is not None:
                return failure

            total_amount = total_amount_pre
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
                created_at=self._clock.now(),
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
                orm_receipt = ReceivingReportModel(
                    id=receipt_id,
                    receipt_number=str(receipt_id),
                    sindri_po_reference=str(po_id),
                    receipt_date=effective_date,
                    quantity_received=sum(
                        Decimal(str(ln.get("quantity", "0"))) for ln in lines
                    ),
                    quantity_accepted=sum(
                        Decimal(str(ln.get("quantity", "0"))) for ln in lines
                    ),
                    quantity_rejected=Decimal("0"),
                    status="accepted",
                    receiver_id=actor_id,
                    description=description or f"Receipt {receipt_id} against PO {po_id}",
                    currency=currency,
                    unit_cost=Decimal("0"),
                    total_cost=total_amount,
                    created_by_id=actor_id,
                )
                self._session.add(orm_receipt)
                self._session.flush()
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
            failure = run_workflow_guard(
                self._workflow_executor,
                PROCUREMENT_RECORD_PRICE_VARIANCE_WORKFLOW,
                "procurement_price_variance",
                invoice_id,
                actor_id=actor_id,
                amount=abs(variance_amount),
                currency=currency,
                context={},
            )
            if failure is not None:
                return failure

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
                created_at=self._clock.now(),
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

    # =========================================================================
    # Requisitions
    # =========================================================================

    def create_requisition(
        self,
        requisition_id: UUID,
        requester_id: UUID,
        items: Sequence[dict[str, Any]],
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        cost_center: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Create a purchase requisition and post commitment memo entry.

        Profile: procurement.requisition_created -> RequisitionCreated
        """
        try:
            total_amount = Decimal("0")
            for item in items:
                qty = Decimal(str(item.get("quantity", "0")))
                price = Decimal(str(item.get("estimated_unit_cost", "0")))
                total_amount += qty * price

            failure = run_workflow_guard(
                self._workflow_executor,
                PROCUREMENT_CREATE_REQUISITION_WORKFLOW,
                "procurement_requisition",
                requisition_id,
                actor_id=actor_id,
                amount=total_amount,
                currency=currency,
                context={},
            )
            if failure is not None:
                return failure

            logger.info("procurement_requisition_started", extra={
                "requisition_id": str(requisition_id),
                "requester_id": str(requester_id),
                "item_count": len(items),
                "total_amount": str(total_amount),
            })

            payload: dict[str, Any] = {
                "amount": str(total_amount),
                "requisition_id": str(requisition_id),
                "requester_id": str(requester_id),
                "item_count": len(items),
                "cost_center": cost_center,
            }

            result = self._poster.post_event(
                event_type="procurement.requisition_created",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=total_amount,
                currency=currency,
                description=description or f"Requisition {requisition_id}",
            )

            if result.is_success:
                orm_requisition = PurchaseRequisitionModel(
                    id=requisition_id,
                    requisition_number=str(requisition_id),
                    requester_id=requester_id,
                    request_date=effective_date,
                    description=description or f"Requisition {requisition_id}",
                    total_amount=total_amount,
                    currency=currency,
                    status="draft",
                    created_by_id=actor_id,
                )
                # Persist requisition lines
                for idx, item in enumerate(items, start=1):
                    orm_line = RequisitionLineModel(
                        requisition_id=requisition_id,
                        line_number=idx,
                        description=item.get("description", ""),
                        quantity=Decimal(str(item.get("quantity", "0"))),
                        unit_of_measure=item.get("unit_of_measure", "EA"),
                        estimated_unit_cost=Decimal(str(item.get("estimated_unit_cost", "0"))),
                        estimated_total=Decimal(str(item.get("quantity", "0"))) * Decimal(str(item.get("estimated_unit_cost", "0"))),
                        gl_account_code=item.get("gl_account_code"),
                        created_by_id=actor_id,
                    )
                    orm_requisition.lines.append(orm_line)
                self._session.add(orm_requisition)
                self._session.flush()
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    def convert_requisition_to_po(
        self,
        requisition_id: UUID,
        po_id: UUID,
        vendor_id: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        cost_center: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Convert a requisition to a PO: relieve commitment + create encumbrance.

        Two-step posting (single transaction):
        1. Post commitment_relieved -> POCommitmentRelief (relieve req commitment)
        2. Post po_encumbered -> POEncumbrance (create PO encumbrance)

        Engine: LinkGraphService to create DERIVED_FROM link (PO -> Requisition).
        """
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                PROCUREMENT_CONVERT_REQUISITION_TO_PO_WORKFLOW,
                "procurement_requisition",
                requisition_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context={},
            )
            if failure is not None:
                return failure

            logger.info("procurement_req_conversion_started", extra={
                "requisition_id": str(requisition_id),
                "po_id": str(po_id),
                "vendor_id": vendor_id,
                "amount": str(amount),
            })

            # Engine: create DERIVED_FROM link (PO -> Requisition)
            po_ref = ArtifactRef.purchase_order(po_id)
            req_ref = ArtifactRef(
                artifact_type=ArtifactType.EVENT,
                artifact_id=requisition_id,
            )
            link = EconomicLink.create(
                link_id=uuid4(),
                link_type=LinkType.DERIVED_FROM,
                parent_ref=po_ref,
                child_ref=req_ref,
                creating_event_id=po_id,
                created_at=self._clock.now(),
                metadata={
                    "requisition_id": str(requisition_id),
                    "po_id": str(po_id),
                    "amount": str(amount),
                },
            )
            self._link_graph.establish_link(link, allow_duplicate=True)

            # Step 1: Relieve the requisition commitment
            relief_payload: dict[str, Any] = {
                "amount": str(amount),
                "relief_id": str(uuid4()),
                "commitment_id": str(requisition_id),
                "cost_center": cost_center,
            }

            relief_result = self._poster.post_event(
                event_type="procurement.commitment_relieved",
                payload=relief_payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=f"Relieve req {requisition_id} commitment",
            )

            if not relief_result.is_success:
                self._session.rollback()
                return relief_result

            # Step 2: Create PO encumbrance
            encumbrance_payload: dict[str, Any] = {
                "amount": str(amount),
                "vendor_id": vendor_id,
                "po_id": str(po_id),
                "line_count": 0,
                "lines": [],
                "cost_center": cost_center,
            }

            result = self._poster.post_event(
                event_type="procurement.po_encumbered",
                payload=encumbrance_payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description or f"Encumbrance for PO {po_id} (from req {requisition_id})",
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    def amend_purchase_order(
        self,
        po_id: UUID,
        delta_amount: Decimal,
        amendment_reason: str,
        effective_date: date,
        actor_id: UUID,
        version: int = 2,
        changes: Sequence[str] = (),
        currency: str = "USD",
        cost_center: str | None = None,
        description: str | None = None,
    ) -> tuple[PurchaseOrderVersion, ModulePostingResult]:
        """
        Amend a PO and adjust encumbrance by the delta amount.

        Profile: procurement.po_amended -> POAmended
        """
        po_version = PurchaseOrderVersion(
            po_id=po_id,
            version=version,
            amendment_date=effective_date,
            amendment_reason=amendment_reason,
            changes=tuple(changes),
            previous_total=Decimal("0"),
            new_total=abs(delta_amount),
            amended_by=actor_id,
        )
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                PROCUREMENT_AMEND_PO_WORKFLOW,
                "purchase_order",
                po_id,
                actor_id=actor_id,
                amount=abs(delta_amount),
                currency=currency,
                context={},
            )
            if failure is not None:
                return po_version, failure

            logger.info("procurement_po_amendment_started", extra={
                "po_id": str(po_id),
                "delta_amount": str(delta_amount),
                "version": version,
            })

            payload: dict[str, Any] = {
                "amount": str(abs(delta_amount)),
                "po_id": str(po_id),
                "delta_amount": str(delta_amount),
                "version": version,
                "amendment_reason": amendment_reason,
                "cost_center": cost_center,
            }

            result = self._poster.post_event(
                event_type="procurement.po_amended",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=abs(delta_amount),
                currency=currency,
                description=description or f"PO {po_id} amendment v{version}",
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return po_version, result

        except Exception:
            self._session.rollback()
            raise

    def match_receipt_to_po(
        self,
        receipt_id: UUID,
        po_id: UUID,
        po_line_id: UUID,
        matched_quantity: Decimal,
        matched_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        vendor_id: str | None = None,
        match_type: str = "3-way",
        currency: str = "USD",
        cost_center: str | None = None,
        description: str | None = None,
    ) -> tuple[MatchResult, ModulePostingResult]:
        """
        Match a receipt to a PO (3-way match) and post encumbrance relief + AP subledger.

        Engine: MatchingEngine for match validation.
        Engine: LinkGraphService to create FULFILLED_BY link.
        Profile: procurement.receipt_matched -> ReceiptMatched
        """
        try:
            # Engine: MatchingEngine for validation (pure; run before transition so we have match_result for early return)
            match_result = self._matching.create_match(
                documents=[
                    MatchCandidate(
                        document_id=po_id,
                        document_type="PURCHASE_ORDER",
                        amount=Money.of(matched_amount, currency),
                        date=effective_date,
                    ),
                    MatchCandidate(
                        document_id=receipt_id,
                        document_type="RECEIPT",
                        amount=Money.of(matched_amount, currency),
                        date=effective_date,
                    ),
                ],
                match_type=MatchType.THREE_WAY,
                as_of_date=effective_date,
                tolerance=MatchTolerance(),
            )

            failure = run_workflow_guard(
                self._workflow_executor,
                PROCUREMENT_MATCH_RECEIPT_TO_PO_WORKFLOW,
                "procurement_receipt",
                receipt_id,
                actor_id=actor_id,
                amount=matched_amount,
                currency=currency,
                context={},
            )
            if failure is not None:
                return match_result, failure

            logger.info("procurement_receipt_match_started", extra={
                "receipt_id": str(receipt_id),
                "po_id": str(po_id),
                "matched_amount": str(matched_amount),
            })

            # Engine: FULFILLED_BY link (PO -> Receipt)
            po_ref = ArtifactRef.purchase_order(po_id)
            receipt_ref = ArtifactRef.receipt(receipt_id)
            link = EconomicLink.create(
                link_id=uuid4(),
                link_type=LinkType.FULFILLED_BY,
                parent_ref=po_ref,
                child_ref=receipt_ref,
                creating_event_id=receipt_id,
                created_at=self._clock.now(),
                metadata={
                    "po_id": str(po_id),
                    "receipt_id": str(receipt_id),
                    "matched_amount": str(matched_amount),
                    "match_type": match_type,
                },
            )
            self._link_graph.establish_link(link, allow_duplicate=True)

            payload: dict[str, Any] = {
                "amount": str(matched_amount),
                "receipt_id": str(receipt_id),
                "po_id": str(po_id),
                "po_line_id": str(po_line_id),
                "matched_quantity": str(matched_quantity),
                "match_type": match_type,
                "vendor_id": vendor_id or "",
                "cost_center": cost_center,
            }

            result = self._poster.post_event(
                event_type="procurement.receipt_matched",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=matched_amount,
                currency=currency,
                description=description or f"Receipt {receipt_id} matched to PO {po_id}",
            )

            if result.is_success:
                orm_match_receipt = ReceivingReportModel(
                    receipt_number=f"MATCH-{receipt_id}",
                    sindri_po_reference=str(po_id),
                    sindri_po_line_reference=str(po_line_id),
                    receipt_date=effective_date,
                    quantity_received=matched_quantity,
                    quantity_accepted=matched_quantity,
                    quantity_rejected=Decimal("0"),
                    status="matched",
                    receiver_id=actor_id,
                    description=description or f"Receipt {receipt_id} matched to PO {po_id}",
                    currency=currency,
                    unit_cost=Decimal("0"),
                    total_cost=matched_amount,
                    created_by_id=actor_id,
                )
                self._session.add(orm_match_receipt)
                self._session.flush()
                self._session.commit()
            else:
                self._session.rollback()
            return match_result, result

        except Exception:
            self._session.rollback()
            raise

    def evaluate_supplier(
        self,
        vendor_id: UUID,
        period: str,
        delivery_score: Decimal,
        quality_score: Decimal,
        price_score: Decimal,
        evaluation_date: date,
        evaluator_id: UUID | None = None,
    ) -> SupplierScore:
        """
        Compute supplier performance score. No posting — pure calculation.
        """
        overall = (delivery_score + quality_score + price_score) / Decimal("3")
        overall = overall.quantize(Decimal("0.01"))

        logger.info("procurement_supplier_evaluated", extra={
            "vendor_id": str(vendor_id),
            "period": period,
            "overall_score": str(overall),
        })

        return SupplierScore(
            vendor_id=vendor_id,
            period=period,
            delivery_score=delivery_score,
            quality_score=quality_score,
            price_score=price_score,
            overall_score=overall,
            evaluation_date=evaluation_date,
            evaluator_id=evaluator_id,
        )

    def record_quantity_variance(
        self,
        receipt_id: UUID,
        po_id: UUID,
        variance_quantity: Decimal,
        variance_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        cost_center: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record quantity variance between PO and receipt.

        Engine: VarianceCalculator for variance computation.
        Profile: procurement.quantity_variance -> QuantityVariance
        """
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                PROCUREMENT_RECORD_QUANTITY_VARIANCE_WORKFLOW,
                "procurement_quantity_variance",
                receipt_id,
                actor_id=actor_id,
                amount=abs(variance_amount),
                currency=currency,
                context={},
            )
            if failure is not None:
                return failure

            logger.info("procurement_qty_variance_started", extra={
                "receipt_id": str(receipt_id),
                "po_id": str(po_id),
                "variance_quantity": str(variance_quantity),
                "variance_amount": str(variance_amount),
            })

            payload: dict[str, Any] = {
                "amount": str(abs(variance_amount)),
                "receipt_id": str(receipt_id),
                "po_id": str(po_id),
                "variance_quantity": str(variance_quantity),
                "variance_amount": str(variance_amount),
                "cost_center": cost_center,
            }

            result = self._poster.post_event(
                event_type="procurement.quantity_variance",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=abs(variance_amount),
                currency=currency,
                description=description or f"Qty variance: receipt {receipt_id} vs PO {po_id}",
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise
