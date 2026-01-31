"""
Subledger ORM Models.

Models for subledger entries and reconciliation tracking.

SubledgerEntryModel:
    Entity-level derived index linked to GL journal entries. Tracks
    individual subledger transactions with reconciliation status.

SubledgerReconciliationModel:
    Match-level reconciliation history pairing debit/credit entries.

ReconciliationFailureReportModel:
    Period-close audit artifact persisted when GL/SL balances diverge.

Invariants:
- SL-G2: Unique constraint (journal_entry_id, subledger_type, source_line_id)
  prevents duplicate subledger entries under retry.
- SL-G10: Currency values are uppercase ISO 4217, normalized at ingestion.
- F13: subledger_type is persisted as SubledgerType.value (canonical string).
- F16: GL linkage uses journal_entry_id / journal_line_id (canonical names).
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase, UUIDString


class ReconciliationStatus(str, Enum):
    """Reconciliation status for a subledger entry."""

    OPEN = "open"
    PARTIAL = "partial"
    RECONCILED = "reconciled"
    WRITTEN_OFF = "written_off"


class SubledgerEntryModel(TrackedBase):
    """
    Subledger entry ORM model.

    Entity-level derived index linked to GL journal entries via
    journal_entry_id / journal_line_id (F16 canonical names).

    The subledger_type is persisted as SubledgerType.value (F13).
    Round-trip mapping: SubledgerType → .value on write,
    SubledgerType(stored_str) on read.
    """

    __tablename__ = "subledger_entries"

    __table_args__ = (
        # SL-G2: Idempotency constraint — prevents duplicate entries under retry
        UniqueConstraint(
            "journal_entry_id",
            "subledger_type",
            "source_line_id",
            name="uq_sl_entry_idempotency",
        ),
        Index("idx_sl_subledger_type", "subledger_type"),
        Index("idx_sl_entity_id", "entity_id"),
        Index("idx_sl_journal_entry", "journal_entry_id"),
        Index("idx_sl_effective_date", "effective_date"),
        Index("idx_sl_reconciliation_status", "reconciliation_status"),
        Index(
            "idx_sl_entity_type_date",
            "entity_id",
            "subledger_type",
            "effective_date",
        ),
    )

    # Subledger type — persisted as SubledgerType.value (F13)
    subledger_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # Entity reference (vendor_id, customer_id, bank_id, etc.)
    entity_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # GL linkage (F16 canonical names)
    journal_entry_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("journal_entries.id"),
        nullable=False,
    )
    journal_line_id: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        nullable=True,
    )

    # Source document reference
    source_document_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    source_document_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    source_line_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # Amounts — exactly one of debit_amount or credit_amount must be set
    debit_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 9),
        nullable=True,
    )
    credit_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 9),
        nullable=True,
    )

    # Currency — uppercase ISO 4217 (SL-G10)
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    # Dates
    effective_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Reconciliation tracking
    reconciliation_status: Mapped[str] = mapped_column(
        String(20),
        default=ReconciliationStatus.OPEN.value,
        nullable=False,
    )
    reconciled_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 9),
        nullable=True,
    )

    # Description
    memo: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    reference: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    # Dimensions for multi-dimensional tracking
    dimensions: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )


class SubledgerReconciliationModel(TrackedBase):
    """
    Match-level reconciliation history.

    Records individual debit-to-credit entry matches. This is NOT the same
    as ReconciliationFailureReportModel, which records period-close audit
    artifacts (F11).
    """

    __tablename__ = "subledger_reconciliations"

    __table_args__ = (
        Index("idx_sl_recon_debit", "debit_entry_id"),
        Index("idx_sl_recon_credit", "credit_entry_id"),
    )

    debit_entry_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("subledger_entries.id"),
        nullable=False,
    )
    credit_entry_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("subledger_entries.id"),
        nullable=False,
    )
    reconciled_amount: Mapped[Decimal] = mapped_column(
        Numeric(38, 9),
        nullable=False,
    )
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    is_full_match: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )
    reconciled_by: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )


class ReconciliationFailureReportModel(TrackedBase):
    """
    Period-close audit artifact (F11, SL-G6).

    Persisted when enforce_on_close=True reconciliation fails. Records the
    GL/SL balance delta for audit review. Referenced by
    SubledgerPeriodStatusModel.reconciliation_report_id (SL-Phase 8).

    Distinct from SubledgerReconciliationModel which records match-level
    entry pairs.
    """

    __tablename__ = "reconciliation_failure_reports"

    __table_args__ = (
        Index("idx_recon_fail_type_period", "subledger_type", "period_code"),
    )

    subledger_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    period_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    gl_control_balance: Mapped[Decimal] = mapped_column(
        Numeric(38, 9),
        nullable=False,
    )
    sl_aggregate_balance: Mapped[Decimal] = mapped_column(
        Numeric(38, 9),
        nullable=False,
    )
    delta_amount: Mapped[Decimal] = mapped_column(
        Numeric(38, 9),
        nullable=False,
    )
    # Currency — uppercase ISO 4217 (SL-G10)
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )
    # Per-entity breakdown of imbalances
    entity_deltas: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class SubledgerPeriodStatus(str, Enum):
    """Status of a subledger period."""

    OPEN = "open"
    RECONCILING = "reconciling"
    CLOSED = "closed"


class SubledgerPeriodStatusModel(TrackedBase):
    """
    Subledger period close status (SL-Phase 8, SL-G6).

    Tracks close state per subledger type per fiscal period. Makes close
    state auditable and queryable rather than implied by absence of failures.

    F17: period_code format is defined by FiscalPeriod as the single
    source of truth. The FK constraint enforces referential integrity.
    """

    __tablename__ = "subledger_period_status"

    __table_args__ = (
        UniqueConstraint(
            "subledger_type",
            "period_code",
            name="uq_sl_period_status",
        ),
        Index("idx_sl_period_type", "subledger_type"),
        Index("idx_sl_period_code", "period_code"),
    )

    subledger_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    period_code: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("fiscal_periods.period_code"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default=SubledgerPeriodStatus.OPEN.value,
        nullable=False,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    closed_by: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        nullable=True,
    )
    reconciliation_report_id: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        ForeignKey("reconciliation_failure_reports.id"),
        nullable=True,
    )
