"""
ModulePostingService - Orchestrates Pipeline B end-to-end.

Connects PolicySelector, MeaningBuilder, profile_bridge, and
InterpretationCoordinator into a single service that modules call
to post economic events.

Pipeline B flow:
    post_event(event_type, payload, effective_date, actor_id, ...)
      1. Validate period is open (PeriodService)
      2. Ingest event record (IngestorService — FK requirement)
      3. Find profile (PolicySelector.find_for_event with payload)
      4. Build meaning (MeaningBuilder.build)
      5. Build intent (profile_bridge.build_accounting_intent)
      6. Post atomically (InterpretationCoordinator.interpret_and_post)
      7. Commit or rollback

R7 Compliance: Manages its own transaction boundary.
All state-changing operations define their own transaction scope.
"""

import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID
from uuid import uuid4 as _uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.meaning_builder import MeaningBuilder, MeaningBuilderResult
from finance_kernel.domain.policy_bridge import build_accounting_intent
from finance_kernel.domain.policy_selector import PolicyNotFoundError, PolicySelector
from finance_kernel.logging_config import get_logger, LogContext
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.ingestor_service import IngestorService, IngestStatus
from finance_kernel.services.interpretation_coordinator import (
    InterpretationCoordinator,
    InterpretationResult,
)
from finance_kernel.services.journal_writer import JournalWriter, RoleResolver
from finance_kernel.services.outcome_recorder import OutcomeRecorder
from finance_kernel.services.period_service import PeriodService

logger = get_logger("services.module_posting")


class ModulePostingStatus(str, Enum):
    """Status of a module posting operation."""

    POSTED = "posted"
    ALREADY_POSTED = "already_posted"
    PERIOD_CLOSED = "period_closed"
    ADJUSTMENTS_NOT_ALLOWED = "adjustments_not_allowed"
    INGESTION_FAILED = "ingestion_failed"
    PROFILE_NOT_FOUND = "profile_not_found"
    MEANING_FAILED = "meaning_failed"
    GUARD_REJECTED = "guard_rejected"
    GUARD_BLOCKED = "guard_blocked"
    INTENT_FAILED = "intent_failed"
    POSTING_FAILED = "posting_failed"


@dataclass(frozen=True)
class ModulePostingResult:
    """Result of a module posting operation."""

    status: ModulePostingStatus
    event_id: UUID
    journal_entry_ids: tuple[UUID, ...] = ()
    interpretation_result: InterpretationResult | None = None
    meaning_result: MeaningBuilderResult | None = None
    profile_name: str | None = None
    message: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status in (
            ModulePostingStatus.POSTED,
            ModulePostingStatus.ALREADY_POSTED,
        )


class ModulePostingService:
    """
    Orchestrates Pipeline B: profile-driven posting from modules.

    This is the missing service that connects:
    - PolicySelector (profile lookup by event_type + payload)
    - MeaningBuilder (economic interpretation)
    - profile_bridge (AccountingIntent construction)
    - InterpretationCoordinator (atomic posting)

    Usage:
        service = ModulePostingService(
            session=session,
            role_resolver=resolver,
            clock=clock,
        )

        result = service.post_event(
            event_type="inventory.receipt",
            payload={"quantity": "100", "unit_cost": "25.00", ...},
            effective_date=date(2024, 6, 15),
            actor_id=actor_uuid,
            amount=Decimal("2500.00"),
            currency="USD",
        )

        if result.is_success:
            # Journal entries created
            ...
    """

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
        auto_commit: bool = True,
    ):
        self._session = session
        self._clock = clock or SystemClock()
        self._auto_commit = auto_commit

        # Build internal services
        self._auditor = AuditorService(session, clock)
        self._ingestor = IngestorService(session, clock, self._auditor)
        self._period_service = PeriodService(session, clock)
        self._meaning_builder = MeaningBuilder()
        self._journal_writer = JournalWriter(
            session, role_resolver, clock, self._auditor
        )
        self._outcome_recorder = OutcomeRecorder(session, self._clock)
        self._coordinator = InterpretationCoordinator(
            session=session,
            journal_writer=self._journal_writer,
            outcome_recorder=self._outcome_recorder,
            clock=clock,
        )

    def post_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        effective_date: date,
        actor_id: UUID,
        amount: Decimal,
        currency: str = "USD",
        producer: str | None = None,
        event_id: UUID | None = None,
        occurred_at: datetime | None = None,
        schema_version: int = 1,
        is_adjustment: bool = False,
        description: str | None = None,
        coa_version: int = 1,
        dimension_schema_version: int = 1,
    ) -> ModulePostingResult:
        """
        Post an economic event through Pipeline B.

        R7 Compliance: Commits on success, rolls back on failure
        (when auto_commit=True).

        Args:
            event_type: Namespaced event type (e.g., "inventory.receipt").
            payload: Event payload with domain-specific data.
            effective_date: Accounting effective date.
            actor_id: Who caused the event.
            amount: Primary monetary amount for the entry.
            currency: Currency code (default: "USD").
            producer: System that produced the event (defaults to event_type prefix).
            event_id: Optional event ID (generated if not provided).
            occurred_at: When the event happened (defaults to clock.now()).
            schema_version: Schema version for the event.
            is_adjustment: Whether this is an adjusting entry.
            description: Optional entry description.
            coa_version: COA version for snapshot.
            dimension_schema_version: Dimension schema version for snapshot.

        Returns:
            ModulePostingResult with status and artifacts.
        """
        resolved_event_id = event_id or _uuid4()
        resolved_occurred_at = occurred_at or self._clock.now()
        resolved_producer = producer or event_type.split(".")[0]

        correlation_id = str(_uuid4())
        with LogContext.bind(
            correlation_id=correlation_id,
            event_id=str(resolved_event_id),
            actor_id=str(actor_id),
            producer=resolved_producer,
        ):
            logger.info(
                "module_posting_started",
                extra={
                    "event_type": event_type,
                    "effective_date": str(effective_date),
                    "amount": str(amount),
                    "currency": currency,
                },
            )
            t0 = time.monotonic()

            try:
                result = self._do_post_event(
                    event_id=resolved_event_id,
                    event_type=event_type,
                    payload=payload,
                    effective_date=effective_date,
                    actor_id=actor_id,
                    amount=amount,
                    currency=currency,
                    producer=resolved_producer,
                    occurred_at=resolved_occurred_at,
                    schema_version=schema_version,
                    is_adjustment=is_adjustment,
                    description=description,
                    coa_version=coa_version,
                    dimension_schema_version=dimension_schema_version,
                )

                # R7: Commit on success
                if self._auto_commit and result.is_success:
                    self._session.commit()

                duration_ms = round((time.monotonic() - t0) * 1000, 2)
                logger.info(
                    "module_posting_completed",
                    extra={
                        "status": result.status.value,
                        "duration_ms": duration_ms,
                        "profile_name": result.profile_name,
                        "entry_count": len(result.journal_entry_ids),
                    },
                )
                return result

            except Exception:
                duration_ms = round((time.monotonic() - t0) * 1000, 2)
                if self._auto_commit:
                    self._session.rollback()
                logger.error(
                    "module_posting_failed",
                    extra={"duration_ms": duration_ms},
                    exc_info=True,
                )
                raise

    def _do_post_event(
        self,
        event_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        effective_date: date,
        actor_id: UUID,
        amount: Decimal,
        currency: str,
        producer: str,
        occurred_at: datetime,
        schema_version: int,
        is_adjustment: bool,
        description: str | None,
        coa_version: int,
        dimension_schema_version: int,
    ) -> ModulePostingResult:
        """Internal posting logic (without transaction management)."""

        # 1. Validate period is open
        from finance_kernel.exceptions import AdjustmentsNotAllowedError

        try:
            self._period_service.validate_adjustment_allowed(
                effective_date, is_adjustment=is_adjustment
            )
        except AdjustmentsNotAllowedError:
            return ModulePostingResult(
                status=ModulePostingStatus.ADJUSTMENTS_NOT_ALLOWED,
                event_id=event_id,
                message="Period does not allow adjustments",
            )
        except Exception as e:
            return ModulePostingResult(
                status=ModulePostingStatus.PERIOD_CLOSED,
                event_id=event_id,
                message=str(e),
            )

        # 2. Ingest event record (FK requirement for journal entries)
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
            return ModulePostingResult(
                status=ModulePostingStatus.INGESTION_FAILED,
                event_id=event_id,
                message=ingest_result.message,
            )

        if ingest_result.status == IngestStatus.DUPLICATE:
            return ModulePostingResult(
                status=ModulePostingStatus.ALREADY_POSTED,
                event_id=event_id,
                message="Event already ingested (idempotent duplicate)",
            )

        # 3. Find profile via PolicySelector (with where-clause dispatch)
        try:
            profile = PolicySelector.find_for_event(
                event_type, effective_date, payload=payload
            )
        except PolicyNotFoundError as e:
            return ModulePostingResult(
                status=ModulePostingStatus.PROFILE_NOT_FOUND,
                event_id=event_id,
                message=str(e),
            )

        logger.info(
            "profile_matched",
            extra={
                "profile_name": profile.name,
                "profile_version": profile.version,
            },
        )

        # 4. Build meaning (MeaningBuilder — pure domain)
        meaning_result = self._meaning_builder.build(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            effective_date=effective_date,
            profile=profile,
        )

        if not meaning_result.success:
            # Check for guard rejection vs block
            if meaning_result.guard_result and meaning_result.guard_result.rejected:
                return ModulePostingResult(
                    status=ModulePostingStatus.GUARD_REJECTED,
                    event_id=event_id,
                    meaning_result=meaning_result,
                    profile_name=profile.name,
                    message=f"Guard rejected: {meaning_result.guard_result.reason_code}",
                )
            if meaning_result.guard_result and meaning_result.guard_result.blocked:
                return ModulePostingResult(
                    status=ModulePostingStatus.GUARD_BLOCKED,
                    event_id=event_id,
                    meaning_result=meaning_result,
                    profile_name=profile.name,
                    message=f"Guard blocked: {meaning_result.guard_result.reason_code}",
                )
            return ModulePostingResult(
                status=ModulePostingStatus.MEANING_FAILED,
                event_id=event_id,
                meaning_result=meaning_result,
                profile_name=profile.name,
                message="MeaningBuilder failed to produce economic event",
            )

        # 5. Build accounting intent (profile_bridge)
        try:
            accounting_intent = build_accounting_intent(
                profile_name=profile.name,
                source_event_id=event_id,
                effective_date=effective_date,
                amount=amount,
                currency=currency,
                payload=payload,
                description=description,
                coa_version=coa_version,
                dimension_schema_version=dimension_schema_version,
            )
        except ValueError as e:
            return ModulePostingResult(
                status=ModulePostingStatus.INTENT_FAILED,
                event_id=event_id,
                profile_name=profile.name,
                message=str(e),
            )

        # 6. Post atomically (InterpretationCoordinator)
        interpretation_result = self._coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=accounting_intent,
            actor_id=actor_id,
        )

        if not interpretation_result.success:
            return ModulePostingResult(
                status=ModulePostingStatus.POSTING_FAILED,
                event_id=event_id,
                interpretation_result=interpretation_result,
                profile_name=profile.name,
                message=interpretation_result.error_message,
            )

        # Success
        journal_entry_ids = ()
        if interpretation_result.journal_result:
            journal_entry_ids = interpretation_result.journal_result.entry_ids

        return ModulePostingResult(
            status=ModulePostingStatus.POSTED,
            event_id=event_id,
            journal_entry_ids=journal_entry_ids,
            interpretation_result=interpretation_result,
            meaning_result=meaning_result,
            profile_name=profile.name,
            message="Event posted successfully via Pipeline B",
        )
