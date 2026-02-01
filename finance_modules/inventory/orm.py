"""
Module: finance_modules.inventory.orm
Responsibility: SQLAlchemy ORM persistence models for the Inventory module.
    Maps frozen dataclass DTOs from inventory.models to relational tables for
    receipts, issues, adjustments, transfers, and cycle counts.

Architecture position: Modules > Inventory > ORM.  Inherits from TrackedBase
    (finance_kernel.db.base).  References Sindri-owned entities (Item, Location)
    via String columns with NO foreign key constraints.

Invariants enforced:
    - All monetary fields use Decimal (Numeric(38,9)) -- NEVER float (R16/R17).
    - Enum fields stored as String(50) for portability and readability.
    - TrackedBase provides: id (UUID PK), created_at, updated_at,
      created_by_id (NOT NULL), updated_by_id (nullable).

Failure modes:
    - IntegrityError on duplicate receipt/issue/adjustment/transfer/cycle-count id.

Audit relevance:
    - These ORM records are operational artifacts.  The authoritative financial
      truth remains JournalEntry/JournalLine.  These records support traceability
      from journal postings back to the originating inventory movement.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import String, Index, Text, Boolean, Date
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import TrackedBase


# =============================================================================
# InventoryReceiptModel
# =============================================================================

class InventoryReceiptModel(TrackedBase):
    """
    ORM model for inventory receipts (from purchase orders, production, or returns).

    Maps to: finance_modules.inventory.models.InventoryReceipt (frozen dataclass).

    Guarantees:
        - total_cost, unit_cost, quantity use Decimal (Numeric(38,9)).
        - item_id and location_id reference Sindri entities via UUID (no FK).
        - source_id references external entities via UUID (no FK).
    """

    __tablename__ = "inventory_receipts"

    __table_args__ = (
        Index("idx_inv_receipt_item", "item_id"),
        Index("idx_inv_receipt_location", "location_id"),
        Index("idx_inv_receipt_date", "receipt_date"),
        Index("idx_inv_receipt_status", "status"),
        Index("idx_inv_receipt_source", "source_type", "source_id"),
    )

    # Sindri entity references (no FK)
    item_id: Mapped[UUID] = mapped_column()
    location_id: Mapped[UUID] = mapped_column()

    # Receipt details
    receipt_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column()
    unit_cost: Mapped[Decimal] = mapped_column()
    total_cost: Mapped[Decimal] = mapped_column()

    # Status (ReceiptStatus enum stored as string)
    status: Mapped[str] = mapped_column(String(50), default="pending")

    # Source traceability
    source_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )  # purchase_order, production_order, return
    source_id: Mapped[UUID | None] = mapped_column(nullable=True)

    # Lot tracking
    lot_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    def to_dto(self):
        """Convert ORM model to frozen InventoryReceipt DTO."""
        from finance_modules.inventory.models import InventoryReceipt, ReceiptStatus
        return InventoryReceipt(
            id=self.id,
            item_id=self.item_id,
            location_id=self.location_id,
            receipt_date=self.receipt_date,
            quantity=self.quantity,
            unit_cost=self.unit_cost,
            total_cost=self.total_cost,
            status=ReceiptStatus(self.status),
            source_type=self.source_type,
            source_id=self.source_id,
            lot_number=self.lot_number,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "InventoryReceiptModel":
        """Create ORM model from frozen InventoryReceipt DTO."""
        return cls(
            id=dto.id,
            item_id=dto.item_id,
            location_id=dto.location_id,
            receipt_date=dto.receipt_date,
            quantity=dto.quantity,
            unit_cost=dto.unit_cost,
            total_cost=dto.total_cost,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            source_type=dto.source_type,
            source_id=dto.source_id,
            lot_number=dto.lot_number,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<InventoryReceiptModel {self.id} item={self.item_id} "
            f"qty={self.quantity} status={self.status}>"
        )


# =============================================================================
# InventoryIssueModel
# =============================================================================

class InventoryIssueModel(TrackedBase):
    """
    ORM model for inventory issues (to production, sales, or scrap).

    Maps to: finance_modules.inventory.models.InventoryIssue (frozen dataclass).

    Guarantees:
        - total_cost determined by costing engine (FIFO/LIFO), persisted here.
        - destination_id references external entities via UUID (no FK).
    """

    __tablename__ = "inventory_issues"

    __table_args__ = (
        Index("idx_inv_issue_item", "item_id"),
        Index("idx_inv_issue_location", "location_id"),
        Index("idx_inv_issue_date", "issue_date"),
        Index("idx_inv_issue_status", "status"),
        Index("idx_inv_issue_dest", "destination_type", "destination_id"),
    )

    # Sindri entity references (no FK)
    item_id: Mapped[UUID] = mapped_column()
    location_id: Mapped[UUID] = mapped_column()

    # Issue details
    issue_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column()
    unit_cost: Mapped[Decimal] = mapped_column()
    total_cost: Mapped[Decimal] = mapped_column()

    # Status (IssueStatus enum stored as string)
    status: Mapped[str] = mapped_column(String(50), default="requested")

    # Destination traceability
    destination_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )  # work_order, sales_order, scrap
    destination_id: Mapped[UUID | None] = mapped_column(nullable=True)

    # Lot tracking
    lot_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    def to_dto(self):
        """Convert ORM model to frozen InventoryIssue DTO."""
        from finance_modules.inventory.models import InventoryIssue, IssueStatus
        return InventoryIssue(
            id=self.id,
            item_id=self.item_id,
            location_id=self.location_id,
            issue_date=self.issue_date,
            quantity=self.quantity,
            unit_cost=self.unit_cost,
            total_cost=self.total_cost,
            status=IssueStatus(self.status),
            destination_type=self.destination_type,
            destination_id=self.destination_id,
            lot_number=self.lot_number,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "InventoryIssueModel":
        """Create ORM model from frozen InventoryIssue DTO."""
        return cls(
            id=dto.id,
            item_id=dto.item_id,
            location_id=dto.location_id,
            issue_date=dto.issue_date,
            quantity=dto.quantity,
            unit_cost=dto.unit_cost,
            total_cost=dto.total_cost,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            destination_type=dto.destination_type,
            destination_id=dto.destination_id,
            lot_number=dto.lot_number,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<InventoryIssueModel {self.id} item={self.item_id} "
            f"qty={self.quantity} status={self.status}>"
        )


# =============================================================================
# InventoryAdjustmentModel
# =============================================================================

class InventoryAdjustmentModel(TrackedBase):
    """
    ORM model for inventory adjustments (cycle count, physical count, write-off).

    Maps to: finance_modules.inventory.models.InventoryAdjustment (frozen dataclass).

    Guarantees:
        - reason_code is mandatory for audit traceability.
        - quantity_change and value_change use Decimal (signed values).
    """

    __tablename__ = "inventory_adjustments"

    __table_args__ = (
        Index("idx_inv_adj_item", "item_id"),
        Index("idx_inv_adj_location", "location_id"),
        Index("idx_inv_adj_date", "adjustment_date"),
        Index("idx_inv_adj_reason", "reason_code"),
    )

    # Sindri entity references (no FK)
    item_id: Mapped[UUID] = mapped_column()
    location_id: Mapped[UUID] = mapped_column()

    # Adjustment details
    adjustment_date: Mapped[date] = mapped_column(Date)
    quantity_change: Mapped[Decimal] = mapped_column()
    value_change: Mapped[Decimal] = mapped_column()

    # Audit fields
    reason_code: Mapped[str] = mapped_column(String(50))
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def to_dto(self):
        """Convert ORM model to frozen InventoryAdjustment DTO."""
        from finance_modules.inventory.models import InventoryAdjustment
        return InventoryAdjustment(
            id=self.id,
            item_id=self.item_id,
            location_id=self.location_id,
            adjustment_date=self.adjustment_date,
            quantity_change=self.quantity_change,
            value_change=self.value_change,
            reason_code=self.reason_code,
            reference=self.reference,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "InventoryAdjustmentModel":
        """Create ORM model from frozen InventoryAdjustment DTO."""
        return cls(
            id=dto.id,
            item_id=dto.item_id,
            location_id=dto.location_id,
            adjustment_date=dto.adjustment_date,
            quantity_change=dto.quantity_change,
            value_change=dto.value_change,
            reason_code=dto.reason_code,
            reference=dto.reference,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<InventoryAdjustmentModel {self.id} item={self.item_id} "
            f"qty_change={self.quantity_change} reason={self.reason_code}>"
        )


# =============================================================================
# StockTransferModel
# =============================================================================

class StockTransferModel(TrackedBase):
    """
    ORM model for stock transfers between locations.

    Maps to: finance_modules.inventory.models.StockTransfer (frozen dataclass).

    Guarantees:
        - from_location_id != to_location_id (caller-enforced, not ORM-enforced).
        - status tracks the transfer lifecycle: REQUESTED -> IN_TRANSIT -> RECEIVED.
    """

    __tablename__ = "inventory_transfers"

    __table_args__ = (
        Index("idx_inv_xfer_item", "item_id"),
        Index("idx_inv_xfer_from", "from_location_id"),
        Index("idx_inv_xfer_to", "to_location_id"),
        Index("idx_inv_xfer_date", "transfer_date"),
        Index("idx_inv_xfer_status", "status"),
    )

    # Sindri entity references (no FK)
    item_id: Mapped[UUID] = mapped_column()
    from_location_id: Mapped[UUID] = mapped_column()
    to_location_id: Mapped[UUID] = mapped_column()

    # Transfer details
    transfer_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column()

    # Status (TransferStatus enum stored as string)
    status: Mapped[str] = mapped_column(String(50), default="requested")

    # Lot tracking
    lot_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    def to_dto(self):
        """Convert ORM model to frozen StockTransfer DTO."""
        from finance_modules.inventory.models import StockTransfer, TransferStatus
        return StockTransfer(
            id=self.id,
            item_id=self.item_id,
            from_location_id=self.from_location_id,
            to_location_id=self.to_location_id,
            transfer_date=self.transfer_date,
            quantity=self.quantity,
            status=TransferStatus(self.status),
            lot_number=self.lot_number,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "StockTransferModel":
        """Create ORM model from frozen StockTransfer DTO."""
        return cls(
            id=dto.id,
            item_id=dto.item_id,
            from_location_id=dto.from_location_id,
            to_location_id=dto.to_location_id,
            transfer_date=dto.transfer_date,
            quantity=dto.quantity,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            lot_number=dto.lot_number,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<StockTransferModel {self.id} item={self.item_id} "
            f"from={self.from_location_id} to={self.to_location_id} "
            f"qty={self.quantity} status={self.status}>"
        )


# =============================================================================
# CycleCountModel
# =============================================================================

class CycleCountModel(TrackedBase):
    """
    ORM model for cycle count results.

    Maps to: finance_modules.inventory.models.CycleCount (frozen dataclass).

    Guarantees:
        - variance_quantity = actual_quantity - expected_quantity (caller-enforced).
        - variance_amount uses Decimal (Numeric(38,9)).
        - item_id and location_id are string references to Sindri entities.
    """

    __tablename__ = "inventory_cycle_counts"

    __table_args__ = (
        Index("idx_inv_cc_item", "item_id"),
        Index("idx_inv_cc_location", "location_id"),
        Index("idx_inv_cc_date", "count_date"),
        Index("idx_inv_cc_counter", "counter_id"),
    )

    # Count details
    count_date: Mapped[date] = mapped_column(Date)

    # Sindri entity references (string-based, no FK)
    item_id: Mapped[str] = mapped_column(String(100))
    location_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Quantities
    expected_quantity: Mapped[Decimal] = mapped_column()
    actual_quantity: Mapped[Decimal] = mapped_column()
    variance_quantity: Mapped[Decimal] = mapped_column()
    variance_amount: Mapped[Decimal] = mapped_column()

    # Currency
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Counter (employee who performed the count)
    counter_id: Mapped[UUID | None] = mapped_column(nullable=True)

    # Notes
    notes: Mapped[str] = mapped_column(Text, default="")

    def to_dto(self):
        """Convert ORM model to frozen CycleCount DTO."""
        from finance_modules.inventory.models import CycleCount
        return CycleCount(
            id=self.id,
            count_date=self.count_date,
            item_id=self.item_id,
            location_id=self.location_id,
            expected_quantity=self.expected_quantity,
            actual_quantity=self.actual_quantity,
            variance_quantity=self.variance_quantity,
            variance_amount=self.variance_amount,
            currency=self.currency,
            counter_id=self.counter_id,
            notes=self.notes,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "CycleCountModel":
        """Create ORM model from frozen CycleCount DTO."""
        return cls(
            id=dto.id,
            count_date=dto.count_date,
            item_id=dto.item_id,
            location_id=dto.location_id,
            expected_quantity=dto.expected_quantity,
            actual_quantity=dto.actual_quantity,
            variance_quantity=dto.variance_quantity,
            variance_amount=dto.variance_amount,
            currency=dto.currency,
            counter_id=dto.counter_id,
            notes=dto.notes,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<CycleCountModel {self.id} item={self.item_id} "
            f"variance_qty={self.variance_quantity}>"
        )


# =============================================================================
# ABCClassificationModel
# =============================================================================

class ABCClassificationModel(TrackedBase):
    """
    ORM model for ABC analysis classification results.

    Maps to: finance_modules.inventory.models.ABCClassification (frozen dataclass).

    Note: The DTO does not have an ``id`` field -- the ORM model adds one via
    TrackedBase.  The natural key is (item_id, as_of_date).

    Guarantees:
        - classification is one of "A", "B", "C".
        - annual_value and cumulative_percent use Decimal (Numeric(38,9)).
    """

    __tablename__ = "inventory_abc_classifications"

    __table_args__ = (
        Index("idx_inv_abc_item", "item_id"),
        Index("idx_inv_abc_date", "as_of_date"),
        Index("idx_inv_abc_class", "classification"),
    )

    # Sindri entity reference (string-based, no FK)
    item_id: Mapped[str] = mapped_column(String(100))

    # Classification result
    classification: Mapped[str] = mapped_column(String(1))  # A, B, or C
    annual_value: Mapped[Decimal] = mapped_column()
    cumulative_percent: Mapped[Decimal] = mapped_column()
    as_of_date: Mapped[date] = mapped_column(Date)

    def to_dto(self):
        """Convert ORM model to frozen ABCClassification DTO."""
        from finance_modules.inventory.models import ABCClassification
        return ABCClassification(
            item_id=self.item_id,
            classification=self.classification,
            annual_value=self.annual_value,
            cumulative_percent=self.cumulative_percent,
            as_of_date=self.as_of_date,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ABCClassificationModel":
        """Create ORM model from frozen ABCClassification DTO."""
        return cls(
            item_id=dto.item_id,
            classification=dto.classification,
            annual_value=dto.annual_value,
            cumulative_percent=dto.cumulative_percent,
            as_of_date=dto.as_of_date,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ABCClassificationModel item={self.item_id} "
            f"class={self.classification} as_of={self.as_of_date}>"
        )


# =============================================================================
# ReorderPointModel
# =============================================================================

class ReorderPointModel(TrackedBase):
    """
    ORM model for computed reorder-point parameters.

    Maps to: finance_modules.inventory.models.ReorderPoint (frozen dataclass).

    Note: The DTO does not have an ``id`` field -- the ORM model adds one via
    TrackedBase.  The natural key is (item_id, location_id).

    Guarantees:
        - All quantity/cost fields use Decimal (Numeric(38,9)).
        - lead_time_days is a BigInteger (via type_annotation_map).
    """

    __tablename__ = "inventory_reorder_points"

    __table_args__ = (
        Index("idx_inv_rop_item", "item_id"),
        Index("idx_inv_rop_location", "location_id"),
    )

    # Sindri entity references (string-based, no FK)
    item_id: Mapped[str] = mapped_column(String(100))
    location_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Reorder parameters
    reorder_point: Mapped[Decimal] = mapped_column()
    safety_stock: Mapped[Decimal] = mapped_column()
    eoq: Mapped[Decimal] = mapped_column()
    avg_daily_usage: Mapped[Decimal] = mapped_column()
    lead_time_days: Mapped[int] = mapped_column()

    def to_dto(self):
        """Convert ORM model to frozen ReorderPoint DTO."""
        from finance_modules.inventory.models import ReorderPoint
        return ReorderPoint(
            item_id=self.item_id,
            location_id=self.location_id,
            reorder_point=self.reorder_point,
            safety_stock=self.safety_stock,
            eoq=self.eoq,
            avg_daily_usage=self.avg_daily_usage,
            lead_time_days=self.lead_time_days,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ReorderPointModel":
        """Create ORM model from frozen ReorderPoint DTO."""
        return cls(
            item_id=dto.item_id,
            location_id=dto.location_id,
            reorder_point=dto.reorder_point,
            safety_stock=dto.safety_stock,
            eoq=dto.eoq,
            avg_daily_usage=dto.avg_daily_usage,
            lead_time_days=dto.lead_time_days,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ReorderPointModel item={self.item_id} "
            f"rop={self.reorder_point} eoq={self.eoq}>"
        )
