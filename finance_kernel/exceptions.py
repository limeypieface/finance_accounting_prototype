"""
Custom exception hierarchy for the finance kernel.

All exceptions inherit from FinanceKernelError for easy catching.

R18 Compliance: All domain errors use typed exceptions with machine-readable codes.
No string-matching on error messages required - use exception type or code attribute.
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


class PostingRuleNotFoundError(PostingError):
    """No posting rule found for event type."""

    code: str = "POSTING_RULE_NOT_FOUND"

    def __init__(self, event_type: str):
        self.event_type = event_type
        super().__init__(f"No posting rule found for event type: {event_type}")


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
