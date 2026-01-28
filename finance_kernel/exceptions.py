"""
Typed Exception Hierarchy for the Finance Kernel (R18 Compliance).

===============================================================================
WHY TYPED EXCEPTIONS
===============================================================================

Financial systems must handle errors precisely. Generic exceptions like
ValueError or RuntimeError force callers to parse error messages - which is:
  - Fragile (message wording changes break code)
  - Non-portable (different languages, logs, APIs)
  - Hard to test (string matching in tests is brittle)

R18 Compliance requires:
  1. Every error has a TYPED exception class (catch by type, not message)
  2. Every exception has a CODE attribute (machine-readable, API-safe)
  3. Exceptions carry structured DATA (not just a message string)

Example - WRONG way to handle errors:
    try:
        post_event(event)
    except Exception as e:
        if "closed period" in str(e):  # FRAGILE - message might change
            handle_closed_period()

Example - RIGHT way (what this module enables):
    try:
        post_event(event)
    except ClosedPeriodError as e:  # Typed catch
        log.warning(f"Period {e.period_code} is closed")  # Structured data
        api_response(code=e.code, period=e.period_code)   # Machine-readable

===============================================================================
EXCEPTION HIERARCHY
===============================================================================

All exceptions inherit from FinanceKernelError:

    FinanceKernelError (base)
    |
    +-- EventError
    |   +-- EventNotFoundError
    |   +-- EventAlreadyExistsError
    |   +-- PayloadMismatchError
    |   +-- UnsupportedSchemaVersionError
    |
    +-- PostingError
    |   +-- AlreadyPostedError
    |   +-- UnbalancedEntryError
    |   +-- InvalidAccountError
    |   +-- MissingDimensionError
    |   +-- InvalidDimensionValueError
    |   +-- InactiveDimensionError
    |   +-- InactiveDimensionValueError
    |   +-- DimensionNotFoundError
    |
    +-- PeriodError
    |   +-- ClosedPeriodError
    |   +-- PeriodNotFoundError
    |   +-- PeriodAlreadyClosedError
    |   +-- PeriodOverlapError
    |   +-- PeriodImmutableError
    |   +-- AdjustmentsNotAllowedError
    |
    +-- AccountError
    |   +-- AccountNotFoundError
    |   +-- AccountInactiveError
    |   +-- AccountReferencedError
    |   +-- RoundingAccountNotFoundError
    |
    +-- CurrencyError
    |   +-- InvalidCurrencyError
    |   +-- CurrencyMismatchError
    |   +-- ExchangeRateNotFoundError
    |
    +-- AuditError
    |   +-- AuditChainBrokenError
    |
    +-- ReversalError
    |   +-- EntryNotPostedError
    |   +-- EntryAlreadyReversedError
    |
    +-- ConcurrencyError
    |   +-- OptimisticLockError
    |
    +-- ImmutabilityError
    |   +-- ImmutabilityViolationError
    |
    +-- RoundingError
    |   +-- MultipleRoundingLinesError
    |   +-- RoundingAmountExceededError
    |
    +-- ExchangeRateError
        +-- ExchangeRateImmutableError
        +-- ExchangeRateReferencedError
        +-- InvalidExchangeRateError
        +-- ExchangeRateArbitrageError

===============================================================================
ERROR CODES - QUICK REFERENCE
===============================================================================

Category        | Code                        | When Raised
----------------|-----------------------------|-----------------------------------------
Event           | EVENT_NOT_FOUND             | Event ID doesn't exist
                | EVENT_ALREADY_EXISTS        | Duplicate event ID (idempotency)
                | PAYLOAD_MISMATCH            | Same ID, different payload (tampering?)
                | UNSUPPORTED_SCHEMA_VERSION  | Event version not supported
----------------|-----------------------------|-----------------------------------------
Posting         | ALREADY_POSTED              | Event already has journal entry (OK)
                | UNBALANCED_ENTRY            | Debits != Credits
                | INVALID_ACCOUNT             | Account can't be posted to
                | MISSING_DIMENSION           | Required dimension not provided
                | INVALID_DIMENSION_VALUE     | Dimension value doesn't exist
                | INACTIVE_DIMENSION          | Dimension is deactivated
                | INACTIVE_DIMENSION_VALUE    | Dimension value is deactivated
                | DIMENSION_NOT_FOUND         | Dimension code doesn't exist
                | POSTING_RULE_NOT_FOUND      | No strategy for event type
----------------|-----------------------------|-----------------------------------------
Period          | CLOSED_PERIOD               | Posting to closed period (R12)
                | PERIOD_NOT_FOUND            | No period covers this date
                | PERIOD_ALREADY_CLOSED       | Period already closed
                | PERIOD_OVERLAP              | Date range conflicts (R12)
                | PERIOD_IMMUTABLE            | Modifying closed period (R13)
                | ADJUSTMENTS_NOT_ALLOWED     | Period disallows adjustments (R13)
----------------|-----------------------------|-----------------------------------------
Account         | ACCOUNT_NOT_FOUND           | Account ID doesn't exist
                | ACCOUNT_INACTIVE            | Account is deactivated
                | ACCOUNT_REFERENCED          | Can't delete, has posted lines
                | ROUNDING_ACCOUNT_NOT_FOUND  | No rounding account for currency
----------------|-----------------------------|-----------------------------------------
Currency        | INVALID_CURRENCY            | Not a valid ISO 4217 code
                | CURRENCY_MISMATCH           | Mixed currencies in operation
                | EXCHANGE_RATE_NOT_FOUND     | No rate for currency pair/date
----------------|-----------------------------|-----------------------------------------
Audit           | AUDIT_CHAIN_BROKEN          | Hash chain validation failed (R10)
----------------|-----------------------------|-----------------------------------------
Reversal        | ENTRY_NOT_POSTED            | Can only reverse posted entries
                | ENTRY_ALREADY_REVERSED      | Entry was already reversed
----------------|-----------------------------|-----------------------------------------
Concurrency     | OPTIMISTIC_LOCK_CONFLICT    | Concurrent modification detected
----------------|-----------------------------|-----------------------------------------
Immutability    | IMMUTABILITY_VIOLATION      | Modifying immutable record (R10)
----------------|-----------------------------|-----------------------------------------
Rounding        | MULTIPLE_ROUNDING_LINES     | >1 rounding line (fraud prevention)
                | ROUNDING_AMOUNT_EXCEEDED    | Rounding too large (fraud prevention)
----------------|-----------------------------|-----------------------------------------
Exchange Rate   | EXCHANGE_RATE_IMMUTABLE     | Rate used, can't modify
                | EXCHANGE_RATE_REFERENCED    | Rate used, can't delete
                | INVALID_EXCHANGE_RATE       | Rate is zero/negative/invalid
                | EXCHANGE_RATE_ARBITRAGE     | Rate creates arbitrage opportunity

===============================================================================
HANDLING PATTERNS
===============================================================================

1. CATCH SPECIFIC EXCEPTIONS (not base classes):

    try:
        orchestrator.post(event)
    except ClosedPeriodError as e:
        # Handle closed period specifically
        notify_user(f"Period {e.period_code} is closed")
    except PostingError as e:
        # Catch-all for other posting issues
        log.error(f"Posting failed: {e.code}")

2. USE STRUCTURED DATA (not message parsing):

    except UnbalancedEntryError as e:
        return {
            "error": e.code,
            "debits": e.debits,
            "credits": e.credits,
            "currency": e.currency,
        }

3. IDEMPOTENCY HANDLING (AlreadyPostedError is success):

    try:
        entry = orchestrator.post(event)
    except AlreadyPostedError as e:
        # This is OK - event was already processed
        entry = get_entry(e.journal_entry_id)
    return entry

4. AUDIT CHAIN ERRORS (critical - investigate immediately):

    except AuditChainBrokenError as e:
        alert_security_team(e)
        halt_processing()  # Don't continue with broken audit trail

===============================================================================
DESIGN DECISIONS
===============================================================================

1. WHY INHERIT FROM Exception (not ValueError, etc.)?
   Domain exceptions should be catchable as a group. Inheriting from
   built-in types mixes domain errors with programming errors.

2. WHY code CLASS ATTRIBUTE (not instance)?
   Codes are static per exception type. Class attribute enables:
   - ClosedPeriodError.code without instantiation
   - API documentation generation
   - Static analysis

3. WHY STORE ALL CONTEXT AS ATTRIBUTES?
   Exceptions may be logged, serialized, or sent to APIs. Structured
   attributes survive; parsed message strings don't.

4. WHY SEPARATE ERROR CATEGORIES?
   Enables middleware to handle categories differently:
   - PeriodError -> user-facing "try different date"
   - ImmutabilityError -> log security alert
   - ConcurrencyError -> auto-retry

===============================================================================
"""


class FinanceKernelError(Exception):
    """
    Base exception for all finance kernel errors.

    R18 Compliance: All subclasses must have a `code` class attribute
    for machine-readable error identification.
    """

    code: str = "FINANCE_KERNEL_ERROR"


# Event-related exceptions


class EventError(FinanceKernelError):
    """Base exception for event-related errors."""

    code: str = "EVENT_ERROR"


class EventNotFoundError(EventError):
    """Event with given ID was not found."""

    code: str = "EVENT_NOT_FOUND"

    def __init__(self, event_id: str):
        self.event_id = event_id
        super().__init__(f"Event not found: {event_id}")


class EventAlreadyExistsError(EventError):
    """Event with given ID already exists."""

    code: str = "EVENT_ALREADY_EXISTS"

    def __init__(self, event_id: str):
        self.event_id = event_id
        super().__init__(f"Event already exists: {event_id}")


class PayloadMismatchError(EventError):
    """
    Event ID exists but with different payload hash.

    This is a protocol violation - events are immutable.
    """

    code: str = "PAYLOAD_MISMATCH"

    def __init__(self, event_id: str, expected_hash: str, received_hash: str):
        self.event_id = event_id
        self.expected_hash = expected_hash
        self.received_hash = received_hash
        super().__init__(
            f"Payload mismatch for event {event_id}: "
            f"expected {expected_hash}, received {received_hash}"
        )


class UnsupportedSchemaVersionError(EventError):
    """Event schema version is not supported."""

    code: str = "UNSUPPORTED_SCHEMA_VERSION"

    def __init__(self, event_id: str, schema_version: int):
        self.event_id = event_id
        self.schema_version = schema_version
        super().__init__(
            f"Unsupported schema version {schema_version} for event {event_id}"
        )


class SchemaValidationError(EventError):
    """
    Event payload does not match registered schema.

    P10 Compliance: All event payloads must validate against their registered
    schema before processing.
    """

    code: str = "SCHEMA_VALIDATION_ERROR"

    def __init__(
        self,
        event_type: str,
        schema_version: int,
        field_errors: list[dict],
    ):
        self.event_type = event_type
        self.schema_version = schema_version
        self.field_errors = field_errors
        super().__init__(
            f"Schema validation failed for {event_type} v{schema_version}: "
            f"{len(field_errors)} error(s)"
        )


class InvalidFieldReferenceError(EventError):
    """
    Field reference does not exist in event schema.

    P10 Compliance: Profile field references must be validated against
    the event schema during compilation.
    """

    code: str = "INVALID_FIELD_REFERENCE"

    def __init__(
        self,
        event_type: str,
        schema_version: int,
        field_path: str,
    ):
        self.event_type = event_type
        self.schema_version = schema_version
        self.field_path = field_path
        super().__init__(
            f"Field '{field_path}' does not exist in schema for "
            f"{event_type} v{schema_version}"
        )


# Posting-related exceptions


class PostingError(FinanceKernelError):
    """Base exception for posting-related errors."""

    code: str = "POSTING_ERROR"


class AlreadyPostedError(PostingError):
    """Event has already been posted (idempotent success)."""

    code: str = "ALREADY_POSTED"

    def __init__(self, event_id: str, journal_entry_id: str):
        self.event_id = event_id
        self.journal_entry_id = journal_entry_id
        super().__init__(
            f"Event {event_id} already posted as journal entry {journal_entry_id}"
        )


class UnbalancedEntryError(PostingError):
    """Journal entry debits do not equal credits."""

    code: str = "UNBALANCED_ENTRY"

    def __init__(self, debits: str, credits: str, currency: str):
        self.debits = debits
        self.credits = credits
        self.currency = currency
        super().__init__(
            f"Unbalanced entry in {currency}: debits={debits}, credits={credits}"
        )


class InvalidAccountError(PostingError):
    """Account is invalid for posting."""

    code: str = "INVALID_ACCOUNT"

    def __init__(self, account_id: str, reason: str):
        self.account_id = account_id
        self.reason = reason
        super().__init__(f"Invalid account {account_id}: {reason}")


class MissingDimensionError(PostingError):
    """Required dimension is missing from posting."""

    code: str = "MISSING_DIMENSION"

    def __init__(self, dimension_code: str):
        self.dimension_code = dimension_code
        super().__init__(f"Missing required dimension: {dimension_code}")


class InvalidDimensionValueError(PostingError):
    """Dimension value is invalid."""

    code: str = "INVALID_DIMENSION_VALUE"

    def __init__(self, dimension_code: str, value: str):
        self.dimension_code = dimension_code
        self.value = value
        super().__init__(f"Invalid value '{value}' for dimension {dimension_code}")


class InactiveDimensionError(PostingError):
    """Dimension is inactive and cannot be used for posting."""

    code: str = "INACTIVE_DIMENSION"

    def __init__(self, dimension_code: str):
        self.dimension_code = dimension_code
        super().__init__(f"Dimension '{dimension_code}' is inactive and cannot be used for posting")


class InactiveDimensionValueError(PostingError):
    """Dimension value is inactive and cannot be used for posting."""

    code: str = "INACTIVE_DIMENSION_VALUE"

    def __init__(self, dimension_code: str, value: str):
        self.dimension_code = dimension_code
        self.value = value
        super().__init__(
            f"Dimension value '{value}' for dimension '{dimension_code}' "
            "is inactive and cannot be used for posting"
        )


class DimensionNotFoundError(PostingError):
    """Dimension with given code was not found."""

    code: str = "DIMENSION_NOT_FOUND"

    def __init__(self, dimension_code: str):
        self.dimension_code = dimension_code
        super().__init__(f"Dimension not found: {dimension_code}")


# Period-related exceptions


class PeriodError(FinanceKernelError):
    """Base exception for period-related errors."""

    code: str = "PERIOD_ERROR"


class ClosedPeriodError(PeriodError):
    """Attempted to post to a closed period."""

    code: str = "CLOSED_PERIOD"

    def __init__(self, period_code: str, effective_date: str):
        self.period_code = period_code
        self.effective_date = effective_date
        super().__init__(
            f"Cannot post to closed period {period_code} "
            f"(effective_date: {effective_date})"
        )


class PeriodNotFoundError(PeriodError):
    """No period found for the given date."""

    code: str = "PERIOD_NOT_FOUND"

    def __init__(self, effective_date: str):
        self.effective_date = effective_date
        super().__init__(f"No fiscal period found for date: {effective_date}")


class PeriodAlreadyClosedError(PeriodError):
    """Period is already closed."""

    code: str = "PERIOD_ALREADY_CLOSED"

    def __init__(self, period_code: str):
        self.period_code = period_code
        super().__init__(f"Period {period_code} is already closed")


class PeriodOverlapError(PeriodError):
    """
    New period date range overlaps with existing period.

    R12 Compliance: Date ranges must not overlap.
    """

    code: str = "PERIOD_OVERLAP"

    def __init__(
        self,
        new_period_code: str,
        existing_period_code: str,
        overlap_start: str,
        overlap_end: str,
    ):
        self.new_period_code = new_period_code
        self.existing_period_code = existing_period_code
        self.overlap_start = overlap_start
        self.overlap_end = overlap_end
        super().__init__(
            f"Period {new_period_code} overlaps with {existing_period_code} "
            f"({overlap_start} to {overlap_end})"
        )


class PeriodImmutableError(PeriodError):
    """
    Attempted to modify a closed period.

    R13 Compliance: Closed periods must be immutable.
    """

    code: str = "PERIOD_IMMUTABLE"

    def __init__(self, period_code: str, operation: str):
        self.period_code = period_code
        self.operation = operation
        super().__init__(
            f"Cannot {operation} closed period {period_code}: "
            "closed periods are immutable"
        )


class AdjustmentsNotAllowedError(PeriodError):
    """
    Attempted to post an adjusting entry to a period that doesn't allow adjustments.

    R13 Compliance: allows_adjustments must be enforced.
    """

    code: str = "ADJUSTMENTS_NOT_ALLOWED"

    def __init__(self, period_code: str):
        self.period_code = period_code
        super().__init__(
            f"Period {period_code} does not allow adjusting entries"
        )


# Account-related exceptions


class AccountError(FinanceKernelError):
    """Base exception for account-related errors."""

    code: str = "ACCOUNT_ERROR"


class AccountNotFoundError(AccountError):
    """Account was not found."""

    code: str = "ACCOUNT_NOT_FOUND"

    def __init__(self, account_id: str):
        self.account_id = account_id
        super().__init__(f"Account not found: {account_id}")


class AccountInactiveError(AccountError):
    """Account is not active for posting."""

    code: str = "ACCOUNT_INACTIVE"

    def __init__(self, account_id: str):
        self.account_id = account_id
        super().__init__(f"Account is inactive: {account_id}")


class AccountReferencedError(AccountError):
    """Account cannot be deleted because it is referenced by posted lines."""

    code: str = "ACCOUNT_REFERENCED"

    def __init__(self, account_id: str):
        self.account_id = account_id
        super().__init__(
            f"Account {account_id} cannot be deleted: referenced by posted lines"
        )


class RoundingAccountNotFoundError(AccountError):
    """No rounding account found for currency."""

    code: str = "ROUNDING_ACCOUNT_NOT_FOUND"

    def __init__(self, currency: str):
        self.currency = currency
        super().__init__(f"No rounding account found for currency: {currency}")


# Currency-related exceptions


class CurrencyError(FinanceKernelError):
    """Base exception for currency-related errors."""

    code: str = "CURRENCY_ERROR"


class InvalidCurrencyError(CurrencyError):
    """Invalid ISO 4217 currency code provided."""

    code: str = "INVALID_CURRENCY"

    def __init__(self, currency: str):
        self.currency = currency
        super().__init__(f"Invalid ISO 4217 currency code: '{currency}'")


class CurrencyMismatchError(CurrencyError):
    """Attempted operation on mismatched currencies."""

    code: str = "CURRENCY_MISMATCH"

    def __init__(self, currency1: str, currency2: str):
        self.currency1 = currency1
        self.currency2 = currency2
        super().__init__(f"Currency mismatch: {currency1} vs {currency2}")


class ExchangeRateNotFoundError(CurrencyError):
    """No exchange rate found for the currency pair."""

    code: str = "EXCHANGE_RATE_NOT_FOUND"

    def __init__(self, from_currency: str, to_currency: str, as_of: str):
        self.from_currency = from_currency
        self.to_currency = to_currency
        self.as_of = as_of
        super().__init__(
            f"No exchange rate found for {from_currency}/{to_currency} as of {as_of}"
        )


# Audit-related exceptions


class AuditError(FinanceKernelError):
    """Base exception for audit-related errors."""

    code: str = "AUDIT_ERROR"


class AuditChainBrokenError(AuditError):
    """Audit hash chain validation failed."""

    code: str = "AUDIT_CHAIN_BROKEN"

    def __init__(self, audit_event_id: str, expected_hash: str, actual_hash: str):
        self.audit_event_id = audit_event_id
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        super().__init__(
            f"Audit chain broken at {audit_event_id}: "
            f"expected {expected_hash}, found {actual_hash}"
        )


# Reversal-related exceptions


class ReversalError(FinanceKernelError):
    """Base exception for reversal-related errors."""

    code: str = "REVERSAL_ERROR"


class EntryNotPostedError(ReversalError):
    """Cannot reverse an entry that is not posted."""

    code: str = "ENTRY_NOT_POSTED"

    def __init__(self, journal_entry_id: str, status: str):
        self.journal_entry_id = journal_entry_id
        self.status = status
        super().__init__(
            f"Cannot reverse entry {journal_entry_id}: status is {status}, not posted"
        )


class EntryAlreadyReversedError(ReversalError):
    """Entry has already been reversed."""

    code: str = "ENTRY_ALREADY_REVERSED"

    def __init__(self, journal_entry_id: str):
        self.journal_entry_id = journal_entry_id
        super().__init__(f"Entry {journal_entry_id} has already been reversed")


# Concurrency-related exceptions


class ConcurrencyError(FinanceKernelError):
    """Base exception for concurrency-related errors."""

    code: str = "CONCURRENCY_ERROR"


class OptimisticLockError(ConcurrencyError):
    """Optimistic locking conflict detected."""

    code: str = "OPTIMISTIC_LOCK_CONFLICT"

    def __init__(self, entity_type: str, entity_id: str):
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(
            f"Optimistic lock conflict on {entity_type} {entity_id}: "
            "entity was modified by another transaction"
        )


# Immutability-related exceptions


class ImmutabilityError(FinanceKernelError):
    """Base exception for immutability-related errors."""

    code: str = "IMMUTABILITY_ERROR"


class ImmutabilityViolationError(ImmutabilityError):
    """
    Attempted to modify or delete an immutable record.

    R10 Compliance: JournalEntry, JournalLine, and AuditEvent
    are immutable after posting/creation.
    """

    code: str = "IMMUTABILITY_VIOLATION"

    def __init__(self, entity_type: str, entity_id: str, reason: str):
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.reason = reason
        super().__init__(
            f"Immutability violation on {entity_type} {entity_id}: {reason}"
        )


# Rounding-related exceptions


class RoundingError(FinanceKernelError):
    """Base exception for rounding-related errors."""

    code: str = "ROUNDING_ERROR"


class MultipleRoundingLinesError(RoundingError):
    """
    Entry has more than one line marked is_rounding=True.

    From journal.py docstring: "exactly one line must be marked is_rounding=true"
    This invariant prevents hidden manipulation via multiple rounding entries.
    """

    code: str = "MULTIPLE_ROUNDING_LINES"

    def __init__(self, entry_id: str, rounding_count: int):
        self.entry_id = entry_id
        self.rounding_count = rounding_count
        super().__init__(
            f"Entry {entry_id} has {rounding_count} rounding lines. "
            f"At most ONE line can have is_rounding=True."
        )


class RoundingAmountExceededError(RoundingError):
    """
    Rounding line amount exceeds the maximum allowed threshold.

    Rounding is for sub-penny currency conversion remainders only.
    A large "rounding" adjustment is not rounding - it's an error or fraud.
    Typical threshold: 0.01 per non-rounding line (1 minor unit).
    """

    code: str = "ROUNDING_AMOUNT_EXCEEDED"

    # Maximum allowed rounding per non-rounding line (1 minor unit)
    MAX_ROUNDING_PER_LINE = "0.01"

    def __init__(self, entry_id: str, rounding_amount: str, threshold: str, currency: str):
        self.entry_id = entry_id
        self.rounding_amount = rounding_amount
        self.threshold = threshold
        self.currency = currency
        super().__init__(
            f"Rounding amount {rounding_amount} {currency} exceeds threshold {threshold} {currency} "
            f"for entry {entry_id}. Rounding is for sub-penny currency conversion only."
        )


# Reference Snapshot related exceptions


class ReferenceSnapshotError(FinanceKernelError):
    """Base exception for reference snapshot related errors."""

    code: str = "REFERENCE_SNAPSHOT_ERROR"


class MissingReferenceSnapshotError(ReferenceSnapshotError):
    """
    Posted journal entry is missing required reference snapshot version identifiers.

    R21 Compliance: Every posted JournalEntry must record immutable version
    identifiers for all reference data used during posting. This enables
    deterministic replay.
    """

    code: str = "MISSING_REFERENCE_SNAPSHOT"

    def __init__(self, entry_id: str, missing_fields: list[str]):
        self.entry_id = entry_id
        self.missing_fields = missing_fields
        super().__init__(
            f"Journal entry {entry_id} is missing required reference snapshot fields: "
            f"{', '.join(missing_fields)}. R21 requires all reference data versions "
            "to be recorded at post time for deterministic replay."
        )


# Strategy lifecycle related exceptions


class StrategyLifecycleError(FinanceKernelError):
    """Base exception for strategy lifecycle related errors."""

    code: str = "STRATEGY_LIFECYCLE_ERROR"


class StrategyVersionError(StrategyLifecycleError):
    """
    Strategy version is outside its supported range.

    R23 Compliance: Each strategy declares supported_from_version and
    supported_to_version (nullable). Replay must enforce compatibility.
    """

    code: str = "STRATEGY_VERSION_OUT_OF_RANGE"

    def __init__(
        self,
        event_type: str,
        strategy_version: int,
        supported_from: int,
        supported_to: int | None,
    ):
        self.event_type = event_type
        self.strategy_version = strategy_version
        self.supported_from = supported_from
        self.supported_to = supported_to
        to_str = str(supported_to) if supported_to else "current"
        super().__init__(
            f"Strategy version {strategy_version} for {event_type} is outside "
            f"supported range [{supported_from}, {to_str}]"
        )


class StrategyRoundingViolationError(StrategyLifecycleError):
    """
    Strategy attempted to create rounding lines directly.

    R22 Compliance: Only the Bookkeeper may generate is_rounding=true
    JournalLines. Strategies are prohibited from targeting rounding
    accounts directly.
    """

    code: str = "STRATEGY_ROUNDING_VIOLATION"

    def __init__(self, event_type: str, strategy_version: int):
        self.event_type = event_type
        self.strategy_version = strategy_version
        super().__init__(
            f"Strategy {event_type} v{strategy_version} attempted to create rounding lines. "
            "Only the Bookkeeper may generate is_rounding=true JournalLines (R22)."
        )


# Exchange Rate related exceptions


class ExchangeRateError(FinanceKernelError):
    """Base exception for exchange rate related errors."""

    code: str = "EXCHANGE_RATE_ERROR"


class ExchangeRateImmutableError(ExchangeRateError):
    """
    Attempted to modify an ExchangeRate that has been used in journal lines.

    Once an ExchangeRate is referenced by any JournalLine (via exchange_rate_id),
    it becomes immutable. This prevents retroactive manipulation of historical
    multi-currency transactions.
    """

    code: str = "EXCHANGE_RATE_IMMUTABLE"

    def __init__(self, rate_id: str, from_currency: str, to_currency: str):
        self.rate_id = rate_id
        self.from_currency = from_currency
        self.to_currency = to_currency
        super().__init__(
            f"ExchangeRate {rate_id} ({from_currency}/{to_currency}) is immutable: "
            f"it has been used in posted journal entries"
        )


class ExchangeRateReferencedError(ExchangeRateError):
    """
    Attempted to delete an ExchangeRate that is referenced by journal lines.

    Exchange rates cannot be deleted once used in any JournalLine, as this
    would break the audit trail and make historical entries uninterpretable.
    """

    code: str = "EXCHANGE_RATE_REFERENCED"

    def __init__(self, rate_id: str, reference_count: int):
        self.rate_id = rate_id
        self.reference_count = reference_count
        super().__init__(
            f"ExchangeRate {rate_id} cannot be deleted: "
            f"referenced by {reference_count} journal line(s)"
        )


class InvalidExchangeRateError(ExchangeRateError):
    """
    Exchange rate value is invalid (zero, negative, or mathematically impossible).

    Exchange rates must be positive non-zero values. A rate of zero would
    make currency conversion undefined, and negative rates are meaningless.
    """

    code: str = "INVALID_EXCHANGE_RATE"

    def __init__(self, rate_value: str, reason: str):
        self.rate_value = rate_value
        self.reason = reason
        super().__init__(
            f"Invalid exchange rate value {rate_value}: {reason}"
        )


class ExchangeRateArbitrageError(ExchangeRateError):
    """
    Exchange rate creates an arbitrage opportunity with its inverse.

    If rate A/B = X exists, then the inverse rate B/A must equal 1/X
    (within tolerance). Inconsistent rates could be exploited to create
    phantom value or hide losses.
    """

    code: str = "EXCHANGE_RATE_ARBITRAGE"

    def __init__(
        self,
        from_currency: str,
        to_currency: str,
        forward_rate: str,
        inverse_rate: str,
        expected_inverse: str,
    ):
        self.from_currency = from_currency
        self.to_currency = to_currency
        self.forward_rate = forward_rate
        self.inverse_rate = inverse_rate
        self.expected_inverse = expected_inverse
        super().__init__(
            f"Arbitrage detected: {from_currency}/{to_currency} rate {forward_rate} "
            f"implies inverse {expected_inverse}, but {to_currency}/{from_currency} "
            f"rate is {inverse_rate}. This inconsistency could hide value manipulation."
        )


# Economic Link related exceptions


class EconomicLinkError(FinanceKernelError):
    """Base exception for economic link related errors."""

    code: str = "ECONOMIC_LINK_ERROR"


class SelfLinkError(EconomicLinkError):
    """
    Attempted to create a link where parent equals child.

    L2 Compliance: Self-links are not allowed. An artifact cannot be
    its own parent or child.
    """

    code: str = "SELF_LINK"

    def __init__(self, artifact_ref: str):
        self.artifact_ref = artifact_ref
        super().__init__(
            f"Self-link not allowed: artifact {artifact_ref} cannot link to itself"
        )


class InvalidLinkTypeError(EconomicLinkError):
    """
    Link type is not valid for the given artifact types.

    L5 Compliance: Each link type defines valid parent/child artifact
    type combinations. Attempting to create a link with incompatible
    types is rejected.
    """

    code: str = "INVALID_LINK_TYPE"

    def __init__(
        self,
        link_type: str,
        parent_type: str,
        child_type: str,
        reason: str,
    ):
        self.link_type = link_type
        self.parent_type = parent_type
        self.child_type = child_type
        self.reason = reason
        super().__init__(
            f"Invalid link: {link_type} cannot connect {parent_type} to {child_type}. "
            f"{reason}"
        )


class LinkCycleError(EconomicLinkError):
    """
    Creating this link would introduce a cycle in the link graph.

    L3 Compliance: The link graph must be acyclic for certain link types
    (e.g., FULFILLED_BY, SOURCED_FROM). Cycles would create infinite loops
    in traversal and represent impossible economic relationships.
    """

    code: str = "LINK_CYCLE"

    def __init__(self, link_type: str, path: list[str]):
        self.link_type = link_type
        self.path = path
        path_str = " -> ".join(path)
        super().__init__(
            f"Cycle detected in {link_type} links: {path_str}"
        )


class DuplicateLinkError(EconomicLinkError):
    """
    A link with the same parent, child, and type already exists.

    Links are unique on (link_type, parent_ref, child_ref). Creating
    a duplicate would double-count the relationship.
    """

    code: str = "DUPLICATE_LINK"

    def __init__(self, link_type: str, parent_ref: str, child_ref: str):
        self.link_type = link_type
        self.parent_ref = parent_ref
        self.child_ref = child_ref
        super().__init__(
            f"Link already exists: {link_type} from {parent_ref} to {child_ref}"
        )


class MaxChildrenExceededError(EconomicLinkError):
    """
    Parent artifact has reached maximum allowed children for this link type.

    Some link types are constrained (e.g., REVERSED_BY can only have one
    child - an entry can only be reversed once).
    """

    code: str = "MAX_CHILDREN_EXCEEDED"

    def __init__(
        self,
        link_type: str,
        parent_ref: str,
        max_children: int,
        current_children: int,
    ):
        self.link_type = link_type
        self.parent_ref = parent_ref
        self.max_children = max_children
        self.current_children = current_children
        super().__init__(
            f"Cannot add child to {parent_ref} for {link_type}: "
            f"max {max_children} children allowed, already has {current_children}"
        )


class ArtifactNotFoundError(EconomicLinkError):
    """
    Referenced artifact does not exist.

    Links must reference existing artifacts. Creating a link to a
    non-existent artifact would leave a dangling pointer.
    """

    code: str = "ARTIFACT_NOT_FOUND"

    def __init__(self, artifact_type: str, artifact_id: str):
        self.artifact_type = artifact_type
        self.artifact_id = artifact_id
        super().__init__(
            f"Artifact not found: {artifact_type}:{artifact_id}"
        )


class LinkImmutableError(EconomicLinkError):
    """
    Attempted to modify or delete an immutable link.

    L1 Compliance: Links are immutable once created. They cannot be
    updated or deleted. To "undo" a link, create a compensating
    relationship (e.g., REVERSED_BY).
    """

    code: str = "LINK_IMMUTABLE"

    def __init__(self, link_id: str, operation: str):
        self.link_id = link_id
        self.operation = operation
        super().__init__(
            f"Cannot {operation} link {link_id}: links are immutable (L1)"
        )
