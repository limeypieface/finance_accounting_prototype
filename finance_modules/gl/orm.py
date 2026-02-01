"""
Module: finance_modules.gl.orm
Responsibility:
    SQLAlchemy ORM persistence models for the General Ledger module.
    Maps frozen dataclass DTOs from ``finance_modules.gl.models`` to
    relational tables for durable storage.

Architecture position:
    **Modules layer** -- ORM models that inherit from ``TrackedBase``
    (kernel DB base).  These models persist GL-specific entities that are
    NOT already covered by kernel models (Account, JournalEntry,
    JournalLine, FiscalPeriod are in the kernel).

    GL ORM models cover: recurring entry templates, journal batches,
    account reconciliations, period close tasks, translation results,
    and revaluation results.

Invariants enforced:
    - All monetary fields use Decimal (maps to Numeric(38,9) via TrackedBase).
    - Enum fields stored as String(50) for safe serialization.
    - TrackedBase provides id, created_at, updated_at, created_by_id,
      updated_by_id automatically.

Failure modes:
    - IntegrityError on duplicate unique constraints.
    - ForeignKey violation on invalid parent references.

Audit relevance:
    - RecurringEntryModel and RecurringLineModel support recurring entry
      template audit.
    - AccountReconciliationModel tracks period-end reconciliation sign-offs.
    - PeriodCloseTaskModel tracks close checklist compliance.
    - TranslationResultModel and RevaluationResultModel support
      multi-currency disclosure requirements.
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
# Recurring Entry Template
# =============================================================================


class RecurringEntryModel(TrackedBase):
    """
    Persistent recurring journal entry template.

    Contract:
        Each RecurringEntryModel defines a reusable journal entry that can be
        generated on a schedule.  Lines are stored via RecurringLineModel
        (one-to-many relationship).

    Guarantees:
        - ``name`` is unique within the system (uq_recurring_entry_name).
        - ``frequency`` is one of: monthly, quarterly, annually.
        - TrackedBase provides id, created_at, updated_at, created_by_id.

    Non-goals:
        - Scheduling execution is handled by the service layer, not the model.
    """

    __tablename__ = "gl_recurring_entries"

    __table_args__ = (
        UniqueConstraint("name", name="uq_gl_recurring_entry_name"),
        Index("idx_gl_recurring_entry_active", "is_active"),
        Index("idx_gl_recurring_entry_frequency", "frequency"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str] = mapped_column(String(50), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_generated_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Child lines
    lines: Mapped[list["RecurringLineModel"]] = relationship(
        "RecurringLineModel",
        back_populates="recurring_entry",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.gl.models import RecurringEntry

        return RecurringEntry(
            id=self.id,
            name=self.name,
            description=self.description,
            frequency=self.frequency,
            start_date=self.start_date,
            end_date=self.end_date,
            last_generated_date=self.last_generated_date,
            is_active=self.is_active,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "RecurringEntryModel":
        return cls(
            id=dto.id,
            name=dto.name,
            description=dto.description,
            frequency=dto.frequency,
            start_date=dto.start_date,
            end_date=dto.end_date,
            last_generated_date=dto.last_generated_date,
            is_active=dto.is_active,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<RecurringEntryModel {self.name} ({self.frequency})>"


# =============================================================================
# Recurring Line (child of RecurringEntry)
# =============================================================================


class RecurringLineModel(TrackedBase):
    """
    A single debit/credit line within a recurring entry template.

    Contract:
        Each RecurringLineModel belongs to exactly one RecurringEntryModel.
        Together they define the template lines that are replicated each
        time the recurring entry is generated.

    Guarantees:
        - ``recurring_entry_id`` references a valid RecurringEntryModel.
        - ``account_code`` stores a GL account code (no FK to kernel Account).
        - ``side`` is "debit" or "credit".
        - ``amount`` is Decimal (Numeric(38,9)).
    """

    __tablename__ = "gl_recurring_lines"

    __table_args__ = (
        Index("idx_gl_recurring_line_entry", "recurring_entry_id"),
    )

    recurring_entry_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("gl_recurring_entries.id"),
        nullable=False,
    )
    account_code: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # debit or credit
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Parent relationship
    recurring_entry: Mapped["RecurringEntryModel"] = relationship(
        "RecurringEntryModel",
        back_populates="lines",
    )

    def __repr__(self) -> str:
        return f"<RecurringLineModel {self.account_code} {self.side} {self.amount}>"


# =============================================================================
# Journal Batch
# =============================================================================


class JournalBatchModel(TrackedBase):
    """
    A batch of journal entries for approval/posting workflow.

    Contract:
        Groups multiple journal entries for batch approval and posting.
        Status tracks the batch through the approval workflow.

    Guarantees:
        - ``batch_number`` is unique (uq_gl_batch_number).
        - ``status`` is one of: open, submitted, approved, posted, rejected.
        - Debit/credit totals are Decimal (Numeric(38,9)).
    """

    __tablename__ = "gl_journal_batches"

    __table_args__ = (
        UniqueConstraint("batch_number", name="uq_gl_batch_number"),
        Index("idx_gl_batch_status", "status"),
        Index("idx_gl_batch_source", "source"),
        Index("idx_gl_batch_date", "batch_date"),
    )

    batch_number: Mapped[str] = mapped_column(String(50), nullable=False)
    batch_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    entry_count: Mapped[int] = mapped_column(default=0)
    total_debits: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    total_credits: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(50), default="open")
    approved_by_id: Mapped[UUID | None] = mapped_column(
        UUIDString(), nullable=True,
    )

    def to_dto(self):
        from finance_modules.gl.models import BatchStatus, JournalBatch

        return JournalBatch(
            id=self.id,
            batch_number=self.batch_number,
            batch_date=self.batch_date,
            description=self.description,
            source=self.source,
            entry_count=self.entry_count,
            total_debits=self.total_debits,
            total_credits=self.total_credits,
            status=BatchStatus(self.status),
            created_by=self.created_by_id,
            approved_by=self.approved_by_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "JournalBatchModel":
        return cls(
            id=dto.id,
            batch_number=dto.batch_number,
            batch_date=dto.batch_date,
            description=dto.description,
            source=dto.source,
            entry_count=dto.entry_count,
            total_debits=dto.total_debits,
            total_credits=dto.total_credits,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            approved_by_id=dto.approved_by,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<JournalBatchModel {self.batch_number} ({self.status})>"


# =============================================================================
# Account Reconciliation
# =============================================================================


class AccountReconciliationModel(TrackedBase):
    """
    Period-end account reconciliation sign-off record.

    Contract:
        Records that a specific account balance has been verified for a
        given period by a specific actor.  Supports period-close checklist
        compliance.

    Guarantees:
        - (account_id, period) is unique (uq_gl_recon_account_period).
        - ``status`` is one of: pending, reconciled, exception.
        - ``balance_confirmed`` is Decimal (Numeric(38,9)).
    """

    __tablename__ = "gl_account_reconciliations"

    __table_args__ = (
        UniqueConstraint(
            "account_id", "period", name="uq_gl_recon_account_period",
        ),
        Index("idx_gl_recon_status", "status"),
        Index("idx_gl_recon_period", "period"),
        Index("idx_gl_recon_account", "account_id"),
    )

    account_id: Mapped[UUID] = mapped_column(UUIDString(), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    reconciled_date: Mapped[date] = mapped_column(Date, nullable=False)
    reconciled_by_id: Mapped[UUID] = mapped_column(UUIDString(), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    balance_confirmed: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    def to_dto(self):
        from finance_modules.gl.models import (
            AccountReconciliation,
            ReconciliationStatus,
        )

        return AccountReconciliation(
            id=self.id,
            account_id=self.account_id,
            period=self.period,
            reconciled_date=self.reconciled_date,
            reconciled_by=self.reconciled_by_id,
            status=ReconciliationStatus(self.status),
            notes=self.notes,
            balance_confirmed=self.balance_confirmed,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "AccountReconciliationModel":
        return cls(
            id=dto.id,
            account_id=dto.account_id,
            period=dto.period,
            reconciled_date=dto.reconciled_date,
            reconciled_by_id=dto.reconciled_by,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            notes=dto.notes,
            balance_confirmed=dto.balance_confirmed,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<AccountReconciliationModel {self.period} ({self.status})>"


# =============================================================================
# Period Close Task
# =============================================================================


class PeriodCloseTaskModel(TrackedBase):
    """
    Individual period-close checklist item.

    Contract:
        Tracks each task in the period-close checklist (e.g., reconcile bank,
        post depreciation, accrue payroll).  Each task belongs to a period and
        module.

    Guarantees:
        - (period, task_name, module) is unique (uq_gl_close_task).
        - ``status`` is one of: pending, in_progress, completed, skipped.
    """

    __tablename__ = "gl_period_close_tasks"

    __table_args__ = (
        UniqueConstraint(
            "period", "task_name", "module", name="uq_gl_close_task",
        ),
        Index("idx_gl_close_task_period", "period"),
        Index("idx_gl_close_task_status", "status"),
        Index("idx_gl_close_task_module", "module"),
    )

    period: Mapped[str] = mapped_column(String(20), nullable=False)
    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    completed_by_id: Mapped[UUID | None] = mapped_column(
        UUIDString(), nullable=True,
    )
    completed_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    def to_dto(self):
        from finance_modules.gl.models import CloseTaskStatus, PeriodCloseTask

        return PeriodCloseTask(
            id=self.id,
            period=self.period,
            task_name=self.task_name,
            module=self.module,
            status=CloseTaskStatus(self.status),
            completed_by=self.completed_by_id,
            completed_date=self.completed_date,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "PeriodCloseTaskModel":
        return cls(
            id=dto.id,
            period=dto.period,
            task_name=dto.task_name,
            module=dto.module,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            completed_by_id=dto.completed_by,
            completed_date=dto.completed_date,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<PeriodCloseTaskModel {self.period}/{self.task_name} ({self.status})>"


# =============================================================================
# Translation Result
# =============================================================================


class TranslationResultModel(TrackedBase):
    """
    Result of a currency translation calculation per ASC 830.

    Contract:
        Records the outcome of translating an entity's balances from one
        currency to another for a given period.  Supports multi-currency
        disclosure requirements.

    Guarantees:
        - (entity_id, period, source_currency, target_currency) is unique.
        - ``method`` is one of: current_rate, temporal.
        - All monetary fields are Decimal (Numeric(38,9)).
    """

    __tablename__ = "gl_translation_results"

    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "period",
            "source_currency",
            "target_currency",
            name="uq_gl_translation_entity_period_ccy",
        ),
        Index("idx_gl_translation_period", "period"),
        Index("idx_gl_translation_entity", "entity_id"),
    )

    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    target_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    method: Mapped[str] = mapped_column(String(50), nullable=False)
    translated_amount: Mapped[Decimal] = mapped_column(nullable=False)
    cta_amount: Mapped[Decimal] = mapped_column(nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(nullable=False)

    def to_dto(self):
        from finance_modules.gl.models import TranslationMethod, TranslationResult

        return TranslationResult(
            id=self.id,
            entity_id=self.entity_id,
            period=self.period,
            source_currency=self.source_currency,
            target_currency=self.target_currency,
            method=TranslationMethod(self.method),
            translated_amount=self.translated_amount,
            cta_amount=self.cta_amount,
            exchange_rate=self.exchange_rate,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TranslationResultModel":
        return cls(
            id=dto.id,
            entity_id=dto.entity_id,
            period=dto.period,
            source_currency=dto.source_currency,
            target_currency=dto.target_currency,
            method=dto.method.value if hasattr(dto.method, "value") else dto.method,
            translated_amount=dto.translated_amount,
            cta_amount=dto.cta_amount,
            exchange_rate=dto.exchange_rate,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<TranslationResultModel {self.entity_id} {self.period} "
            f"{self.source_currency}->{self.target_currency}>"
        )


# =============================================================================
# Revaluation Result
# =============================================================================


class RevaluationResultModel(TrackedBase):
    """
    Result of a period-end FX revaluation run.

    Contract:
        Summarizes the outcome of revaluing foreign-currency-denominated
        balances at period end.  Records total gains, losses, and entries
        posted.

    Guarantees:
        - (period, revaluation_date) is unique (uq_gl_reval_period_date).
        - All monetary fields are Decimal (Numeric(38,9)).
    """

    __tablename__ = "gl_revaluation_results"

    __table_args__ = (
        UniqueConstraint(
            "period", "revaluation_date", name="uq_gl_reval_period_date",
        ),
        Index("idx_gl_reval_period", "period"),
    )

    period: Mapped[str] = mapped_column(String(20), nullable=False)
    revaluation_date: Mapped[date] = mapped_column(Date, nullable=False)
    currencies_processed: Mapped[int] = mapped_column(default=0)
    total_gain: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    total_loss: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    entries_posted: Mapped[int] = mapped_column(default=0)

    def to_dto(self):
        from finance_modules.gl.models import RevaluationResult

        return RevaluationResult(
            id=self.id,
            period=self.period,
            revaluation_date=self.revaluation_date,
            currencies_processed=self.currencies_processed,
            total_gain=self.total_gain,
            total_loss=self.total_loss,
            entries_posted=self.entries_posted,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "RevaluationResultModel":
        return cls(
            id=dto.id,
            period=dto.period,
            revaluation_date=dto.revaluation_date,
            currencies_processed=dto.currencies_processed,
            total_gain=dto.total_gain,
            total_loss=dto.total_loss,
            entries_posted=dto.entries_posted,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<RevaluationResultModel {self.period} "
            f"gain={self.total_gain} loss={self.total_loss}>"
        )
