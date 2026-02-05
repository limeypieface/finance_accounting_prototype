"""
ReversalService -- thin orchestrator for journal entry reversals.

Responsibility:
    Validates reversal preconditions, delegates the mechanical line-flip to
    JournalWriter.write_reversal(), and atomically creates secondary artifacts
    (REVERSED_BY economic link, audit event) in the same transaction.

Architecture position:
    Kernel > Services -- imperative shell.  Consumes JournalWriter,
    AuditorService, LinkGraphService, and PeriodService.

Invariants enforced:
    R10 -- Posted entries never mutated.  Reversal state is derived from
           the canonical reversal_of_id linkage (no status mutation).
    R12 -- Reversal target period must be OPEN (validated via PeriodService).
    R3  -- Idempotency via JournalWriter idempotency key uniqueness.
    R4  -- Balance per currency (enforced by JournalWriter.write_reversal).
    R9  -- Monotonic sequence (enforced by JournalWriter._finalize_posting).
    R11 -- Audit chain integrity (enforced by AuditorService).

Failure modes:
    - EntryNotPostedError: Original entry is not POSTED.
    - EntryAlreadyReversedError: A reversal entry already exists (unique
      partial index on reversal_of_id).
    - ClosedPeriodError: Target effective_date is in a closed period (R12).
    - PeriodNotFoundError: No period covers the target effective_date.

Audit relevance:
    Every reversal creates an audit event via AuditorService.record_reversal()
    and a REVERSED_BY economic link.  Both are atomic with the journal entry.

Design principles:
    1. Posted rows never change -- no POSTED->REVERSED exemption.
    2. One canonical linkage -- reversal_of_id FK is the single source of truth.
    3. Reversal is a first-class posting mode routed through JournalWriter.
    4. Period semantics are explicit -- callers must choose same_period or
       current_period; no silent defaulting.
    5. Idempotency and atomicity are mandatory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.economic_link import (
    ArtifactRef,
    EconomicLink,
    LinkType,
)
from finance_kernel.exceptions import (
    EntryAlreadyReversedError,
    EntryNotPostedError,
)
from finance_kernel.logging_config import get_logger
from finance_kernel.models.event import Event
from finance_kernel.models.journal import JournalEntry, JournalEntryStatus
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.journal_writer import JournalWriter
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.period_service import PeriodService
from finance_kernel.utils.hashing import hash_payload

logger = get_logger("services.reversal")


@dataclass(frozen=True)
class ReversalResult:
    """Immutable result of a successful reversal operation.

    Contains all IDs needed for downstream processing and audit trail.
    """

    original_entry_id: UUID
    reversal_entry_id: UUID
    reversal_seq: int
    effective_date: date
    link_id: UUID


class ReversalService:
    """Thin orchestrator for journal entry reversals.

    Contract:
        Accepts a journal entry ID and a reason, validates preconditions,
        creates a reversal entry via JournalWriter, establishes a
        REVERSED_BY economic link, and records an audit event -- all
        atomically in the same database transaction.

    Guarantees:
        - Original entry is never mutated (R10).
        - At most one reversal per original entry (unique partial index
          on reversal_of_id + max_children=1 on REVERSED_BY link type).
        - Reversal entry is POSTED with a monotonic sequence (R9).
        - Target effective_date is in an OPEN period (R12).
        - Idempotent: concurrent reversals of the same entry result in
          exactly one reversal (DB unique constraint wins the race).

    Non-goals:
        - Does NOT call session.commit() -- caller controls boundaries.
        - Does NOT handle partial reversals (line-level selection).
        - Does NOT handle module-level void workflows (AP void, AR void).
    """

    def __init__(
        self,
        session: Session,
        journal_writer: JournalWriter,
        auditor: AuditorService,
        link_graph: LinkGraphService,
        period_service: PeriodService,
        clock: Clock | None = None,
    ):
        self._session = session
        self._journal_writer = journal_writer
        self._auditor = auditor
        self._link_graph = link_graph
        self._period_service = period_service
        self._clock = clock or SystemClock()

    def reverse_in_same_period(
        self,
        original_entry_id: UUID,
        reason: str,
        actor_id: UUID,
        reversal_event_id: UUID | None = None,
    ) -> ReversalResult:
        """Reverse an entry into the original entry's period.

        Uses the original entry's effective_date as the reversal's
        effective_date.  Fails if the original period is closed.

        Preconditions:
            - original_entry_id references a POSTED JournalEntry.
            - The original entry's effective_date falls in an OPEN period.

        Postconditions:
            - A new POSTED reversal entry exists with reversal_of_id set.
            - A REVERSED_BY economic link exists.
            - An audit event has been recorded.

        Args:
            original_entry_id: ID of the entry to reverse.
            reason: Human-readable reason for the reversal.
            actor_id: Who is performing the reversal.
            reversal_event_id: Optional pre-generated event UUID. If None,
                a new UUID is generated.

        Returns:
            ReversalResult with all relevant IDs.

        Raises:
            EntryNotPostedError: If the entry is not POSTED.
            EntryAlreadyReversedError: If a reversal already exists.
            ClosedPeriodError: If the original period is closed.
            PeriodNotFoundError: If no period covers the effective date.
        """
        original = self._load_and_validate(original_entry_id)
        effective_date = original.effective_date

        # R12: Validate target period is open
        self._period_service.validate_effective_date(effective_date)

        return self._execute_reversal(
            original=original,
            effective_date=effective_date,
            reason=reason,
            actor_id=actor_id,
            reversal_event_id=reversal_event_id,
        )

    def reverse_in_current_period(
        self,
        original_entry_id: UUID,
        reason: str,
        actor_id: UUID,
        effective_date: date,
        reversal_event_id: UUID | None = None,
    ) -> ReversalResult:
        """Reverse an entry into a specified open period.

        Required when the original entry's period is closed.  The caller
        provides the target effective_date explicitly.

        Preconditions:
            - original_entry_id references a POSTED JournalEntry.
            - effective_date falls in an OPEN period.

        Postconditions:
            - A new POSTED reversal entry exists with the specified
              effective_date and reversal_of_id set.
            - A REVERSED_BY economic link exists.
            - An audit event has been recorded.

        Args:
            original_entry_id: ID of the entry to reverse.
            reason: Human-readable reason for the reversal.
            actor_id: Who is performing the reversal.
            effective_date: Accounting date for the reversal entry.
            reversal_event_id: Optional pre-generated event UUID. If None,
                a new UUID is generated.

        Returns:
            ReversalResult with all relevant IDs.

        Raises:
            EntryNotPostedError: If the entry is not POSTED.
            EntryAlreadyReversedError: If a reversal already exists.
            ClosedPeriodError: If the specified period is closed.
            PeriodNotFoundError: If no period covers the effective date.
        """
        original = self._load_and_validate(original_entry_id)

        # R12: Validate caller-provided target period is open
        self._period_service.validate_effective_date(effective_date)

        return self._execute_reversal(
            original=original,
            effective_date=effective_date,
            reason=reason,
            actor_id=actor_id,
            reversal_event_id=reversal_event_id,
        )

    # =========================================================================
    # Internal Implementation
    # =========================================================================

    def _load_and_validate(self, original_entry_id: UUID) -> JournalEntry:
        """Load the original entry and validate reversal preconditions.

        Preconditions:
            - original_entry_id is a valid UUID.

        Postconditions:
            - Returns a POSTED JournalEntry that has not been reversed.

        Raises:
            EntryNotPostedError: If entry not found or not POSTED.
            EntryAlreadyReversedError: If a reversal entry already exists.
        """
        # Load original entry with row lock to serialize concurrent reversals (idempotency at service layer)
        original = self._session.execute(
            select(JournalEntry)
            .where(JournalEntry.id == original_entry_id)
            .with_for_update()
        ).scalar_one_or_none()

        if original is None:
            raise EntryNotPostedError(
                journal_entry_id=str(original_entry_id),
                status="NOT_FOUND",
            )

        # Validate: must be POSTED
        if original.status != JournalEntryStatus.POSTED:
            raise EntryNotPostedError(
                journal_entry_id=str(original_entry_id),
                status=original.status.value,
            )

        # Validate: not already reversed
        # Query for existing reversal entry (canonical check via reversal_of_id)
        existing_reversal = self._session.execute(
            select(JournalEntry).where(
                JournalEntry.reversal_of_id == original_entry_id
            )
        ).scalar_one_or_none()

        if existing_reversal is not None:
            raise EntryAlreadyReversedError(
                journal_entry_id=str(original_entry_id),
            )

        return original

    def _execute_reversal(
        self,
        original: JournalEntry,
        effective_date: date,
        reason: str,
        actor_id: UUID,
        reversal_event_id: UUID | None,
    ) -> ReversalResult:
        """Execute the reversal: create event, entry, link, audit event.

        All steps execute in the same database transaction.  If any step
        fails, the entire transaction rolls back (caller controls commit).

        Steps:
            1. Create reversal Event (immutable source record).
            2. Call JournalWriter.write_reversal() (R4, R9, R21).
            3. Establish REVERSED_BY economic link (secondary linkage).
            4. Record audit event (R11 hash chain).

        Postconditions:
            - Reversal Event, JournalEntry, EconomicLink, and AuditEvent
              are all flushed to the session.
        """
        now = self._clock.now()
        event_id = reversal_event_id or uuid4()

        # Step 1: Create the reversal Event
        reversal_payload = {
            "original_entry_id": str(original.id),
            "original_seq": original.seq,
            "reason": reason,
        }
        payload_hash = hash_payload(reversal_payload)

        reversal_event = Event(
            event_id=event_id,
            event_type="system.reversal",
            occurred_at=now,
            effective_date=effective_date,
            actor_id=actor_id,
            producer="kernel.reversal_service",
            payload=reversal_payload,
            payload_hash=payload_hash,
            schema_version=1,
            ingested_at=now,
        )
        self._session.add(reversal_event)
        self._session.flush()

        logger.info(
            "reversal_event_created",
            extra={
                "event_id": str(event_id),
                "original_entry_id": str(original.id),
                "effective_date": str(effective_date),
            },
        )

        # Step 2: Create reversal journal entry via JournalWriter (same ledger as original — R4 ledger boundary)
        original_ledger_id = (
            (original.entry_metadata or {}).get("ledger_id", "GL")
        )
        reversal_entry = self._journal_writer.write_reversal(
            original_entry=original,
            source_event_id=event_id,
            actor_id=actor_id,
            effective_date=effective_date,
            reason=reason,
            expected_ledger_id=original_ledger_id,
        )

        logger.info(
            "reversal_entry_posted",
            extra={
                "reversal_entry_id": str(reversal_entry.id),
                "reversal_seq": reversal_entry.seq,
                "original_entry_id": str(original.id),
            },
        )

        # Step 3: Establish REVERSED_BY economic link (secondary linkage)
        # Canonical linkage is reversal_of_id FK; this is the graph edge.
        link_id = uuid4()
        link = EconomicLink.create(
            link_id=link_id,
            link_type=LinkType.REVERSED_BY,
            parent_ref=ArtifactRef.journal_entry(original.id),
            child_ref=ArtifactRef.journal_entry(reversal_entry.id),
            creating_event_id=event_id,
            created_at=now,
            metadata={
                "reason": reason,
                "original_seq": original.seq,
                "reversal_seq": reversal_entry.seq,
            },
        )
        self._link_graph.establish_link(link)

        logger.info(
            "reversal_link_established",
            extra={
                "link_id": str(link_id),
                "original_entry_id": str(original.id),
                "reversal_entry_id": str(reversal_entry.id),
            },
        )

        # Step 4: Record audit event
        self._auditor.record_reversal(
            entry_id=reversal_entry.id,
            original_entry_id=original.id,
            reason=reason,
            actor_id=actor_id,
        )

        logger.info(
            "reversal_completed",
            extra={
                "original_entry_id": str(original.id),
                "reversal_entry_id": str(reversal_entry.id),
                "reversal_seq": reversal_entry.seq,
                "effective_date": str(effective_date),
                "reason": reason,
            },
        )

        return ReversalResult(
            original_entry_id=original.id,
            reversal_entry_id=reversal_entry.id,
            reversal_seq=reversal_entry.seq,
            effective_date=effective_date,
            link_id=link_id,
        )
