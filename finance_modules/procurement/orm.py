"""
SQLAlchemy ORM persistence models for the Procurement module.

Responsibility
--------------
Provide database-backed persistence for finance-specific procurement
entities: purchase requisitions, requisition lines, and receiving reports.

**Sindri Integration Note**: Purchase orders (``PurchaseOrder``,
``PurchaseOrderLine``) are owned by Sindri and do NOT get ORM models here.
Only finance-specific procurement records are modeled.  References to
Sindri entities use ``String(100)`` fields with NO foreign key constraints.

Architecture position
---------------------
**Modules layer** -- ORM models consumed by ``ProcurementService`` for
persistence.  Inherits from ``TrackedBase`` (kernel db layer).

Invariants enforced
-------------------
* All monetary fields use ``Decimal`` (Numeric(38,9)) -- NEVER float.
* Enum fields stored as String(50) for readability and portability.
* Sindri references are ``String(100)`` -- no FK to avoid cross-system
  coupling.
* ``RequisitionLineModel`` belongs to exactly one ``PurchaseRequisitionModel``.

Audit relevance
---------------
* ``PurchaseRequisitionModel`` tracks the full requisition lifecycle
  (draft -> submitted -> approved -> converted).
* ``ReceivingReportModel`` provides three-way matching evidence for AP
  processing and DCAA compliance.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase

# ---------------------------------------------------------------------------
# PurchaseRequisitionModel
# ---------------------------------------------------------------------------


class PurchaseRequisitionModel(TrackedBase):
    """
    A purchase requisition (internal request to procure goods/services).

    Maps to the ``Requisition`` DTO in ``finance_modules.procurement.models``.

    Guarantees:
        - ``requisition_number`` is unique.
        - ``requester_id`` references a Party (employee).
        - ``status`` follows the lifecycle:
          draft -> submitted -> approved -> rejected -> converted -> cancelled.
    """

    __tablename__ = "procurement_requisitions"

    __table_args__ = (
        UniqueConstraint("requisition_number", name="uq_requisition_number"),
        Index("idx_requisition_requester", "requester_id"),
        Index("idx_requisition_status", "status"),
        Index("idx_requisition_date", "request_date"),
    )

    requisition_number: Mapped[str] = mapped_column(String(50), nullable=False)
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("parties.id"), nullable=False)
    request_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    department_id: Mapped[UUID | None]
    approved_by: Mapped[UUID | None]
    approved_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Reference to Sindri PO (if converted)
    sindri_po_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relationships
    lines: Mapped[list["RequisitionLineModel"]] = relationship(
        "RequisitionLineModel",
        back_populates="requisition",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.procurement.models import Requisition, RequisitionStatus

        line_dtos = tuple(line.to_dto() for line in self.lines) if self.lines else ()

        return Requisition(
            id=self.id,
            requisition_number=self.requisition_number,
            requester_id=self.requester_id,
            request_date=self.request_date,
            description=self.description,
            total_amount=self.total_amount,
            currency=self.currency,
            status=RequisitionStatus(self.status),
            department_id=self.department_id,
            approved_by=self.approved_by,
            approved_date=self.approved_date,
            lines=line_dtos,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "PurchaseRequisitionModel":
        from finance_modules.procurement.models import RequisitionStatus

        return cls(
            id=dto.id,
            requisition_number=dto.requisition_number,
            requester_id=dto.requester_id,
            request_date=dto.request_date,
            description=dto.description,
            total_amount=dto.total_amount,
            currency=dto.currency,
            status=dto.status.value if isinstance(dto.status, RequisitionStatus) else dto.status,
            department_id=dto.department_id,
            approved_by=dto.approved_by,
            approved_date=dto.approved_date,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<PurchaseRequisitionModel {self.requisition_number} [{self.status}]>"


# ---------------------------------------------------------------------------
# RequisitionLineModel
# ---------------------------------------------------------------------------


class RequisitionLineModel(TrackedBase):
    """
    A line item on a purchase requisition.

    Maps to the ``RequisitionLine`` DTO in
    ``finance_modules.procurement.models``.

    Guarantees:
        - Belongs to exactly one ``PurchaseRequisitionModel``.
        - (requisition_id, line_number) is unique.
    """

    __tablename__ = "procurement_requisition_lines"

    __table_args__ = (
        UniqueConstraint(
            "requisition_id", "line_number",
            name="uq_requisition_line_number",
        ),
        Index("idx_req_line_requisition", "requisition_id"),
    )

    requisition_id: Mapped[UUID] = mapped_column(
        ForeignKey("procurement_requisitions.id"), nullable=False,
    )
    line_number: Mapped[int]
    item_id: Mapped[UUID | None]
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    quantity: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    unit_of_measure: Mapped[str] = mapped_column(String(20), nullable=False, default="EA")
    estimated_unit_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    estimated_total: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    required_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gl_account_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    project_id: Mapped[UUID | None]

    # Parent relationship
    requisition: Mapped["PurchaseRequisitionModel"] = relationship(
        "PurchaseRequisitionModel",
        back_populates="lines",
    )

    def to_dto(self):
        from finance_modules.procurement.models import RequisitionLine

        return RequisitionLine(
            id=self.id,
            requisition_id=self.requisition_id,
            line_number=self.line_number,
            item_id=self.item_id,
            description=self.description,
            quantity=self.quantity,
            unit_of_measure=self.unit_of_measure,
            estimated_unit_cost=self.estimated_unit_cost,
            estimated_total=self.estimated_total,
            required_date=self.required_date,
            gl_account_code=self.gl_account_code,
            project_id=self.project_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "RequisitionLineModel":
        return cls(
            id=dto.id,
            requisition_id=dto.requisition_id,
            line_number=dto.line_number,
            item_id=dto.item_id,
            description=dto.description,
            quantity=dto.quantity,
            unit_of_measure=dto.unit_of_measure,
            estimated_unit_cost=dto.estimated_unit_cost,
            estimated_total=dto.estimated_total,
            required_date=dto.required_date,
            gl_account_code=dto.gl_account_code,
            project_id=dto.project_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<RequisitionLineModel #{self.line_number} qty={self.quantity}>"


# ---------------------------------------------------------------------------
# ReceivingReportModel
# ---------------------------------------------------------------------------


class ReceivingReportModel(TrackedBase):
    """
    A goods receipt / receiving report for three-way matching.

    Finance-specific record that tracks goods received against a purchase
    order.  The PO itself is owned by Sindri; we reference it via
    ``sindri_po_reference`` (String, no FK).

    Guarantees:
        - ``receipt_number`` is unique.
        - ``sindri_po_reference`` links to the Sindri PO (no FK).
        - Tracks quantities received, accepted, and rejected.
    """

    __tablename__ = "procurement_receiving_reports"

    __table_args__ = (
        UniqueConstraint("receipt_number", name="uq_receiving_report_number"),
        Index("idx_receiving_po_ref", "sindri_po_reference"),
        Index("idx_receiving_status", "status"),
        Index("idx_receiving_date", "receipt_date"),
    )

    receipt_number: Mapped[str] = mapped_column(String(50), nullable=False)
    sindri_po_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sindri_po_line_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity_received: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    quantity_accepted: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    quantity_rejected: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    receiver_id: Mapped[UUID | None]
    location_id: Mapped[UUID | None]
    lot_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    unit_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    total_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    def to_dto(self) -> dict:
        return {
            "id": self.id,
            "receipt_number": self.receipt_number,
            "sindri_po_reference": self.sindri_po_reference,
            "sindri_po_line_reference": self.sindri_po_line_reference,
            "receipt_date": self.receipt_date,
            "quantity_received": self.quantity_received,
            "quantity_accepted": self.quantity_accepted,
            "quantity_rejected": self.quantity_rejected,
            "status": self.status,
            "receiver_id": self.receiver_id,
            "location_id": self.location_id,
            "lot_number": self.lot_number,
            "description": self.description,
            "currency": self.currency,
            "unit_cost": self.unit_cost,
            "total_cost": self.total_cost,
        }

    @classmethod
    def from_dto(cls, dto: dict, created_by_id: UUID) -> "ReceivingReportModel":
        return cls(
            id=dto.get("id"),
            receipt_number=dto["receipt_number"],
            sindri_po_reference=dto.get("sindri_po_reference"),
            sindri_po_line_reference=dto.get("sindri_po_line_reference"),
            receipt_date=dto["receipt_date"],
            quantity_received=dto.get("quantity_received", Decimal("0")),
            quantity_accepted=dto.get("quantity_accepted", Decimal("0")),
            quantity_rejected=dto.get("quantity_rejected", Decimal("0")),
            status=dto.get("status", "pending"),
            receiver_id=dto.get("receiver_id"),
            location_id=dto.get("location_id"),
            lot_number=dto.get("lot_number"),
            description=dto.get("description"),
            currency=dto.get("currency", "USD"),
            unit_cost=dto.get("unit_cost", Decimal("0")),
            total_cost=dto.get("total_cost", Decimal("0")),
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<ReceivingReportModel {self.receipt_number} qty={self.quantity_received} [{self.status}]>"
