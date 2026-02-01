"""
Module: finance_kernel.models.journal
Responsibility: ORM persistence for journal entries and journal lines -- the
    single source of financial truth in this system.
Architecture position: Kernel > Models.  May import from db/base.py only.
    MUST NOT import from services/, selectors/, domain/, or outer layers.

Invariants enforced:
    R3  -- Idempotency key uniqueness (UNIQUE constraint on idempotency_key).
    R4  -- Balance per currency (checked by JournalWriter before commit;
           verified here via is_balanced property for read-side assertions).
    R5  -- Rounding line uniqueness (at most ONE is_rounding=True per entry;
           threshold enforced by DB trigger trg_journal_line_single_rounding).
    R9  -- Sequence safety (seq is monotonic, gap-safe; UNIQUE constraint).
    R10 -- Immutability (ORM listeners in db/immutability.py + DB triggers
           prevent UPDATE/DELETE on posted entries and their lines).
    R21 -- Reference snapshot determinism (coa_version, dimension_schema_version,
           rounding_policy_version, currency_registry_version columns).

Failure modes:
    - IntegrityError on duplicate idempotency_key (R3/R8).
    - ImmutabilityViolationError on UPDATE/DELETE of posted entry/line (R10).
    - UnbalancedEntryError if debits != credits at posting time (R4).

Audit relevance:
    JournalEntry and JournalLine rows are the authoritative financial record.
    Every audit, replay, and reporting query ultimately derives from these rows.
    Immutability after POSTED status is the foundational audit guarantee.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
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

if TYPE_CHECKING:
    from finance_kernel.models.account import Account
    from finance_kernel.models.event import Event


class JournalEntryStatus(str, Enum):
    """Lifecycle status of a journal entry.

    Contract: Transitions are one-way: DRAFT -> POSTED -> REVERSED.
    Guarantees: No backward transitions are permitted (R10).
    """

    DRAFT = "draft"
    POSTED = "posted"
    REVERSED = "reversed"


class LineSide(str, Enum):
    """Which side of the entry this line is on.

    Contract: Every JournalLine has exactly one side -- DEBIT or CREDIT.
    Guarantees: Amount is always positive; side determines sign convention.
    """

    DEBIT = "debit"
    CREDIT = "credit"


class JournalEntry(TrackedBase):
    """
    Journal entry header -- the atomic unit of double-entry accounting.

    Contract:
        Each JournalEntry is derived from exactly one source event and carries
        an idempotency_key that is unique across the entire system (R3/R8).
        Once status transitions to POSTED, the row and all child JournalLines
        become immutable (R10).

    Guarantees:
        - Debits == Credits per currency per entry (R4, checked at posting).
        - Monotonic seq assigned at posting time via SequenceService (R9).
        - R21 snapshot columns populated at post time for deterministic replay.

    Non-goals:
        - This model does NOT enforce balance at the ORM level; enforcement
          lives in JournalWriter.  The is_balanced property is a read-side
          convenience, not a write-time guard.
    """

    __tablename__ = "journal_entries"

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_journal_idempotency"),
        UniqueConstraint("seq", name="uq_journal_seq"),
        Index("idx_journal_source_event", "source_event_id"),
        Index("idx_journal_effective_date", "effective_date"),
        Index("idx_journal_status", "status"),
        Index("idx_journal_seq", "seq"),
    )

    # Source event that created this entry
    source_event_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("events.event_id"),
        nullable=False,
    )

    # Event type for quick filtering
    source_event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # When the event occurred
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Accounting date (drives period assignment)
    effective_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # When the entry was posted
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Who/what triggered the posting
    actor_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        nullable=False,
    )

    # Current status
    status: Mapped[JournalEntryStatus] = mapped_column(
        String(10),
        default=JournalEntryStatus.DRAFT,
        nullable=False,
    )

    # If this is a reversal, points to the original entry
    reversal_of_id: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        ForeignKey("journal_entries.id"),
        nullable=True,
    )

    # Idempotency key: producer:event_type:event_id
    idempotency_key: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        unique=True,
    )

    # Version of posting rules used
    posting_rule_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    # ==========================================================================
    # R21: Reference Snapshot Determinism
    # Every posted JournalEntry must record immutable version identifiers for
    # all reference data used during posting. This enables deterministic replay.
    # ==========================================================================

    # Chart of accounts version at time of posting
    coa_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,  # Nullable for draft entries, required at post time
    )

    # Dimension schema version at time of posting
    dimension_schema_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Rounding policy version at time of posting
    rounding_policy_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Currency registry version at time of posting
    currency_registry_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Monotonic global sequence number (assigned at posting)
    seq: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        unique=True,
    )

    # Optional metadata (named entry_metadata to avoid SQLAlchemy reserved name)
    entry_metadata: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Description/memo for the entry
    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # Relationships
    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    source_event: Mapped["Event"] = relationship(
        foreign_keys=[source_event_id],
        primaryjoin="JournalEntry.source_event_id == Event.event_id",
    )

    reversal_of: Mapped["JournalEntry | None"] = relationship(
        remote_side="JournalEntry.id",
        foreign_keys=[reversal_of_id],
    )

    def __repr__(self) -> str:
        return f"<JournalEntry {self.id} status={self.status.value}>"

    @property
    def is_posted(self) -> bool:
        """Check if entry is posted.

        Postconditions: Returns True iff status is POSTED.
        """
        return self.status == JournalEntryStatus.POSTED

    @property
    def is_draft(self) -> bool:
        """Check if entry is still a draft.

        Postconditions: Returns True iff status is DRAFT.
        """
        return self.status == JournalEntryStatus.DRAFT

    @property
    def is_reversed(self) -> bool:
        """Check if entry has been reversed.

        Postconditions: Returns True iff status is REVERSED.
        """
        return self.status == JournalEntryStatus.REVERSED

    @property
    def total_debits(self) -> Decimal:
        """Sum of all debit line amounts.

        Postconditions: Result >= 0 (amounts are always positive).
        """
        return sum(
            (line.amount for line in self.lines if line.side == LineSide.DEBIT),
            Decimal("0"),
        )

    @property
    def total_credits(self) -> Decimal:
        """Sum of all credit line amounts.

        Postconditions: Result >= 0 (amounts are always positive).
        """
        return sum(
            (line.amount for line in self.lines if line.side == LineSide.CREDIT),
            Decimal("0"),
        )

    @property
    def is_balanced(self) -> bool:
        """Check if debits equal credits (R4 read-side convenience).

        Postconditions: Returns True iff total_debits == total_credits.
        """
        return self.total_debits == self.total_credits


class JournalLine(TrackedBase):
    """
    Individual debit or credit line within a journal entry.

    Contract:
        Each line belongs to exactly one JournalEntry, references exactly one
        Account, and records a positive amount on one side (DEBIT or CREDIT).
        Lines are immutable after the parent entry transitions to POSTED (R10).

    Guarantees:
        - amount is always positive; the side column determines sign (R4).
        - At most ONE line per entry may have is_rounding=True (R5/R22).
        - currency is a 3-character ISO 4217 code (R16).
        - line_seq provides deterministic ordering for hash computation (R24).

    Non-goals:
        - This model does not validate account existence or activity status;
          that is enforced by JournalWriter at posting time.
    """

    __tablename__ = "journal_lines"

    __table_args__ = (
        Index("idx_line_entry", "journal_entry_id"),
        Index("idx_line_account", "account_id"),
        Index("idx_line_account_currency", "account_id", "currency"),
    )

    # Parent journal entry
    journal_entry_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("journal_entries.id"),
        nullable=False,
    )

    # Account being debited or credited
    account_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("accounts.id"),
        nullable=False,
    )

    # Debit or credit
    side: Mapped[LineSide] = mapped_column(
        String(10),
        nullable=False,
    )

    # Amount (always positive; side determines debit/credit)
    amount: Mapped[Decimal] = mapped_column(
        Numeric(38, 9),
        nullable=False,
    )

    # Currency of this line
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    # Dimensions (stored as JSON map)
    dimensions: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Whether this is a rounding adjustment line
    is_rounding: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # Line-level memo
    line_memo: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # Exchange rate ID used (if currency conversion involved)
    exchange_rate_id: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        ForeignKey("exchange_rates.id"),
        nullable=True,
    )

    # Line sequence within entry (for deterministic ordering)
    line_seq: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Relationships
    entry: Mapped["JournalEntry"] = relationship(
        back_populates="lines",
    )

    account: Mapped["Account"] = relationship(
        back_populates="journal_lines",
    )

    def __repr__(self) -> str:
        return f"<JournalLine {self.side.value} {self.amount} {self.currency}>"

    @property
    def is_debit(self) -> bool:
        """Check if this is a debit line.

        Postconditions: Returns True iff side is DEBIT.
        """
        return self.side == LineSide.DEBIT

    @property
    def is_credit(self) -> bool:
        """Check if this is a credit line.

        Postconditions: Returns True iff side is CREDIT.
        """
        return self.side == LineSide.CREDIT

    @property
    def signed_amount(self) -> Decimal:
        """Return amount with sign based on side.

        Postconditions: Debits are positive, credits are negative.
        """
        if self.is_debit:
            return self.amount
        return -self.amount
