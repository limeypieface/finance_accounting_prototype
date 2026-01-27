"""
Posting strategy protocol and base implementation.

A PostingStrategy is a pure function that transforms an event into
a proposed journal entry. It has NO side effects and NO access to:
- Database
- Clock/time
- I/O
- External services

All dependencies are passed in as ReferenceData.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from finance_kernel.domain.dtos import (
    EventEnvelope,
    LineSide,
    LineSpec,
    ProposedJournalEntry,
    ProposedLine,
    ReferenceData,
    ValidationError,
    ValidationResult,
)
from finance_kernel.domain.currency import CurrencyRegistry
from finance_kernel.domain.values import Money

if TYPE_CHECKING:
    pass


# =============================================================================
# R23: Strategy Lifecycle Governance
# =============================================================================


class ReplayPolicy(str, Enum):
    """
    Replay policy for a strategy (R23).

    Determines how the replay system handles version mismatches.
    """

    STRICT = "strict"  # Replay must use exact same version that was used originally
    PERMISSIVE = "permissive"  # Replay can use any compatible version


@dataclass(frozen=True)
class StrategyResult:
    """
    Result of applying a posting strategy.

    Either contains a proposed entry OR validation errors, never both.
    """

    proposed_entry: ProposedJournalEntry | None
    validation: ValidationResult

    @classmethod
    def success(cls, entry: ProposedJournalEntry) -> "StrategyResult":
        """Create a successful result."""
        return cls(proposed_entry=entry, validation=ValidationResult.success())

    @classmethod
    def failure(cls, *errors: ValidationError) -> "StrategyResult":
        """Create a failed result."""
        return cls(
            proposed_entry=None, validation=ValidationResult.failure(*errors)
        )

    @property
    def is_valid(self) -> bool:
        return self.validation.is_valid and self.proposed_entry is not None


class PostingStrategy(ABC):
    """
    Abstract base for posting strategies.

    Each event type has its own strategy that knows how to
    transform that event into journal lines.

    Strategies MUST be:
    - Pure: No side effects
    - Deterministic: Same input always produces same output
    - Immutable: No internal state changes

    R23 Compliance: Strategies declare lifecycle metadata for replay governance.
    """

    @property
    @abstractmethod
    def event_type(self) -> str:
        """The event type this strategy handles."""
        ...

    @property
    @abstractmethod
    def version(self) -> int:
        """Version of this strategy (for replay)."""
        ...

    # =========================================================================
    # R23: Strategy Lifecycle Governance
    # =========================================================================

    @property
    def supported_from_version(self) -> int:
        """
        Minimum system version this strategy supports (R23).

        For replay: if an event was originally posted when system version < this,
        the replay system should use an older version of this strategy.

        Default: 1 (supports all versions from the beginning)
        """
        return 1

    @property
    def supported_to_version(self) -> int | None:
        """
        Maximum system version this strategy supports, or None for "current" (R23).

        If not None, this strategy is deprecated and should not be used for
        new postings when system version > this.

        Default: None (still current, no deprecation)
        """
        return None

    @property
    def replay_policy(self) -> ReplayPolicy:
        """
        Replay policy for this strategy (R23).

        - STRICT: Replay must use exact same strategy version
        - PERMISSIVE: Replay can use any compatible version

        Default: STRICT (safest for financial systems)
        """
        return ReplayPolicy.STRICT

    def is_compatible_with_system_version(self, system_version: int) -> bool:
        """
        Check if this strategy is compatible with a given system version (R23).

        Args:
            system_version: The system version to check against.

        Returns:
            True if compatible, False otherwise.
        """
        if system_version < self.supported_from_version:
            return False
        if self.supported_to_version is not None and system_version > self.supported_to_version:
            return False
        return True

    @abstractmethod
    def propose(
        self,
        event: EventEnvelope,
        reference_data: ReferenceData,
    ) -> StrategyResult:
        """
        Transform an event into a proposed journal entry.

        This is the core method. It:
        1. Extracts amounts and accounts from the event payload
        2. Creates LineSpecs for each journal line
        3. Validates the proposed entry (balanced, valid accounts, etc.)
        4. Returns either a ProposedJournalEntry or validation errors

        Args:
            event: The event to transform.
            reference_data: Reference data for lookups (accounts, currencies, etc.)

        Returns:
            StrategyResult with either a proposed entry or errors.
        """
        ...


class BasePostingStrategy(PostingStrategy):
    """
    Base implementation with common validation and transformation logic.

    Subclasses only need to implement _compute_line_specs() to define
    how the event payload maps to journal lines.
    """

    @abstractmethod
    def _compute_line_specs(
        self,
        event: EventEnvelope,
        reference_data: ReferenceData,
    ) -> list[LineSpec]:
        """
        Compute line specifications from the event.

        Subclasses implement this to define the mapping from
        event payload to journal lines.

        Args:
            event: The event to transform.
            reference_data: Reference data for lookups.

        Returns:
            List of LineSpecs (account_code, side, amount, currency).
        """
        ...

    def propose(
        self,
        event: EventEnvelope,
        reference_data: ReferenceData,
    ) -> StrategyResult:
        """
        Transform an event into a proposed journal entry.

        Implements the full transformation pipeline:
        1. Compute line specs from event
        2. Validate currencies
        3. Resolve account codes to IDs
        4. Validate accounts are active
        5. Validate dimensions
        6. Check balance per currency
        7. Apply rounding if needed
        8. Create ProposedJournalEntry
        """
        # 1. Compute line specs from event
        try:
            line_specs = self._compute_line_specs(event, reference_data)
        except Exception as e:
            return StrategyResult.failure(
                ValidationError(
                    code="COMPUTATION_ERROR",
                    message=f"Failed to compute lines: {e}",
                )
            )

        if not line_specs:
            return StrategyResult.failure(
                ValidationError(
                    code="NO_LINES",
                    message="Strategy produced no journal lines",
                )
            )

        # R22: Validate strategy did not attempt to create rounding lines
        # Only the Bookkeeper (_balance_and_round) may generate is_rounding=True lines
        rounding_violation = self._validate_no_rounding_lines(line_specs)
        if rounding_violation:
            return StrategyResult.failure(rounding_violation)

        # 2. Validate currencies
        currency_errors = self._validate_currencies(line_specs, reference_data)
        if currency_errors:
            return StrategyResult.failure(*currency_errors)

        # 3. Resolve account codes and validate
        proposed_lines, account_errors = self._resolve_accounts(
            line_specs, reference_data
        )
        if account_errors:
            return StrategyResult.failure(*account_errors)

        # 4. Validate dimensions
        dimension_errors = self._validate_dimensions(
            proposed_lines, reference_data
        )
        if dimension_errors:
            return StrategyResult.failure(*dimension_errors)

        # 5. Check balance and apply rounding
        balanced_lines, balance_errors = self._balance_and_round(
            proposed_lines, reference_data
        )
        if balance_errors:
            return StrategyResult.failure(*balance_errors)

        # 6. Validate rounding invariants (fraud prevention)
        rounding_errors = self._validate_rounding_invariants(balanced_lines)
        if rounding_errors:
            return StrategyResult.failure(*rounding_errors)

        # 7. Create proposed entry (R21: Include reference snapshot versions)
        try:
            entry = ProposedJournalEntry(
                event_envelope=event,
                lines=tuple(balanced_lines),
                description=self._get_description(event),
                metadata=self._get_metadata(event),
                posting_rule_version=self.version,
                rounding_rule_version=1,  # Rounding is versioned separately
                # R21: Reference snapshot version identifiers for deterministic replay
                coa_version=reference_data.coa_version,
                dimension_schema_version=reference_data.dimension_schema_version,
                rounding_policy_version=reference_data.rounding_policy_version,
                currency_registry_version=reference_data.currency_registry_version,
            )
            return StrategyResult.success(entry)
        except ValueError as e:
            return StrategyResult.failure(
                ValidationError(code="INVALID_ENTRY", message=str(e))
            )

    def _validate_no_rounding_lines(
        self,
        lines: list[LineSpec],
    ) -> ValidationError | None:
        """
        R22: Validate strategy did not create rounding lines.

        Only the Bookkeeper may generate is_rounding=True JournalLines.
        Strategies are prohibited from targeting rounding accounts directly.
        This prevents strategies from injecting hidden amounts via fake rounding.

        Args:
            lines: The line specs computed by the strategy.

        Returns:
            ValidationError if violation detected, None otherwise.
        """
        for i, line in enumerate(lines):
            if line.is_rounding:
                return ValidationError(
                    code="STRATEGY_ROUNDING_VIOLATION",
                    message=(
                        f"Strategy attempted to create rounding line at index {i}. "
                        "Only the Bookkeeper may generate is_rounding=True lines (R22)."
                    ),
                    field=f"lines[{i}].is_rounding",
                    details={
                        "event_type": self.event_type,
                        "strategy_version": self.version,
                        "line_index": i,
                        "account_code": line.account_code,
                    },
                )
        return None

    def _validate_currencies(
        self,
        lines: list[LineSpec],
        reference_data: ReferenceData,
    ) -> list[ValidationError]:
        """Validate all currency codes are valid ISO 4217."""
        errors = []
        for i, line in enumerate(lines):
            if not CurrencyRegistry.is_valid(line.currency):
                errors.append(
                    ValidationError(
                        code="INVALID_CURRENCY",
                        message=f"Invalid ISO 4217 currency code: {line.currency}",
                        field=f"lines[{i}].currency",
                    )
                )
        return errors

    def _resolve_accounts(
        self,
        lines: list[LineSpec],
        reference_data: ReferenceData,
    ) -> tuple[list[ProposedLine], list[ValidationError]]:
        """Resolve account codes to IDs and validate accounts are active."""
        proposed_lines = []
        errors = []

        for i, spec in enumerate(lines):
            account_id = reference_data.get_account_id(spec.account_code)

            if account_id is None:
                errors.append(
                    ValidationError(
                        code="ACCOUNT_NOT_FOUND",
                        message=f"Account not found: {spec.account_code}",
                        field=f"lines[{i}].account_code",
                    )
                )
                continue

            if not reference_data.is_account_active(spec.account_code):
                errors.append(
                    ValidationError(
                        code="ACCOUNT_INACTIVE",
                        message=f"Account is inactive: {spec.account_code}",
                        field=f"lines[{i}].account_code",
                    )
                )
                continue

            proposed_lines.append(
                ProposedLine(
                    account_id=account_id,
                    account_code=spec.account_code,
                    side=spec.side,
                    money=spec.money,
                    dimensions=spec.dimensions,
                    memo=spec.memo,
                    is_rounding=spec.is_rounding,
                    line_seq=i,
                )
            )

        return proposed_lines, errors

    def _validate_dimensions(
        self,
        lines: list[ProposedLine],
        reference_data: ReferenceData,
    ) -> list[ValidationError]:
        """Validate required dimensions are present on all lines."""
        errors = []
        required = reference_data.required_dimensions

        if not required:
            return errors

        for i, line in enumerate(lines):
            dimensions = line.dimensions or {}
            for dim in required:
                if dim not in dimensions:
                    errors.append(
                        ValidationError(
                            code="MISSING_DIMENSION",
                            message=f"Missing required dimension: {dim}",
                            field=f"lines[{i}].dimensions.{dim}",
                        )
                    )

        return errors

    def _balance_and_round(
        self,
        lines: list[ProposedLine],
        reference_data: ReferenceData,
    ) -> tuple[list[ProposedLine], list[ValidationError]]:
        """
        Check balance per currency and apply rounding if needed.

        Returns the lines (possibly with rounding lines added) and any errors.
        """
        errors = []
        result_lines = list(lines)

        # Group by currency and check balance
        currencies = set(line.currency for line in lines)

        for currency in currencies:
            debits = sum(
                line.amount
                for line in lines
                if line.currency == currency and line.side == LineSide.DEBIT
            )
            credits = sum(
                line.amount
                for line in lines
                if line.currency == currency and line.side == LineSide.CREDIT
            )

            imbalance = debits - credits

            if imbalance == Decimal("0"):
                continue

            # Check if imbalance is within rounding tolerance
            tolerance = CurrencyRegistry.get_rounding_tolerance(currency)

            if abs(imbalance) > tolerance:
                errors.append(
                    ValidationError(
                        code="UNBALANCED_ENTRY",
                        message=(
                            f"Entry unbalanced in {currency}: "
                            f"debits={debits}, credits={credits}"
                        ),
                        details={
                            "currency": currency,
                            "debits": str(debits),
                            "credits": str(credits),
                            "imbalance": str(imbalance),
                        },
                    )
                )
                continue

            # Apply rounding
            rounding_account_id = reference_data.get_rounding_account_id(currency)
            if rounding_account_id is None:
                errors.append(
                    ValidationError(
                        code="NO_ROUNDING_ACCOUNT",
                        message=f"No rounding account for currency: {currency}",
                    )
                )
                continue

            # Add rounding line
            rounding_side = LineSide.CREDIT if imbalance > 0 else LineSide.DEBIT
            rounding_line = ProposedLine(
                account_id=rounding_account_id,
                account_code="ROUNDING",  # Placeholder code
                side=rounding_side,
                money=Money.of(abs(imbalance), currency),
                is_rounding=True,
                line_seq=len(result_lines),
            )
            result_lines.append(rounding_line)

        return result_lines, errors

    def _validate_rounding_invariants(
        self,
        lines: list[ProposedLine],
    ) -> list[ValidationError]:
        """
        Validate rounding invariants to prevent fraud.

        Enforces:
        1. At most ONE line can have is_rounding=True per entry
        2. Rounding amount must be < 0.01 per non-rounding line

        These invariants prevent:
        - Multiple hidden rounding lines (could inject extra amounts)
        - Large "rounding" amounts (could hide embezzlement)

        Args:
            lines: The proposed lines to validate.

        Returns:
            List of validation errors (empty if valid).
        """
        errors = []

        # Separate rounding and non-rounding lines
        rounding_lines = [line for line in lines if line.is_rounding]
        non_rounding_lines = [line for line in lines if not line.is_rounding]

        # Invariant 1: At most ONE rounding line per entry
        if len(rounding_lines) > 1:
            errors.append(
                ValidationError(
                    code="MULTIPLE_ROUNDING_LINES",
                    message=(
                        f"Entry has {len(rounding_lines)} rounding lines. "
                        f"At most ONE line can have is_rounding=True."
                    ),
                    details={
                        "rounding_count": len(rounding_lines),
                        "rounding_amounts": [str(line.amount) for line in rounding_lines],
                    },
                )
            )

        # Invariant 2: Rounding amount threshold
        # Max allowed: 0.01 per non-rounding line (minimum 0.01)
        if rounding_lines:
            max_allowed = max(
                Decimal("0.01"),
                Decimal("0.01") * len(non_rounding_lines),
            )

            for i, rounding_line in enumerate(rounding_lines):
                if rounding_line.amount > max_allowed:
                    errors.append(
                        ValidationError(
                            code="ROUNDING_AMOUNT_EXCEEDED",
                            message=(
                                f"Rounding amount {rounding_line.amount} {rounding_line.currency} "
                                f"exceeds maximum allowed {max_allowed}. "
                                f"Large 'rounding' is not rounding - it may indicate fraud."
                            ),
                            details={
                                "rounding_amount": str(rounding_line.amount),
                                "currency": rounding_line.currency,
                                "max_allowed": str(max_allowed),
                                "non_rounding_line_count": len(non_rounding_lines),
                            },
                        )
                    )

        return errors

    def _get_description(self, event: EventEnvelope) -> str | None:
        """Get description from event payload."""
        return event.payload.get("description")

    def _get_metadata(self, event: EventEnvelope) -> dict | None:
        """Get metadata from event payload."""
        return event.payload.get("metadata")
