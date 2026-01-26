"""
Bookkeeper - Pure functional core for posting.

The Bookkeeper is responsible for transforming events into proposed
journal entries using posting strategies. It has NO side effects
and NO access to:
- Database
- Clock/time (time is part of the event)
- I/O
- External services

All dependencies are passed in as arguments.
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

    Either contains a proposed entry OR an error, never both.
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
        """Create a successful result."""
        return cls(
            proposed_entry=entry,
            validation=ValidationResult.success(),
            strategy_version=strategy_version,
        )

    @classmethod
    def failure(cls, *errors: ValidationError) -> "BookkeeperResult":
        """Create a failed result."""
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

    The Bookkeeper:
    1. Looks up the appropriate strategy for the event type
    2. Invokes the strategy to transform the event into a proposed entry
    3. Returns the proposed entry or validation errors

    The Bookkeeper does NOT:
    - Access the database
    - Create any records
    - Have any side effects
    - Use system time

    It is fully deterministic: same inputs always produce same outputs.
    """

    def __init__(self, registry: StrategyRegistry | None = None):
        """
        Initialize the Bookkeeper.

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

        Args:
            event: The event to transform.
            reference_data: Reference data for account/currency lookups.
            strategy_version: Optional specific strategy version (for replay).
                            If None, uses the latest version.

        Returns:
            BookkeeperResult with either a proposed entry or errors.
        """
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

        Args:
            event_type: The event type to check.

        Returns:
            True if a strategy exists for this event type.
        """
        return self._registry.has_strategy(event_type)

    def get_strategy_version(self, event_type: str) -> int | None:
        """
        Get the latest strategy version for an event type.

        Args:
            event_type: The event type.

        Returns:
            The latest version number, or None if no strategy exists.
        """
        try:
            return self._registry.get_latest_version(event_type)
        except StrategyNotFoundError:
            return None
