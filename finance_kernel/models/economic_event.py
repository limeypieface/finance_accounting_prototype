"""
Economic Event model.

The EconomicEvent represents the interpreted meaning of a BusinessEvent.
It captures:
- What economic activity occurred (economic_type)
- The quantity and value
- The dimensions for reporting
- Which profile interpreted it
- Reference snapshots for deterministic replay

Invariant L4: Replay using stored snapshots produces identical results.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, Index, Numeric, String
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import Base, UUIDString


class EconomicEvent(Base):
    """
    Immutable interpreted fact.

    Represents the economic meaning derived from a BusinessEvent
    by applying an AccountingPolicy.
    """

    __tablename__ = "economic_events"

    __table_args__ = (
        Index("idx_econ_event_source", "source_event_id"),
        Index("idx_econ_event_type", "economic_type"),
        Index("idx_econ_event_effective", "effective_date"),
        Index("idx_econ_event_profile", "profile_id", "profile_version"),
    )

    # Source event reference
    source_event_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        nullable=False,
    )

    # Economic classification
    economic_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # Quantity (if applicable)
    quantity: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 9),
        nullable=True,
    )

    # Dimensions for reporting (JSON dict of dimension_code -> value)
    dimensions: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Accounting date
    effective_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # Accounting basis information
    accounting_basis_used: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    accounting_basis_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Profile that created this economic event
    profile_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    profile_version: Mapped[int] = mapped_column(
        nullable=False,
    )

    profile_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Trace ID for audit trail
    trace_id: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        nullable=True,
    )

    # Valuation fields (versioned independently)
    value: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 9),
        nullable=True,
    )

    currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )

    valuation_model_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    valuation_model_version: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    # Reference snapshot fields (for audit replay - L4/R21)
    coa_version: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    dimension_schema_version: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    currency_registry_version: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    fx_policy_version: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    # Hash chain for tamper evidence
    prev_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<EconomicEvent {self.economic_type} from event {self.source_event_id}>"
