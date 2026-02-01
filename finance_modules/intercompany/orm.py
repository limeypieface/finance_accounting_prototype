"""
SQLAlchemy ORM persistence models for the Intercompany module.

Responsibility
--------------
Provide database-backed persistence for intercompany accounting entities:
intercompany transactions, intercompany agreements, and intercompany
settlements.  Transient computation DTOs (``EliminationRule``,
``ConsolidationResult``, ``ICReconciliationResult``) are derived at
consolidation time and do not require ORM persistence.

Architecture position
---------------------
**Modules layer** -- ORM models consumed by ``IntercompanyService`` for
persistence.  Inherits from ``TrackedBase`` (kernel db layer).

Invariants enforced
-------------------
* All monetary fields use ``Decimal`` (Numeric(38,9)) -- NEVER float.
* Entity identifiers stored as String(100) -- intercompany entities are
  typically identified by legal entity codes, not kernel UUIDs.
* ``IntercompanyTransactionModel`` must reference either an agreement
  or provide explicit from/to entity codes.

Audit relevance
---------------
* ``IntercompanyAgreementModel`` governs the terms of IC transactions
  between two legal entities.
* ``IntercompanyTransactionModel`` records each IC transfer for
  consolidation elimination.
* ``IntercompanySettlementModel`` records cash settlements that clear
  IC balances.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase


# ---------------------------------------------------------------------------
# IntercompanyAgreementModel
# ---------------------------------------------------------------------------


class IntercompanyAgreementModel(TrackedBase):
    """
    An agreement governing intercompany transactions between two entities.

    Maps to the ``IntercompanyAgreement`` DTO in
    ``finance_modules.intercompany.models``.

    Guarantees:
        - (entity_a, entity_b, agreement_type) is unique per effective period.
        - ``markup_rate`` defaults to zero (at-cost transfers).
        - ``effective_from`` and ``effective_to`` define the agreement window.
    """

    __tablename__ = "intercompany_agreements"

    __table_args__ = (
        UniqueConstraint(
            "entity_a", "entity_b", "agreement_type",
            name="uq_ic_agreement_entities_type",
        ),
        Index("idx_ic_agreement_entity_a", "entity_a"),
        Index("idx_ic_agreement_entity_b", "entity_b"),
    )

    entity_a: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_b: Mapped[str] = mapped_column(String(100), nullable=False)
    agreement_type: Mapped[str] = mapped_column(String(50), nullable=False, default="transfer")
    markup_rate: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Relationships
    transactions: Mapped[list["IntercompanyTransactionModel"]] = relationship(
        "IntercompanyTransactionModel",
        back_populates="agreement",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.intercompany.models import IntercompanyAgreement

        return IntercompanyAgreement(
            id=self.id,
            entity_a=self.entity_a,
            entity_b=self.entity_b,
            agreement_type=self.agreement_type,
            markup_rate=self.markup_rate,
            currency=self.currency,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "IntercompanyAgreementModel":
        return cls(
            id=dto.id,
            entity_a=dto.entity_a,
            entity_b=dto.entity_b,
            agreement_type=dto.agreement_type,
            markup_rate=dto.markup_rate,
            currency=dto.currency,
            effective_from=dto.effective_from,
            effective_to=dto.effective_to,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<IntercompanyAgreementModel {self.entity_a}<->{self.entity_b} [{self.agreement_type}]>"


# ---------------------------------------------------------------------------
# IntercompanyTransactionModel
# ---------------------------------------------------------------------------


class IntercompanyTransactionModel(TrackedBase):
    """
    A single intercompany transaction between two legal entities.

    Maps to the ``ICTransaction`` DTO in
    ``finance_modules.intercompany.models``.

    Guarantees:
        - Records from_entity, to_entity, amount, and transaction_date.
        - Optionally references an ``IntercompanyAgreementModel``.
    """

    __tablename__ = "intercompany_transactions"

    __table_args__ = (
        Index("idx_ic_txn_agreement", "agreement_id"),
        Index("idx_ic_txn_from", "from_entity"),
        Index("idx_ic_txn_to", "to_entity"),
        Index("idx_ic_txn_date", "transaction_date"),
    )

    agreement_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("intercompany_agreements.id"), nullable=True,
    )
    from_entity: Mapped[str] = mapped_column(String(100), nullable=False)
    to_entity: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_event_id: Mapped[UUID | None]
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")

    # Parent relationship
    agreement: Mapped["IntercompanyAgreementModel | None"] = relationship(
        "IntercompanyAgreementModel",
        back_populates="transactions",
    )

    def to_dto(self):
        from finance_modules.intercompany.models import ICTransaction

        return ICTransaction(
            id=self.id,
            agreement_id=self.agreement_id,
            from_entity=self.from_entity,
            to_entity=self.to_entity,
            amount=self.amount,
            currency=self.currency,
            transaction_date=self.transaction_date,
            description=self.description or "",
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "IntercompanyTransactionModel":
        return cls(
            id=dto.id,
            agreement_id=dto.agreement_id,
            from_entity=dto.from_entity,
            to_entity=dto.to_entity,
            amount=dto.amount,
            currency=dto.currency,
            transaction_date=dto.transaction_date,
            description=dto.description,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<IntercompanyTransactionModel {self.from_entity}->{self.to_entity} "
            f"{self.amount} {self.currency}>"
        )


# ---------------------------------------------------------------------------
# IntercompanySettlementModel
# ---------------------------------------------------------------------------


class IntercompanySettlementModel(TrackedBase):
    """
    A cash settlement that clears intercompany balances between entities.

    Guarantees:
        - Records the paying and receiving entities.
        - Links to specific IC transactions being settled (via settlement_ref).
        - Tracks settlement amount, date, and status.
    """

    __tablename__ = "intercompany_settlements"

    __table_args__ = (
        Index("idx_ic_settlement_from", "from_entity"),
        Index("idx_ic_settlement_to", "to_entity"),
        Index("idx_ic_settlement_date", "settlement_date"),
        Index("idx_ic_settlement_status", "status"),
    )

    from_entity: Mapped[str] = mapped_column(String(100), nullable=False)
    to_entity: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)
    settlement_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    settled_by: Mapped[UUID | None]

    def to_dto(self) -> dict:
        return {
            "id": self.id,
            "from_entity": self.from_entity,
            "to_entity": self.to_entity,
            "amount": self.amount,
            "currency": self.currency,
            "settlement_date": self.settlement_date,
            "settlement_reference": self.settlement_reference,
            "status": self.status,
            "payment_method": self.payment_method,
            "period": self.period,
            "settled_by": self.settled_by,
        }

    @classmethod
    def from_dto(cls, dto: dict, created_by_id: UUID) -> "IntercompanySettlementModel":
        return cls(
            id=dto.get("id"),
            from_entity=dto["from_entity"],
            to_entity=dto["to_entity"],
            amount=dto["amount"],
            currency=dto.get("currency", "USD"),
            settlement_date=dto["settlement_date"],
            settlement_reference=dto.get("settlement_reference"),
            status=dto.get("status", "pending"),
            payment_method=dto.get("payment_method"),
            period=dto["period"],
            settled_by=dto.get("settled_by"),
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<IntercompanySettlementModel {self.from_entity}->{self.to_entity} "
            f"{self.amount} [{self.status}]>"
        )
