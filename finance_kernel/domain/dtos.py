"""
Pure domain data transfer objects.

These DTOs are:
- Immutable (frozen dataclasses)
- Free of ORM dependencies
- Free of database dependencies
- Free of I/O dependencies
- Deterministic and testable in isolation

R3 Compliance: Domain logic may not accept or return ORM entities.
R4 Compliance: Uses Money, Currency value objects - no primitive types for financial fields.

Data flow:
    EventEnvelope → ProposedJournalEntry → JournalEntryDraft → JournalEntryRecord
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
    """Which side of the entry this line is on."""

    DEBIT = "debit"
    CREDIT = "credit"


class EntryStatus(str, Enum):
    """Status of a journal entry."""

    DRAFT = "draft"
    POSTED = "posted"
    REVERSED = "reversed"


@dataclass(frozen=True)
class LineSpec:
    """
    Specification for a journal line.

    Used as input to the posting strategy.
    Immutable and contains no IDs (pure domain object).

    R4 Compliance: Uses Money value object instead of separate amount/currency.
    """

    account_code: str  # Account code, not ID
    side: LineSide
    money: Money  # R4: Amount and currency paired as value object
    dimensions: dict[str, str] | None = None
    memo: str | None = None
    is_rounding: bool = False

    def __post_init__(self) -> None:
        if self.money.amount < Decimal("0"):
            raise ValueError("Line amount must be non-negative")

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

    Contains account_id (resolved from account_code) and all
    attributes needed for persistence.

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

    Contains all data needed for posting without ORM references.
    This is the input to the posting strategy.

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
        if isinstance(self.payload, dict) and not isinstance(self.payload, MappingProxyType):
            object.__setattr__(self, "payload", _deep_freeze_dict(self.payload))

    @property
    def idempotency_key(self) -> str:
        """Generate the idempotency key for this event."""
        return f"{self.producer}:{self.event_type}:{self.event_id}"

    @classmethod
    def from_model(cls, model: "EventModel") -> "EventEnvelope":
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
    """A single validation error."""

    code: str
    message: str
    field: str | None = None
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class ValidationResult:
    """Result of validation."""

    is_valid: bool
    errors: tuple[ValidationError, ...] = field(default_factory=tuple)

    @classmethod
    def success(cls) -> "ValidationResult":
        """Create a successful validation result."""
        return cls(is_valid=True, errors=())

    @classmethod
    def failure(cls, *errors: ValidationError) -> "ValidationResult":
        """Create a failed validation result."""
        return cls(is_valid=False, errors=tuple(errors))

    def __bool__(self) -> bool:
        return self.is_valid


@dataclass(frozen=True)
class ProposedJournalEntry:
    """
    Pure domain output from the posting strategy.

    Contains all information needed to create a journal entry
    but with NO ORM objects, NO database IDs except for resolved
    account_ids, and NO side effects.

    This is deterministic: same input always produces same output.

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
        # Validation is performed by the strategy, but we enforce
        # basic invariants here
        if not self.lines:
            raise ValueError("ProposedJournalEntry must have at least one line")

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
        """Check if debits equal credits for given currency (or all)."""
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

    Created by the Ledger service from a ProposedJournalEntry.
    Contains the assigned UUID but no sequence number yet.
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

    Contains all information after posting including:
    - Assigned sequence number
    - Posted timestamp
    - Final status

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
    def from_model(cls, model: "JournalEntryModel") -> "JournalEntryRecord":
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

    Contains lookups for accounts, currencies, exchange rates, etc.
    This is passed to the pure strategy layer instead of giving
    strategies access to the database.

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
    """Status of a fiscal period."""

    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class FiscalPeriodInfo:
    """
    Pure domain representation of a fiscal period.

    R3 Compliance: Domain logic returns this DTO instead of ORM FiscalPeriod.
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
    def from_model(cls, model: "FiscalPeriodModel") -> "FiscalPeriodInfo":
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

    R3 Compliance: Domain logic returns this DTO instead of ORM Account.
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
    def from_model(cls, model: "AccountModel") -> "AccountInfo":
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
    def from_model(cls, model: "ExchangeRateModel") -> "ExchangeRateInfo":
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
