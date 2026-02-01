"""
SQLAlchemy ORM persistence models for the Government Contracts module.

Responsibility
--------------
Provide database-backed persistence for government contract accounting
entities: contract deliverables, milestones, billings, and funding records.
Note that the core ``Contract`` and ``ContractLineItem`` ORM models live
in ``finance_kernel.models.contract`` -- this module adds finance-specific
tracking artifacts for DCAA compliance.

Architecture position
---------------------
**Modules layer** -- ORM models consumed by ``GovernmentContractsService``
for persistence.  Inherits from ``TrackedBase`` (kernel db layer).

Invariants enforced
-------------------
* All monetary fields use ``Decimal`` (Numeric(38,9)) -- NEVER float.
* Enum fields stored as String(50) for readability and portability.
* ``contract_id`` references the kernel ``contracts`` table via FK.

Audit relevance
---------------
* ``ContractDeliverableModel`` tracks CDRL/deliverable status for contract
  compliance.
* ``ContractMilestoneModel`` supports progress billing and earned value.
* ``ContractBillingModel`` records all billing events for cost-reimbursement
  and fixed-price contracts.
* ``ContractFundingModel`` tracks incremental funding actions (obligations)
  against a contract.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase

# ---------------------------------------------------------------------------
# ContractDeliverableModel
# ---------------------------------------------------------------------------


class ContractDeliverableModel(TrackedBase):
    """
    A contract deliverable (CDRL or SOW deliverable).

    Guarantees:
        - Belongs to a kernel ``Contract`` via FK.
        - (contract_id, deliverable_number) is unique.
        - Tracks status, due date, and acceptance.
    """

    __tablename__ = "contracts_deliverables"

    __table_args__ = (
        UniqueConstraint(
            "contract_id", "deliverable_number",
            name="uq_contract_deliverable_number",
        ),
        Index("idx_deliverable_contract", "contract_id"),
        Index("idx_deliverable_status", "status"),
        Index("idx_deliverable_due_date", "due_date"),
    )

    contract_id: Mapped[UUID] = mapped_column(ForeignKey("contracts.id"), nullable=False)
    deliverable_number: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    submitted_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    accepted_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    accepted_by: Mapped[UUID | None]
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def to_dto(self) -> dict:
        return {
            "id": self.id,
            "contract_id": self.contract_id,
            "deliverable_number": self.deliverable_number,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "due_date": self.due_date,
            "submitted_date": self.submitted_date,
            "accepted_date": self.accepted_date,
            "accepted_by": self.accepted_by,
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dto(cls, dto: dict, created_by_id: UUID) -> "ContractDeliverableModel":
        return cls(
            id=dto.get("id"),
            contract_id=dto["contract_id"],
            deliverable_number=dto["deliverable_number"],
            title=dto["title"],
            description=dto.get("description"),
            status=dto.get("status", "pending"),
            due_date=dto.get("due_date"),
            submitted_date=dto.get("submitted_date"),
            accepted_date=dto.get("accepted_date"),
            accepted_by=dto.get("accepted_by"),
            rejection_reason=dto.get("rejection_reason"),
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<ContractDeliverableModel {self.deliverable_number}: {self.title[:30]}>"


# ---------------------------------------------------------------------------
# ContractMilestoneModel
# ---------------------------------------------------------------------------


class ContractMilestoneModel(TrackedBase):
    """
    A contract milestone for progress billing and earned value tracking.

    Maps to the ``Milestone`` DTO in ``finance_modules.project.models``
    when associated with a project, or stands alone for contract-level
    milestones.

    Guarantees:
        - Belongs to a kernel ``Contract`` via FK.
        - (contract_id, name) is unique.
        - Tracks completion percentage and billing status.
    """

    __tablename__ = "contracts_milestones"

    __table_args__ = (
        UniqueConstraint("contract_id", "name", name="uq_contract_milestone_name"),
        Index("idx_milestone_contract", "contract_id"),
        Index("idx_milestone_billed", "is_billed"),
    )

    contract_id: Mapped[UUID] = mapped_column(ForeignKey("contracts.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    completion_pct: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    is_billed: Mapped[bool] = mapped_column(default=False)
    billed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    def to_dto(self) -> dict:
        return {
            "id": self.id,
            "contract_id": self.contract_id,
            "name": self.name,
            "description": self.description,
            "amount": self.amount,
            "completion_pct": self.completion_pct,
            "is_billed": self.is_billed,
            "billed_date": self.billed_date,
            "due_date": self.due_date,
            "currency": self.currency,
        }

    @classmethod
    def from_dto(cls, dto: dict, created_by_id: UUID) -> "ContractMilestoneModel":
        return cls(
            id=dto.get("id"),
            contract_id=dto["contract_id"],
            name=dto["name"],
            description=dto.get("description"),
            amount=dto.get("amount", Decimal("0")),
            completion_pct=dto.get("completion_pct", Decimal("0")),
            is_billed=dto.get("is_billed", False),
            billed_date=dto.get("billed_date"),
            due_date=dto.get("due_date"),
            currency=dto.get("currency", "USD"),
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<ContractMilestoneModel {self.name} {self.completion_pct}%>"


# ---------------------------------------------------------------------------
# ContractBillingModel
# ---------------------------------------------------------------------------


class ContractBillingModel(TrackedBase):
    """
    A billing event against a contract (progress billing, cost voucher,
    milestone billing, or final invoice).

    Guarantees:
        - Belongs to a kernel ``Contract`` via FK.
        - Records billing type, period, amounts, and approval status.
    """

    __tablename__ = "contracts_billings"

    __table_args__ = (
        Index("idx_billing_contract", "contract_id"),
        Index("idx_billing_period", "billing_period"),
        Index("idx_billing_status", "status"),
        Index("idx_billing_date", "billing_date"),
    )

    contract_id: Mapped[UUID] = mapped_column(ForeignKey("contracts.id"), nullable=False)
    billing_number: Mapped[str] = mapped_column(String(50), nullable=False)
    billing_type: Mapped[str] = mapped_column(String(50), nullable=False)
    billing_period: Mapped[str] = mapped_column(String(20), nullable=False)
    billing_date: Mapped[date] = mapped_column(Date, nullable=False)
    direct_costs: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    indirect_costs: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    fee_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    approved_by: Mapped[UUID | None]
    approved_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    milestone_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contracts_milestones.id"), nullable=True,
    )

    def to_dto(self) -> dict:
        return {
            "id": self.id,
            "contract_id": self.contract_id,
            "billing_number": self.billing_number,
            "billing_type": self.billing_type,
            "billing_period": self.billing_period,
            "billing_date": self.billing_date,
            "direct_costs": self.direct_costs,
            "indirect_costs": self.indirect_costs,
            "fee_amount": self.fee_amount,
            "total_amount": self.total_amount,
            "currency": self.currency,
            "status": self.status,
            "approved_by": self.approved_by,
            "approved_date": self.approved_date,
            "milestone_id": self.milestone_id,
        }

    @classmethod
    def from_dto(cls, dto: dict, created_by_id: UUID) -> "ContractBillingModel":
        return cls(
            id=dto.get("id"),
            contract_id=dto["contract_id"],
            billing_number=dto["billing_number"],
            billing_type=dto["billing_type"],
            billing_period=dto["billing_period"],
            billing_date=dto["billing_date"],
            direct_costs=dto.get("direct_costs", Decimal("0")),
            indirect_costs=dto.get("indirect_costs", Decimal("0")),
            fee_amount=dto.get("fee_amount", Decimal("0")),
            total_amount=dto.get("total_amount", Decimal("0")),
            currency=dto.get("currency", "USD"),
            status=dto.get("status", "draft"),
            approved_by=dto.get("approved_by"),
            approved_date=dto.get("approved_date"),
            milestone_id=dto.get("milestone_id"),
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<ContractBillingModel {self.billing_number} {self.billing_type} {self.total_amount}>"


# ---------------------------------------------------------------------------
# ContractFundingModel
# ---------------------------------------------------------------------------


class ContractFundingModel(TrackedBase):
    """
    An incremental funding action (obligation) against a contract.

    Tracks funding modifications, obligated amounts, and the authorizing
    document reference for government contract compliance.

    Guarantees:
        - Belongs to a kernel ``Contract`` via FK.
        - Records the funding action number, amount, and effective date.
    """

    __tablename__ = "contracts_funding"

    __table_args__ = (
        UniqueConstraint(
            "contract_id", "funding_action_number",
            name="uq_contract_funding_action",
        ),
        Index("idx_funding_contract", "contract_id"),
        Index("idx_funding_date", "effective_date"),
    )

    contract_id: Mapped[UUID] = mapped_column(ForeignKey("contracts.id"), nullable=False)
    funding_action_number: Mapped[str] = mapped_column(String(50), nullable=False)
    funding_type: Mapped[str] = mapped_column(String(50), nullable=False, default="incremental")
    amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    cumulative_funded: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    modification_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    document_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    authorized_by: Mapped[UUID | None]

    def to_dto(self) -> dict:
        return {
            "id": self.id,
            "contract_id": self.contract_id,
            "funding_action_number": self.funding_action_number,
            "funding_type": self.funding_type,
            "amount": self.amount,
            "cumulative_funded": self.cumulative_funded,
            "currency": self.currency,
            "effective_date": self.effective_date,
            "modification_number": self.modification_number,
            "document_reference": self.document_reference,
            "authorized_by": self.authorized_by,
        }

    @classmethod
    def from_dto(cls, dto: dict, created_by_id: UUID) -> "ContractFundingModel":
        return cls(
            id=dto.get("id"),
            contract_id=dto["contract_id"],
            funding_action_number=dto["funding_action_number"],
            funding_type=dto.get("funding_type", "incremental"),
            amount=dto.get("amount", Decimal("0")),
            cumulative_funded=dto.get("cumulative_funded", Decimal("0")),
            currency=dto.get("currency", "USD"),
            effective_date=dto["effective_date"],
            modification_number=dto.get("modification_number"),
            document_reference=dto.get("document_reference"),
            authorized_by=dto.get("authorized_by"),
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<ContractFundingModel {self.funding_action_number} {self.amount}>"
