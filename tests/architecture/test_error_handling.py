"""
Tests for R18 and R19 compliance.

R18. Deterministic errors
- All domain errors must use typed exceptions with machine-readable codes
- No string-matching on error messages

R19. No silent correction
- Financial inconsistencies must fail or be explicitly compensated
  with a traceable rounding or adjustment line
"""

import pytest
import inspect
from decimal import Decimal
from datetime import date, datetime
from uuid import uuid4

from finance_kernel.exceptions import (
    FinanceKernelError,
    EventError,
    EventNotFoundError,
    EventAlreadyExistsError,
    PayloadMismatchError,
    UnsupportedSchemaVersionError,
    PostingError,
    AlreadyPostedError,
    UnbalancedEntryError,
    InvalidAccountError,
    MissingDimensionError,
    InvalidDimensionValueError,
    PostingRuleNotFoundError,
    PeriodError,
    ClosedPeriodError,
    PeriodNotFoundError,
    PeriodAlreadyClosedError,
    PeriodOverlapError,
    PeriodImmutableError,
    AdjustmentsNotAllowedError,
    AccountError,
    AccountNotFoundError,
    AccountInactiveError,
    AccountReferencedError,
    RoundingAccountNotFoundError,
    CurrencyError,
    InvalidCurrencyError,
    CurrencyMismatchError,
    ExchangeRateNotFoundError,
    AuditError,
    AuditChainBrokenError,
    ReversalError,
    EntryNotPostedError,
    EntryAlreadyReversedError,
    ConcurrencyError,
    OptimisticLockError,
    ImmutabilityError,
    ImmutabilityViolationError,
)
from finance_kernel.domain.strategy_registry import (
    StrategyNotFoundError,
    StrategyVersionNotFoundError,
)
from finance_kernel.domain.dtos import ValidationError, ValidationResult


class TestR18DeterministicErrors:
    """Tests for R18: Deterministic errors with machine-readable codes."""

    # All exception classes that should have codes
    EXCEPTION_CLASSES = [
        FinanceKernelError,
        EventError,
        EventNotFoundError,
        EventAlreadyExistsError,
        PayloadMismatchError,
        UnsupportedSchemaVersionError,
        PostingError,
        AlreadyPostedError,
        UnbalancedEntryError,
        InvalidAccountError,
        MissingDimensionError,
        InvalidDimensionValueError,
        PostingRuleNotFoundError,
        PeriodError,
        ClosedPeriodError,
        PeriodNotFoundError,
        PeriodAlreadyClosedError,
        PeriodOverlapError,
        PeriodImmutableError,
        AdjustmentsNotAllowedError,
        AccountError,
        AccountNotFoundError,
        AccountInactiveError,
        AccountReferencedError,
        RoundingAccountNotFoundError,
        CurrencyError,
        InvalidCurrencyError,
        CurrencyMismatchError,
        ExchangeRateNotFoundError,
        AuditError,
        AuditChainBrokenError,
        ReversalError,
        EntryNotPostedError,
        EntryAlreadyReversedError,
        ConcurrencyError,
        OptimisticLockError,
        ImmutabilityError,
        ImmutabilityViolationError,
        StrategyNotFoundError,
        StrategyVersionNotFoundError,
    ]

    def test_all_exceptions_have_code_attribute(self):
        """
        All domain exceptions must have a machine-readable code.

        R18: All domain errors must use typed exceptions with machine-readable codes.
        """
        for exc_class in self.EXCEPTION_CLASSES:
            assert hasattr(exc_class, "code"), (
                f"{exc_class.__name__} missing 'code' class attribute"
            )
            assert isinstance(exc_class.code, str), (
                f"{exc_class.__name__}.code must be a string"
            )
            assert len(exc_class.code) > 0, (
                f"{exc_class.__name__}.code must not be empty"
            )

    def test_exception_codes_are_uppercase_snake_case(self):
        """Error codes should follow UPPERCASE_SNAKE_CASE convention."""
        for exc_class in self.EXCEPTION_CLASSES:
            code = exc_class.code
            # Check format: uppercase letters, numbers, and underscores only
            assert code == code.upper(), (
                f"{exc_class.__name__}.code '{code}' should be uppercase"
            )
            assert all(c.isalnum() or c == "_" for c in code), (
                f"{exc_class.__name__}.code '{code}' should only contain "
                "alphanumeric characters and underscores"
            )

    def test_exception_codes_are_unique(self):
        """Each exception class should have a unique code."""
        codes = {}
        for exc_class in self.EXCEPTION_CLASSES:
            code = exc_class.code
            if code in codes:
                # Base classes may share codes with their children
                # but leaf classes should be unique
                existing = codes[code]
                assert issubclass(exc_class, existing) or issubclass(existing, exc_class), (
                    f"Duplicate code '{code}' used by both "
                    f"{existing.__name__} and {exc_class.__name__}"
                )
            codes[code] = exc_class

    def test_exceptions_inherit_from_finance_kernel_error(self):
        """All domain exceptions must inherit from FinanceKernelError."""
        for exc_class in self.EXCEPTION_CLASSES:
            if exc_class is FinanceKernelError:
                continue
            assert issubclass(exc_class, FinanceKernelError), (
                f"{exc_class.__name__} must inherit from FinanceKernelError"
            )

    def test_exception_instances_have_typed_attributes(self):
        """Exceptions must have typed attributes for machine-readable access."""
        # Test EventNotFoundError
        exc = EventNotFoundError("event-123")
        assert exc.event_id == "event-123"
        assert exc.code == "EVENT_NOT_FOUND"

        # Test PayloadMismatchError
        exc = PayloadMismatchError("event-123", "hash1", "hash2")
        assert exc.event_id == "event-123"
        assert exc.expected_hash == "hash1"
        assert exc.received_hash == "hash2"
        assert exc.code == "PAYLOAD_MISMATCH"

        # Test UnbalancedEntryError
        exc = UnbalancedEntryError("100.00", "99.00", "USD")
        assert exc.debits == "100.00"
        assert exc.credits == "99.00"
        assert exc.currency == "USD"
        assert exc.code == "UNBALANCED_ENTRY"

        # Test ClosedPeriodError
        exc = ClosedPeriodError("2024-01", "2024-01-15")
        assert exc.period_code == "2024-01"
        assert exc.effective_date == "2024-01-15"
        assert exc.code == "CLOSED_PERIOD"

    def test_validation_error_has_code(self):
        """ValidationError must have a code field."""
        error = ValidationError(
            code="TEST_ERROR",
            message="Test message",
            field="test_field",
            details={"key": "value"},
        )
        assert error.code == "TEST_ERROR"
        assert error.message == "Test message"
        assert error.field == "test_field"
        assert error.details == {"key": "value"}

    def test_validation_result_preserves_error_codes(self):
        """ValidationResult must preserve error codes for all errors."""
        errors = [
            ValidationError(code="ERROR_1", message="First error"),
            ValidationError(code="ERROR_2", message="Second error"),
        ]
        result = ValidationResult.failure(*errors)

        assert not result.is_valid
        assert len(result.errors) == 2
        assert result.errors[0].code == "ERROR_1"
        assert result.errors[1].code == "ERROR_2"

    def test_strategy_errors_have_codes(self):
        """Strategy registry errors must have machine-readable codes."""
        exc = StrategyNotFoundError("test.event.type")
        assert exc.code == "STRATEGY_NOT_FOUND"
        assert exc.event_type == "test.event.type"

        exc = StrategyVersionNotFoundError("test.event", 5, [1, 2, 3])
        assert exc.code == "STRATEGY_VERSION_NOT_FOUND"
        assert exc.event_type == "test.event"
        assert exc.version == 5
        assert exc.available_versions == [1, 2, 3]


class TestR18NoStringMatching:
    """Tests to verify no string-matching on error messages is required."""

    def test_can_identify_error_by_type(self):
        """Errors should be identifiable by type, not message parsing."""
        exc = EventNotFoundError("event-123")

        # Correct way: check by type
        assert isinstance(exc, EventNotFoundError)
        assert isinstance(exc, EventError)
        assert isinstance(exc, FinanceKernelError)

        # Correct way: check by code
        assert exc.code == "EVENT_NOT_FOUND"

    def test_can_identify_error_by_code_attribute(self):
        """Errors should be identifiable by code attribute."""
        exc = ClosedPeriodError("2024-01", "2024-01-15")

        # Correct way: check code
        assert exc.code == "CLOSED_PERIOD"

        # Access typed attributes instead of parsing message
        assert exc.period_code == "2024-01"
        assert exc.effective_date == "2024-01-15"

    def test_error_context_accessible_without_parsing(self):
        """All error context must be accessible via typed attributes."""
        exc = AuditChainBrokenError("audit-123", "expected-hash", "actual-hash")

        # All context available via attributes
        assert exc.audit_event_id == "audit-123"
        assert exc.expected_hash == "expected-hash"
        assert exc.actual_hash == "actual-hash"
        assert exc.code == "AUDIT_CHAIN_BROKEN"


class TestR19NoSilentCorrection:
    """Tests for R19: No silent correction of financial inconsistencies."""

    def test_rounding_lines_are_explicitly_marked(self):
        """Rounding lines must be explicitly marked with is_rounding=True."""
        from finance_kernel.domain.dtos import LineSpec, ProposedLine, LineSide
        from finance_kernel.domain.values import Money

        # LineSpec tracks rounding flag
        line = LineSpec(
            account_code="9999",
            side=LineSide.DEBIT,
            money=Money.of(Decimal("0.01"), "USD"),
            is_rounding=True,
        )
        assert line.is_rounding is True

        # ProposedLine tracks rounding flag
        proposed = ProposedLine(
            account_id=uuid4(),
            account_code="9999",
            side=LineSide.CREDIT,
            money=Money.of(Decimal("0.01"), "USD"),
            is_rounding=True,
            line_seq=0,
        )
        assert proposed.is_rounding is True

    def test_unbalanced_entry_fails_not_silently_corrected(self):
        """Unbalanced entries must fail, not be silently corrected."""
        from finance_kernel.domain.dtos import ProposedJournalEntry, EventEnvelope
        from finance_kernel.domain.strategy import BasePostingStrategy, StrategyResult
        from finance_kernel.domain.dtos import LineSpec, ReferenceData, LineSide
        from finance_kernel.domain.values import Money, Currency

        # Create strategy that produces unbalanced lines
        class UnbalancedStrategy(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "test.unbalanced"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(self, event, reference_data):
                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="2000",
                        side=LineSide.CREDIT,
                        money=Money.of(Decimal("50.00"), "USD"),  # Unbalanced!
                    ),
                ]

        strategy = UnbalancedStrategy()

        event = EventEnvelope(
            event_id=uuid4(),
            event_type="test.unbalanced",
            occurred_at=datetime.now(),
            effective_date=date.today(),
            actor_id=uuid4(),
            producer="test",
            payload={},
            payload_hash="test",
            schema_version=1,
        )

        reference_data = ReferenceData(
            account_ids_by_code={"1000": uuid4(), "2000": uuid4()},
            active_account_codes=frozenset(["1000", "2000"]),
            valid_currencies=frozenset([Currency("USD")]),
            rounding_account_ids={},  # No rounding account - should fail
        )

        result = strategy.propose(event, reference_data)

        # Must fail, not silently correct
        assert not result.is_valid
        assert any(e.code == "UNBALANCED_ENTRY" for e in result.validation.errors)

    def test_small_imbalance_creates_traceable_rounding_line(self):
        """Small imbalances within tolerance create explicit rounding lines."""
        from finance_kernel.domain.dtos import EventEnvelope
        from finance_kernel.domain.strategy import BasePostingStrategy
        from finance_kernel.domain.dtos import LineSpec, ReferenceData, LineSide
        from finance_kernel.domain.values import Money, Currency

        rounding_account_id = uuid4()

        # Create strategy with small imbalance (within tolerance)
        class SmallImbalanceStrategy(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "test.small.imbalance"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(self, event, reference_data):
                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="2000",
                        side=LineSide.CREDIT,
                        money=Money.of(Decimal("99.99"), "USD"),  # 0.01 imbalance
                    ),
                ]

        strategy = SmallImbalanceStrategy()

        event = EventEnvelope(
            event_id=uuid4(),
            event_type="test.small.imbalance",
            occurred_at=datetime.now(),
            effective_date=date.today(),
            actor_id=uuid4(),
            producer="test",
            payload={},
            payload_hash="test",
            schema_version=1,
        )

        reference_data = ReferenceData(
            account_ids_by_code={"1000": uuid4(), "2000": uuid4()},
            active_account_codes=frozenset(["1000", "2000"]),
            valid_currencies=frozenset([Currency("USD")]),
            rounding_account_ids={"USD": rounding_account_id},
        )

        result = strategy.propose(event, reference_data)

        # Should succeed with explicit rounding line
        assert result.is_valid
        assert result.proposed_entry is not None

        # Find the rounding line
        rounding_lines = [
            line for line in result.proposed_entry.lines if line.is_rounding
        ]
        assert len(rounding_lines) == 1, "Must have exactly one rounding line"

        rounding_line = rounding_lines[0]
        assert rounding_line.is_rounding is True  # Explicitly marked
        assert rounding_line.account_id == rounding_account_id  # Uses rounding account
        assert rounding_line.amount == Decimal("0.01")  # Correct amount

    def test_rounding_without_rounding_account_fails(self):
        """Rounding is impossible without a configured rounding account."""
        from finance_kernel.domain.dtos import EventEnvelope
        from finance_kernel.domain.strategy import BasePostingStrategy
        from finance_kernel.domain.dtos import LineSpec, ReferenceData, LineSide
        from finance_kernel.domain.values import Money, Currency

        # Create strategy with small imbalance
        class ImbalanceStrategy(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "test.no.rounding.account"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(self, event, reference_data):
                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="2000",
                        side=LineSide.CREDIT,
                        money=Money.of(Decimal("99.99"), "USD"),
                    ),
                ]

        strategy = ImbalanceStrategy()

        event = EventEnvelope(
            event_id=uuid4(),
            event_type="test.no.rounding.account",
            occurred_at=datetime.now(),
            effective_date=date.today(),
            actor_id=uuid4(),
            producer="test",
            payload={},
            payload_hash="test",
            schema_version=1,
        )

        # No rounding account configured
        reference_data = ReferenceData(
            account_ids_by_code={"1000": uuid4(), "2000": uuid4()},
            active_account_codes=frozenset(["1000", "2000"]),
            valid_currencies=frozenset([Currency("USD")]),
            rounding_account_ids={},  # Empty!
        )

        result = strategy.propose(event, reference_data)

        # Must fail - cannot silently correct without rounding account
        assert not result.is_valid
        assert any(e.code == "NO_ROUNDING_ACCOUNT" for e in result.validation.errors)

    def test_journal_lines_track_rounding_flag_in_database(self):
        """JournalLine model must have is_rounding field for traceability."""
        from finance_kernel.models.journal import JournalLine
        from sqlalchemy import inspect

        # Verify the model has the is_rounding field
        assert hasattr(JournalLine, "is_rounding")

        # Check it's a mapped column
        mapper = inspect(JournalLine)
        column_names = [col.key for col in mapper.columns]
        assert "is_rounding" in column_names, (
            "JournalLine must have is_rounding column for R19 traceability"
        )
