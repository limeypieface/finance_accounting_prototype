"""
Module: finance_kernel.selectors.journal_selector
Responsibility: Read-only query access to journal entries and their lines.
    Converts ORM models to frozen DTOs for clean layer separation.
Architecture position: Kernel > Selectors.  May import from models/ and
    selectors/base.py.  MUST NOT import from services/, domain/, or outer layers.

Invariants enforced:
    - Read-only: No mutations performed on any queried data.
    - DTO convention: All public methods return JournalEntryDTO/JournalLineDTO,
      never raw ORM models.
    - Lines are sorted by line_seq for deterministic ordering (R24 hash stability).

Failure modes:
    - Returns None or empty list when no matching entries exist (never raises
      on absence of data).

Audit relevance:
    JournalSelector is the primary read path for journal entries in the system.
    It supports audit queries by event ID, date range, account, and status.
    All results derive from the authoritative journal_entries/journal_lines
    tables -- the single source of financial truth.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from finance_kernel.models.journal import (
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    LineSide,
)
from finance_kernel.selectors.base import BaseSelector


@dataclass
class JournalLineDTO:
    """Data transfer object for a journal line."""

    id: UUID
    account_id: UUID
    side: LineSide
    amount: Decimal
    currency: str
    dimensions: dict | None
    is_rounding: bool
    line_memo: str | None
    line_seq: int


@dataclass
class JournalEntryDTO:
    """Data transfer object for a journal entry."""

    id: UUID
    source_event_id: UUID
    source_event_type: str
    effective_date: date
    occurred_at: datetime
    posted_at: datetime | None
    status: JournalEntryStatus
    seq: int | None
    description: str | None
    lines: list[JournalLineDTO]

    @property
    def total_debits(self) -> Decimal:
        """Sum of debit amounts."""
        return sum(
            (line.amount for line in self.lines if line.side == LineSide.DEBIT),
            Decimal("0"),
        )

    @property
    def total_credits(self) -> Decimal:
        """Sum of credit amounts."""
        return sum(
            (line.amount for line in self.lines if line.side == LineSide.CREDIT),
            Decimal("0"),
        )

    @property
    def is_balanced(self) -> bool:
        """Check if debits equal credits."""
        return self.total_debits == self.total_credits


class JournalSelector(BaseSelector[JournalEntry]):
    """
    Selector for journal entry queries.

    Contract:
        All public query methods return JournalEntryDTO instances (or lists
        thereof).  Lines within each DTO are sorted by line_seq for
        deterministic ordering.

    Guarantees:
        - Read-only: No mutations are performed.
        - Eager loading: JournalEntry.lines are loaded via selectinload to
          avoid N+1 queries.
        - Ordering: Multi-entry results are ordered by JournalEntry.seq.

    Non-goals:
        - This selector does NOT compute balances; use LedgerSelector for that.
    """

    def __init__(self, session: Session):
        super().__init__(session)

    def _to_dto(self, entry: JournalEntry) -> JournalEntryDTO:
        """Convert ORM model to DTO."""
        lines = [
            JournalLineDTO(
                id=line.id,
                account_id=line.account_id,
                side=line.side,
                amount=line.amount,
                currency=line.currency,
                dimensions=line.dimensions,
                is_rounding=line.is_rounding,
                line_memo=line.line_memo,
                line_seq=line.line_seq,
            )
            for line in sorted(entry.lines, key=lambda x: x.line_seq)
        ]

        return JournalEntryDTO(
            id=entry.id,
            source_event_id=entry.source_event_id,
            source_event_type=entry.source_event_type,
            effective_date=entry.effective_date,
            occurred_at=entry.occurred_at,
            posted_at=entry.posted_at,
            status=entry.status,
            seq=entry.seq,
            description=entry.description,
            lines=lines,
        )

    def get_entry(self, journal_entry_id: UUID) -> JournalEntryDTO | None:
        """
        Get a journal entry by ID.

        Preconditions: journal_entry_id is a valid UUID.
        Postconditions: Returns JournalEntryDTO with all lines if found,
            None if no entry exists with the given ID.

        Args:
            journal_entry_id: Entry ID.

        Returns:
            JournalEntryDTO if found, None otherwise.
        """
        entry = self.session.execute(
            select(JournalEntry)
            .options(selectinload(JournalEntry.lines))
            .where(JournalEntry.id == journal_entry_id)
        ).scalar_one_or_none()

        if entry is None:
            return None

        return self._to_dto(entry)

    def get_entry_by_event(self, event_id: UUID) -> JournalEntryDTO | None:
        """
        Get a journal entry by source event ID (single result expected).

        Preconditions: event_id is a valid UUID.
        Postconditions: Returns the first JournalEntryDTO matching the event,
            or None.  If multiple entries exist for the same event, only the
            first is returned -- use get_entries_by_event() for the full list.

        Args:
            event_id: Event ID.

        Returns:
            JournalEntryDTO if found, None otherwise.
        """
        entry = self.session.execute(
            select(JournalEntry)
            .options(selectinload(JournalEntry.lines))
            .where(JournalEntry.source_event_id == event_id)
        ).scalar_one_or_none()

        if entry is None:
            return None

        return self._to_dto(entry)

    def get_entries_by_event(self, event_id: UUID) -> list[JournalEntryDTO]:
        """
        Get all journal entries for a source event ID.

        Args:
            event_id: Event ID.

        Returns:
            List of JournalEntryDTOs (may be empty).
        """
        entries = self.session.execute(
            select(JournalEntry)
            .options(selectinload(JournalEntry.lines))
            .where(JournalEntry.source_event_id == event_id)
            .order_by(JournalEntry.seq)
        ).scalars().all()

        return [self._to_dto(entry) for entry in entries]

    def get_entries_by_period(
        self,
        start_date: date,
        end_date: date,
        status: JournalEntryStatus | None = None,
    ) -> list[JournalEntryDTO]:
        """
        Get journal entries within a date range.

        Preconditions: start_date <= end_date.
        Postconditions: Returns all entries where start_date <= effective_date
            <= end_date, ordered by seq.  Empty list if none found.

        Args:
            start_date: Start of period (inclusive).
            end_date: End of period (inclusive).
            status: Optional status filter.

        Returns:
            List of JournalEntryDTOs.
        """
        query = (
            select(JournalEntry)
            .options(selectinload(JournalEntry.lines))
            .where(
                JournalEntry.effective_date >= start_date,
                JournalEntry.effective_date <= end_date,
            )
            .order_by(JournalEntry.seq)
        )

        if status is not None:
            query = query.where(JournalEntry.status == status)

        entries = self.session.execute(query).scalars().all()

        return [self._to_dto(entry) for entry in entries]

    def get_posted_entries(
        self,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[JournalEntryDTO]:
        """
        Get posted journal entries ordered by sequence.

        Args:
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            List of JournalEntryDTOs.
        """
        query = (
            select(JournalEntry)
            .options(selectinload(JournalEntry.lines))
            .where(JournalEntry.status == JournalEntryStatus.POSTED)
            .order_by(JournalEntry.seq)
        )

        if limit is not None:
            query = query.limit(limit)

        if offset is not None:
            query = query.offset(offset)

        entries = self.session.execute(query).scalars().all()

        return [self._to_dto(entry) for entry in entries]

    def count_entries(self, status: JournalEntryStatus | None = None) -> int:
        """
        Count journal entries.

        Args:
            status: Optional status filter.

        Returns:
            Number of entries.
        """
        from sqlalchemy import func

        query = select(func.count(JournalEntry.id))

        if status is not None:
            query = query.where(JournalEntry.status == status)

        return self.session.execute(query).scalar_one()

    def get_entries_for_account(
        self,
        account_id: UUID,
        as_of_date: date | None = None,
    ) -> list[JournalEntryDTO]:
        """
        Get posted journal entries that affect a specific account.

        Preconditions: account_id is a valid UUID.
        Postconditions: Returns only POSTED entries that have at least one
            JournalLine referencing the given account.  Entries are ordered
            by seq.  If as_of_date is set, only entries with effective_date
            <= as_of_date are included.

        Args:
            account_id: Account ID.
            as_of_date: Optional cutoff date.

        Returns:
            List of JournalEntryDTOs.
        """
        # Subquery to find entry IDs that have lines for this account
        subquery = (
            select(JournalLine.journal_entry_id)
            .where(JournalLine.account_id == account_id)
            .distinct()
        )

        query = (
            select(JournalEntry)
            .options(selectinload(JournalEntry.lines))
            .where(
                JournalEntry.id.in_(subquery),
                JournalEntry.status == JournalEntryStatus.POSTED,
            )
            .order_by(JournalEntry.seq)
        )

        if as_of_date is not None:
            query = query.where(JournalEntry.effective_date <= as_of_date)

        entries = self.session.execute(query).scalars().all()

        return [self._to_dto(entry) for entry in entries]
