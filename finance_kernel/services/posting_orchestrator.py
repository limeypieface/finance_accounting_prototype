"""
Posting Orchestrator - Coordinates the posting workflow.

The Orchestrator ties together:
- Ingestor: Event ingestion and validation
- Bookkeeper: Pure transformation logic
- Ledger: Persistence
- Auditor: Audit trail

R7 Compliance: Manages its own transaction boundary.
All state-changing operations define their own transaction scope.
No reliance on ambient or nested transactions.
"""

import time
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID
from uuid import uuid4 as _uuid4

from sqlalchemy.orm import Session

from finance_kernel.logging_config import get_logger, LogContext

from finance_kernel.domain.bookkeeper import Bookkeeper, BookkeeperResult
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.dtos import (
    EventEnvelope,
    JournalEntryRecord,
    ReferenceData,
    ValidationError,
    ValidationResult,
)
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.ingestor_service import IngestorService, IngestStatus
from finance_kernel.services.ledger_service import LedgerService, PersistResult
from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.reference_data_loader import ReferenceDataLoader

logger = get_logger("services.posting_orchestrator")


class PostingStatus(str, Enum):
    """Status of a posting operation."""

    POSTED = "posted"
    ALREADY_POSTED = "already_posted"
    VALIDATION_FAILED = "validation_failed"
    PERIOD_CLOSED = "period_closed"
    ADJUSTMENTS_NOT_ALLOWED = "adjustments_not_allowed"  # R13
    INGESTION_FAILED = "ingestion_failed"


@dataclass(frozen=True)
class PostingResult:
    """Result of a posting operation."""

    status: PostingStatus
    event_id: UUID
    journal_entry_id: UUID | None = None
    seq: int | None = None
    record: JournalEntryRecord | None = None
    validation: ValidationResult | None = None
    message: str | None = None

    @property
    def is_success(self) -> bool:
        """Check if posting was successful (including idempotent success)."""
        return self.status in (PostingStatus.POSTED, PostingStatus.ALREADY_POSTED)


class PostingOrchestrator:
    """
    Orchestrates the complete posting workflow.

    The Orchestrator manages:
    1. Event ingestion via Ingestor
    2. Period validation
    3. Transformation via Bookkeeper (pure)
    4. Persistence via Ledger
    5. Audit via Auditor

    R7 Compliance: Defines its own transaction boundary.
    By default, post_event() commits on success and rolls back on failure.
    Set auto_commit=False in constructor to delegate transaction control
    to the caller (for testing or special scenarios).
    """

    def __init__(
        self,
        session: Session,
        clock: Clock | None = None,
        auto_commit: bool = True,
    ):
        """
        Initialize the Posting Orchestrator.

        Args:
            session: SQLAlchemy session.
            clock: Clock for timestamps. Defaults to SystemClock.
            auto_commit: If True (default), commits on success, rolls back on
                failure. If False, caller manages transaction. Set False for
                testing or when explicit transaction control is needed.
        """
        self._session = session
        self._clock = clock or SystemClock()
        self._auto_commit = auto_commit

        # Initialize services with injected clock
        self._auditor = AuditorService(session, clock)
        self._ingestor = IngestorService(session, clock, self._auditor)
        self._ledger = LedgerService(session, clock, self._auditor)
        self._period_service = PeriodService(session, clock)
        self._bookkeeper = Bookkeeper()
        self._reference_loader = ReferenceDataLoader(session)

    def post_event(
        self,
        event_id: UUID,
        event_type: str,
        occurred_at: datetime,
        effective_date: date,
        actor_id: UUID,
        producer: str,
        payload: dict[str, Any],
        schema_version: int = 1,
        required_dimensions: set[str] | None = None,
        is_adjustment: bool = False,
    ) -> PostingResult:
        """
        Post an event to the journal.

        R7 Compliance: Defines its own transaction boundary.
        Commits on success, rolls back on failure (if auto_commit=True).

        R13 Compliance: Enforces allows_adjustments policy when is_adjustment=True.

        This is the main entry point. It:
        1. Ingests the event (validates at boundary)
        2. Validates period is open (and allows adjustments if applicable)
        3. Loads reference data
        4. Transforms event to proposed entry (pure)
        5. Persists the entry (with idempotency check)
        6. Commits the transaction (if auto_commit=True)

        Args:
            event_id: Globally unique event identifier.
            event_type: Namespaced event type.
            occurred_at: When the event happened.
            effective_date: Accounting date.
            actor_id: Who caused the event.
            producer: System that produced the event.
            payload: Event data.
            schema_version: Schema version.
            required_dimensions: Optional dimension codes that must be present.
            is_adjustment: Whether this is an adjusting entry (R13).

        Returns:
            PostingResult with status and journal entry details.
        """
        correlation_id = str(_uuid4())
        with LogContext.bind(
            correlation_id=correlation_id,
            event_id=str(event_id),
            actor_id=str(actor_id),
            producer=producer,
        ):
            logger.info(
                "posting_started",
                extra={"event_type": event_type, "effective_date": str(effective_date)},
            )
            t0 = time.monotonic()
            try:
                result = self._do_post_event(
                    event_id=event_id,
                    event_type=event_type,
                    occurred_at=occurred_at,
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer=producer,
                    payload=payload,
                    schema_version=schema_version,
                    required_dimensions=required_dimensions,
                    is_adjustment=is_adjustment,
                )

                # R7: Commit on success (if auto_commit enabled)
                if self._auto_commit and result.is_success:
                    self._session.commit()

                duration_ms = round((time.monotonic() - t0) * 1000, 2)
                logger.info(
                    "posting_completed",
                    extra={
                        "status": result.status.value,
                        "duration_ms": duration_ms,
                        "journal_entry_id": str(result.journal_entry_id) if result.journal_entry_id else None,
                        "seq": result.seq,
                    },
                )
                return result

            except Exception:
                duration_ms = round((time.monotonic() - t0) * 1000, 2)
                # R7: Rollback on failure (if auto_commit enabled)
                if self._auto_commit:
                    self._session.rollback()
                logger.error("posting_failed", extra={"duration_ms": duration_ms}, exc_info=True)
                raise

    def _do_post_event(
        self,
        event_id: UUID,
        event_type: str,
        occurred_at: datetime,
        effective_date: date,
        actor_id: UUID,
        producer: str,
        payload: dict[str, Any],
        schema_version: int = 1,
        required_dimensions: set[str] | None = None,
        is_adjustment: bool = False,
    ) -> PostingResult:
        """Internal posting logic (without transaction management)."""
        # 1. Ingest the event
        ingest_result = self._ingestor.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            effective_date=effective_date,
            actor_id=actor_id,
            producer=producer,
            payload=payload,
            schema_version=schema_version,
        )

        if ingest_result.status == IngestStatus.REJECTED:
            logger.warning(
                "posting_ingestion_failed",
                extra={"reason": ingest_result.message},
            )
            return PostingResult(
                status=PostingStatus.INGESTION_FAILED,
                event_id=event_id,
                validation=ingest_result.validation,
                message=ingest_result.message,
            )

        event_envelope = ingest_result.event_envelope
        assert event_envelope is not None

        # 2. Check period is open (and allows adjustments if applicable)
        # R13 Compliance: Enforces allows_adjustments policy
        from finance_kernel.exceptions import AdjustmentsNotAllowedError

        try:
            self._period_service.validate_adjustment_allowed(
                effective_date,
                is_adjustment=is_adjustment,
            )
        except AdjustmentsNotAllowedError as e:
            # R13: Adjustment policy violation
            logger.warning(
                "posting_period_violation",
                extra={"status": PostingStatus.ADJUSTMENTS_NOT_ALLOWED.value},
            )
            return PostingResult(
                status=PostingStatus.ADJUSTMENTS_NOT_ALLOWED,
                event_id=event_id,
                message=str(e),
            )
        except Exception as e:
            logger.warning(
                "posting_period_violation",
                extra={"status": PostingStatus.PERIOD_CLOSED.value},
            )
            return PostingResult(
                status=PostingStatus.PERIOD_CLOSED,
                event_id=event_id,
                message=str(e),
            )

        # 3. Load reference data
        reference_data = self._reference_loader.load(
            required_dimensions=frozenset(required_dimensions or set())
        )

        # 4. Transform via Bookkeeper (pure)
        bookkeeper_result: BookkeeperResult = self._bookkeeper.propose(
            event_envelope,
            reference_data,
        )

        if not bookkeeper_result.is_valid:
            logger.warning(
                "posting_validation_failed",
                extra={"status": PostingStatus.VALIDATION_FAILED.value},
            )
            return PostingResult(
                status=PostingStatus.VALIDATION_FAILED,
                event_id=event_id,
                validation=bookkeeper_result.validation,
                message="Posting validation failed",
            )

        assert bookkeeper_result.proposed_entry is not None

        # 5. Persist via Ledger
        ledger_result = self._ledger.persist(bookkeeper_result.proposed_entry)

        if ledger_result.status == PersistResult.ALREADY_EXISTS:
            logger.info(
                "posting_already_posted",
                extra={"existing_entry_id": str(ledger_result.existing_entry_id) if ledger_result.existing_entry_id else None},
            )
            return PostingResult(
                status=PostingStatus.ALREADY_POSTED,
                event_id=event_id,
                journal_entry_id=ledger_result.existing_entry_id,
                message=ledger_result.message,
            )

        if ledger_result.status == PersistResult.FAILED:
            return PostingResult(
                status=PostingStatus.VALIDATION_FAILED,
                event_id=event_id,
                message=ledger_result.message,
            )

        assert ledger_result.record is not None

        return PostingResult(
            status=PostingStatus.POSTED,
            event_id=event_id,
            journal_entry_id=ledger_result.record.id,
            seq=ledger_result.record.seq,
            record=ledger_result.record,
            message="Event posted successfully",
        )

    def post_existing_event(
        self,
        event_id: UUID,
        required_dimensions: set[str] | None = None,
        strategy_version: int | None = None,
        is_adjustment: bool = False,
    ) -> PostingResult:
        """
        Post an already-ingested event.

        Useful for:
        - Retrying failed postings
        - Replay scenarios
        - Re-posting with different strategy version

        R13 Compliance: Enforces allows_adjustments policy when is_adjustment=True.

        Args:
            event_id: ID of the already-ingested event.
            required_dimensions: Optional dimension codes.
            strategy_version: Optional specific strategy version (for replay).
            is_adjustment: Whether this is an adjusting entry (R13).

        Returns:
            PostingResult with status and journal entry details.
        """
        correlation_id = str(_uuid4())
        with LogContext.bind(
            correlation_id=correlation_id,
            event_id=str(event_id),
        ):
            logger.info(
                "posting_existing_started",
                extra={"strategy_version": strategy_version},
            )
            t0 = time.monotonic()

            # Get the event envelope
            event_envelope = self._ingestor.get_event(event_id)

            if event_envelope is None:
                return PostingResult(
                    status=PostingStatus.INGESTION_FAILED,
                    event_id=event_id,
                    message="Event not found",
                )

            # Check period is open (and allows adjustments if applicable)
            # R13 Compliance: Enforces allows_adjustments policy (same as post_event)
            from finance_kernel.exceptions import AdjustmentsNotAllowedError

            try:
                self._period_service.validate_adjustment_allowed(
                    event_envelope.effective_date,
                    is_adjustment=is_adjustment,
                )
            except AdjustmentsNotAllowedError as e:
                # R13: Adjustment policy violation
                return PostingResult(
                    status=PostingStatus.ADJUSTMENTS_NOT_ALLOWED,
                    event_id=event_id,
                    message=str(e),
                )
            except Exception as e:
                return PostingResult(
                    status=PostingStatus.PERIOD_CLOSED,
                    event_id=event_id,
                    message=str(e),
                )

            # Load reference data
            reference_data = self._reference_loader.load(
                required_dimensions=frozenset(required_dimensions or set())
            )

            # Transform
            bookkeeper_result = self._bookkeeper.propose(
                event_envelope,
                reference_data,
                strategy_version=strategy_version,
            )

            if not bookkeeper_result.is_valid:
                return PostingResult(
                    status=PostingStatus.VALIDATION_FAILED,
                    event_id=event_id,
                    validation=bookkeeper_result.validation,
                    message="Posting validation failed",
                )

            assert bookkeeper_result.proposed_entry is not None

            # Persist
            ledger_result = self._ledger.persist(bookkeeper_result.proposed_entry)

            if ledger_result.status == PersistResult.ALREADY_EXISTS:
                return PostingResult(
                    status=PostingStatus.ALREADY_POSTED,
                    event_id=event_id,
                    journal_entry_id=ledger_result.existing_entry_id,
                    message=ledger_result.message,
                )

            if ledger_result.status == PersistResult.FAILED:
                return PostingResult(
                    status=PostingStatus.VALIDATION_FAILED,
                    event_id=event_id,
                    message=ledger_result.message,
                )

            assert ledger_result.record is not None

            duration_ms = round((time.monotonic() - t0) * 1000, 2)
            logger.info(
                "posting_existing_completed",
                extra={
                    "status": PostingStatus.POSTED.value,
                    "duration_ms": duration_ms,
                    "journal_entry_id": str(ledger_result.record.id),
                    "seq": ledger_result.record.seq,
                },
            )

            return PostingResult(
                status=PostingStatus.POSTED,
                event_id=event_id,
                journal_entry_id=ledger_result.record.id,
                seq=ledger_result.record.seq,
                record=ledger_result.record,
                message="Event posted successfully",
            )

    def validate_chain(self) -> bool:
        """
        Validate the audit chain.

        Returns:
            True if chain is valid.

        Raises:
            AuditChainBrokenError: If validation fails.
        """
        return self._auditor.validate_chain()
