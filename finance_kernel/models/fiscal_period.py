"""
Fiscal Period model.

Controls which periods are open for posting.

Hard invariants:
- No JournalEntry may be posted with an effective_date inside a closed period
- Corrections to closed periods must post into the current open period
- Closed periods are immutable once closed
"""

from datetime import date, datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import TrackedBase, UUIDString


class PeriodStatus(str, Enum):
    """Status of a fiscal period."""

    OPEN = "open"
    CLOSED = "closed"


class FiscalPeriod(TrackedBase):
    """
    Fiscal period for accounting control.

    Periods control when postings can occur. Once a period is closed,
    no new postings can be made with effective_dates in that period.
    """

    __tablename__ = "fiscal_periods"

    __table_args__ = (
        UniqueConstraint("period_code", name="uq_period_code"),
        Index("idx_period_dates", "start_date", "end_date"),
        Index("idx_period_status", "status"),
    )

    # Period identifier (e.g., "2024-01", "2024-Q1", "FY2024")
    period_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # Human-readable name
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # Period boundaries (inclusive)
    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    end_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # Current status
    status: Mapped[PeriodStatus] = mapped_column(
        String(10),
        default=PeriodStatus.OPEN,
        nullable=False,
    )

    # When the period was closed
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Who closed the period
    closed_by_id: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        nullable=True,
    )

    # Whether adjusting entries are allowed (for period-end adjustments)
    allows_adjustments: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<FiscalPeriod {self.period_code}: {self.status.value}>"

    @property
    def is_open(self) -> bool:
        """Check if period is open for posting."""
        return self.status == PeriodStatus.OPEN

    @property
    def is_closed(self) -> bool:
        """Check if period is closed."""
        return self.status == PeriodStatus.CLOSED

    def contains_date(self, check_date: date) -> bool:
        """Check if a date falls within this period."""
        return self.start_date <= check_date <= self.end_date

    def close(self, actor_id: UUID, closed_at: datetime) -> None:
        """
        Close the period.

        R5 Compliance: Requires closed_at timestamp - does not call datetime.now().

        Args:
            actor_id: UUID of the user closing the period.
            closed_at: Timestamp when the period was closed (from injected clock).

        Raises:
            ValueError: If period is already closed.
        """
        if self.is_closed:
            raise ValueError(f"Period {self.period_code} is already closed")

        self.status = PeriodStatus.CLOSED
        self.closed_at = closed_at
        self.closed_by_id = actor_id
