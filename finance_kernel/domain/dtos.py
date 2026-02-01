"""
DTOs -- Pure domain data transfer objects.

Responsibility:
    Defines the immutable data structures that flow through the posting
    pipeline: EventEnvelope (input), LineSpec (strategy output), ProposedLine,
    ProposedJournalEntry (domain output), JournalEntryDraft, JournalEntryRecord
    (persistence boundary), and all supporting DTOs (validation, periods,
    accounts, exchange rates, reference data).

Architecture position:
    Kernel > Domain -- pure functional core, zero I/O.
    Free of ORM dependencies, database access, and external services.
    from_model() class methods exist as boundary converters but are only
    invoked from the service layer (never from domain logic).

Invariants enforced:
    R2.5 -- Payload deep-freeze prevents mutation by strategies
    R3   -- Domain logic accepts/returns DTOs, never ORM entities
    R4   -- Money value objects for all monetary fields (never raw Decimal)
    R5   -- At most one is_rounding=True line per entry (enforced downstream)
    R21  -- Reference snapshot version IDs recorded on ProposedJournalEntry

Failure modes:
    - ValueError on ProposedJournalEntry with no lines
    - ValueError on LineSpec with negative amount
    - ValueError on LedgerIntent with no lines

Audit relevance:
    ProposedJournalEntry is the auditable artifact produced by the pure domain.
    Auditors verify that reference snapshot versions (R21) are present, that
    rounding lines are isolated (R5/R22), and that the entry is balanced per
    currency (R4). ReferenceData deep-freeze (R2.5) guarantees that strategy
    inputs cannot be tampered with after snapshot.

Data flow:
    EventEnvelope -> ProposedJournalEntry -> JournalEntryDraft -> JournalEntryRecord
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from finance_kernel.domain.values import Currency, ExchangeRate, Money

if TYPE_CHECKING:
    from finance_kernel.models.account import Account as AccountModel
    from finance_kernel.models.event import Event as EventModel
    from finance_kernel.models.exchange_rate import ExchangeRate as ExchangeRateModel
    from finance_kernel.models.fiscal_period import FiscalPeriod as FiscalPeriodModel
    from finance_kernel.models.journal import (
        JournalEntry as JournalEntryModel,
    )
    from finance_kernel.models.journal import (
        JournalLine as JournalLineModel,
    )


def _deep_freeze_dict(d: dict[str, Any]) -> MappingProxyType:
    """
    Deep-freeze a dictionary by converting nested dicts to MappingProxyType
    and nested lists to tuples.

    R2.5 Compliance: Prevents mutation of strategy inputs.
    """
    frozen = {}
    for k, v in d.items():
        if isinstance(v, dict):
            frozen[k] = _deep_freeze_dict(v)
        elif isinstance(v, list):
            frozen[k] = tuple(_deep_freeze_value(item) for item in v)
        else:
            frozen[k] = v
    return MappingProxyType(frozen)


def _deep_freeze_value(v: Any) -> Any:
    """Deep-freeze a single value."""
    if isinstance(v, dict):
        return _deep_freeze_dict(v)
    elif isinstance(v, list):
        return tuple(_deep_freeze_value(item) for item in v)
    return v


class LineSide(str, Enum):
    """
    Which side of the entry this line is on.

    Contract:
        Exactly two values: DEBIT and CREDIT. Every journal line must have one.

    Guarantees:
        - Exhaustive enumeration -- no other sides exist in double-entry.
    """

    DEBIT = "debit"
    CREDIT = "credit"


class EntryStatus(str, Enum):
    """
    Status of a journal entry.

    Contract:
        Lifecycle: DRAFT -> POSTED -> REVERSED.
        Once POSTED, the entry is immutable (R10).
        REVERSED entries are also immutable; correction is via new reversal entries.

    Guarantees:
        - Only these three states exist in the system.
    """

    DRAFT = "draft"
    POSTED = "posted"
    REVERSED = "reversed"


@dataclass(frozen=True)
class LineSpec:
    """
    Specification for a journal line.

    Contract:
        Strategy output describing one journal line. Contains an account code
        (not ID), a side, and a Money value. Immutable, pure domain object.

    Guarantees:
        - amount is always non-negative (validated in __post_init__)
        - money is always a Money value object (R4)
        - is_rounding defaults to False; only Bookkeeper may set True (R22)

    Non-goals:
        - Does NOT contain account IDs (resolved later by _resolve_accounts)
        - Does NOT validate account existence (that is the strategy pipeline's job)

    R4 Compliance: Uses Money value object instead of separate amount/currency.
    """

    account_code: str  # Account code, not ID
    side: LineSide
    money: Money  # R4: Amount and currency paired as value object
    dimensions: dict[str, str] | None = None
    memo: str | None = None
    is_rounding: bool = False

    def __post_init__(self) -> None:
        # INVARIANT: R4 -- line amounts must be non-negative (side indicates direction)
        if self.money.amount < Decimal("0"):
            raise ValueError("Line amount must be non-negative")
        assert self.money.amount >= Decimal("0"), f"R4 violation: negative line amount {self.money.amount}"

    # Backward compatibility properties
    @property
    def amount(self) -> Decimal:
        """Get the amount (for backward compatibility)."""
        return self.money.amount

    @property
    def currency(self) -> str:
        """Get the currency code (for backward compatibility)."""
        return self.money.currency.code

    @classmethod
    def create(
        cls,
        account_code: str,
        side: LineSide,
        amount: Decimal | str,
        currency: str,
        dimensions: dict[str, str] | None = None,
        memo: str | None = None,
        is_rounding: bool = False,
    ) -> LineSpec:
        """
        Factory method for creating LineSpec with separate amount/currency.

        Provided for backward compatibility and convenience.
        """
        if isinstance(amount, str):
            amount = Decimal(amount)
        return cls(
            account_code=account_code,
            side=side,
            money=Money.of(amount, currency),
            dimensions=dimensions,
            memo=memo,
            is_rounding=is_rounding,
        )


@dataclass(frozen=True)
class ProposedLine:
    """
    A proposed journal line within a proposed entry.

    Contract:
        Contains account_id (resolved from account_code) and all attributes
        needed for persistence. This is the resolved form of LineSpec.

    Guarantees:
        - Immutable (frozen dataclass)
        - account_id is a valid UUID (resolved from reference data)
        - money is a Money value object (R4)

    Non-goals:
        - Does NOT validate that account_id exists in the database
        - Does NOT enforce balance constraints (that is the entry's job)

    R4 Compliance: Uses Money value object instead of separate amount/currency.
    """

    account_id: UUID
    account_code: str
    side: LineSide
    money: Money  # R4: Amount and currency paired as value object
    dimensions: dict[str, str] | None = None
    memo: str | None = None
    is_rounding: bool = False
    exchange_rate_id: UUID | None = None
    line_seq: int = 0

    # Backward compatibility properties
    @property
    def amount(self) -> Decimal:
        """Get the amount (for backward compatibility)."""
        return self.money.amount

    @property
    def currency(self) -> str:
        """Get the currency code (for backward compatibility)."""
        return self.money.currency.code

    @classmethod
    def create(
        cls,
        account_id: UUID,
        account_code: str,
        side: LineSide,
        amount: Decimal | str,
        currency: str,
        dimensions: dict[str, str] | None = None,
        memo: str | None = None,
        is_rounding: bool = False,
        exchange_rate_id: UUID | None = None,
        line_seq: int = 0,
    ) -> ProposedLine:
        """
        Factory method for creating ProposedLine with separate amount/currency.

        Provided for backward compatibility and convenience.
        """
        if isinstance(amount, str):
            amount = Decimal(amount)
        return cls(
            account_id=account_id,
            account_code=account_code,
            side=side,
            money=Money.of(amount, currency),
            dimensions=dimensions,
            memo=memo,
            is_rounding=is_rounding,
            exchange_rate_id=exchange_rate_id,
            line_seq=line_seq,
        )


@dataclass(frozen=True)
class EventEnvelope:
    """
    Pure domain representation of an event.

    Contract:
        Contains all data needed for posting without ORM references.
        This is the canonical input to the posting strategy pipeline.

    Guarantees:
        - Immutable (frozen dataclass)
        - payload is deep-frozen (MappingProxyType) to prevent mutation (R2.5)
        - event_id and payload_hash are present for idempotency (R1, R3)

    Non-goals:
        - Does NOT validate payload_hash correctness (IngestorService does that)
        - Does NOT store ORM references

    R2.5 Compliance: Payload is deep-frozen to prevent mutation by strategies.
    """

    event_id: UUID
    event_type: str
    occurred_at: datetime
    effective_date: date
    actor_id: UUID
    producer: str
    payload: dict[str, Any] | MappingProxyType
    payload_hash: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        """R2.5: Deep-freeze the payload to prevent mutation by strategies."""
        # INVARIANT: R2.5 -- payload must be immutable to prevent strategy tampering
        if isinstance(self.payload, dict) and not isinstance(self.payload, MappingProxyType):
            object.__setattr__(self, "payload", _deep_freeze_dict(self.payload))

    @property
    def idempotency_key(self) -> str:
        """Generate the idempotency key for this event."""
        return f"{self.producer}:{self.event_type}:{self.event_id}"

    @classmethod
    def from_model(cls, model: EventModel) -> EventEnvelope:
        """
        Create an EventEnvelope from an Event ORM model.

        Sindri compatibility: This method follows the from_model() pattern
        used throughout the Sindri codebase for ORM-to-DTO conversion.

        Args:
            model: Event ORM model instance.

        Returns:
            EventEnvelope DTO.
        """
        return cls(
            event_id=model.event_id,
            event_type=model.event_type,
            occurred_at=model.occurred_at,
            effective_date=model.effective_date,
            actor_id=model.actor_id,
            producer=model.producer,
            payload=dict(model.payload) if model.payload else {},
            payload_hash=model.payload_hash,
            schema_version=model.schema_version,
        )


@dataclass(frozen=True)
class ValidationError:
    """
    A single validation error.

    Contract:
        Carries a machine-readable code, human-readable message, optional field
        path, and optional details dict. Used throughout the posting pipeline.

    Guarantees:
        - Immutable (frozen dataclass)
        - code is always present (R18 -- machine-readable error codes)

    Non-goals:
        - Does NOT raise exceptions -- it IS the error representation.
    """

    code: str
    message: str
    field: str | None = None
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class ValidationResult:
    """
    Result of validation.

    Contract:
        Aggregates zero or more ValidationErrors. is_valid is True only when
        there are no errors.

    Guarantees:
        - Immutable (frozen dataclass)
        - errors is always a tuple (never None)
        - bool(result) == result.is_valid for convenience

    Non-goals:
        - Does NOT contain warnings -- only hard errors.
    """

    is_valid: bool
    errors: tuple[ValidationError, ...] = field(default_factory=tuple)

    @classmethod
    def success(cls) -> ValidationResult:
        """Create a successful validation result."""
        return cls(is_valid=True, errors=())

    @classmethod
    def failure(cls, *errors: ValidationError) -> ValidationResult:
        """Create a failed validation result."""
        return cls(is_valid=False, errors=tuple(errors))

    def __bool__(self) -> bool:
        return self.is_valid


@dataclass(frozen=True)
class ProposedJournalEntry:
    """
    Pure domain output from the posting strategy.

    Contract:
        Contains all information needed to create a journal entry:
        lines, event envelope, description, metadata, and reference snapshot
        version IDs. No ORM objects, no side effects.

    Guarantees:
        - Immutable (frozen dataclass)
        - At least one line (validated in __post_init__)
        - Deterministic: same input always produces same output
        - Reference snapshot versions present for replay (R21)
        - is_balanced() checks R4 compliance per currency

    Non-goals:
        - Does NOT enforce balance -- that is the strategy's responsibility
        - Does NOT persist -- JournalWriter handles persistence
        - Does NOT allocate sequence numbers

    R21 Compliance: Includes reference snapshot versions for deterministic replay.
    """

    event_envelope: EventEnvelope
    lines: tuple[ProposedLine, ...]
    description: str | None = None
    metadata: dict[str, Any] | None = None
    posting_rule_version: int = 1
    rounding_rule_version: int = 1

    # R21: Reference snapshot version identifiers (required for deterministic replay)
    coa_version: int = 1
    dimension_schema_version: int = 1
    rounding_policy_version: int = 1
    currency_registry_version: int = 1

    def __post_init__(self) -> None:
        # INVARIANT: Every journal entry must have at least one line
        if not self.lines:
            raise ValueError("ProposedJournalEntry must have at least one line")
        assert len(self.lines) >= 1, "ProposedJournalEntry must have at least one line"

    @property
    def idempotency_key(self) -> str:
        """Idempotency key derived from the event."""
        return self.event_envelope.idempotency_key

    @property
    def currencies(self) -> frozenset[str]:
        """All currencies in this entry."""
        return frozenset(line.currency for line in self.lines)

    def total_debits(self, currency: str | None = None) -> Decimal:
        """Sum of debit amounts, optionally filtered by currency."""
        return sum(
            (
                line.amount
                for line in self.lines
                if line.side == LineSide.DEBIT
                and (currency is None or line.currency == currency)
            ),
            Decimal("0"),
        )

    def total_credits(self, currency: str | None = None) -> Decimal:
        """Sum of credit amounts, optionally filtered by currency."""
        return sum(
            (
                line.amount
                for line in self.lines
                if line.side == LineSide.CREDIT
                and (currency is None or line.currency == currency)
            ),
            Decimal("0"),
        )

    def is_balanced(self, currency: str | None = None) -> bool:
        """
        Check if debits equal credits for given currency (or all).

        Postconditions:
            - Returns True only if R4 (debits == credits per currency) holds.
        """
        # INVARIANT: R4 -- debits must equal credits per currency per entry
        if currency:
            return self.total_debits(currency) == self.total_credits(currency)
        # Check all currencies
        for curr in self.currencies:
            if self.total_debits(curr) != self.total_credits(curr):
                return False
        return True

    def imbalance(self, currency: str) -> Decimal:
        """Calculate imbalance for a specific currency (debits - credits)."""
        return self.total_debits(currency) - self.total_credits(currency)


@dataclass(frozen=True)
class JournalEntryDraft:
    """
    A draft journal entry ready for persistence.

    Contract:
        Created by the Ledger service from a ProposedJournalEntry.
        Contains the assigned UUID but no sequence number yet.

    Guarantees:
        - Immutable (frozen dataclass)
        - Carries the full ProposedJournalEntry for audit trail

    Non-goals:
        - Does NOT have a sequence number (assigned at post time by SequenceService)
    """

    id: UUID
    proposed_entry: ProposedJournalEntry
    status: EntryStatus = EntryStatus.DRAFT

    @property
    def idempotency_key(self) -> str:
        return self.proposed_entry.idempotency_key


@dataclass(frozen=True)
class JournalEntryRecord:
    """
    A finalized journal entry record.

    Contract:
        The read-side DTO for a posted journal entry. Contains all information
        after posting including assigned sequence number, posted timestamp,
        and final status.

    Guarantees:
        - Immutable (frozen dataclass)
        - seq is the monotonic sequence number (R9)
        - idempotency_key is unique (R3/R8)
        - Reference snapshot versions present for replay (R21)

    Non-goals:
        - Does NOT enforce immutability at the persistence level (ORM + triggers do that)
        - Does NOT validate balance (already validated at proposal time)

    R21 Compliance: Includes reference snapshot versions for deterministic replay.
    """

    id: UUID
    seq: int
    idempotency_key: str
    event_id: UUID
    event_type: str
    occurred_at: datetime
    effective_date: date
    posted_at: datetime
    actor_id: UUID
    status: EntryStatus
    lines: tuple[ProposedLine, ...]
    description: str | None = None
    metadata: dict[str, Any] | None = None
    posting_rule_version: int = 1
    reversal_of_id: UUID | None = None

    # R21: Reference snapshot version identifiers
    coa_version: int | None = None
    dimension_schema_version: int | None = None
    rounding_policy_version: int | None = None
    currency_registry_version: int | None = None

    @classmethod
    def from_model(cls, model: JournalEntryModel) -> JournalEntryRecord:
        """
        Create a JournalEntryRecord from a JournalEntry ORM model.

        Sindri compatibility: This method follows the from_model() pattern
        used throughout the Sindri codebase for ORM-to-DTO conversion.

        Args:
            model: JournalEntry ORM model instance.

        Returns:
            JournalEntryRecord DTO.
        """
        # Convert lines from ORM to DTO
        lines = tuple(
            ProposedLine(
                account_id=line.account_id,
                account_code=line.account.code if line.account else "",
                side=LineSide(line.side.value),
                money=Money.of(line.amount, line.currency),
                dimensions=dict(line.dimensions) if line.dimensions else None,
                memo=line.line_memo,
                is_rounding=line.is_rounding,
                exchange_rate_id=line.exchange_rate_id,
                line_seq=line.line_seq,
            )
            for line in sorted(model.lines, key=lambda x: x.line_seq)
        )

        return cls(
            id=model.id,
            seq=model.seq or 0,
            idempotency_key=model.idempotency_key,
            event_id=model.source_event_id,
            event_type=model.source_event_type,
            occurred_at=model.occurred_at,
            effective_date=model.effective_date,
            posted_at=model.posted_at or model.created_at,
            actor_id=model.actor_id,
            status=EntryStatus(model.status.value),
            lines=lines,
            description=model.description,
            metadata=dict(model.entry_metadata) if model.entry_metadata else None,
            posting_rule_version=model.posting_rule_version,
            reversal_of_id=model.reversal_of_id,
            # R21: Reference snapshot versions
            coa_version=model.coa_version,
            dimension_schema_version=model.dimension_schema_version,
            rounding_policy_version=model.rounding_policy_version,
            currency_registry_version=model.currency_registry_version,
        )


@dataclass(frozen=True)
class ReferenceData:
    """
    Reference data needed by posting strategies.

    Contract:
        Provides all lookup data (accounts, currencies, exchange rates,
        dimensions) required by pure strategies. Passed into the strategy
        layer instead of giving strategies database access.

    Guarantees:
        - Immutable (frozen dataclass)
        - All mutable dict fields deep-frozen to MappingProxyType (R2.5)
        - Version IDs present for deterministic replay (R21)
        - Currency and ExchangeRate are value objects (R4)

    Non-goals:
        - Does NOT query the database (snapshot is taken before strategy invocation)
        - Does NOT enforce period locks (service-layer concern)

    R4 Compliance: Uses Currency and ExchangeRate value objects.
    R2.5 Compliance: All mutable fields are deep-frozen to prevent mutation.
    R21 Compliance: Includes version identifiers for deterministic replay.
    """

    account_ids_by_code: dict[str, UUID] | MappingProxyType
    active_account_codes: frozenset[str]
    valid_currencies: frozenset[Currency]  # R4: Currency value objects
    rounding_account_ids: dict[str, UUID] | MappingProxyType  # currency code -> rounding account ID
    exchange_rates: tuple[ExchangeRate, ...] | None = None  # R4: ExchangeRate value objects
    required_dimensions: frozenset[str] = field(default_factory=frozenset)
    # Dimension validation data
    active_dimensions: frozenset[str] = field(default_factory=frozenset)  # Active dimension codes
    active_dimension_values: dict[str, frozenset[str]] | MappingProxyType = field(default_factory=dict)  # dim_code -> active value codes

    # ==========================================================================
    # R21: Reference Snapshot Version Identifiers
    # These versions must be recorded on JournalEntry at post time for
    # deterministic replay.
    # ==========================================================================
    coa_version: int = 1  # Chart of accounts version
    dimension_schema_version: int = 1  # Dimension schema version
    rounding_policy_version: int = 1  # Rounding policy version
    currency_registry_version: int = 1  # Currency registry version

    def __post_init__(self) -> None:
        """R2.5: Deep-freeze mutable fields to prevent mutation by strategies."""
        # INVARIANT: R2.5 -- all mutable containers must be frozen before strategy access
        # Freeze account_ids_by_code
        if isinstance(self.account_ids_by_code, dict) and not isinstance(self.account_ids_by_code, MappingProxyType):
            object.__setattr__(self, "account_ids_by_code", MappingProxyType(dict(self.account_ids_by_code)))

        # Freeze rounding_account_ids
        if isinstance(self.rounding_account_ids, dict) and not isinstance(self.rounding_account_ids, MappingProxyType):
            object.__setattr__(self, "rounding_account_ids", MappingProxyType(dict(self.rounding_account_ids)))

        # Freeze active_dimension_values
        if isinstance(self.active_dimension_values, dict) and not isinstance(self.active_dimension_values, MappingProxyType):
            object.__setattr__(self, "active_dimension_values", MappingProxyType(dict(self.active_dimension_values)))

    def get_account_id(self, code: str) -> UUID | None:
        """Get account ID by code."""
        return self.account_ids_by_code.get(code)

    def is_account_active(self, code: str) -> bool:
        """Check if account is active."""
        return code in self.active_account_codes

    def is_valid_currency(self, currency: str | Currency) -> bool:
        """Check if currency is valid."""
        if isinstance(currency, str):
            code = currency.upper()
        else:
            code = currency.code
        return any(c.code == code for c in self.valid_currencies)

    def get_currency(self, code: str) -> Currency | None:
        """Get Currency value object by code."""
        code_upper = code.upper()
        for c in self.valid_currencies:
            if c.code == code_upper:
                return c
        return None

    def get_decimal_places(self, currency: str | Currency) -> int:
        """Get decimal places for a currency."""
        if isinstance(currency, Currency):
            return currency.decimal_places
        c = self.get_currency(currency)
        return c.decimal_places if c else 2

    def get_rounding_account_id(self, currency: str | Currency) -> UUID | None:
        """Get the rounding account ID for a currency."""
        code = currency.code if isinstance(currency, Currency) else currency.upper()
        return self.rounding_account_ids.get(code)

    def get_exchange_rate(
        self, from_currency: str | Currency, to_currency: str | Currency
    ) -> ExchangeRate | None:
        """Get exchange rate between two currencies."""
        if self.exchange_rates is None:
            return None
        from_code = from_currency.code if isinstance(from_currency, Currency) else from_currency.upper()
        to_code = to_currency.code if isinstance(to_currency, Currency) else to_currency.upper()
        for rate in self.exchange_rates:
            if rate.from_currency.code == from_code and rate.to_currency.code == to_code:
                return rate
        return None

    def is_dimension_active(self, dimension_code: str) -> bool:
        """Check if a dimension is active."""
        return dimension_code in self.active_dimensions

    def is_dimension_value_active(self, dimension_code: str, value_code: str) -> bool:
        """Check if a dimension value is active."""
        active_values = self.active_dimension_values.get(dimension_code, frozenset())
        return value_code in active_values

    def validate_dimensions(self, dimensions: dict[str, str] | None) -> list[str]:
        """
        Validate dimensions for posting.

        Returns list of error messages (empty if valid).
        """
        errors = []
        if dimensions is None:
            return errors

        for dim_code, value_code in dimensions.items():
            if not self.is_dimension_active(dim_code):
                errors.append(f"Dimension '{dim_code}' is inactive")
            elif not self.is_dimension_value_active(dim_code, value_code):
                errors.append(f"Dimension value '{value_code}' for '{dim_code}' is inactive or invalid")

        return errors


# =============================================================================
# Period DTOs (R3 Compliance)
# =============================================================================


class PeriodStatus(str, Enum):
    """
    Status of a fiscal period.

    Contract:
        Lifecycle: OPEN -> CLOSING -> CLOSED -> LOCKED.
        R12: No posting to CLOSED or LOCKED periods.
        R13: Adjustment postings require allows_adjustments=True even on OPEN periods.
    """

    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    LOCKED = "locked"


@dataclass(frozen=True)
class FiscalPeriodInfo:
    """
    Pure domain representation of a fiscal period.

    Contract:
        Immutable snapshot of fiscal period state. Used by domain logic
        to check period status without ORM access.

    Guarantees:
        - Immutable (frozen dataclass)
        - R3 compliant: domain logic uses this DTO, not ORM FiscalPeriod

    Non-goals:
        - Does NOT enforce period locks (PeriodService does that)
    """

    id: UUID
    period_code: str
    name: str
    start_date: date
    end_date: date
    status: PeriodStatus
    allows_adjustments: bool
    closed_at: datetime | None = None
    closed_by_id: UUID | None = None

    @property
    def is_open(self) -> bool:
        """Check if the period is open for posting."""
        return self.status == PeriodStatus.OPEN

    @property
    def is_closed(self) -> bool:
        """Check if the period is closed."""
        return self.status == PeriodStatus.CLOSED

    def contains_date(self, check_date: date) -> bool:
        """Check if a date falls within this period."""
        return self.start_date <= check_date <= self.end_date

    @classmethod
    def from_model(cls, model: FiscalPeriodModel) -> FiscalPeriodInfo:
        """
        Create a FiscalPeriodInfo from a FiscalPeriod ORM model.

        Sindri compatibility: This method follows the from_model() pattern
        used throughout the Sindri codebase for ORM-to-DTO conversion.

        Args:
            model: FiscalPeriod ORM model instance.

        Returns:
            FiscalPeriodInfo DTO.
        """
        return cls(
            id=model.id,
            period_code=model.period_code,
            name=model.name,
            start_date=model.start_date,
            end_date=model.end_date,
            status=PeriodStatus(model.status.value),
            allows_adjustments=model.allows_adjustments,
            closed_at=model.closed_at,
            closed_by_id=model.closed_by_id,
        )


@dataclass(frozen=True)
class AccountInfo:
    """
    Pure domain representation of an account.

    Contract:
        Immutable snapshot of account state. Used by domain logic
        to check account type and status without ORM access.

    Guarantees:
        - Immutable (frozen dataclass)
        - R3 compliant: domain logic uses this DTO, not ORM Account

    Non-goals:
        - Does NOT enforce account activation/deactivation lifecycle
    """

    id: UUID
    account_code: str
    name: str
    account_type: str  # Asset, Liability, Equity, Revenue, Expense
    normal_balance: str  # debit or credit
    is_active: bool
    parent_id: UUID | None = None
    description: str | None = None

    @classmethod
    def from_model(cls, model: AccountModel) -> AccountInfo:
        """
        Create an AccountInfo from an Account ORM model.

        Sindri compatibility: This method follows the from_model() pattern
        used throughout the Sindri codebase for ORM-to-DTO conversion.

        Args:
            model: Account ORM model instance.

        Returns:
            AccountInfo DTO.
        """
        return cls(
            id=model.id,
            account_code=model.code,
            name=model.name,
            account_type=model.account_type.value,
            normal_balance=model.normal_balance.value,
            is_active=model.is_active,
            parent_id=model.parent_id,
            description=None,  # Account model doesn't have description field
        )


@dataclass(frozen=True)
class ExchangeRateInfo:
    """
    Pure domain representation of an exchange rate record.

    R3 Compliance: Domain logic returns this DTO instead of ORM ExchangeRate.
    R4 Compliance: Contains ExchangeRate value object.
    """

    id: UUID
    rate: ExchangeRate  # R4: Value object
    effective_at: datetime
    source: str | None = None  # e.g., "ECB", "manual"

    @property
    def from_currency(self) -> Currency:
        return self.rate.from_currency

    @property
    def to_currency(self) -> Currency:
        return self.rate.to_currency

    @property
    def rate_value(self) -> Decimal:
        return self.rate.rate

    @classmethod
    def from_model(cls, model: ExchangeRateModel) -> ExchangeRateInfo:
        """
        Create an ExchangeRateInfo from an ExchangeRate ORM model.

        Sindri compatibility: This method follows the from_model() pattern
        used throughout the Sindri codebase for ORM-to-DTO conversion.

        Args:
            model: ExchangeRate ORM model instance.

        Returns:
            ExchangeRateInfo DTO.
        """
        # Create the ExchangeRate value object
        rate_value_object = ExchangeRate(
            from_currency=Currency(model.from_currency),
            to_currency=Currency(model.to_currency),
            rate=model.rate,
            rate_id=model.id,
        )

        return cls(
            id=model.id,
            rate=rate_value_object,
            effective_at=model.effective_at,
            source=model.source,
        )
