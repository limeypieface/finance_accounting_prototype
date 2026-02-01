"""
Module: finance_modules.revenue.orm
Responsibility:
    SQLAlchemy ORM persistence models for the Revenue Recognition module
    (ASC 606 five-step model).  Maps frozen dataclass DTOs from
    ``finance_modules.revenue.models`` to relational tables.

Architecture position:
    **Modules layer** -- ORM models inheriting from ``TrackedBase``
    (kernel DB base).  These models persist ASC 606 concepts:
    revenue contracts, performance obligations, transaction prices,
    SSP allocations, recognition schedules, and contract modifications.

Invariants enforced:
    - All monetary fields use Decimal (maps to Numeric(38,9) via TrackedBase).
    - Enum fields stored as String(50) for safe serialization.
    - TrackedBase provides id, created_at, updated_at, created_by_id,
      updated_by_id automatically.
    - FK to kernel Party (customer_id -> parties.id).

Failure modes:
    - IntegrityError on duplicate unique constraints.
    - ForeignKey violation on invalid parent references.

Audit relevance:
    - RevenueContractModel tracks ASC 606 Step 1 contract identification.
    - PerformanceObligationModel tracks Step 2 obligation identification.
    - TransactionPriceModel tracks Step 3 price determination.
    - SSPAllocationModel tracks Step 4 allocation results.
    - RecognitionScheduleModel tracks Step 5 recognition timing.
    - ContractModificationModel tracks ASC 606-10-25-12 modifications.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase, UUIDString


# =============================================================================
# Revenue Contract (ASC 606 Step 1)
# =============================================================================


class RevenueContractModel(TrackedBase):
    """
    An identified revenue contract per ASC 606 Step 1.

    Contract:
        Each RevenueContractModel represents a contract with a customer
        that has been identified and assessed for the five criteria of
        ASC 606-10-25-1.

    Guarantees:
        - ``contract_number`` is unique (uq_revenue_contract_number).
        - ``customer_id`` references parties.id (FK to kernel Party).
        - ``status`` is one of: identified, active, modified, completed,
          terminated.
        - ``currency`` defaults to "USD" (ISO 4217).
        - All monetary fields are Decimal (Numeric(38,9)).
    """

    __tablename__ = "revenue_contracts"

    __table_args__ = (
        UniqueConstraint("contract_number", name="uq_revenue_contract_number"),
        Index("idx_revenue_contract_customer", "customer_id"),
        Index("idx_revenue_contract_status", "status"),
        Index("idx_revenue_contract_start", "start_date"),
    )

    customer_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("parties.id"),
        nullable=False,
    )
    contract_number: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_consideration: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    variable_consideration: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(50), default="identified")
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Child relationships
    obligations: Mapped[list["PerformanceObligationModel"]] = relationship(
        "PerformanceObligationModel",
        back_populates="contract",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    transaction_prices: Mapped[list["TransactionPriceModel"]] = relationship(
        "TransactionPriceModel",
        back_populates="contract",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    allocations: Mapped[list["SSPAllocationModel"]] = relationship(
        "SSPAllocationModel",
        back_populates="contract",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    schedule_entries: Mapped[list["RecognitionScheduleModel"]] = relationship(
        "RecognitionScheduleModel",
        back_populates="contract",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    modifications: Mapped[list["ContractModificationModel"]] = relationship(
        "ContractModificationModel",
        back_populates="contract",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.revenue.models import RevenueContract, ContractStatus

        return RevenueContract(
            id=self.id,
            customer_id=self.customer_id,
            contract_number=self.contract_number,
            start_date=self.start_date,
            end_date=self.end_date,
            total_consideration=self.total_consideration,
            variable_consideration=self.variable_consideration,
            status=ContractStatus(self.status),
            currency=self.currency,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "RevenueContractModel":
        return cls(
            id=dto.id,
            customer_id=dto.customer_id,
            contract_number=dto.contract_number,
            start_date=dto.start_date,
            end_date=dto.end_date,
            total_consideration=dto.total_consideration,
            variable_consideration=dto.variable_consideration,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            currency=dto.currency,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<RevenueContractModel {self.contract_number} ({self.status})>"


# =============================================================================
# Performance Obligation (ASC 606 Step 2)
# =============================================================================


class PerformanceObligationModel(TrackedBase):
    """
    An identified performance obligation per ASC 606 Step 2.

    Contract:
        Each obligation belongs to exactly one RevenueContractModel.
        Tracks whether the obligation is distinct, its SSP, allocated price,
        recognition method, and satisfaction status.

    Guarantees:
        - ``contract_id`` references revenue_contracts.id.
        - ``recognition_method`` is one of: point_in_time, over_time_input,
          over_time_output.
        - All monetary fields are Decimal (Numeric(38,9)).
    """

    __tablename__ = "revenue_performance_obligations"

    __table_args__ = (
        Index("idx_revenue_po_contract", "contract_id"),
        Index("idx_revenue_po_satisfied", "satisfied"),
    )

    contract_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("revenue_contracts.id"),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_distinct: Mapped[bool] = mapped_column(Boolean, default=True)
    standalone_selling_price: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    allocated_price: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    recognition_method: Mapped[str] = mapped_column(
        String(50), default="point_in_time",
    )
    satisfied: Mapped[bool] = mapped_column(Boolean, default=False)
    satisfaction_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Parent relationship
    contract: Mapped["RevenueContractModel"] = relationship(
        "RevenueContractModel",
        back_populates="obligations",
    )

    def to_dto(self):
        from finance_modules.revenue.models import (
            PerformanceObligation,
            RecognitionMethod,
        )

        return PerformanceObligation(
            id=self.id,
            contract_id=self.contract_id,
            description=self.description,
            is_distinct=self.is_distinct,
            standalone_selling_price=self.standalone_selling_price,
            allocated_price=self.allocated_price,
            recognition_method=RecognitionMethod(self.recognition_method),
            satisfied=self.satisfied,
            satisfaction_date=self.satisfaction_date,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "PerformanceObligationModel":
        return cls(
            id=dto.id,
            contract_id=dto.contract_id,
            description=dto.description,
            is_distinct=dto.is_distinct,
            standalone_selling_price=dto.standalone_selling_price,
            allocated_price=dto.allocated_price,
            recognition_method=(
                dto.recognition_method.value
                if hasattr(dto.recognition_method, "value")
                else dto.recognition_method
            ),
            satisfied=dto.satisfied,
            satisfaction_date=dto.satisfaction_date,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<PerformanceObligationModel {self.description[:40]} "
            f"({self.recognition_method})>"
        )


# =============================================================================
# Transaction Price (ASC 606 Step 3)
# =============================================================================


class TransactionPriceModel(TrackedBase):
    """
    Determined transaction price per ASC 606 Step 3.

    Contract:
        Records the full transaction price breakdown for a revenue contract,
        including variable consideration, financing component, noncash
        consideration, and consideration payable to customer.

    Guarantees:
        - ``contract_id`` references revenue_contracts.id.
        - All monetary fields are Decimal (Numeric(38,9)).
        - ``total_transaction_price`` = base + variable + financing +
          noncash - consideration_payable (enforced at service layer).
    """

    __tablename__ = "revenue_transaction_prices"

    __table_args__ = (
        Index("idx_revenue_txn_price_contract", "contract_id"),
    )

    contract_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("revenue_contracts.id"),
        nullable=False,
    )
    base_price: Mapped[Decimal] = mapped_column(nullable=False)
    variable_consideration: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    constraint_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    financing_component: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    noncash_consideration: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    consideration_payable: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    total_transaction_price: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Parent relationship
    contract: Mapped["RevenueContractModel"] = relationship(
        "RevenueContractModel",
        back_populates="transaction_prices",
    )

    def to_dto(self):
        from finance_modules.revenue.models import TransactionPrice

        return TransactionPrice(
            id=self.id,
            contract_id=self.contract_id,
            base_price=self.base_price,
            variable_consideration=self.variable_consideration,
            constraint_applied=self.constraint_applied,
            financing_component=self.financing_component,
            noncash_consideration=self.noncash_consideration,
            consideration_payable=self.consideration_payable,
            total_transaction_price=self.total_transaction_price,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TransactionPriceModel":
        return cls(
            id=dto.id,
            contract_id=dto.contract_id,
            base_price=dto.base_price,
            variable_consideration=dto.variable_consideration,
            constraint_applied=dto.constraint_applied,
            financing_component=dto.financing_component,
            noncash_consideration=dto.noncash_consideration,
            consideration_payable=dto.consideration_payable,
            total_transaction_price=dto.total_transaction_price,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<TransactionPriceModel contract={self.contract_id} "
            f"total={self.total_transaction_price}>"
        )


# =============================================================================
# SSP Allocation (ASC 606 Step 4)
# =============================================================================


class SSPAllocationModel(TrackedBase):
    """
    SSP allocation result per ASC 606 Step 4.

    Contract:
        Records the proportional allocation of the transaction price to
        each performance obligation based on standalone selling prices.

    Guarantees:
        - ``contract_id`` references revenue_contracts.id.
        - ``obligation_id`` references revenue_performance_obligations.id.
        - ``allocation_percentage`` is Decimal in range (0, 1] (enforced
          at service layer).
        - All monetary fields are Decimal (Numeric(38,9)).
    """

    __tablename__ = "revenue_ssp_allocations"

    __table_args__ = (
        UniqueConstraint(
            "contract_id", "obligation_id", name="uq_revenue_ssp_alloc",
        ),
        Index("idx_revenue_ssp_contract", "contract_id"),
        Index("idx_revenue_ssp_obligation", "obligation_id"),
    )

    contract_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("revenue_contracts.id"),
        nullable=False,
    )
    obligation_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("revenue_performance_obligations.id"),
        nullable=False,
    )
    standalone_selling_price: Mapped[Decimal] = mapped_column(nullable=False)
    allocated_amount: Mapped[Decimal] = mapped_column(nullable=False)
    allocation_percentage: Mapped[Decimal] = mapped_column(nullable=False)

    # Parent relationship
    contract: Mapped["RevenueContractModel"] = relationship(
        "RevenueContractModel",
        back_populates="allocations",
    )
    obligation: Mapped["PerformanceObligationModel"] = relationship(
        "PerformanceObligationModel",
        foreign_keys=[obligation_id],
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.revenue.models import SSPAllocation

        return SSPAllocation(
            id=self.id,
            contract_id=self.contract_id,
            obligation_id=self.obligation_id,
            standalone_selling_price=self.standalone_selling_price,
            allocated_amount=self.allocated_amount,
            allocation_percentage=self.allocation_percentage,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "SSPAllocationModel":
        return cls(
            id=dto.id,
            contract_id=dto.contract_id,
            obligation_id=dto.obligation_id,
            standalone_selling_price=dto.standalone_selling_price,
            allocated_amount=dto.allocated_amount,
            allocation_percentage=dto.allocation_percentage,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<SSPAllocationModel obligation={self.obligation_id} "
            f"alloc={self.allocation_percentage}>"
        )


# =============================================================================
# Recognition Schedule (ASC 606 Step 5)
# =============================================================================


class RecognitionScheduleModel(TrackedBase):
    """
    Revenue recognition schedule entry per ASC 606 Step 5.

    Contract:
        Each entry represents one period's recognition amount for one
        performance obligation within a revenue contract.

    Guarantees:
        - (contract_id, obligation_id, period) is unique
          (uq_revenue_sched_entry).
        - ``amount`` is Decimal (Numeric(38,9)).
    """

    __tablename__ = "revenue_recognition_schedules"

    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            "obligation_id",
            "period",
            name="uq_revenue_sched_entry",
        ),
        Index("idx_revenue_sched_contract", "contract_id"),
        Index("idx_revenue_sched_obligation", "obligation_id"),
        Index("idx_revenue_sched_period", "period"),
        Index("idx_revenue_sched_recognized", "recognized"),
    )

    contract_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("revenue_contracts.id"),
        nullable=False,
    )
    obligation_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("revenue_performance_obligations.id"),
        nullable=False,
    )
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    recognized: Mapped[bool] = mapped_column(Boolean, default=False)
    recognized_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Parent relationship
    contract: Mapped["RevenueContractModel"] = relationship(
        "RevenueContractModel",
        back_populates="schedule_entries",
    )
    obligation: Mapped["PerformanceObligationModel"] = relationship(
        "PerformanceObligationModel",
        foreign_keys=[obligation_id],
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.revenue.models import RecognitionSchedule

        return RecognitionSchedule(
            id=self.id,
            contract_id=self.contract_id,
            obligation_id=self.obligation_id,
            period=self.period,
            amount=self.amount,
            recognized=self.recognized,
            recognized_date=self.recognized_date,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "RecognitionScheduleModel":
        return cls(
            id=dto.id,
            contract_id=dto.contract_id,
            obligation_id=dto.obligation_id,
            period=dto.period,
            amount=dto.amount,
            recognized=dto.recognized,
            recognized_date=dto.recognized_date,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        status = "recognized" if self.recognized else "pending"
        return (
            f"<RecognitionScheduleModel {self.period} "
            f"amount={self.amount} ({status})>"
        )


# =============================================================================
# Contract Modification (ASC 606-10-25-12)
# =============================================================================


class ContractModificationModel(TrackedBase):
    """
    A contract modification record per ASC 606-10-25-12.

    Contract:
        Records modifications to revenue contracts, including the
        modification type (separate contract, cumulative catch-up,
        prospective, or termination), price change, and scope change.

    Guarantees:
        - ``contract_id`` references revenue_contracts.id.
        - ``modification_type`` is one of: separate_contract,
          cumulative_catch_up, prospective, termination.
        - ``price_change`` is Decimal (may be negative for reductions).
    """

    __tablename__ = "revenue_contract_modifications"

    __table_args__ = (
        Index("idx_revenue_mod_contract", "contract_id"),
        Index("idx_revenue_mod_date", "modification_date"),
        Index("idx_revenue_mod_type", "modification_type"),
    )

    contract_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("revenue_contracts.id"),
        nullable=False,
    )
    modification_date: Mapped[date] = mapped_column(Date, nullable=False)
    modification_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    price_change: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    scope_change: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actor_id: Mapped[UUID | None] = mapped_column(UUIDString(), nullable=True)

    # Parent relationship
    contract: Mapped["RevenueContractModel"] = relationship(
        "RevenueContractModel",
        back_populates="modifications",
    )

    def to_dto(self):
        from finance_modules.revenue.models import (
            ContractModification,
            ModificationType,
        )

        return ContractModification(
            id=self.id,
            contract_id=self.contract_id,
            modification_date=self.modification_date,
            modification_type=ModificationType(self.modification_type),
            description=self.description,
            price_change=self.price_change,
            scope_change=self.scope_change,
            actor_id=self.actor_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ContractModificationModel":
        return cls(
            id=dto.id,
            contract_id=dto.contract_id,
            modification_date=dto.modification_date,
            modification_type=(
                dto.modification_type.value
                if hasattr(dto.modification_type, "value")
                else dto.modification_type
            ),
            description=dto.description,
            price_change=dto.price_change,
            scope_change=dto.scope_change,
            actor_id=dto.actor_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ContractModificationModel {self.modification_date} "
            f"({self.modification_type})>"
        )
