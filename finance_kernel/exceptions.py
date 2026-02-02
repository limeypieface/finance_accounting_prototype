"""Typed exception hierarchy for the finance kernel (R18 compliance)."""


class FinanceKernelError(Exception):
    """Base exception for all finance kernel errors (R18)."""

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
    """Event ID exists but with different payload hash (protocol violation)."""

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
    """Event payload does not match registered schema (P10)."""

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
    """Field reference does not exist in event schema (P10)."""

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
    """New period date range overlaps with existing period (R12)."""

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
    """Attempted to modify a closed period (R13)."""

    code: str = "PERIOD_IMMUTABLE"

    def __init__(self, period_code: str, operation: str):
        self.period_code = period_code
        self.operation = operation
        super().__init__(
            f"Cannot {operation} closed period {period_code}: "
            "closed periods are immutable"
        )


class AdjustmentsNotAllowedError(PeriodError):
    """Period does not allow adjusting entries (R13)."""

    code: str = "ADJUSTMENTS_NOT_ALLOWED"

    def __init__(self, period_code: str):
        self.period_code = period_code
        super().__init__(
            f"Period {period_code} does not allow adjusting entries"
        )


class PeriodClosingError(PeriodError):
    """Non-close posting attempted on a period that is mid-close (R25)."""

    code: str = "PERIOD_CLOSING"

    def __init__(self, period_code: str):
        self.period_code = period_code
        super().__init__(
            f"Period {period_code} is in CLOSING state — "
            f"only close-related postings are permitted"
        )


class CloseAuthorityError(PeriodError):
    """Actor lacks authority for the requested close operation."""

    code: str = "CLOSE_AUTHORITY_DENIED"

    def __init__(
        self,
        actor_id: str,
        required_role: str,
        actual_role: str,
        phase: int,
    ):
        self.actor_id = actor_id
        self.required_role = required_role
        self.actual_role = actual_role
        self.phase = phase
        super().__init__(
            f"Actor {actor_id} has role {actual_role} but phase {phase} "
            f"requires {required_role}"
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
    """Attempted to modify or delete an immutable record (R10)."""

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
    """Entry has more than one line marked is_rounding=True."""

    code: str = "MULTIPLE_ROUNDING_LINES"

    def __init__(self, entry_id: str, rounding_count: int):
        self.entry_id = entry_id
        self.rounding_count = rounding_count
        super().__init__(
            f"Entry {entry_id} has {rounding_count} rounding lines. "
            f"At most ONE line can have is_rounding=True."
        )


class RoundingAmountExceededError(RoundingError):
    """Rounding line amount exceeds the maximum allowed threshold."""

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
    """Posted entry missing required reference snapshot version identifiers (R21)."""

    code: str = "MISSING_REFERENCE_SNAPSHOT"

    def __init__(self, entry_id: str, missing_fields: list[str]):
        self.entry_id = entry_id
        self.missing_fields = missing_fields
        super().__init__(
            f"Journal entry {entry_id} is missing required reference snapshot fields: "
            f"{', '.join(missing_fields)}. R21 requires all reference data versions "
            "to be recorded at post time for deterministic replay."
        )


class StaleReferenceSnapshotError(ReferenceSnapshotError):
    """Reference snapshot is stale -- current data has changed since capture (G10)."""

    code: str = "STALE_REFERENCE_SNAPSHOT"

    def __init__(
        self,
        entry_id: str,
        stale_components: list[str],
    ):
        self.entry_id = entry_id
        self.stale_components = stale_components
        super().__init__(
            f"Reference snapshot for entry {entry_id} is stale. "
            f"Changed components: {', '.join(stale_components)}. "
            "Re-capture the snapshot and retry."
        )


# Strategy lifecycle related exceptions


class StrategyLifecycleError(FinanceKernelError):
    """Base exception for strategy lifecycle related errors."""

    code: str = "STRATEGY_LIFECYCLE_ERROR"


class StrategyVersionError(StrategyLifecycleError):
    """Strategy version is outside its supported range (R23)."""

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
    """Strategy attempted to create rounding lines directly (R22)."""

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
    """ExchangeRate referenced by journal lines cannot be modified."""

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
    """ExchangeRate referenced by journal lines cannot be deleted."""

    code: str = "EXCHANGE_RATE_REFERENCED"

    def __init__(self, rate_id: str, reference_count: int):
        self.rate_id = rate_id
        self.reference_count = reference_count
        super().__init__(
            f"ExchangeRate {rate_id} cannot be deleted: "
            f"referenced by {reference_count} journal line(s)"
        )


class InvalidExchangeRateError(ExchangeRateError):
    """Exchange rate value is invalid (zero, negative, or out of range)."""

    code: str = "INVALID_EXCHANGE_RATE"

    def __init__(self, rate_value: str, reason: str):
        self.rate_value = rate_value
        self.reason = reason
        super().__init__(
            f"Invalid exchange rate value {rate_value}: {reason}"
        )


class ExchangeRateArbitrageError(ExchangeRateError):
    """Exchange rate creates an arbitrage opportunity with its inverse."""

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
    """Artifact cannot link to itself (L2)."""

    code: str = "SELF_LINK"

    def __init__(self, artifact_ref: str):
        self.artifact_ref = artifact_ref
        super().__init__(
            f"Self-link not allowed: artifact {artifact_ref} cannot link to itself"
        )


class InvalidLinkTypeError(EconomicLinkError):
    """Link type is not valid for the given artifact types (L5)."""

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
    """Creating this link would introduce a cycle in the link graph (L3)."""

    code: str = "LINK_CYCLE"

    def __init__(self, link_type: str, path: list[str]):
        self.link_type = link_type
        self.path = path
        path_str = " -> ".join(path)
        super().__init__(
            f"Cycle detected in {link_type} links: {path_str}"
        )


class DuplicateLinkError(EconomicLinkError):
    """A link with the same parent, child, and type already exists."""

    code: str = "DUPLICATE_LINK"

    def __init__(self, link_type: str, parent_ref: str, child_ref: str):
        self.link_type = link_type
        self.parent_ref = parent_ref
        self.child_ref = child_ref
        super().__init__(
            f"Link already exists: {link_type} from {parent_ref} to {child_ref}"
        )


class MaxChildrenExceededError(EconomicLinkError):
    """Parent artifact has reached maximum allowed children for this link type."""

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
    """Referenced artifact does not exist."""

    code: str = "ARTIFACT_NOT_FOUND"

    def __init__(self, artifact_type: str, artifact_id: str):
        self.artifact_type = artifact_type
        self.artifact_id = artifact_id
        super().__init__(
            f"Artifact not found: {artifact_type}:{artifact_id}"
        )


class LinkImmutableError(EconomicLinkError):
    """Links are immutable once created (L1)."""

    code: str = "LINK_IMMUTABLE"

    def __init__(self, link_id: str, operation: str):
        self.link_id = link_id
        self.operation = operation
        super().__init__(
            f"Cannot {operation} link {link_id}: links are immutable (L1)"
        )


# Reconciliation related exceptions


class ReconciliationError(FinanceKernelError):
    """Base exception for reconciliation related errors."""

    code: str = "RECONCILIATION_ERROR"


class OverapplicationError(ReconciliationError):
    """Applied amount exceeds remaining balance."""

    code: str = "OVERAPPLICATION"

    def __init__(
        self,
        document_ref: str,
        remaining_amount: str,
        attempted_amount: str,
        currency: str,
    ):
        self.document_ref = document_ref
        self.remaining_amount = remaining_amount
        self.attempted_amount = attempted_amount
        self.currency = currency
        super().__init__(
            f"Cannot apply {attempted_amount} {currency} to {document_ref}: "
            f"only {remaining_amount} {currency} remaining"
        )


class DocumentAlreadyMatchedError(ReconciliationError):
    """Document is already fully matched."""

    code: str = "DOCUMENT_ALREADY_MATCHED"

    def __init__(self, document_ref: str):
        self.document_ref = document_ref
        super().__init__(
            f"Document {document_ref} is already fully matched"
        )


class MatchVarianceExceededError(ReconciliationError):
    """Three-way match variance exceeds configured tolerance."""

    code: str = "MATCH_VARIANCE_EXCEEDED"

    def __init__(
        self,
        match_type: str,
        variance_type: str,
        variance_amount: str,
        tolerance: str,
        currency: str | None = None,
    ):
        self.match_type = match_type
        self.variance_type = variance_type
        self.variance_amount = variance_amount
        self.tolerance = tolerance
        self.currency = currency
        unit = f" {currency}" if currency else ""
        super().__init__(
            f"{match_type} {variance_type} variance of {variance_amount}{unit} "
            f"exceeds tolerance of {tolerance}{unit}"
        )


class BankReconciliationError(ReconciliationError):
    """Bank statement reconciliation error."""

    code: str = "BANK_RECONCILIATION_ERROR"

    def __init__(self, statement_line_id: str, reason: str):
        self.statement_line_id = statement_line_id
        self.reason = reason
        super().__init__(
            f"Bank reconciliation error for line {statement_line_id}: {reason}"
        )


class SubledgerReconciliationError(ReconciliationError):
    """Subledger/GL control account reconciliation failed at posting time (G9)."""

    code: str = "SUBLEDGER_RECONCILIATION_FAILED"

    def __init__(
        self,
        ledger_id: str,
        violations: list[str],
    ):
        self.ledger_id = ledger_id
        self.violations = violations
        super().__init__(
            f"Subledger reconciliation failed for ledger '{ledger_id}': "
            + "; ".join(violations)
        )


# Valuation related exceptions


class ValuationError(FinanceKernelError):
    """Base exception for valuation/costing related errors."""

    code: str = "VALUATION_ERROR"


class InsufficientInventoryError(ValuationError):
    """Not enough inventory available to fulfill the requested quantity."""

    code: str = "INSUFFICIENT_INVENTORY"

    def __init__(
        self,
        item_id: str,
        requested_quantity: str,
        available_quantity: str,
        unit: str,
    ):
        self.item_id = item_id
        self.requested_quantity = requested_quantity
        self.available_quantity = available_quantity
        self.unit = unit
        super().__init__(
            f"Insufficient inventory for item {item_id}: "
            f"requested {requested_quantity} {unit}, "
            f"only {available_quantity} {unit} available"
        )


class LotNotFoundError(ValuationError):
    """Specified cost lot does not exist."""

    code: str = "LOT_NOT_FOUND"

    def __init__(self, lot_id: str, item_id: str | None = None):
        self.lot_id = lot_id
        self.item_id = item_id
        item_info = f" for item {item_id}" if item_id else ""
        super().__init__(
            f"Cost lot {lot_id} not found{item_info}"
        )


class LotDepletedError(ValuationError):
    """Cost lot has no remaining quantity."""

    code: str = "LOT_DEPLETED"

    def __init__(self, lot_id: str, item_id: str):
        self.lot_id = lot_id
        self.item_id = item_id
        super().__init__(
            f"Cost lot {lot_id} for item {item_id} is fully depleted"
        )


class StandardCostNotFoundError(ValuationError):
    """No standard cost defined for the item."""

    code: str = "STANDARD_COST_NOT_FOUND"

    def __init__(self, item_id: str, as_of_date: str | None = None):
        self.item_id = item_id
        self.as_of_date = as_of_date
        date_info = f" as of {as_of_date}" if as_of_date else ""
        super().__init__(
            f"No standard cost defined for item {item_id}{date_info}"
        )


# Correction related exceptions


class CorrectionError(FinanceKernelError):
    """Base exception for correction/unwind related errors."""

    code: str = "CORRECTION_ERROR"


class AlreadyCorrectedError(CorrectionError):
    """Document has already been corrected/voided."""

    code: str = "ALREADY_CORRECTED"

    def __init__(self, document_ref: str, correction_ref: str):
        self.document_ref = document_ref
        self.correction_ref = correction_ref
        super().__init__(
            f"Document {document_ref} has already been corrected by {correction_ref}"
        )


class CorrectionCascadeBlockedError(CorrectionError):
    """Correction cascade is blocked by a downstream artifact."""

    code: str = "CORRECTION_CASCADE_BLOCKED"

    def __init__(
        self,
        root_ref: str,
        blocked_ref: str,
        reason: str,
        depth: int,
    ):
        self.root_ref = root_ref
        self.blocked_ref = blocked_ref
        self.reason = reason
        self.depth = depth
        super().__init__(
            f"Cascade correction of {root_ref} blocked at depth {depth}: "
            f"{blocked_ref} cannot be unwound - {reason}"
        )


class UnwindDepthExceededError(CorrectionError):
    """Correction cascade exceeded maximum allowed depth."""

    code: str = "UNWIND_DEPTH_EXCEEDED"

    def __init__(self, root_ref: str, max_depth: int, reached_depth: int):
        self.root_ref = root_ref
        self.max_depth = max_depth
        self.reached_depth = reached_depth
        super().__init__(
            f"Unwind of {root_ref} exceeded max depth: "
            f"reached {reached_depth}, max is {max_depth}"
        )


class NoGLImpactError(CorrectionError):
    """Document has no GL entries to correct."""

    code: str = "NO_GL_IMPACT"

    def __init__(self, document_ref: str):
        self.document_ref = document_ref
        super().__init__(
            f"Document {document_ref} has no GL entries to correct"
        )


# Party related exceptions


class PartyError(FinanceKernelError):
    """Base exception for party-related errors."""

    code: str = "PARTY_ERROR"


class PartyNotFoundError(PartyError):
    """Party with given code or ID was not found."""

    code: str = "PARTY_NOT_FOUND"

    def __init__(self, party_code: str):
        self.party_code = party_code
        super().__init__(f"Party not found: {party_code}")


class PartyFrozenError(PartyError):
    """Party is frozen and cannot transact."""

    code: str = "PARTY_FROZEN"

    def __init__(self, party_code: str, reason: str | None = None):
        self.party_code = party_code
        self.reason = reason
        reason_text = f": {reason}" if reason else ""
        super().__init__(
            f"Party {party_code} is frozen and cannot transact{reason_text}"
        )


class PartyInactiveError(PartyError):
    """Party is inactive and cannot be used for new transactions."""

    code: str = "PARTY_INACTIVE"

    def __init__(self, party_code: str):
        self.party_code = party_code
        super().__init__(
            f"Party {party_code} is inactive and cannot be used for new transactions"
        )


class CreditLimitExceededError(PartyError):
    """Transaction would exceed party's credit limit."""

    code: str = "CREDIT_LIMIT_EXCEEDED"

    def __init__(
        self,
        party_code: str,
        credit_limit: str,
        current_balance: str,
        requested_amount: str,
        currency: str,
    ):
        self.party_code = party_code
        self.credit_limit = credit_limit
        self.current_balance = current_balance
        self.requested_amount = requested_amount
        self.currency = currency
        available = f"{credit_limit} - {current_balance}"
        super().__init__(
            f"Credit limit exceeded for {party_code}: "
            f"limit {credit_limit} {currency}, balance {current_balance} {currency}, "
            f"requested {requested_amount} {currency}"
        )


class PartyReferencedError(PartyError):
    """Party cannot be deleted because it is referenced by transactions."""

    code: str = "PARTY_REFERENCED"

    def __init__(self, party_code: str, reference_count: int):
        self.party_code = party_code
        self.reference_count = reference_count
        super().__init__(
            f"Party {party_code} cannot be deleted: "
            f"referenced by {reference_count} transaction(s)"
        )


# Contract related exceptions


class ContractError(FinanceKernelError):
    """Base exception for contract-related errors."""

    code: str = "CONTRACT_ERROR"


class ContractNotFoundError(ContractError):
    """Contract with given number or ID was not found."""

    code: str = "CONTRACT_NOT_FOUND"

    def __init__(self, contract_id: str):
        self.contract_id = contract_id
        super().__init__(f"Contract not found: {contract_id}")


class ContractInactiveError(ContractError):
    """Contract is not active and cannot accept new charges."""

    code: str = "CONTRACT_INACTIVE"

    def __init__(self, contract_number: str, status: str):
        self.contract_number = contract_number
        self.status = status
        super().__init__(
            f"Contract {contract_number} is {status} and cannot accept charges"
        )


class ContractFundingExceededError(ContractError):
    """Charge would exceed contract's funded amount (DCAA)."""

    code: str = "CONTRACT_FUNDING_EXCEEDED"

    def __init__(
        self,
        contract_number: str,
        funded_amount: str,
        incurred_amount: str,
        charge_amount: str,
        currency: str,
    ):
        self.contract_number = contract_number
        self.funded_amount = funded_amount
        self.incurred_amount = incurred_amount
        self.charge_amount = charge_amount
        self.currency = currency
        super().__init__(
            f"Funding exceeded for contract {contract_number}: "
            f"funded {funded_amount} {currency}, incurred {incurred_amount} {currency}, "
            f"charge would add {charge_amount} {currency}"
        )


class ContractCeilingExceededError(ContractError):
    """Charge would exceed contract's ceiling amount."""

    code: str = "CONTRACT_CEILING_EXCEEDED"

    def __init__(
        self,
        contract_number: str,
        ceiling_amount: str,
        current_total: str,
        charge_amount: str,
        currency: str,
    ):
        self.contract_number = contract_number
        self.ceiling_amount = ceiling_amount
        self.current_total = current_total
        self.charge_amount = charge_amount
        self.currency = currency
        super().__init__(
            f"Ceiling exceeded for contract {contract_number}: "
            f"ceiling {ceiling_amount} {currency}, current {current_total} {currency}, "
            f"charge would add {charge_amount} {currency}"
        )


class ContractPOPExpiredError(ContractError):
    """Charge date is outside contract's period of performance."""

    code: str = "CONTRACT_POP_EXPIRED"

    def __init__(
        self,
        contract_number: str,
        charge_date: str,
        pop_start: str | None,
        pop_end: str | None,
    ):
        self.contract_number = contract_number
        self.charge_date = charge_date
        self.pop_start = pop_start
        self.pop_end = pop_end
        pop_range = f"{pop_start or 'N/A'} to {pop_end or 'N/A'}"
        super().__init__(
            f"Charge date {charge_date} is outside period of performance "
            f"for contract {contract_number}: {pop_range}"
        )


class CLINNotFoundError(ContractError):
    """Contract line item (CLIN) was not found."""

    code: str = "CLIN_NOT_FOUND"

    def __init__(self, contract_number: str, clin_number: str):
        self.contract_number = contract_number
        self.clin_number = clin_number
        super().__init__(
            f"CLIN {clin_number} not found on contract {contract_number}"
        )


class CLINInactiveError(ContractError):
    """CLIN is not active and cannot accept charges."""

    code: str = "CLIN_INACTIVE"

    def __init__(self, contract_number: str, clin_number: str):
        self.contract_number = contract_number
        self.clin_number = clin_number
        super().__init__(
            f"CLIN {clin_number} on contract {contract_number} is inactive"
        )


class UnallowableCostToContractError(ContractError):
    """Unallowable cost cannot be charged to a government contract (DCAA)."""

    code: str = "UNALLOWABLE_COST_TO_CONTRACT"

    def __init__(
        self,
        contract_number: str,
        cost_type: str,
        unallowable_reason: str | None = None,
    ):
        self.contract_number = contract_number
        self.cost_type = cost_type
        self.unallowable_reason = unallowable_reason
        reason_text = f" ({unallowable_reason})" if unallowable_reason else ""
        super().__init__(
            f"Unallowable {cost_type} cost{reason_text} cannot be charged "
            f"to contract {contract_number}"
        )


# Actor-related exceptions


class ActorError(FinanceKernelError):
    """Base exception for actor validation errors."""

    code: str = "ACTOR_ERROR"


class InvalidActorError(ActorError):
    """Actor ID does not reference a valid, active party."""

    code: str = "INVALID_ACTOR"

    def __init__(self, actor_id: str):
        self.actor_id = actor_id
        super().__init__(
            f"Actor {actor_id} is not a valid, active party"
        )


class ActorFrozenError(ActorError):
    """Actor is frozen and cannot perform postings."""

    code: str = "ACTOR_FROZEN"

    def __init__(self, actor_id: str, reason: str | None = None):
        self.actor_id = actor_id
        self.reason = reason
        reason_text = f": {reason}" if reason else ""
        super().__init__(
            f"Actor {actor_id} is frozen and cannot post{reason_text}"
        )


# Approval-related exceptions


class ApprovalError(FinanceKernelError):
    """Base exception for approval system errors."""

    code: str = "APPROVAL_ERROR"


class ApprovalRequiredError(ApprovalError):
    """Transition requires approval that has not been granted."""

    code: str = "APPROVAL_REQUIRED"

    def __init__(self, workflow_name: str, action: str, request_id: str | None = None):
        self.workflow_name = workflow_name
        self.action = action
        self.request_id = request_id
        msg = f"Approval required for {workflow_name}/{action}"
        if request_id:
            msg += f" (request {request_id})"
        super().__init__(msg)


class ApprovalNotFoundError(ApprovalError):
    """Referenced approval request does not exist."""

    code: str = "APPROVAL_NOT_FOUND"

    def __init__(self, request_id: str):
        self.request_id = request_id
        super().__init__(f"Approval request not found: {request_id}")


class UnauthorizedApproverError(ApprovalError):
    """Actor does not have the required role to approve."""

    code: str = "UNAUTHORIZED_APPROVER"

    def __init__(self, actor_id: str, actor_role: str, required_roles: tuple):
        self.actor_id = actor_id
        self.actor_role = actor_role
        self.required_roles = required_roles
        super().__init__(
            f"Actor {actor_id} (role={actor_role}) not authorized; "
            f"required: {required_roles}"
        )


class ApprovalAlreadyResolvedError(ApprovalError):
    """Approval request has already been resolved (AL-1)."""

    code: str = "APPROVAL_ALREADY_RESOLVED"

    def __init__(self, request_id: str, current_status: str):
        self.request_id = request_id
        self.current_status = current_status
        super().__init__(
            f"Approval request {request_id} already resolved: {current_status}"
        )


class InvalidApprovalTransitionError(ApprovalError):
    """Status transition violates the approval lifecycle state machine (AL-1)."""

    code: str = "INVALID_APPROVAL_TRANSITION"

    def __init__(self, from_status: str, to_status: str):
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Invalid approval transition: {from_status} -> {to_status}"
        )


class PolicyDriftError(ApprovalError):
    """Active policy version is lower than the snapshotted version (AL-5)."""

    code: str = "POLICY_DRIFT"

    def __init__(self, policy_name: str, snapshotted_version: int, active_version: int):
        self.policy_name = policy_name
        self.snapshotted_version = snapshotted_version
        self.active_version = active_version
        super().__init__(
            f"Policy '{policy_name}' downgraded: request has v{snapshotted_version}, "
            f"active is v{active_version}"
        )


class ApprovalCurrencyMismatchError(ApprovalError):
    """Request currency does not match the policy's declared currency (AL-3)."""

    code: str = "APPROVAL_CURRENCY_MISMATCH"

    def __init__(self, request_currency: str, policy_currency: str):
        self.request_currency = request_currency
        self.policy_currency = policy_currency
        super().__init__(
            f"Currency mismatch: request={request_currency}, "
            f"policy={policy_currency}"
        )


class DuplicateApprovalError(ApprovalError):
    """Same actor attempted to approve the same request twice (AL-7)."""

    code: str = "DUPLICATE_APPROVAL"

    def __init__(self, request_id: str, actor_id: str):
        self.request_id = request_id
        self.actor_id = actor_id
        super().__init__(
            f"Actor {actor_id} already decided on request {request_id}"
        )


class TamperDetectedError(ApprovalError):
    """Approval request hash does not match stored hash (AL-8)."""

    code: str = "APPROVAL_TAMPER_DETECTED"

    def __init__(self, request_id: str):
        self.request_id = request_id
        super().__init__(
            f"Tamper detected on approval request {request_id}: "
            f"hash mismatch"
        )


class DuplicateApprovalRequestError(ApprovalError):
    """Duplicate pending approval request for the same transition (AL-10)."""

    code: str = "DUPLICATE_APPROVAL_REQUEST"

    def __init__(self, entity_type: str, entity_id: str, action: str):
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.action = action
        super().__init__(
            f"Duplicate pending request for {entity_type}/{entity_id} "
            f"action={action}"
        )
