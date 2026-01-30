"""
JournalWriter service for atomic multi-ledger posting.

The JournalWriter takes an AccountingIntent and creates JournalEntry records
for all affected ledgers in a single transaction.

Invariants enforced:
- P11: Multi-ledger postings are atomic (single transaction)
- L1: Every role resolves to exactly one COA account
- L5: Coordinated with OutcomeRecorder (same transaction boundary)

The JournalWriter does NOT:
- Manage the transaction boundary (caller's responsibility for L5)
- Create the InterpretationOutcome (that's OutcomeRecorder's job)
- Transform events (that's MeaningBuilder's job)
"""

import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    IntentLine,
    IntentLineSide,
    LedgerIntent,
    ResolvedIntentLine,
)
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.exceptions import (
    MissingReferenceSnapshotError,
    MultipleRoundingLinesError,
    RoundingAmountExceededError,
)
from finance_kernel.models.journal import (
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    LineSide,
)
from finance_kernel.logging_config import get_logger
from finance_kernel.services.sequence_service import SequenceService

if TYPE_CHECKING:
    from finance_kernel.services.auditor_service import AuditorService

logger = get_logger("services.journal_writer")


class WriteStatus(str, Enum):
    """Status of a write operation."""

    WRITTEN = "written"
    ALREADY_EXISTS = "already_exists"
    ROLE_RESOLUTION_FAILED = "role_resolution_failed"
    VALIDATION_FAILED = "validation_failed"
    FAILED = "failed"


class RoleResolutionError(Exception):
    """Failed to resolve account role to COA account."""

    code: str = "ROLE_RESOLUTION_FAILED"

    def __init__(self, role: str, ledger_id: str, coa_version: int):
        self.role = role
        self.ledger_id = ledger_id
        self.coa_version = coa_version
        super().__init__(
            f"Cannot resolve role '{role}' for ledger '{ledger_id}' "
            f"at COA version {coa_version}"
        )


class UnbalancedIntentError(Exception):
    """Ledger intent is not balanced."""

    code: str = "UNBALANCED_INTENT"

    def __init__(self, ledger_id: str, currency: str, imbalance: Decimal):
        self.ledger_id = ledger_id
        self.currency = currency
        self.imbalance = imbalance
        super().__init__(
            f"Ledger '{ledger_id}' is unbalanced for {currency}: "
            f"imbalance = {imbalance}"
        )


@dataclass(frozen=True)
class WrittenEntry:
    """A successfully written journal entry."""

    entry_id: UUID
    ledger_id: str
    seq: int
    idempotency_key: str


@dataclass(frozen=True)
class JournalWriteResult:
    """
    Result of a JournalWriter.write() operation.

    Contains the status and either written entries or error info.
    """

    status: WriteStatus
    entries: tuple[WrittenEntry, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    unresolved_roles: tuple[str, ...] | None = None

    @classmethod
    def success(cls, entries: tuple[WrittenEntry, ...]) -> "JournalWriteResult":
        """Create a successful result."""
        return cls(status=WriteStatus.WRITTEN, entries=entries)

    @classmethod
    def already_exists(
        cls, entries: tuple[WrittenEntry, ...]
    ) -> "JournalWriteResult":
        """Create an already-exists result (idempotent success)."""
        return cls(status=WriteStatus.ALREADY_EXISTS, entries=entries)

    @classmethod
    def role_resolution_failed(
        cls, roles: tuple[str, ...], message: str
    ) -> "JournalWriteResult":
        """Create a role resolution failure result."""
        return cls(
            status=WriteStatus.ROLE_RESOLUTION_FAILED,
            error_code="ROLE_RESOLUTION_FAILED",
            error_message=message,
            unresolved_roles=roles,
        )

    @classmethod
    def validation_failed(
        cls, error_code: str, message: str
    ) -> "JournalWriteResult":
        """Create a validation failure result."""
        return cls(
            status=WriteStatus.VALIDATION_FAILED,
            error_code=error_code,
            error_message=message,
        )

    @classmethod
    def failure(cls, error_code: str, message: str) -> "JournalWriteResult":
        """Create a general failure result."""
        return cls(
            status=WriteStatus.FAILED,
            error_code=error_code,
            error_message=message,
        )

    @property
    def is_success(self) -> bool:
        """Check if operation was successful (including idempotent success)."""
        return self.status in (WriteStatus.WRITTEN, WriteStatus.ALREADY_EXISTS)

    @property
    def entry_ids(self) -> tuple[UUID, ...]:
        """Get all entry IDs."""
        return tuple(e.entry_id for e in self.entries)


class RoleResolver:
    """
    Resolves account roles to COA accounts.

    This is a simple in-memory resolver. In production, this would
    query the COA binding table based on coa_version and effective date.
    """

    def __init__(self):
        # role -> (account_id, account_code) mapping
        # In production, this would be loaded from database
        self._bindings: dict[str, tuple[UUID, str]] = {}

    def register_binding(
        self, role: str, account_id: UUID, account_code: str
    ) -> None:
        """Register a role binding."""
        self._bindings[role] = (account_id, account_code)

    def resolve(
        self,
        role: str,
        ledger_id: str,
        coa_version: int,
    ) -> tuple[UUID, str]:
        """
        Resolve a role to account.

        Args:
            role: The account role (e.g., "InventoryAsset")
            ledger_id: The target ledger
            coa_version: The COA version to use

        Returns:
            Tuple of (account_id, account_code)

        Raises:
            RoleResolutionError: If role cannot be resolved
        """
        if role not in self._bindings:
            raise RoleResolutionError(role, ledger_id, coa_version)
        return self._bindings[role]

    def clear(self) -> None:
        """Clear all bindings. For testing only."""
        self._bindings.clear()


class JournalWriter:
    """
    Service for atomic multi-ledger journal posting.

    The JournalWriter:
    1. Resolves account roles to COA accounts
    2. Validates balancing for each ledger
    3. Creates JournalEntry and JournalLine records
    4. Assigns sequence numbers transactionally
    5. Handles idempotency per ledger

    P11: All ledger entries are created in the same transaction.
    The caller must also create the InterpretationOutcome in the same
    transaction for L5 compliance.

    Usage:
        writer = JournalWriter(session, role_resolver, clock)
        result = writer.write(accounting_intent, actor_id)

        if result.is_success:
            # All entries written
            for entry in result.entries:
                print(f"Wrote {entry.entry_id} to {entry.ledger_id}")
        else:
            # Handle failure
            print(f"Failed: {result.error_message}")
    """

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
        auditor: "AuditorService | None" = None,
    ):
        """
        Initialize the JournalWriter.

        Args:
            session: SQLAlchemy session.
            role_resolver: Resolver for account roles.
            clock: Clock for timestamps. Defaults to SystemClock.
            auditor: Optional auditor service.
        """
        self._session = session
        self._role_resolver = role_resolver
        self._clock = clock or SystemClock()
        self._auditor = auditor
        self._sequence_service = SequenceService(session)

    def write(
        self,
        intent: AccountingIntent,
        actor_id: UUID,
        event_type: str = "economic.posting",
    ) -> JournalWriteResult:
        """
        Write journal entries for all ledgers in the intent.

        P11: All ledger entries are written atomically.

        Args:
            intent: The accounting intent to write.
            actor_id: Who is performing the write.
            event_type: Event type for the journal entries.

        Returns:
            JournalWriteResult with written entries or error.
        """
        t0 = time.monotonic()
        logger.info(
            "journal_write_started",
            extra={
                "source_event_id": str(intent.source_event_id),
                "ledger_count": len(intent.ledger_intents),
            },
        )

        # Validate all ledger intents are balanced
        for ledger_intent in intent.ledger_intents:
            for currency in ledger_intent.currencies:
                sum_debit = ledger_intent.total_debits(currency)
                sum_credit = ledger_intent.total_credits(currency)
                balanced = ledger_intent.is_balanced(currency)

                logger.info(
                    "balance_validated",
                    extra={
                        "ledger_id": ledger_intent.ledger_id,
                        "currency": currency,
                        "sum_debit": str(sum_debit),
                        "sum_credit": str(sum_credit),
                        "balanced": balanced,
                        "source_event_id": str(intent.source_event_id),
                    },
                )

                if not balanced:
                    imbalance = sum_debit - sum_credit
                    logger.warning(
                        "unbalanced_intent",
                        extra={
                            "ledger_id": ledger_intent.ledger_id,
                            "currency": currency,
                            "imbalance": str(imbalance),
                        },
                    )
                    return JournalWriteResult.validation_failed(
                        "UNBALANCED_INTENT",
                        f"Ledger '{ledger_intent.ledger_id}' is unbalanced for "
                        f"{currency}: imbalance = {imbalance}",
                    )

        # Resolve all roles first
        try:
            resolved_intents = self._resolve_all_roles(intent)
        except RoleResolutionError as e:
            logger.warning(
                "role_resolution_failed",
                extra={"unresolved_roles": (e.role,)},
            )
            return JournalWriteResult.role_resolution_failed(
                (e.role,), str(e)
            )

        # Check idempotency for all ledgers
        existing_entries: list[WrittenEntry] = []
        new_intents: list[tuple[LedgerIntent, list[ResolvedIntentLine]]] = []

        for ledger_intent, resolved_lines in resolved_intents:
            idempotency_key = intent.idempotency_key(ledger_intent.ledger_id)
            existing = self._get_existing_entry(idempotency_key)

            if existing is not None:
                if existing.status in (
                    JournalEntryStatus.POSTED,
                    JournalEntryStatus.REVERSED,
                ):
                    existing_entries.append(
                        WrittenEntry(
                            entry_id=existing.id,
                            ledger_id=ledger_intent.ledger_id,
                            seq=existing.seq or 0,
                            idempotency_key=idempotency_key,
                        )
                    )
                else:
                    # Draft exists - needs completion
                    new_intents.append((ledger_intent, resolved_lines))
            else:
                new_intents.append((ledger_intent, resolved_lines))

        # If all entries already exist, return idempotent success
        if not new_intents:
            logger.info("journal_write_idempotent")
            return JournalWriteResult.already_exists(tuple(existing_entries))

        # Create new entries for remaining ledgers
        written_entries: list[WrittenEntry] = list(existing_entries)

        for ledger_intent, resolved_lines in new_intents:
            try:
                entry = self._create_entry(
                    intent=intent,
                    ledger_intent=ledger_intent,
                    resolved_lines=resolved_lines,
                    actor_id=actor_id,
                    event_type=event_type,
                )
                written_entries.append(
                    WrittenEntry(
                        entry_id=entry.id,
                        ledger_id=ledger_intent.ledger_id,
                        seq=entry.seq or 0,
                        idempotency_key=entry.idempotency_key,
                    )
                )
            except IntegrityError:
                # Concurrent insert - fetch existing
                self._session.rollback()
                logger.warning("concurrent_insert_conflict")
                idempotency_key = intent.idempotency_key(ledger_intent.ledger_id)
                existing = self._get_existing_entry(idempotency_key)
                if existing:
                    written_entries.append(
                        WrittenEntry(
                            entry_id=existing.id,
                            ledger_id=ledger_intent.ledger_id,
                            seq=existing.seq or 0,
                            idempotency_key=idempotency_key,
                        )
                    )
                else:
                    return JournalWriteResult.failure(
                        "CONCURRENT_INSERT",
                        f"Concurrent insert conflict for ledger "
                        f"'{ledger_intent.ledger_id}'",
                    )

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info(
            "journal_write_completed",
            extra={
                "entry_count": len(written_entries),
                "source_event_id": str(intent.source_event_id),
                "duration_ms": duration_ms,
            },
        )
        return JournalWriteResult.success(tuple(written_entries))

    def _resolve_all_roles(
        self, intent: AccountingIntent
    ) -> list[tuple[LedgerIntent, list[ResolvedIntentLine]]]:
        """Resolve all roles in all ledger intents."""
        resolved: list[tuple[LedgerIntent, list[ResolvedIntentLine]]] = []

        for ledger_intent in intent.ledger_intents:
            resolved_lines: list[ResolvedIntentLine] = []

            for i, line in enumerate(ledger_intent.lines):
                account_id, account_code = self._role_resolver.resolve(
                    line.account_role,
                    ledger_intent.ledger_id,
                    intent.snapshot.coa_version,
                )

                logger.info(
                    "role_resolved",
                    extra={
                        "role": line.account_role,
                        "account_code": account_code,
                        "account_id": str(account_id),
                        "ledger_id": ledger_intent.ledger_id,
                        "coa_version": intent.snapshot.coa_version,
                        "line_seq": i,
                        "side": line.side,
                        "amount": str(line.money.amount),
                        "currency": line.money.currency,
                        "source_event_id": str(intent.source_event_id),
                    },
                )

                resolved_lines.append(
                    ResolvedIntentLine(
                        account_id=account_id,
                        account_code=account_code,
                        account_role=line.account_role,
                        side=line.side,
                        money=line.money,
                        dimensions=line.dimensions,
                        memo=line.memo,
                        is_rounding=line.is_rounding,
                        line_seq=i,
                    )
                )

            resolved.append((ledger_intent, resolved_lines))

        return resolved

    def _get_existing_entry(self, idempotency_key: str) -> JournalEntry | None:
        """Get existing entry by idempotency key."""
        return self._session.execute(
            select(JournalEntry)
            .where(JournalEntry.idempotency_key == idempotency_key)
            .with_for_update()
        ).scalar_one_or_none()

    def _create_entry(
        self,
        intent: AccountingIntent,
        ledger_intent: LedgerIntent,
        resolved_lines: list[ResolvedIntentLine],
        actor_id: UUID,
        event_type: str,
    ) -> JournalEntry:
        """Create a journal entry for a single ledger."""
        now = self._clock.now()
        idempotency_key = intent.idempotency_key(ledger_intent.ledger_id)

        # Create draft entry
        entry = JournalEntry(
            id=uuid4(),
            source_event_id=intent.source_event_id,
            source_event_type=event_type,
            occurred_at=intent.created_at or now,
            effective_date=intent.effective_date,
            actor_id=actor_id,
            status=JournalEntryStatus.DRAFT,
            idempotency_key=idempotency_key,
            posting_rule_version=intent.profile_version,
            description=intent.description,
            entry_metadata={
                "ledger_id": ledger_intent.ledger_id,
                "profile_id": intent.profile_id,
                "econ_event_id": str(intent.econ_event_id),
            },
            created_by_id=actor_id,
            # Reference snapshot versions
            coa_version=intent.snapshot.coa_version,
            dimension_schema_version=intent.snapshot.dimension_schema_version,
            rounding_policy_version=intent.snapshot.rounding_policy_version,
            currency_registry_version=intent.snapshot.currency_registry_version,
        )

        self._session.add(entry)
        self._session.flush()

        # Create lines
        self._create_lines(entry, resolved_lines, actor_id)

        # Finalize posting
        self._finalize_posting(entry)

        return entry

    def _create_lines(
        self,
        entry: JournalEntry,
        resolved_lines: list[ResolvedIntentLine],
        actor_id: UUID,
    ) -> None:
        """Create journal lines for an entry."""
        # Validate rounding invariants
        self._validate_rounding_invariants(entry.id, resolved_lines)

        for line in resolved_lines:
            journal_line = JournalLine(
                journal_entry_id=entry.id,
                account_id=line.account_id,
                side=LineSide(line.side),
                amount=line.amount,
                currency=line.currency,
                dimensions=line.dimensions,
                is_rounding=line.is_rounding,
                line_memo=line.memo,
                line_seq=line.line_seq,
                created_by_id=actor_id,
            )
            self._session.add(journal_line)

            logger.info(
                "line_written",
                extra={
                    "entry_id": str(entry.id),
                    "line_seq": line.line_seq,
                    "role": line.account_role,
                    "account_code": line.account_code,
                    "account_id": str(line.account_id),
                    "side": line.side,
                    "amount": str(line.amount),
                    "currency": line.currency,
                    "is_rounding": line.is_rounding,
                },
            )

        self._session.flush()

    def _validate_rounding_invariants(
        self,
        entry_id: UUID,
        lines: list[ResolvedIntentLine],
    ) -> None:
        """Validate rounding invariants."""
        rounding_lines = [line for line in lines if line.is_rounding]
        non_rounding_lines = [line for line in lines if not line.is_rounding]

        # At most ONE rounding line
        if len(rounding_lines) > 1:
            raise MultipleRoundingLinesError(
                entry_id=str(entry_id),
                rounding_count=len(rounding_lines),
            )

        # Rounding amount threshold
        if rounding_lines:
            rounding_line = rounding_lines[0]
            max_allowed = max(
                Decimal("0.01"),
                Decimal("0.01") * len(non_rounding_lines),
            )
            if rounding_line.amount > max_allowed:
                raise RoundingAmountExceededError(
                    entry_id=str(entry_id),
                    rounding_amount=str(rounding_line.amount),
                    threshold=str(max_allowed),
                    currency=rounding_line.currency,
                )

    def _finalize_posting(self, entry: JournalEntry) -> None:
        """Assign sequence and mark as posted."""
        # Validate reference snapshots
        self._validate_reference_snapshots(entry)

        logger.info(
            "invariant_checked",
            extra={
                "invariant": "R21_REFERENCE_SNAPSHOT",
                "entry_id": str(entry.id),
                "passed": True,
                "coa_version": entry.coa_version,
                "dimension_schema_version": entry.dimension_schema_version,
                "rounding_policy_version": entry.rounding_policy_version,
                "currency_registry_version": entry.currency_registry_version,
            },
        )

        # Assign sequence number
        seq = self._sequence_service.next_value(SequenceService.JOURNAL_ENTRY)
        entry.seq = seq
        entry.posted_at = self._clock.now()
        entry.status = JournalEntryStatus.POSTED

        self._session.flush()

        logger.info(
            "journal_entry_created",
            extra={
                "entry_id": str(entry.id),
                "source_event_id": str(entry.source_event_id),
                "status": entry.status.value,
                "seq": entry.seq,
                "idempotency_key": entry.idempotency_key,
                "effective_date": str(entry.effective_date),
                "posted_at": str(entry.posted_at),
                "profile_id": entry.entry_metadata.get("profile_id") if entry.entry_metadata else None,
                "ledger_id": entry.entry_metadata.get("ledger_id") if entry.entry_metadata else None,
            },
        )

    def _validate_reference_snapshots(self, entry: JournalEntry) -> None:
        """Validate reference snapshot versions are present."""
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

    def get_entries_for_intent(
        self, intent: AccountingIntent
    ) -> list[JournalEntry]:
        """Get all journal entries for an intent."""
        entries = []
        for ledger_intent in intent.ledger_intents:
            idempotency_key = intent.idempotency_key(ledger_intent.ledger_id)
            entry = self._session.execute(
                select(JournalEntry).where(
                    JournalEntry.idempotency_key == idempotency_key
                )
            ).scalar_one_or_none()
            if entry:
                entries.append(entry)
        return entries
