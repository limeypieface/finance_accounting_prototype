"""
Transactional sequence service.

Provides monotonically increasing sequence numbers with proper
transactional guarantees. Uses a dedicated table with row-level
locking to prevent sequence gaps and duplicates under concurrency.
"""

from sqlalchemy import BigInteger, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from finance_kernel.db.base import Base


class SequenceCounter(Base):
    """
    Sequence counter table.

    Each row represents a named sequence with its current value.
    Row-level locking ensures monotonicity under concurrency.
    """

    __tablename__ = "sequence_counters"

    # Sequence name (e.g., "journal_entry", "audit_event")
    name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
    )

    # Current sequence value
    current_value: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )


class SequenceService:
    """
    Service for generating transactional sequence numbers.

    Provides monotonically increasing sequences with:
    - Row-level locking for concurrency safety
    - No gaps under normal operation
    - Transaction isolation (sequence advances only on commit)

    Usage:
        with session.begin():
            seq = sequence_service.next_value("journal_entry")
            # Use seq...
            # If transaction rolls back, seq is not consumed
    """

    # Well-known sequence names
    JOURNAL_ENTRY = "journal_entry"
    AUDIT_EVENT = "audit_event"

    def __init__(self, session: Session):
        """
        Initialize the sequence service.

        Args:
            session: SQLAlchemy session (should be in a transaction).
        """
        self._session = session

    def next_value(self, sequence_name: str) -> int:
        """
        Get the next value for a named sequence.

        This method:
        1. Locks the sequence row (or creates it if not exists)
        2. Increments the counter
        3. Returns the new value

        The increment is only committed when the transaction commits.
        If the transaction rolls back, the sequence value is not consumed.

        Args:
            sequence_name: Name of the sequence.

        Returns:
            The next sequence value.
        """
        # Try to get and lock the existing counter
        counter = self._session.execute(
            select(SequenceCounter)
            .where(SequenceCounter.name == sequence_name)
            .with_for_update()  # Row-level lock
        ).scalar_one_or_none()

        if counter is None:
            # Create new counter (first use of this sequence)
            counter = SequenceCounter(name=sequence_name, current_value=1)
            self._session.add(counter)
            self._session.flush()
            return 1

        # Increment and return
        counter.current_value += 1
        self._session.flush()
        return counter.current_value

    def current_value(self, sequence_name: str) -> int | None:
        """
        Get the current value of a sequence without incrementing.

        Args:
            sequence_name: Name of the sequence.

        Returns:
            Current value, or None if sequence doesn't exist.
        """
        counter = self._session.execute(
            select(SequenceCounter)
            .where(SequenceCounter.name == sequence_name)
        ).scalar_one_or_none()

        return counter.current_value if counter else None

    def reset(self, sequence_name: str, value: int = 0) -> None:
        """
        Reset a sequence to a specific value.

        WARNING: This should only be used in tests or migration scripts.
        Resetting sequences in production can cause integrity issues.

        Args:
            sequence_name: Name of the sequence.
            value: Value to reset to.
        """
        counter = self._session.execute(
            select(SequenceCounter)
            .where(SequenceCounter.name == sequence_name)
            .with_for_update()
        ).scalar_one_or_none()

        if counter is None:
            counter = SequenceCounter(name=sequence_name, current_value=value)
            self._session.add(counter)
        else:
            counter.current_value = value

        self._session.flush()

    def initialize_sequences(self) -> None:
        """
        Initialize all well-known sequences.

        Called during database setup to ensure sequences exist.
        """
        for name in [self.JOURNAL_ENTRY, self.AUDIT_EVENT]:
            existing = self._session.execute(
                select(SequenceCounter)
                .where(SequenceCounter.name == name)
            ).scalar_one_or_none()

            if existing is None:
                counter = SequenceCounter(name=name, current_value=0)
                self._session.add(counter)

        self._session.flush()
