"""Atomic multi-ledger journal posting service."""

import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple
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
    CrossLedgerReversalError,
    MissingReferenceSnapshotError,
    MultipleRoundingLinesError,
    RoundingAmountExceededError,
    StaleReferenceSnapshotError,
    SubledgerReconciliationError,
)
from finance_kernel.logging_config import get_logger
from finance_kernel.models.journal import (
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    LineSide,
)
from finance_kernel.services.sequence_service import SequenceService

if TYPE_CHECKING:
    from finance_kernel.domain.subledger_control import (
        SubledgerControlRegistry,
        SubledgerReconciler,
    )
    from finance_kernel.services.auditor_service import AuditorService
    from finance_kernel.services.reference_snapshot_service import (
        ReferenceSnapshotService,
    )

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
    """Result of a JournalWriter.write() operation."""

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
    """Full provenance record for a role-to-account binding."""

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
    """Resolves account roles to COA accounts."""

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
        """Resolve a role to (account_id, account_code)."""
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
    """Atomic multi-ledger journal posting service (L1, R4, R5, R9, R21, P11)."""

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
        auditor: "AuditorService | None" = None,
        subledger_control_registry: "SubledgerControlRegistry | None" = None,
        snapshot_service: "ReferenceSnapshotService | None" = None,
    ):
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
        """Write journal entries for all ledgers in the intent.

        P11: All ledger entries are written atomically.
        """
        t0 = time.monotonic()
        logger.info(
            "journal_write_started",
            extra={
                "source_event_id": str(intent.source_event_id),
                "ledger_count": len(intent.ledger_intents),
            },
        )

        # INVARIANT: R4 -- Debits = Credits per currency per entry
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

        # INVARIANT: L1 -- Every account role resolves to exactly one COA account
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

        # INVARIANT: R3 -- Idempotency key uniqueness check
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
                # Per-entry balance validation (audit: Dr=Cr per entry)
                for currency in ledger_intent.currencies:
                    sum_d = sum(
                        (line.amount for line in resolved_lines
                         if line.side == "debit" and line.currency == currency),
                        Decimal("0"),
                    )
                    sum_c = sum(
                        (line.amount for line in resolved_lines
                         if line.side == "credit" and line.currency == currency),
                        Decimal("0"),
                    )
                    logger.info(
                        "entry_balance_validated",
                        extra={
                            "entry_id": str(entry.id),
                            "ledger_id": ledger_intent.ledger_id,
                            "currency": currency,
                            "sum_debit": str(sum_d),
                            "sum_credit": str(sum_c),
                            "balanced": sum_d == sum_c,
                            "source_event_id": str(intent.source_event_id),
                        },
                    )
                written_entries.append(
                    WrittenEntry(
                        entry_id=entry.id,
                        ledger_id=ledger_intent.ledger_id,
                        seq=entry.seq or 0,
                        idempotency_key=entry.idempotency_key,
                    )
                )
            except IntegrityError as e:
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

    def write_reversal(
        self,
        original_entry: JournalEntry,
        source_event_id: UUID,
        actor_id: UUID,
        effective_date: date,
        reason: str,
        event_type: str = "system.reversal",
        expected_ledger_id: str | None = None,
    ) -> JournalEntry:
        """Create a reversal entry that mechanically inverts an original entry.

        This is a first-class posting mode that reuses the full posting pipeline
        (_finalize_posting for R9 sequence allocation, R21 snapshot validation)
        but skips role resolution (L1) since we copy exact account IDs.

        Accounting policy: Reversal is evaluated under the same reference data
        snapshot as the original entry, even if posted in a different period.
        This is the correct choice for mechanical backout.

        Preconditions:
            - original_entry must be POSTED.
            - original_entry must have at least one line.
            - source_event_id must reference a valid Event.
            - effective_date must fall in an open fiscal period (enforced by caller).

        Args:
            expected_ledger_id: If provided, the original entry's ledger must match;
                raises CrossLedgerReversalError otherwise (ledger boundary enforcement).

        Postconditions:
            - Returns a new POSTED JournalEntry with reversal_of_id set.
            - Reversal lines are exact mirrors: same accounts, amounts, currencies,
              dimensions, exchange_rate_id -- only side is flipped (DEBIT↔CREDIT).
            - Reversal lines have is_rounding=False (R22: reversals are mechanical
              inversions that should balance exactly without rounding).
            - Idempotency key format: reversal:{original_entry.id}:{ledger_id}
            - R4: Debits == Credits per currency (guaranteed by construction).
            - R9: Monotonic sequence assigned.
            - R21: Snapshot versions copied from original entry.

        Raises:
            CrossLedgerReversalError: If expected_ledger_id is provided and does not
                match the original entry's ledger_id (reject cross-ledger reversals).
        """
        t0 = time.monotonic()

        # Load original lines ordered by line_seq
        original_lines = sorted(original_entry.lines, key=lambda l: l.line_seq)
        if not original_lines:
            raise ValueError(
                f"Cannot reverse entry {original_entry.id}: no lines found"
            )

        # Extract ledger_id from original entry metadata
        ledger_id = (
            original_entry.entry_metadata.get("ledger_id", "GL")
            if original_entry.entry_metadata
            else "GL"
        )

        # Ledger boundary: reversal must be in same ledger as original (reject cross-ledger)
        if expected_ledger_id is not None and expected_ledger_id != ledger_id:
            raise CrossLedgerReversalError(
                journal_entry_id=str(original_entry.id),
                original_ledger_id=ledger_id,
                requested_ledger_id=expected_ledger_id,
            )

        # Deterministic idempotency key
        idempotency_key = f"reversal:{original_entry.id}:{ledger_id}"

        # Check idempotency: if reversal already exists, return it
        existing = self._get_existing_entry(idempotency_key)
        if existing is not None and existing.status in (
            JournalEntryStatus.POSTED,
            JournalEntryStatus.REVERSED,
        ):
            logger.info(
                "reversal_idempotent",
                extra={
                    "original_entry_id": str(original_entry.id),
                    "existing_reversal_id": str(existing.id),
                },
            )
            return existing

        now = self._clock.now()

        # Create the reversal entry (DRAFT initially)
        reversal_entry = JournalEntry(
            id=uuid4(),
            source_event_id=source_event_id,
            source_event_type=event_type,
            occurred_at=now,
            effective_date=effective_date,
            actor_id=actor_id,
            status=JournalEntryStatus.DRAFT,
            reversal_of_id=original_entry.id,
            idempotency_key=idempotency_key,
            posting_rule_version=original_entry.posting_rule_version,
            description=f"Reversal of entry seq {original_entry.seq}: {reason}",
            entry_metadata={
                "ledger_id": ledger_id,
                "reversal_reason": reason,
                "original_entry_id": str(original_entry.id),
            },
            created_by_id=actor_id,
            # R21: Copy snapshot versions from original (same reference data)
            coa_version=original_entry.coa_version,
            dimension_schema_version=original_entry.dimension_schema_version,
            rounding_policy_version=original_entry.rounding_policy_version,
            currency_registry_version=original_entry.currency_registry_version,
        )

        self._session.add(reversal_entry)
        self._session.flush()

        # Create reversal lines: flip side (DEBIT↔CREDIT), preserve everything else
        for original_line in original_lines:
            flipped_side = (
                LineSide.CREDIT
                if original_line.side == LineSide.DEBIT
                else LineSide.DEBIT
            )

            reversal_line = JournalLine(
                journal_entry_id=reversal_entry.id,
                account_id=original_line.account_id,
                side=flipped_side,
                amount=original_line.amount,
                currency=original_line.currency,
                dimensions=original_line.dimensions,
                is_rounding=False,  # R22: reversals don't create rounding lines
                line_memo=f"Reversal of line {original_line.line_seq}",
                exchange_rate_id=original_line.exchange_rate_id,
                line_seq=original_line.line_seq,
                created_by_id=actor_id,
            )
            self._session.add(reversal_line)

        self._session.flush()

        # R4: Verify balance by construction (debits == credits per currency)
        # Since we flipped every line, the reversal is balanced iff original was.
        # Explicit check as defense-in-depth.
        debit_by_ccy: dict[str, Decimal] = {}
        credit_by_ccy: dict[str, Decimal] = {}
        for original_line in original_lines:
            ccy = original_line.currency
            if original_line.side == LineSide.DEBIT:
                # Original debit → reversal credit
                credit_by_ccy[ccy] = credit_by_ccy.get(ccy, Decimal("0")) + original_line.amount
            else:
                # Original credit → reversal debit
                debit_by_ccy[ccy] = debit_by_ccy.get(ccy, Decimal("0")) + original_line.amount

        for ccy in set(debit_by_ccy) | set(credit_by_ccy):
            d = debit_by_ccy.get(ccy, Decimal("0"))
            c = credit_by_ccy.get(ccy, Decimal("0"))
            if d != c:
                raise ValueError(
                    f"R4 violation: reversal of entry {original_entry.id} "
                    f"is unbalanced for {ccy}: debits={d}, credits={c}"
                )

        # Finalize posting: R9 sequence + DRAFT→POSTED + R21 validation
        self._finalize_posting(reversal_entry)

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info(
            "reversal_entry_created",
            extra={
                "reversal_entry_id": str(reversal_entry.id),
                "original_entry_id": str(original_entry.id),
                "seq": reversal_entry.seq,
                "effective_date": str(effective_date),
                "reason": reason,
                "line_count": len(original_lines),
                "duration_ms": duration_ms,
            },
        )

        return reversal_entry

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

    def get_entry(self, entry_id: UUID) -> JournalEntry | None:
        """Load a JournalEntry by its primary key.

        Exposed as a public method so that callers in the services layer
        (e.g. PostingOrchestrator) can load entries without importing
        the JournalEntry model directly (architecture boundary compliance).

        Args:
            entry_id: The UUID primary key of the entry.

        Returns:
            The JournalEntry, or None if not found.
        """
        return self._session.execute(
            select(JournalEntry).where(JournalEntry.id == entry_id)
        ).scalar_one_or_none()

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
        """Validate rounding invariants (R5, R22)."""
        # INVARIANT: R5 -- At most ONE is_rounding=True line per entry
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
        # INVARIANT: R21 -- Reference snapshot determinism
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

        # INVARIANT: R9 -- Sequence monotonicity via locked counter row
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
        """G9: Validate subledger control contracts after posting.

        # INVARIANT: SL-G3, SL-G4, SL-G5
        """
        from finance_kernel.domain.subledger_control import (
            SubledgerReconciler,
            SubledgerType,
        )
        from finance_kernel.domain.values import Money
        from finance_kernel.selectors.ledger_selector import LedgerSelector
        from finance_kernel.selectors.subledger_selector import SubledgerSelector

        registry = self._subledger_control_registry
        if registry is None:
            return

        reconciler = SubledgerReconciler()
        # SL-G4: Selectors created lazily but share the SAME session
        sl_selector: SubledgerSelector | None = None
        gl_selector: LedgerSelector | None = None

        for ledger_intent in intent.ledger_intents:
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
                gl_balances = gl_selector.account_balance(
                    account_id=control_account_id,
                    as_of_date=intent.effective_date,
                    currency=currency,
                )
                if gl_balances:
                    raw_gl_balance = gl_balances[0].balance
                else:
                    raw_gl_balance = Decimal("0")

                # Normalize GL balance to match SL sign convention
                if not contract.binding.is_debit_normal:
                    gl_economic = -raw_gl_balance
                else:
                    gl_economic = raw_gl_balance

                control_balance_after = Money.of(gl_economic, currency)

                sl_before = sl_selector.get_aggregate_balance(
                    subledger_type=sl_type,
                    as_of_date=intent.effective_date,
                    currency=currency,
                )

                debit_total = ledger_intent.total_debits(currency)
                credit_total = ledger_intent.total_credits(currency)

                if contract.binding.is_debit_normal:
                    sl_delta = debit_total - credit_total
                else:
                    sl_delta = credit_total - debit_total

                sl_after = Money.of(sl_before.amount + sl_delta, currency)

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

                # SL-G5: Blocking violations abort the transaction
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
        """G10: Validate reference snapshot is still current."""
        if self._snapshot_service is None:
            return

        snapshot_id = intent.snapshot.full_snapshot_id
        if snapshot_id is None:
            return

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
