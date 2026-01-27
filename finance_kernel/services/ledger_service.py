"""
Ledger service - Persistence layer for journal entries.

The Ledger is responsible for:
- Persisting proposed journal entries
- Enforcing idempotency at the database level
- Assigning sequence numbers transactionally
- Coordinating with the Auditor for audit trail creation

The Ledger does NOT:
- Transform events (that's the Bookkeeper)
- Compute journal lines (that's the Strategy)
- Validate business rules (that's the Strategy)
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.dtos import (
    EntryStatus,
    JournalEntryDraft,
    JournalEntryRecord,
    ProposedJournalEntry,
    ProposedLine,
)
from finance_kernel.exceptions import (
    MissingReferenceSnapshotError,
    MultipleRoundingLinesError,
    RoundingAmountExceededError,
)
from finance_kernel.models.audit_event import AuditAction
from finance_kernel.models.journal import (
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    LineSide,
)
from finance_kernel.services.sequence_service import SequenceService

if TYPE_CHECKING:
    from finance_kernel.services.auditor_service import AuditorService


class PersistResult(str, Enum):
    """Result status of a persist operation."""

    PERSISTED = "persisted"
    ALREADY_EXISTS = "already_exists"
    FAILED = "failed"


@dataclass(frozen=True)
class LedgerResult:
    """
    Result of a Ledger.persist() operation.

    Contains the status and either the persisted record or error info.
    """

    status: PersistResult
    record: JournalEntryRecord | None = None
    message: str | None = None
    existing_entry_id: UUID | None = None

    @classmethod
    def success(cls, record: JournalEntryRecord) -> "LedgerResult":
        """Create a successful result."""
        return cls(status=PersistResult.PERSISTED, record=record)

    @classmethod
    def already_exists(
        cls,
        entry_id: UUID,
        seq: int | None,
    ) -> "LedgerResult":
        """Create an already-exists result (idempotent success)."""
        return cls(
            status=PersistResult.ALREADY_EXISTS,
            existing_entry_id=entry_id,
            message=f"Entry already exists with seq={seq}",
        )

    @classmethod
    def failure(cls, message: str) -> "LedgerResult":
        """Create a failure result."""
        return cls(status=PersistResult.FAILED, message=message)

    @property
    def is_success(self) -> bool:
        """Check if operation was successful (including idempotent success)."""
        return self.status in (PersistResult.PERSISTED, PersistResult.ALREADY_EXISTS)


class LedgerService:
    """
    Persistence layer for journal entries.

    The Ledger takes a ProposedJournalEntry (from the Bookkeeper) and:
    1. Checks idempotency (has this event already been posted?)
    2. Creates JournalEntry and JournalLine records
    3. Assigns a transactional sequence number
    4. Coordinates with Auditor for audit trail
    5. Returns the persisted record

    All operations happen within the caller's transaction boundary.
    """

    def __init__(
        self,
        session: Session,
        clock: Clock | None = None,
        auditor: "AuditorService | None" = None,
    ):
        """
        Initialize the Ledger service.

        Args:
            session: SQLAlchemy session.
            clock: Clock for timestamps. Defaults to SystemClock.
            auditor: Optional Auditor service for audit trail.
        """
        self._session = session
        self._clock = clock or SystemClock()
        self._auditor = auditor
        self._sequence_service = SequenceService(session)

    def persist(
        self,
        proposed_entry: ProposedJournalEntry,
    ) -> LedgerResult:
        """
        Persist a proposed journal entry.

        This is the main entry point. It:
        1. Checks if entry already exists (idempotency)
        2. Creates draft entry
        3. Creates journal lines
        4. Assigns sequence number
        5. Marks as posted
        6. Creates audit event

        All steps happen in the current transaction.

        Args:
            proposed_entry: The proposed entry from the Bookkeeper.

        Returns:
            LedgerResult with persisted record or error.
        """
        idempotency_key = proposed_entry.idempotency_key

        # 1. Check idempotency
        existing = self._get_existing_entry(idempotency_key)
        if existing is not None:
            if existing.status in (
                JournalEntryStatus.POSTED,
                JournalEntryStatus.REVERSED,
            ):
                return LedgerResult.already_exists(existing.id, existing.seq)

            # Draft exists - complete it (crash recovery)
            return self._complete_draft(existing, proposed_entry)

        # 2. Create draft entry
        try:
            entry = self._create_draft(proposed_entry)
        except IntegrityError:
            # Concurrent insert - fetch and return
            self._session.rollback()
            existing = self._get_existing_entry(idempotency_key)
            if existing:
                return LedgerResult.already_exists(existing.id, existing.seq)
            return LedgerResult.failure("Concurrent insert conflict")

        # 3. Create journal lines
        self._create_lines(entry, proposed_entry.lines)

        # 4. Assign sequence and post
        return self._finalize_posting(entry, proposed_entry)

    def _get_existing_entry(self, idempotency_key: str) -> JournalEntry | None:
        """Get existing entry by idempotency key."""
        return self._session.execute(
            select(JournalEntry)
            .where(JournalEntry.idempotency_key == idempotency_key)
            .with_for_update()  # Lock for update
        ).scalar_one_or_none()

    def _create_draft(self, proposed: ProposedJournalEntry) -> JournalEntry:
        """Create a draft journal entry."""
        event = proposed.event_envelope

        entry = JournalEntry(
            id=uuid4(),
            source_event_id=event.event_id,
            source_event_type=event.event_type,
            occurred_at=event.occurred_at,
            effective_date=event.effective_date,
            actor_id=event.actor_id,
            status=JournalEntryStatus.DRAFT,
            idempotency_key=proposed.idempotency_key,
            posting_rule_version=proposed.posting_rule_version,
            description=proposed.description,
            entry_metadata=proposed.metadata,
            created_by_id=event.actor_id,
            # R21: Reference snapshot version identifiers for deterministic replay
            coa_version=proposed.coa_version,
            dimension_schema_version=proposed.dimension_schema_version,
            rounding_policy_version=proposed.rounding_policy_version,
            currency_registry_version=proposed.currency_registry_version,
        )

        self._session.add(entry)
        self._session.flush()

        return entry

    def _create_lines(
        self,
        entry: JournalEntry,
        lines: tuple[ProposedLine, ...],
    ) -> list[JournalLine]:
        """Create journal lines for an entry."""
        # Validate rounding invariants BEFORE creating lines
        self._validate_rounding_invariants(entry.id, lines)

        journal_lines = []

        for i, spec in enumerate(lines):
            line = JournalLine(
                journal_entry_id=entry.id,
                account_id=spec.account_id,
                side=LineSide(spec.side.value),
                amount=spec.amount,
                currency=spec.currency,
                dimensions=spec.dimensions,
                is_rounding=spec.is_rounding,
                line_memo=spec.memo,
                exchange_rate_id=spec.exchange_rate_id,
                line_seq=i,
                created_by_id=entry.actor_id,
            )
            self._session.add(line)
            journal_lines.append(line)

        self._session.flush()
        return journal_lines

    def _validate_rounding_invariants(
        self,
        entry_id: UUID,
        lines: tuple[ProposedLine, ...],
    ) -> None:
        """
        Validate rounding invariants for journal lines.

        Enforces:
        1. At most ONE line can have is_rounding=True
        2. Rounding amount must be < 0.01 per non-rounding line

        These invariants prevent fraud via:
        - Multiple hidden rounding lines
        - Large "rounding" amounts that hide embezzlement

        Args:
            entry_id: The journal entry ID (for error messages).
            lines: The proposed lines to validate.

        Raises:
            MultipleRoundingLinesError: If more than one rounding line.
            RoundingAmountExceededError: If rounding amount exceeds threshold.
        """
        from decimal import Decimal

        # Separate rounding and non-rounding lines
        rounding_lines = [line for line in lines if line.is_rounding]
        non_rounding_lines = [line for line in lines if not line.is_rounding]

        # Invariant 1: At most ONE rounding line
        if len(rounding_lines) > 1:
            raise MultipleRoundingLinesError(
                entry_id=str(entry_id),
                rounding_count=len(rounding_lines),
            )

        # Invariant 2: Rounding amount threshold
        if rounding_lines:
            rounding_line = rounding_lines[0]
            rounding_amount = rounding_line.amount

            # Max allowed: 0.01 per non-rounding line (minimum 0.01)
            max_allowed = max(
                Decimal("0.01"),
                Decimal("0.01") * len(non_rounding_lines),
            )

            if rounding_amount > max_allowed:
                raise RoundingAmountExceededError(
                    entry_id=str(entry_id),
                    rounding_amount=str(rounding_amount),
                    threshold=str(max_allowed),
                    currency=rounding_line.currency,
                )

    def _validate_reference_snapshots(
        self,
        entry: JournalEntry,
    ) -> None:
        """
        R21: Validate reference snapshot versions are present at post time.

        Every posted JournalEntry must record immutable version identifiers
        for all reference data used during posting.

        Args:
            entry: The journal entry being posted.

        Raises:
            MissingReferenceSnapshotError: If required fields are missing.
        """
        missing_fields = []

        if entry.coa_version is None:
            missing_fields.append("coa_version")
        if entry.dimension_schema_version is None:
            missing_fields.append("dimension_schema_version")
        if entry.rounding_policy_version is None:
            missing_fields.append("rounding_policy_version")
        if entry.currency_registry_version is None:
            missing_fields.append("currency_registry_version")

        if missing_fields:
            raise MissingReferenceSnapshotError(
                entry_id=str(entry.id),
                missing_fields=missing_fields,
            )

    def _finalize_posting(
        self,
        entry: JournalEntry,
        proposed: ProposedJournalEntry,
    ) -> LedgerResult:
        """Assign sequence, set status to posted, create audit."""
        # R21: Validate reference snapshots are present before posting
        self._validate_reference_snapshots(entry)

        # Assign sequence number (transactional)
        seq = self._sequence_service.next_value(SequenceService.JOURNAL_ENTRY)
        entry.seq = seq
        entry.posted_at = self._clock.now()
        entry.status = JournalEntryStatus.POSTED

        self._session.flush()

        # Create audit event
        if self._auditor:
            self._auditor.record_posting(
                entry_id=entry.id,
                event_id=proposed.event_envelope.event_id,
                event_type=proposed.event_envelope.event_type,
                effective_date=proposed.event_envelope.effective_date,
                actor_id=proposed.event_envelope.actor_id,
                line_count=len(proposed.lines),
            )

        # Build result record (R21: Include reference snapshot versions)
        record = JournalEntryRecord(
            id=entry.id,
            seq=seq,
            idempotency_key=entry.idempotency_key,
            event_id=proposed.event_envelope.event_id,
            event_type=proposed.event_envelope.event_type,
            occurred_at=proposed.event_envelope.occurred_at,
            effective_date=proposed.event_envelope.effective_date,
            posted_at=entry.posted_at,
            actor_id=proposed.event_envelope.actor_id,
            status=EntryStatus.POSTED,
            lines=proposed.lines,
            description=proposed.description,
            metadata=proposed.metadata,
            posting_rule_version=proposed.posting_rule_version,
            # R21: Reference snapshot versions
            coa_version=entry.coa_version,
            dimension_schema_version=entry.dimension_schema_version,
            rounding_policy_version=entry.rounding_policy_version,
            currency_registry_version=entry.currency_registry_version,
        )

        return LedgerResult.success(record)

    def _complete_draft(
        self,
        entry: JournalEntry,
        proposed: ProposedJournalEntry,
    ) -> LedgerResult:
        """Complete a draft entry (crash recovery path)."""
        # Delete any existing lines and recreate
        for line in entry.lines:
            self._session.delete(line)
        self._session.flush()

        # Recreate lines
        self._create_lines(entry, proposed.lines)

        # Finalize
        return self._finalize_posting(entry, proposed)

    def get_entry(self, entry_id: UUID) -> JournalEntry | None:
        """Get a journal entry by ID."""
        return self._session.execute(
            select(JournalEntry).where(JournalEntry.id == entry_id)
        ).scalar_one_or_none()

    def get_entry_by_event(self, event_id: UUID) -> JournalEntry | None:
        """Get journal entry by source event ID."""
        return self._session.execute(
            select(JournalEntry).where(JournalEntry.source_event_id == event_id)
        ).scalar_one_or_none()

    def get_entry_by_seq(self, seq: int) -> JournalEntry | None:
        """Get journal entry by sequence number."""
        return self._session.execute(
            select(JournalEntry).where(JournalEntry.seq == seq)
        ).scalar_one_or_none()
