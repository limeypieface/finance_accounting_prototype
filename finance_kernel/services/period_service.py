"""
Fiscal period management service.

Controls which periods are open for posting and manages period lifecycle.

R3 Compliance: Returns FiscalPeriodInfo DTOs instead of ORM entities.
R5 Compliance: Uses injected Clock - no datetime.now() or date.today().
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.dtos import (
    FiscalPeriodInfo,
    PeriodStatus as DomainPeriodStatus,
)
from finance_kernel.exceptions import (
    AdjustmentsNotAllowedError,
    ClosedPeriodError,
    PeriodAlreadyClosedError,
    PeriodImmutableError,
    PeriodNotFoundError,
    PeriodOverlapError,
)
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.services.base import BaseService


class PeriodService(BaseService[FiscalPeriod]):
    """
    Service for managing fiscal periods.

    Fiscal periods control when postings can occur. Once a period is closed,
    no new postings can be made with effective_dates in that period.

    R3 Compliance: All public methods return FiscalPeriodInfo DTOs,
    not ORM FiscalPeriod entities.
    R5 Compliance: Uses injected Clock for all time operations.
    """

    def __init__(self, session: Session, clock: Clock | None = None):
        super().__init__(session)
        self._clock = clock or SystemClock()

    def _to_dto(self, period: FiscalPeriod) -> FiscalPeriodInfo:
        """Convert ORM FiscalPeriod to FiscalPeriodInfo DTO."""
        # Handle status which may be a PeriodStatus enum or a string
        status_value = (
            period.status.value if isinstance(period.status, PeriodStatus) else period.status
        )
        return FiscalPeriodInfo(
            id=period.id,
            period_code=period.period_code,
            name=period.name,
            start_date=period.start_date,
            end_date=period.end_date,
            status=DomainPeriodStatus(status_value),
            allows_adjustments=period.allows_adjustments,
            closed_at=period.closed_at,
            closed_by_id=period.closed_by_id,
        )

    def create_period(
        self,
        period_code: str,
        name: str,
        start_date: date,
        end_date: date,
        actor_id: UUID,
        allows_adjustments: bool = False,
    ) -> FiscalPeriodInfo:
        """
        Create a new fiscal period.

        R12 Compliance: Validates date range doesn't overlap with existing periods.

        Args:
            period_code: Unique period identifier (e.g., "2024-01").
            name: Human-readable name.
            start_date: First day of the period (inclusive).
            end_date: Last day of the period (inclusive).
            actor_id: Who is creating the period.
            allows_adjustments: Whether adjusting entries are allowed.

        Returns:
            Created FiscalPeriodInfo DTO.

        Raises:
            ValueError: If start_date > end_date.
            PeriodOverlapError: If date range overlaps with existing period.
        """
        # Validate date range
        if start_date > end_date:
            raise ValueError(
                f"start_date ({start_date}) cannot be after end_date ({end_date})"
            )

        # R12: Check for overlapping periods
        self._validate_no_overlap(period_code, start_date, end_date)

        period = FiscalPeriod(
            period_code=period_code,
            name=name,
            start_date=start_date,
            end_date=end_date,
            status=PeriodStatus.OPEN,
            allows_adjustments=allows_adjustments,
            created_by_id=actor_id,
        )

        self.session.add(period)
        self.session.flush()

        return self._to_dto(period)

    def _validate_no_overlap(
        self,
        new_period_code: str,
        start_date: date,
        end_date: date,
    ) -> None:
        """
        Validate that the date range doesn't overlap with any existing period.

        R12 Compliance: Date ranges must not overlap.

        Two ranges overlap if: start1 <= end2 AND start2 <= end1

        Raises:
            PeriodOverlapError: If overlap is detected.
        """
        # Find any overlapping periods
        overlapping = self.session.execute(
            select(FiscalPeriod).where(
                FiscalPeriod.start_date <= end_date,
                FiscalPeriod.end_date >= start_date,
            )
        ).scalar_one_or_none()

        if overlapping:
            # Calculate actual overlap range for error message
            overlap_start = max(start_date, overlapping.start_date)
            overlap_end = min(end_date, overlapping.end_date)
            raise PeriodOverlapError(
                new_period_code=new_period_code,
                existing_period_code=overlapping.period_code,
                overlap_start=str(overlap_start),
                overlap_end=str(overlap_end),
            )

    def close_period(self, period_code: str, actor_id: UUID) -> FiscalPeriodInfo:
        """
        Close a fiscal period.

        Once closed, no new postings can be made with effective_dates
        in this period.

        Args:
            period_code: Period to close.
            actor_id: Who is closing the period.

        Returns:
            Updated FiscalPeriodInfo DTO.

        Raises:
            PeriodNotFoundError: If period doesn't exist.
            PeriodAlreadyClosedError: If period is already closed.
        """
        period = self._get_period_orm(period_code)
        if period is None:
            raise PeriodNotFoundError(period_code)

        if period.is_closed:
            raise PeriodAlreadyClosedError(period_code)

        period.status = PeriodStatus.CLOSED
        period.closed_at = self._clock.now()  # R5: Use injected clock
        period.closed_by_id = actor_id

        self.session.flush()

        return self._to_dto(period)

    def _get_period_orm(self, period_code: str) -> FiscalPeriod | None:
        """Get ORM FiscalPeriod by code (internal use only)."""
        return self.session.execute(
            select(FiscalPeriod).where(FiscalPeriod.period_code == period_code)
        ).scalar_one_or_none()

    def _get_period_for_date_orm(self, effective_date: date) -> FiscalPeriod | None:
        """Get ORM FiscalPeriod for date (internal use only)."""
        return self.session.execute(
            select(FiscalPeriod).where(
                FiscalPeriod.start_date <= effective_date,
                FiscalPeriod.end_date >= effective_date,
            )
        ).scalar_one_or_none()

    def get_period_by_code(self, period_code: str) -> FiscalPeriodInfo | None:
        """
        Get a period by its code.

        Args:
            period_code: Period identifier.

        Returns:
            FiscalPeriodInfo DTO if found, None otherwise.
        """
        period = self._get_period_orm(period_code)
        return self._to_dto(period) if period else None

    def get_period_for_date(self, effective_date: date) -> FiscalPeriodInfo | None:
        """
        Get the period that contains a specific date.

        Args:
            effective_date: Date to look up.

        Returns:
            FiscalPeriodInfo DTO if found, None otherwise.
        """
        period = self._get_period_for_date_orm(effective_date)
        return self._to_dto(period) if period else None

    def validate_effective_date(self, effective_date: date) -> None:
        """
        Validate that a posting can be made for the given effective date.

        Args:
            effective_date: Date to validate.

        Raises:
            PeriodNotFoundError: If no period exists for the date.
            ClosedPeriodError: If the period is closed.
        """
        period = self._get_period_for_date_orm(effective_date)

        if period is None:
            raise PeriodNotFoundError(str(effective_date))

        if period.is_closed:
            raise ClosedPeriodError(period.period_code, str(effective_date))

    def is_date_in_open_period(self, effective_date: date) -> bool:
        """
        Check if a date is in an open period.

        Args:
            effective_date: Date to check.

        Returns:
            True if date is in an open period.
        """
        try:
            self.validate_effective_date(effective_date)
            return True
        except (PeriodNotFoundError, ClosedPeriodError):
            return False

    def get_open_periods(self) -> list[FiscalPeriodInfo]:
        """
        Get all open periods.

        Returns:
            List of open FiscalPeriodInfo DTOs.
        """
        result = self.session.execute(
            select(FiscalPeriod)
            .where(FiscalPeriod.status == PeriodStatus.OPEN)
            .order_by(FiscalPeriod.start_date)
        )
        return [self._to_dto(p) for p in result.scalars().all()]

    def get_current_period(self, as_of: date | None = None) -> FiscalPeriodInfo | None:
        """
        Get the current period (containing today's date or specified date).

        Args:
            as_of: Date to check. Defaults to today (from injected clock).

        Returns:
            FiscalPeriodInfo DTO if found, None otherwise.
        """
        check_date = as_of or self._clock.now().date()  # R5: Use injected clock
        return self.get_period_for_date(check_date)

    # =========================================================================
    # R13 Compliance: Adjustment policy enforcement
    # =========================================================================

    def validate_adjustment_allowed(
        self,
        effective_date: date,
        is_adjustment: bool = False,
    ) -> None:
        """
        Validate that a posting (possibly an adjustment) can be made.

        R13 Compliance: allows_adjustments must be enforced.

        Args:
            effective_date: Date of the posting.
            is_adjustment: Whether this is an adjusting entry.

        Raises:
            PeriodNotFoundError: If no period exists for the date.
            ClosedPeriodError: If the period is closed.
            AdjustmentsNotAllowedError: If adjustment attempted on period
                that doesn't allow them.
        """
        period = self._get_period_for_date_orm(effective_date)

        if period is None:
            raise PeriodNotFoundError(str(effective_date))

        if period.is_closed:
            raise ClosedPeriodError(period.period_code, str(effective_date))

        # R13: Check adjustment policy
        if is_adjustment and not period.allows_adjustments:
            raise AdjustmentsNotAllowedError(period.period_code)

    def allows_adjustments(self, effective_date: date) -> bool:
        """
        Check if adjustments are allowed for the period containing date.

        Args:
            effective_date: Date to check.

        Returns:
            True if adjustments are allowed, False otherwise.
        """
        period = self._get_period_for_date_orm(effective_date)
        if period is None:
            return False
        return period.allows_adjustments

    def enable_adjustments(
        self,
        period_code: str,
        actor_id: UUID,
    ) -> FiscalPeriodInfo:
        """
        Enable adjusting entries for a period.

        R13 Compliance: Only open periods can be modified.

        Args:
            period_code: Period to modify.
            actor_id: Who is making the change.

        Returns:
            Updated FiscalPeriodInfo DTO.

        Raises:
            PeriodNotFoundError: If period doesn't exist.
            PeriodImmutableError: If period is closed.
        """
        period = self._get_period_orm(period_code)
        if period is None:
            raise PeriodNotFoundError(period_code)

        # R13: Closed periods are immutable
        if period.is_closed:
            raise PeriodImmutableError(period_code, "enable adjustments on")

        period.allows_adjustments = True
        self.session.flush()

        return self._to_dto(period)

    def disable_adjustments(
        self,
        period_code: str,
        actor_id: UUID,
    ) -> FiscalPeriodInfo:
        """
        Disable adjusting entries for a period.

        R13 Compliance: Only open periods can be modified.

        Args:
            period_code: Period to modify.
            actor_id: Who is making the change.

        Returns:
            Updated FiscalPeriodInfo DTO.

        Raises:
            PeriodNotFoundError: If period doesn't exist.
            PeriodImmutableError: If period is closed.
        """
        period = self._get_period_orm(period_code)
        if period is None:
            raise PeriodNotFoundError(period_code)

        # R13: Closed periods are immutable
        if period.is_closed:
            raise PeriodImmutableError(period_code, "disable adjustments on")

        period.allows_adjustments = False
        self.session.flush()

        return self._to_dto(period)

    def reopen_period(self, period_code: str, actor_id: UUID) -> None:
        """
        Attempt to reopen a closed period.

        R13 Compliance: Closed periods must be immutable - this always fails.

        Args:
            period_code: Period to reopen.
            actor_id: Who is attempting to reopen.

        Raises:
            PeriodNotFoundError: If period doesn't exist.
            PeriodImmutableError: Always raised for closed periods.
        """
        period = self._get_period_orm(period_code)
        if period is None:
            raise PeriodNotFoundError(period_code)

        if period.is_closed:
            raise PeriodImmutableError(period_code, "reopen")

        # Period is already open - no-op
        return
