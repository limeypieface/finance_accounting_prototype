"""
Module: finance_kernel.models.subledger
Responsibility: ORM persistence for subledger entries, reconciliation matches,
    reconciliation failure reports, and subledger period close status.  These
    models form the entity-level derived index over the GL journal, enabling
    per-counterparty balance tracking and GL-to-SL reconciliation.
Architecture position: Kernel > Models.  May import from db/base.py only.
    MUST NOT import from services/, selectors/, domain/, or outer layers.

Invariants enforced:
    SL-G2  -- Idempotency.  UNIQUE(journal_entry_id, subledger_type, source_line_id)
              prevents duplicate subledger entries under retry.
    SL-G6  -- ReconciliationFailureReportModel is an append-only audit artifact.
              Once created, no UPDATE or DELETE is permitted (R10, ORM + DB).
    SL-G10 -- Currency normalization.  Currency values are uppercase ISO 4217,
              normalized at the ingestion boundary.
    R10    -- SubledgerEntryModel financial fields are immutable after posting
              (posted_at is set).  Only reconciliation_status and reconciled_amount
              may change post-posting (reconciliation lifecycle).
    F13    -- subledger_type is persisted as SubledgerType.value (canonical string).
    F16    -- GL linkage uses journal_entry_id / journal_line_id (canonical names).
    F17    -- period_code references FiscalPeriod.period_code (FK integrity).

Failure modes:
    - IntegrityError on duplicate (journal_entry_id, subledger_type, source_line_id)
      (SL-G2 idempotency).
    - ImmutabilityViolationError on UPDATE of financial fields on posted
      SubledgerEntryModel (R10).
    - ImmutabilityViolationError on any UPDATE/DELETE of ReconciliationFailureReportModel
      (SL-G6, R10).

Audit relevance:
    Subledger entries provide the per-counterparty view of financial activity
    (AP by vendor, AR by customer, etc.).  GL-to-SL reconciliation verifies
    that subledger aggregate balances match GL control account balances.
    ReconciliationFailureReportModel is a sacred audit artifact that records
    any GL/SL divergence detected during period close.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase, UUIDString


class ReconciliationStatus(str, Enum):
    """Reconciliation status for a subledger entry.

    Contract: Transitions follow OPEN -> PARTIAL -> RECONCILED (or WRITTEN_OFF).
    RECONCILED and WRITTEN_OFF are terminal states.
    """

    OPEN = "open"
    PARTIAL = "partial"
    RECONCILED = "reconciled"
    WRITTEN_OFF = "written_off"


class SubledgerEntryModel(TrackedBase):
    """
    Subledger entry -- entity-level derived index linked to the GL.

    Contract:
        Each SubledgerEntryModel is linked to exactly one JournalEntry via
        journal_entry_id (F16).  The combination (journal_entry_id,
        subledger_type, source_line_id) is unique (SL-G2 idempotency).
        Once posted_at is set, financial fields are immutable (R10); only
        reconciliation_status and reconciled_amount may change.

    Guarantees:
        - subledger_type is persisted as SubledgerType.value (F13).
        - currency is uppercase ISO 4217 (SL-G10).
        - Exactly one of debit_amount or credit_amount is set per entry.
        - journal_entry_id references a valid JournalEntry (FK).

    Non-goals:
        - This model does NOT enforce that exactly one of debit/credit is set
          at the ORM level; that is a service-layer validation.
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
    Match-level reconciliation history -- debit-to-credit entry pairing.

    Contract:
        Each row records one reconciliation match between a debit subledger
        entry and a credit subledger entry.  The reconciled_amount may be
        partial (is_full_match=False) or full (is_full_match=True).

    Guarantees:
        - debit_entry_id and credit_entry_id reference valid SubledgerEntryModel
          rows (FK constraints).
        - reconciled_amount is always positive.
        - reconciled_at records when the match was made.

    Non-goals:
        - This model is NOT the period-close audit artifact; that is
          ReconciliationFailureReportModel (F11, SL-G6).
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
    Period-close audit artifact for GL/SL reconciliation failures.

    Contract:
        ReconciliationFailureReportModel rows are sacred -- append-only, never
        updated or deleted (SL-G6, R10).  Each row records the GL control
        account balance vs. subledger aggregate balance delta for one
        subledger type in one fiscal period.

    Guarantees:
        - Immutable after creation (ORM listener + DB trigger, R10).
        - delta_amount = gl_control_balance - sl_aggregate_balance.
        - entity_deltas (JSON) provides per-entity breakdown of imbalances.
        - checked_at records when the reconciliation check was performed.

    Non-goals:
        - This model does NOT resolve the failure; resolution is a manual
          audit process or automated correction via reversal entries.
        - Distinct from SubledgerReconciliationModel which records match-level
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
    """Status of a subledger period.

    Contract: Transitions follow OPEN -> RECONCILING -> CLOSED.
    CLOSED is a terminal state (no reopening).
    """

    OPEN = "open"
    RECONCILING = "reconciling"
    CLOSED = "closed"


class SubledgerPeriodStatusModel(TrackedBase):
    """
    Subledger period close status tracker.

    Contract:
        Each row tracks the close state for one subledger type in one fiscal
        period.  The combination (subledger_type, period_code) is unique
        (uq_sl_period_status).

    Guarantees:
        - period_code references a valid FiscalPeriod.period_code (FK, F17).
        - status lifecycle: OPEN -> RECONCILING -> CLOSED.
        - reconciliation_report_id links to the failure report if reconciliation
          detected a GL/SL divergence (SL-G6).
        - closed_at and closed_by record the close action for audit.

    Non-goals:
        - This model does NOT enforce the close lifecycle at the ORM level;
          that is the responsibility of the period close orchestrator.
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
