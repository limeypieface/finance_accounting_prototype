"""
Module: finance_kernel.models.economic_event
Responsibility: ORM persistence for the interpreted economic meaning of a
    business event.  Each EconomicEvent is derived from exactly one source
    Event by applying an AccountingPolicy, recording the economic classification,
    valuation, and reference snapshots needed for deterministic replay.
Architecture position: Kernel > Models.  May import from db/base.py only.
    MUST NOT import from services/, selectors/, domain/, or outer layers.

Invariants enforced:
    L4  -- Replay determinism.  Reference snapshot fields (coa_version,
           dimension_schema_version, currency_registry_version, fx_policy_version)
           are recorded at interpretation time so that replaying the same event
           against the same reference data versions produces identical results.
    R10 -- Immutability.  EconomicEvent rows are append-only; once created,
           no fields may be updated or deleted.
    R21 -- Reference snapshot determinism (mirrored from JournalEntry).  The
           snapshot versions on EconomicEvent are the economic-layer complement
           to the posting-layer versions on JournalEntry.

Failure modes:
    - ImmutabilityViolationError on any UPDATE or DELETE attempt (R10).
    - IntegrityError if source_event_id does not exist in events table.

Audit relevance:
    EconomicEvent provides the interpretive bridge between raw business events
    and their journal postings.  The profile_id/profile_version/profile_hash
    fields record exactly which accounting policy version produced this
    interpretation, enabling full audit traceability and replay verification.
    The optional hash chain (prev_hash, hash) supports tamper detection.
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
    Immutable interpreted economic fact.

    Contract:
        Each EconomicEvent is derived from exactly one source Event by
        applying an EconomicProfile (identified by profile_id + profile_version).
        Once created, the row is immutable -- corrections produce new events,
        not mutations.

    Guarantees:
        - source_event_id always references the originating Event (NOT NULL).
        - economic_type classifies the economic activity for downstream dispatch.
        - profile_id + profile_version + profile_hash fully identify the
          interpretation logic version (L4 replay determinism).
        - Reference snapshot fields enable deterministic replay against the
          exact reference data versions used at interpretation time (L4/R21).

    Non-goals:
        - This model does NOT enforce profile existence; the posting pipeline
          validates profile registration before creating EconomicEvent rows.
        - This model does NOT validate economic_type against a closed set;
          new types are added via new profiles (R14/R15).
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

    # ==========================================================================
    # INVARIANT L4/R21: Reference Snapshot Determinism
    # These version fields record the exact reference data state at
    # interpretation time.  Replay with these versions MUST produce
    # identical results.
    # ==========================================================================
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
