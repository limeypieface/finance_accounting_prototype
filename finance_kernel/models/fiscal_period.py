"""
Module: finance_kernel.models.fiscal_period
Responsibility: ORM persistence for fiscal period lifecycle -- controls which
    date ranges accept postings.
Architecture position: Kernel > Models.  May import from db/base.py only.

Invariants enforced:
    R12 -- Closed period enforcement: no JournalEntry may be posted with an
           effective_date inside a CLOSED or LOCKED period.
    R13 -- Adjustment policy: allows_adjustments must be True to accept
           adjusting entries.

Failure modes:
    - ClosedPeriodError when attempting to post into a closed period (R12).
    - PeriodNotFoundError when no period covers the effective_date.
    - PeriodAlreadyClosedError on redundant close attempt.
    - PeriodImmutableError on modification of a closed period (R13).

Audit relevance:
    FiscalPeriod rows govern the temporal boundaries of the ledger.  Period
    close is a privileged operation that produces PERIOD_CLOSED audit events.
    Closed periods guarantee that historical financial statements are stable.
"""

from datetime import date, datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import TrackedBase, UUIDString


class PeriodStatus(str, Enum):
    """Lifecycle status of a fiscal period.

    Contract: Transitions are OPEN -> CLOSING -> CLOSED -> LOCKED.
    Once CLOSED, the period cannot reopen (R12/R13).
    """

    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    LOCKED = "locked"


class FiscalPeriod(TrackedBase):
    """
    Fiscal period for accounting control.

    Contract:
        Periods control when postings can occur.  Once a period transitions
        to CLOSED, no new postings are accepted for effective_dates within
        that period (R12).  Closed periods are immutable (R13).

    Guarantees:
        - period_code is unique (uq_period_code).
        - start_date <= end_date (enforced by service layer).
        - close() requires explicit actor_id and clock-injected timestamp.

    Non-goals:
        - This model does NOT enforce non-overlapping date ranges; that is
          checked by PeriodService at creation time.
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
        String(20),
        default=PeriodStatus.OPEN,
        nullable=False,
    )

    # ID of the PeriodCloseRun that owns the CLOSING lock (R25)
    closing_run_id: Mapped[str | None] = mapped_column(
        UUIDString(),
        nullable=True,
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
        """Check if period is closed or locked."""
        return self.status in (PeriodStatus.CLOSED, PeriodStatus.LOCKED)

    def contains_date(self, check_date: date) -> bool:
        """Check if a date falls within this period."""
        return self.start_date <= check_date <= self.end_date

    def close(self, actor_id: UUID, closed_at: datetime) -> None:
        """Close the period.

        Preconditions: Period must not already be CLOSED or LOCKED.
        Postconditions: status becomes CLOSED, closed_at and closed_by_id
            are populated, closing_run_id is cleared.
        Raises: ValueError if period is already closed (R12).

        Note: Requires closed_at timestamp from injected clock -- does NOT
        call datetime.now() (clock injection rule).
        """
        if self.is_closed:
            raise ValueError(f"Period {self.period_code} is already closed")

        self.status = PeriodStatus.CLOSED
        self.closed_at = closed_at
        self.closed_by_id = actor_id
        self.closing_run_id = None
