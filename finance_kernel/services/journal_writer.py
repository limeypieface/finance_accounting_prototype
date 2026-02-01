"""
JournalWriter — atomic multi-ledger journal posting service.

Responsibility:
    Transforms an AccountingIntent (expressed in account ROLES) into
    persisted JournalEntry and JournalLine rows (expressed in COA codes).
    Handles role resolution, balance validation, rounding invariants,
    sequence assignment, idempotency, and subledger control enforcement.

Architecture position:
    Kernel > Services — imperative shell, owns transaction boundaries.
    Called by InterpretationCoordinator; delegates sequence allocation
    to SequenceService and role resolution to RoleResolver.

Invariants enforced:
    R3  — Idempotency key uniqueness (via UNIQUE constraint + FOR UPDATE).
    R4  — Debits = Credits per currency per entry (balance validation).
    R5  — At most one is_rounding=True line per entry; threshold enforced.
    R9  — Sequence monotonicity via SequenceService (never raw SQL max+1).
    R10 — Posted record immutability (status transitions DRAFT -> POSTED).
    R21 — Reference snapshot versions recorded on every JournalEntry.
    R22 — Only Bookkeeper may create is_rounding lines (enforced at intent).
    L1  — Every account role resolves to exactly one COA account.
    P11 — Multi-ledger postings are atomic (single transaction).
    L5  — Coordinated with OutcomeRecorder (same transaction boundary).
    SL-G3 — Per-currency subledger reconciliation (when registry present).
    SL-G5 — Blocking violations abort the transaction.
    G10 — Reference snapshot freshness validation (when snapshot service present).

Failure modes:
    - ROLE_RESOLUTION_FAILED: Account role has no COA binding.
    - UNBALANCED_INTENT: Debits != Credits for a currency in a ledger.
    - CONCURRENT_INSERT: Race condition on idempotency key.
    - FAILED: General write failure.
    - MultipleRoundingLinesError: More than one rounding line per entry.
    - RoundingAmountExceededError: Rounding line exceeds threshold.
    - MissingReferenceSnapshotError: Required snapshot version is NULL.
    - StaleReferenceSnapshotError: Snapshot has changed since capture.
    - SubledgerReconciliationError: SL/GL balance out of tolerance.

Audit relevance:
    Every role resolution, balance validation, line write, and entry
    finalization is logged with structured fields for the decision journal.
    The invariant_checked log entry records R21 compliance per entry.

Non-goals:
    - Does NOT manage the transaction boundary (caller's responsibility).
    - Does NOT create the InterpretationOutcome (that is OutcomeRecorder).
    - Does NOT transform events (that is MeaningBuilder).
"""

import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import NamedTuple, TYPE_CHECKING
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
    StaleReferenceSnapshotError,
    SubledgerReconciliationError,
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
    from finance_kernel.domain.subledger_control import (
        SubledgerControlRegistry,
        SubledgerReconciler,
    )
    from finance_kernel.services.auditor_service import AuditorService
    from finance_kernel.services.reference_snapshot_service import ReferenceSnapshotService

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


class BindingRecord(NamedTuple):
    """Full provenance record for a role-to-account binding.

    Stored on RoleResolver so that journal_writer can log the complete
    binding context into the decision journal at role-resolution time.
    """

    account_id: UUID
    account_code: str
    account_name: str = ""
    account_type: str = ""
    normal_balance: str = ""
    effective_from: str = ""      # ISO date or ""
    effective_to: str = ""        # ISO date or ""
    config_id: str = ""
    config_version: int = 0


class RoleResolver:
    """
    Resolves account roles to COA accounts.

    This is a simple in-memory resolver. In production, this would
    query the COA binding table based on coa_version and effective date.
    """

    def __init__(self):
        self._bindings: dict[str, BindingRecord] = {}

    def register_binding(
        self,
        role: str,
        account_id: UUID,
        account_code: str,
        *,
        account_name: str = "",
        account_type: str = "",
        normal_balance: str = "",
        effective_from: str = "",
        effective_to: str = "",
        config_id: str = "",
        config_version: int = 0,
    ) -> None:
        """Register a role binding with optional provenance metadata."""
        self._bindings[role] = BindingRecord(
            account_id=account_id,
            account_code=account_code,
            account_name=account_name,
            account_type=account_type,
            normal_balance=normal_balance,
            effective_from=effective_from,
            effective_to=effective_to,
            config_id=config_id,
            config_version=config_version,
        )

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
        binding = self._bindings[role]
        return (binding.account_id, binding.account_code)

    def resolve_full(
        self,
        role: str,
        ledger_id: str,
        coa_version: int,
    ) -> BindingRecord:
        """Resolve a role and return the full BindingRecord with provenance."""
        if role not in self._bindings:
            raise RoleResolutionError(role, ledger_id, coa_version)
        return self._bindings[role]

    def clear(self) -> None:
        """Clear all bindings. For testing only."""
        self._bindings.clear()


class JournalWriter:
    """
    Service for atomic multi-ledger journal posting.

    Contract:
        Accepts an AccountingIntent (which uses abstract account ROLES)
        and produces persisted JournalEntry + JournalLine rows (which
        use concrete COA codes).  Returns a JournalWriteResult indicating
        success, idempotent duplicate, or a typed failure.

    Guarantees:
        - L1: Every role resolves to exactly one COA account at post time.
        - R4: Debits = Credits per currency per entry (validated pre-write).
        - R5: At most one rounding line; rounding amount within threshold.
        - R9: Sequence assigned via SequenceService (never raw SQL max+1).
        - R21: Snapshot version columns populated on every JournalEntry.
        - P11: All ledger entries created in the same transaction.
        - Idempotent: duplicate idempotency_key yields ALREADY_EXISTS.

    Non-goals:
        - Does NOT call session.commit() — caller controls boundaries.
        - Does NOT create the InterpretationOutcome (OutcomeRecorder).
        - Does NOT enforce period locks (PeriodService / caller).

    Usage:
        writer = JournalWriter(session, role_resolver, clock)
        result = writer.write(accounting_intent, actor_id)

        if result.is_success:
            for entry in result.entries:
                print(f"Wrote {entry.entry_id} to {entry.ledger_id}")
        else:
            print(f"Failed: {result.error_message}")
    """

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
        auditor: "AuditorService | None" = None,
        subledger_control_registry: "SubledgerControlRegistry | None" = None,
        snapshot_service: "ReferenceSnapshotService | None" = None,
    ):
        """
        Initialize the JournalWriter.

        Args:
            session: SQLAlchemy session.
            role_resolver: Resolver for account roles.
            clock: Clock for timestamps. Defaults to SystemClock.
            auditor: Optional auditor service.
            subledger_control_registry: Optional subledger control registry
                for post-time reconciliation (G9). When provided, each posted
                entry is validated against subledger control contracts.
            snapshot_service: Optional reference snapshot service for
                freshness validation (G10). When provided, validates that
                the intent's snapshot is still current at posting time.
        """
        self._session = session
        self._role_resolver = role_resolver
        self._clock = clock or SystemClock()
        self._auditor = auditor
        self._subledger_control_registry = subledger_control_registry
        self._snapshot_service = snapshot_service
        self._sequence_service = SequenceService(session)

    def write(
        self,
        intent: AccountingIntent,
        actor_id: UUID,
        event_type: str = "economic.posting",
    ) -> JournalWriteResult:
        """
        Write journal entries for all ledgers in the intent.

        Preconditions:
            - ``intent`` contains at least one LedgerIntent.
            - Each LedgerIntent is balanced per currency (R4).
            - All account roles in the intent have valid bindings.

        Postconditions:
            - On success: All JournalEntry rows are POSTED with
              monotonic sequence numbers and snapshot versions.
            - On idempotent duplicate: ALREADY_EXISTS with existing IDs.
            - On failure: No partial entries are left in DRAFT state.

        Raises:
            MultipleRoundingLinesError: More than one rounding line (R5).
            RoundingAmountExceededError: Rounding exceeds threshold (R5).
            MissingReferenceSnapshotError: Snapshot field is NULL (R21).
            SubledgerReconciliationError: SL/GL imbalance (SL-G5).
            StaleReferenceSnapshotError: Snapshot data changed (G10).

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

        # INVARIANT: R4 — Debits = Credits per currency per entry
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

        # INVARIANT: L1 — Every account role resolves to exactly one COA account
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

        # INVARIANT: R3 — Idempotency key uniqueness check
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

        # G9: Subledger control reconciliation (post-time enforcement)
        if self._subledger_control_registry is not None:
            self._validate_subledger_controls(intent)

        # G10: Reference snapshot freshness validation
        if self._snapshot_service is not None and intent.snapshot.full_snapshot_id:
            self._validate_snapshot_freshness(intent)

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
                binding = self._role_resolver.resolve_full(
                    line.account_role,
                    ledger_intent.ledger_id,
                    intent.snapshot.coa_version,
                )
                account_id = binding.account_id
                account_code = binding.account_code

                logger.info(
                    "role_resolved",
                    extra={
                        "role": line.account_role,
                        "account_code": account_code,
                        "account_id": str(account_id),
                        "account_name": binding.account_name,
                        "account_type": binding.account_type,
                        "normal_balance": binding.normal_balance,
                        "ledger_id": ledger_intent.ledger_id,
                        "coa_version": intent.snapshot.coa_version,
                        "line_seq": i,
                        "side": line.side,
                        "amount": str(line.money.amount),
                        "currency": line.money.currency,
                        "source_event_id": str(intent.source_event_id),
                        "binding_effective_from": binding.effective_from,
                        "binding_effective_to": binding.effective_to or "open",
                        "config_id": binding.config_id,
                        "config_version": binding.config_version,
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
        """Validate rounding invariants (R5, R22).

        Preconditions:
            - ``lines`` is a non-empty list of resolved intent lines.

        Postconditions:
            - At most one line has ``is_rounding=True``.
            - Rounding amount does not exceed the threshold.

        Raises:
            MultipleRoundingLinesError: If more than one rounding line.
            RoundingAmountExceededError: If rounding exceeds threshold.
        """
        # INVARIANT: R5 — At most ONE is_rounding=True line per entry
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
        """Assign sequence and mark as posted.

        Preconditions:
            - ``entry`` is a DRAFT JournalEntry with lines already flushed.

        Postconditions:
            - ``entry.status`` is POSTED.
            - ``entry.seq`` is a monotonically increasing sequence number.
            - ``entry.posted_at`` is set to the current clock time.
            - R21 snapshot columns are validated as non-NULL.

        Raises:
            MissingReferenceSnapshotError: If required snapshot fields are NULL.
        """
        # INVARIANT: R21 — Reference snapshot determinism
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

        # INVARIANT: R9 — Sequence monotonicity via locked counter row
        seq = self._sequence_service.next_value(SequenceService.JOURNAL_ENTRY)
        assert seq > 0, "R9 violation: sequence must be strictly positive"
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

    def _validate_subledger_controls(self, intent: AccountingIntent) -> None:
        """
        G9: Validate subledger control contracts after posting.

        For each subledger ledger intent with enforce_on_post=True:
        1. Get GL control account balance (already includes flushed journal entries)
        2. Get SL aggregate balance (before SL entries are created)
        3. Compute projected SL balance after SL entries would be created
        4. Run reconciler to check if SL-GL will be in balance
        5. Raise SubledgerReconciliationError if blocking violations found

        Invariants enforced:
        - SL-G3: Per-currency reconciliation
        - SL-G4: Same session for all queries (snapshot isolation)
        - SL-G5: Blocking violations abort the transaction
        """
        from finance_kernel.domain.subledger_control import (
            SubledgerReconciler,
            SubledgerType,
        )
        from finance_kernel.domain.values import Money
        from finance_kernel.selectors.subledger_selector import SubledgerSelector
        from finance_kernel.selectors.ledger_selector import LedgerSelector

        registry = self._subledger_control_registry
        if registry is None:
            return

        reconciler = SubledgerReconciler()
        # SL-G4: Selectors created lazily but share the SAME session
        # (snapshot isolation). Lazy init avoids accessing self._session
        # when no subledger ledger intents require enforcement.
        sl_selector: SubledgerSelector | None = None
        gl_selector: LedgerSelector | None = None

        for ledger_intent in intent.ledger_intents:
            # Convert ledger_id (str) to SubledgerType for registry lookup.
            # Non-subledger ledgers (e.g., "GL") won't match any SubledgerType
            # and are safely skipped.
            try:
                sl_type = SubledgerType(ledger_intent.ledger_id)
            except ValueError:
                continue

            contract = registry.get(sl_type)
            if contract is None or not contract.enforce_on_post:
                continue

            # Lazy-init selectors on first use (SL-G4: same session)
            if sl_selector is None:
                sl_selector = SubledgerSelector(self._session)
                gl_selector = LedgerSelector(self._session)

            # Resolve GL control account from the binding's role
            try:
                control_account_id, _ = self._role_resolver.resolve(
                    contract.control_account_role,
                    "GL",
                    intent.snapshot.coa_version,
                )
            except RoleResolutionError:
                logger.warning(
                    "subledger_control_account_unresolvable",
                    extra={
                        "subledger_type": sl_type.value,
                        "control_account_role": contract.control_account_role,
                        "source_event_id": str(intent.source_event_id),
                    },
                )
                continue

            # SL-G3: Check per currency
            for currency in ledger_intent.currencies:
                # 1. GL control account balance "after" posting.
                #    Journal entries are already flushed in the same transaction,
                #    so LedgerSelector sees them.
                gl_balances = gl_selector.account_balance(
                    account_id=control_account_id,
                    as_of_date=intent.effective_date,
                    currency=currency,
                )
                if gl_balances:
                    raw_gl_balance = gl_balances[0].balance  # always debit - credit
                else:
                    raw_gl_balance = Decimal("0")

                # Normalize GL balance to match SL sign convention:
                # GL always returns (debit - credit).
                # Credit-normal accounts (AP, PAYROLL): negate to get
                # economic balance matching SL convention (credit - debit).
                # Debit-normal accounts (AR, INVENTORY, BANK): use as-is.
                if not contract.binding.is_debit_normal:
                    gl_economic = -raw_gl_balance
                else:
                    gl_economic = raw_gl_balance

                control_balance_after = Money.of(gl_economic, currency)

                # 2. SL aggregate balance "before" (SL entries not yet created)
                sl_before = sl_selector.get_aggregate_balance(
                    subledger_type=sl_type,
                    as_of_date=intent.effective_date,
                    currency=currency,
                )

                # 3. Compute projected SL delta from intent lines.
                #    SL balance convention:
                #      debit-normal:  delta = sum(debits) - sum(credits)
                #      credit-normal: delta = sum(credits) - sum(debits)
                debit_total = ledger_intent.total_debits(currency)
                credit_total = ledger_intent.total_credits(currency)

                if contract.binding.is_debit_normal:
                    sl_delta = debit_total - credit_total
                else:
                    sl_delta = credit_total - debit_total

                sl_after = Money.of(sl_before.amount + sl_delta, currency)

                # 4. Run reconciler.
                #    validate_post() only uses "after" values for enforcement;
                #    "before" values are passed for audit completeness.
                checked_at = self._clock.now()
                violations = reconciler.validate_post(
                    contract=contract,
                    subledger_balance_before=sl_before,
                    subledger_balance_after=sl_after,
                    control_balance_before=control_balance_after,
                    control_balance_after=control_balance_after,
                    as_of_date=intent.effective_date,
                    checked_at=checked_at,
                )

                # 5. SL-G5: Blocking violations abort the transaction
                blocking = [v for v in violations if v.blocking]
                if blocking:
                    violation_msgs = [v.message for v in blocking]
                    logger.error(
                        "subledger_control_violation",
                        extra={
                            "subledger_type": sl_type.value,
                            "currency": currency,
                            "sl_balance_before": str(sl_before.amount),
                            "sl_balance_after": str(sl_after.amount),
                            "gl_control_balance": str(gl_economic),
                            "variance": str(sl_after.amount - gl_economic),
                            "source_event_id": str(intent.source_event_id),
                            "violations": violation_msgs,
                        },
                    )
                    raise SubledgerReconciliationError(
                        ledger_id=ledger_intent.ledger_id,
                        violations=violation_msgs,
                    )

                # Non-blocking violations: log warning and continue
                non_blocking = [v for v in violations if not v.blocking]
                if non_blocking:
                    for v in non_blocking:
                        logger.warning(
                            "subledger_control_warning",
                            extra={
                                "subledger_type": sl_type.value,
                                "currency": currency,
                                "message": v.message,
                                "source_event_id": str(intent.source_event_id),
                            },
                        )
                else:
                    logger.info(
                        "subledger_control_check",
                        extra={
                            "subledger_type": sl_type.value,
                            "currency": currency,
                            "sl_balance_after": str(sl_after.amount),
                            "gl_control_balance": str(gl_economic),
                            "status": "reconciled",
                            "source_event_id": str(intent.source_event_id),
                        },
                    )

    def _validate_snapshot_freshness(self, intent: AccountingIntent) -> None:
        """
        G10: Validate reference snapshot is still current.

        Retrieves the full snapshot by ID and validates that its
        component hashes still match the current state of reference data.
        If any component has changed, raises StaleReferenceSnapshotError.
        """
        if self._snapshot_service is None:
            return

        snapshot_id = intent.snapshot.full_snapshot_id
        if snapshot_id is None:
            return

        # Retrieve the full snapshot object
        full_snapshot = self._snapshot_service.get(snapshot_id)
        if full_snapshot is None:
            logger.warning(
                "snapshot_not_found_for_freshness_check",
                extra={
                    "snapshot_id": str(snapshot_id),
                    "source_event_id": str(intent.source_event_id),
                },
            )
            return

        validation_result = self._snapshot_service.validate_integrity(
            full_snapshot
        )

        if not validation_result.is_valid:
            stale_components = [
                err.component_type if hasattr(err, "component_type") else str(err)
                for err in validation_result.errors
            ]
            raise StaleReferenceSnapshotError(
                entry_id=str(intent.source_event_id),
                stale_components=stale_components,
            )

        logger.info(
            "snapshot_freshness_validated",
            extra={
                "snapshot_id": str(snapshot_id),
                "source_event_id": str(intent.source_event_id),
                "is_valid": True,
            },
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
