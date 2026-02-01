"""
Bookkeeper -- Pure functional core for posting.

Responsibility:
    Transforms EventEnvelope + ReferenceData into ProposedJournalEntry by
    dispatching to the registered PostingStrategy for the event type. This is
    the single entry point for all posting computations in the kernel.

Architecture position:
    Kernel > Domain -- pure functional core, zero I/O.
    No database access, no clock/time, no external services.
    All dependencies are passed in as arguments.

Invariants enforced:
    R4  -- Balance per currency (delegated to strategy pipeline)
    R5  -- Rounding line uniqueness (delegated to strategy pipeline)
    R14 -- No central dispatch; strategy registry lookup, never if/switch
    R15 -- Open/closed compliance; new event type = new strategy only
    R22 -- Only the Bookkeeper may originate is_rounding=True lines
    R23 -- Strategy lifecycle governance (version + replay policy)

Failure modes:
    - StrategyNotFoundError  when no strategy is registered for an event type
    - StrategyVersionNotFoundError  when a specific replay version is missing
    - ValidationError (via BookkeeperResult.failure) for strategy execution errors

Audit relevance:
    The Bookkeeper is the ONLY component that may produce rounding lines (R22).
    Auditors verify that every ProposedJournalEntry returned by propose() is
    balanced per currency, carries the correct strategy_version, and that no
    rounding lines were injected by strategies.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from finance_kernel.domain.dtos import (
    EventEnvelope,
    ProposedJournalEntry,
    ReferenceData,
    ValidationError,
    ValidationResult,
)
from finance_kernel.domain.strategy import StrategyResult
from finance_kernel.domain.strategy_registry import (
    StrategyNotFoundError,
    StrategyRegistry,
    StrategyVersionNotFoundError,
)

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class BookkeeperResult:
    """
    Result of the Bookkeeper.propose() operation.

    Contract:
        Either contains a proposed entry OR validation errors, never both.
        When is_valid is True, proposed_entry is guaranteed non-None and
        strategy_version is populated.

    Guarantees:
        - Immutable (frozen dataclass)
        - Success result always carries strategy_version for replay (R23)
        - Failure result always carries at least one ValidationError

    Non-goals:
        - Does NOT persist anything -- purely a return value
        - Does NOT validate balance -- that is the strategy's job
    """

    proposed_entry: ProposedJournalEntry | None
    validation: ValidationResult
    strategy_version: int | None = None

    @classmethod
    def success(
        cls,
        entry: ProposedJournalEntry,
        strategy_version: int,
    ) -> "BookkeeperResult":
        """
        Create a successful result.

        Preconditions:
            - entry is a valid ProposedJournalEntry (non-None, has lines)
            - strategy_version >= 1

        Postconditions:
            - Returned result has is_valid == True
            - proposed_entry is the provided entry
            - strategy_version is recorded for replay (R23)

        Raises:
            No exceptions -- constructor validates via frozen dataclass.
        """
        # INVARIANT: R23 -- strategy version must be recorded for replay
        assert strategy_version >= 1, f"R23 violation: strategy_version must be >= 1, got {strategy_version}"
        return cls(
            proposed_entry=entry,
            validation=ValidationResult.success(),
            strategy_version=strategy_version,
        )

    @classmethod
    def failure(cls, *errors: ValidationError) -> "BookkeeperResult":
        """
        Create a failed result.

        Preconditions:
            - At least one ValidationError is provided.

        Postconditions:
            - Returned result has is_valid == False
            - proposed_entry is None
            - strategy_version is None

        Raises:
            No exceptions.
        """
        return cls(
            proposed_entry=None,
            validation=ValidationResult.failure(*errors),
            strategy_version=None,
        )

    @property
    def is_valid(self) -> bool:
        return self.validation.is_valid and self.proposed_entry is not None


class Bookkeeper:
    """
    Pure functional core for posting.

    Contract:
        Given an EventEnvelope and ReferenceData, the Bookkeeper returns a
        BookkeeperResult containing either a balanced ProposedJournalEntry
        or a list of validation errors. The transformation is fully
        deterministic: same inputs always produce same outputs.

    Guarantees:
        - Strategy lookup via StrategyRegistry (R14 -- no if/switch)
        - Strategy version is captured for replay (R23)
        - Rounding lines originate exclusively here (R22)
        - No side effects, no database, no clock, no I/O

    Non-goals:
        - Does NOT persist entries
        - Does NOT allocate sequence numbers
        - Does NOT enforce period locks (that is a service-layer concern)
    """

    def __init__(self, registry: StrategyRegistry | None = None):
        """
        Initialize the Bookkeeper.

        Preconditions:
            - registry, if provided, must be a StrategyRegistry instance or class.

        Postconditions:
            - self._registry is set to the provided registry or the global class.

        Args:
            registry: Optional strategy registry. If None, uses global registry.
        """
        self._registry = registry or StrategyRegistry

    def propose(
        self,
        event: EventEnvelope,
        reference_data: ReferenceData,
        strategy_version: int | None = None,
    ) -> BookkeeperResult:
        """
        Transform an event into a proposed journal entry.

        This is the main entry point. It:
        1. Looks up the strategy for the event type
        2. Invokes the strategy to compute lines
        3. Returns the proposed entry or validation errors

        Preconditions:
            - event is a valid EventEnvelope with non-empty event_type
            - reference_data contains account/currency data required by the strategy
            - strategy_version, if provided, must be >= 1 (R23)

        Postconditions:
            - On success: result.is_valid is True, result.proposed_entry is non-None,
              result.strategy_version records the version used
            - On failure: result.is_valid is False, result.validation.errors is non-empty

        Raises:
            No exceptions -- all errors are captured in BookkeeperResult.

        Args:
            event: The event to transform.
            reference_data: Reference data for account/currency lookups.
            strategy_version: Optional specific strategy version (for replay).
                            If None, uses the latest version.

        Returns:
            BookkeeperResult with either a proposed entry or errors.
        """
        # INVARIANT: R14 -- Strategy lookup via registry, no if/switch on event_type
        # 1. Look up strategy
        try:
            strategy = self._registry.get(event.event_type, strategy_version)
        except StrategyNotFoundError as e:
            return BookkeeperResult.failure(
                ValidationError(
                    code="STRATEGY_NOT_FOUND",
                    message=str(e),
                    details={"event_type": event.event_type},
                )
            )
        except StrategyVersionNotFoundError as e:
            return BookkeeperResult.failure(
                ValidationError(
                    code="STRATEGY_VERSION_NOT_FOUND",
                    message=str(e),
                    details={
                        "event_type": event.event_type,
                        "version": e.version,
                        "available_versions": e.available_versions,
                    },
                )
            )

        # 2. Invoke strategy
        try:
            result: StrategyResult = strategy.propose(event, reference_data)
        except Exception as e:
            return BookkeeperResult.failure(
                ValidationError(
                    code="STRATEGY_ERROR",
                    message=f"Strategy execution failed: {e}",
                    details={"event_type": event.event_type},
                )
            )

        # 3. Return result
        if result.is_valid and result.proposed_entry is not None:
            return BookkeeperResult.success(
                result.proposed_entry,
                strategy.version,
            )
        else:
            return BookkeeperResult.failure(*result.validation.errors)

    def validate_event(
        self,
        event: EventEnvelope,
        reference_data: ReferenceData,
    ) -> ValidationResult:
        """
        Validate an event without creating a proposed entry.

        Useful for pre-validation before committing to a posting.

        Preconditions:
            - event and reference_data are valid domain objects.

        Postconditions:
            - Returns ValidationResult; no side effects.

        Raises:
            No exceptions -- all errors captured in ValidationResult.

        Args:
            event: The event to validate.
            reference_data: Reference data for lookups.

        Returns:
            ValidationResult indicating success or failure.
        """
        result = self.propose(event, reference_data)
        return result.validation

    def can_handle(self, event_type: str) -> bool:
        """
        Check if the Bookkeeper can handle an event type.

        Preconditions:
            - event_type is a non-empty string.

        Postconditions:
            - Returns True if a strategy is registered for this event type (R14).

        Raises:
            No exceptions.

        Args:
            event_type: The event type to check.

        Returns:
            True if a strategy exists for this event type.
        """
        return self._registry.has_strategy(event_type)

    def get_strategy_version(self, event_type: str) -> int | None:
        """
        Get the latest strategy version for an event type.

        Preconditions:
            - event_type is a non-empty string.

        Postconditions:
            - Returns the latest version number (>= 1), or None if no strategy exists.

        Raises:
            No exceptions -- StrategyNotFoundError is caught internally.

        Args:
            event_type: The event type.

        Returns:
            The latest version number, or None if no strategy exists.
        """
        try:
            return self._registry.get_latest_version(event_type)
        except StrategyNotFoundError:
            return None
