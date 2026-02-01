"""
PeriodService -- fiscal period lifecycle and posting-date validation.

Responsibility:
    Manages the fiscal period lifecycle (OPEN -> CLOSING -> CLOSED -> LOCKED)
    and validates that postings target an open period before they reach
    the journal writer.

Architecture position:
    Kernel > Services -- imperative shell.
    Called by ModulePostingService at the start of every posting pipeline
    to validate the effective_date, and by PeriodCloseOrchestrator to
    drive the close/lock lifecycle.

Invariants enforced:
    R12 -- Closed period enforcement: no posting to CLOSED or LOCKED
           periods.  Enforced by ``validate_effective_date()`` and
           ``validate_adjustment_allowed()``.
    R13 -- Adjustment policy enforcement: adjusting entries are only
           accepted when ``period.allows_adjustments`` is True.
    R3  -- Returns frozen ``FiscalPeriodInfo`` DTOs, never ORM entities.
    R7  -- Flush-only: never commits or rolls back the session.
    R25 -- CLOSING period blocks non-close postings.

Failure modes:
    - PeriodNotFoundError: No period covers the effective_date.
    - ClosedPeriodError: Period is CLOSED or LOCKED (R12).
    - AdjustmentsNotAllowedError: Adjusting entry in a period that
      does not allow adjustments (R13).
    - PeriodAlreadyClosedError: Attempt to close an already-closed period.
    - PeriodClosingError: Attempt to post to a CLOSING period without
      close-posting flag (R25).
    - PeriodOverlapError: New period date range overlaps with existing.
    - PeriodImmutableError: Modification attempted on closed period.

Audit relevance:
    Period creation, close, and lock are significant state transitions
    logged with structured fields: period_code, actor_id, timestamps.
    Validation failures (R12/R13/R25) are logged at WARNING level.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.dtos import (
    FiscalPeriodInfo,
)
from finance_kernel.domain.dtos import (
    PeriodStatus as DomainPeriodStatus,
)
from finance_kernel.exceptions import (
    AdjustmentsNotAllowedError,
    ClosedPeriodError,
    PeriodAlreadyClosedError,
    PeriodClosingError,
    PeriodImmutableError,
    PeriodNotFoundError,
    PeriodOverlapError,
)
from finance_kernel.logging_config import get_logger
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.services.base import BaseService

logger = get_logger("services.period")


class PeriodService(BaseService[FiscalPeriod]):
    """
    Service for managing fiscal period lifecycle.

    Contract:
        Accepts period codes or effective dates and returns frozen
        ``FiscalPeriodInfo`` DTOs.  Validation methods raise typed
        exceptions on R12/R13/R25 violations.  Lifecycle methods
        (create, close, lock) flush within the caller's transaction.

    Guarantees:
        - R12: ``validate_effective_date()`` and ``validate_adjustment_allowed()``
          reject postings to CLOSED or LOCKED periods.
        - R13: Adjusting entries are only accepted when ``allows_adjustments``
          is True on the period.
        - R25: CLOSING periods block non-close postings.
        - R3: All public methods return immutable DTOs.
        - Concurrent close serialization via ``SELECT ... FOR UPDATE``
          on the period row.

    Non-goals:
        - Does NOT call ``session.commit()`` -- caller controls boundaries.
        - Does NOT execute period-close accruals or reconciliation
          (that is PeriodCloseOrchestrator in finance_services/).
        - Does NOT manage the COA or account structures.
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

        logger.info(
            "period_created",
            extra={
                "period_code": period_code,
                "start_date": str(start_date),
                "end_date": str(end_date),
            },
        )

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
        in this period (R12 enforcement).

        Uses SELECT FOR UPDATE to serialize concurrent close attempts.
        If a concurrent session already closed the period, the row lock
        ensures we see the committed status and raise PeriodAlreadyClosedError
        at the application level rather than hitting the database trigger.

        Accepts periods in OPEN or CLOSING state (CLOSING -> CLOSED via orchestrator).

        Postconditions:
            - ``period.status`` is CLOSED.
            - ``period.closed_at`` is set to current clock time.
            - ``period.closed_by_id`` is set to ``actor_id``.
            - Future calls to ``validate_effective_date()`` for dates
              in this period will raise ``ClosedPeriodError`` (R12).

        Args:
            period_code: Period to close.
            actor_id: Who is closing the period.

        Returns:
            Updated FiscalPeriodInfo DTO.

        Raises:
            PeriodNotFoundError: If period doesn't exist.
            PeriodAlreadyClosedError: If period is already closed.
        """
        period = self._get_period_for_update(period_code)
        if period is None:
            raise PeriodNotFoundError(period_code)

        if period.is_closed:
            raise PeriodAlreadyClosedError(period_code)

        period.status = PeriodStatus.CLOSED
        period.closed_at = self._clock.now()  # R5: Use injected clock
        period.closed_by_id = actor_id
        period.closing_run_id = None  # Release close lock

        try:
            self.session.flush()
        except IntegrityError:
            # Defense-in-depth: trigger fired before lock was acquired
            self.session.rollback()
            logger.warning(
                "concurrent_period_close_conflict",
                extra={"period_code": period_code},
            )
            raise PeriodAlreadyClosedError(period_code)

        logger.info(
            "period_closed",
            extra={"period_code": period_code},
        )

        return self._to_dto(period)

    def begin_closing(
        self, period_code: str, closing_run_id: str, actor_id: UUID
    ) -> FiscalPeriodInfo:
        """
        Acquire exclusive close lock on a period (R25).

        Transitions period from OPEN to CLOSING and sets closing_run_id.
        Uses SELECT FOR UPDATE to serialize concurrent close attempts.

        Args:
            period_code: Period to begin closing.
            closing_run_id: UUID string of the PeriodCloseRun owning the lock.
            actor_id: Who is initiating the close.

        Returns:
            Updated FiscalPeriodInfo DTO.

        Raises:
            PeriodNotFoundError: If period doesn't exist.
            PeriodAlreadyClosedError: If period is already closed.
            PeriodClosingError: If period is already in CLOSING state.
        """
        period = self._get_period_for_update(period_code)
        if period is None:
            raise PeriodNotFoundError(period_code)

        if period.is_closed:
            raise PeriodAlreadyClosedError(period_code)

        if period.status == PeriodStatus.CLOSING:
            raise PeriodClosingError(period_code)

        period.status = PeriodStatus.CLOSING
        period.closing_run_id = closing_run_id
        self.session.flush()

        logger.info(
            "period_closing_begun",
            extra={
                "period_code": period_code,
                "closing_run_id": closing_run_id,
            },
        )

        return self._to_dto(period)

    def cancel_closing(self, period_code: str, actor_id: UUID) -> FiscalPeriodInfo:
        """
        Release close lock and revert period to OPEN.

        Args:
            period_code: Period to cancel closing.
            actor_id: Who is cancelling the close.

        Returns:
            Updated FiscalPeriodInfo DTO.

        Raises:
            PeriodNotFoundError: If period doesn't exist.
            ValueError: If period is not in CLOSING state.
        """
        period = self._get_period_for_update(period_code)
        if period is None:
            raise PeriodNotFoundError(period_code)

        if period.status != PeriodStatus.CLOSING:
            raise ValueError(
                f"Period {period_code} is not in CLOSING state "
                f"(current: {period.status.value})"
            )

        period.status = PeriodStatus.OPEN
        period.closing_run_id = None
        self.session.flush()

        logger.info(
            "period_closing_cancelled",
            extra={"period_code": period_code},
        )

        return self._to_dto(period)

    def lock_period(self, period_code: str, actor_id: UUID) -> FiscalPeriodInfo:
        """
        Permanently lock a closed period (year-end).

        Transitions CLOSED -> LOCKED. No reopening possible.

        Args:
            period_code: Period to lock.
            actor_id: Who is locking the period.

        Returns:
            Updated FiscalPeriodInfo DTO.

        Raises:
            PeriodNotFoundError: If period doesn't exist.
            ValueError: If period is not in CLOSED state.
        """
        period = self._get_period_for_update(period_code)
        if period is None:
            raise PeriodNotFoundError(period_code)

        if period.status != PeriodStatus.CLOSED:
            raise ValueError(
                f"Period {period_code} must be CLOSED to lock "
                f"(current: {period.status.value})"
            )

        period.status = PeriodStatus.LOCKED
        self.session.flush()

        logger.info(
            "period_locked",
            extra={"period_code": period_code},
        )

        return self._to_dto(period)

    def _get_period_orm(self, period_code: str) -> FiscalPeriod | None:
        """Get ORM FiscalPeriod by code (internal use only)."""
        return self.session.execute(
            select(FiscalPeriod).where(FiscalPeriod.period_code == period_code)
        ).scalar_one_or_none()

    def _get_period_for_update(self, period_code: str) -> FiscalPeriod | None:
        """Get ORM FiscalPeriod by code with row lock for concurrent mutation."""
        return self.session.execute(
            select(FiscalPeriod)
            .where(FiscalPeriod.period_code == period_code)
            .with_for_update()
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

    def validate_effective_date(
        self, effective_date: date, *, is_close_posting: bool = False
    ) -> None:
        """
        Validate that a posting can be made for the given effective date.

        Preconditions:
            - ``effective_date`` is a valid ``date`` object.

        Postconditions:
            - Returns normally only if a period exists for the date and
              is in a state that allows posting (OPEN, or CLOSING with
              ``is_close_posting=True``).

        Raises:
            PeriodNotFoundError: If no period exists for the date.
            ClosedPeriodError: If the period is closed (R12).
            PeriodClosingError: If the period is CLOSING and is_close_posting is False (R25).

        Args:
            effective_date: Date to validate.
            is_close_posting: If True, allow posting to CLOSING periods (R25).
        """
        period = self._get_period_for_date_orm(effective_date)

        if period is None:
            raise PeriodNotFoundError(str(effective_date))

        # INVARIANT: R12 -- Closed period enforcement
        if period.is_closed:
            raise ClosedPeriodError(period.period_code, str(effective_date))

        # INVARIANT: R25 -- CLOSING period blocks non-close postings
        if period.status == PeriodStatus.CLOSING and not is_close_posting:
            raise PeriodClosingError(period.period_code)

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
        except (PeriodNotFoundError, ClosedPeriodError, PeriodClosingError):
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
        *,
        is_close_posting: bool = False,
    ) -> None:
        """
        Validate that a posting (possibly an adjustment) can be made.

        Preconditions:
            - ``effective_date`` is a valid ``date`` object.

        Postconditions:
            - Returns normally only if the period is open (R12), not
              in an exclusive CLOSING state without close flag (R25),
              and allows adjustments if ``is_adjustment=True`` (R13).

        Raises:
            PeriodNotFoundError: If no period exists for the date.
            ClosedPeriodError: If the period is closed (R12).
            PeriodClosingError: If the period is CLOSING and not a close posting (R25).
            AdjustmentsNotAllowedError: If adjustment attempted on period
                that doesn't allow them (R13).

        Args:
            effective_date: Date of the posting.
            is_adjustment: Whether this is an adjusting entry.
            is_close_posting: If True, allow posting to CLOSING periods (R25).
        """
        period = self._get_period_for_date_orm(effective_date)

        if period is None:
            raise PeriodNotFoundError(str(effective_date))

        # INVARIANT: R12 -- Closed period enforcement
        if period.is_closed:
            logger.warning("period_closed_violation")
            raise ClosedPeriodError(period.period_code, str(effective_date))

        # INVARIANT: R25 -- CLOSING period blocks non-close postings
        if period.status == PeriodStatus.CLOSING and not is_close_posting:
            raise PeriodClosingError(period.period_code)

        # INVARIANT: R13 -- Adjustment policy enforcement
        if is_adjustment and not period.allows_adjustments:
            logger.warning("adjustments_not_allowed")
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
