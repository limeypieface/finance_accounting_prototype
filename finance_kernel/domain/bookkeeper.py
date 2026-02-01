"""Bookkeeper -- Pure functional core for posting."""

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
    """Result of the Bookkeeper.propose() operation."""

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
        # INVARIANT: R23 -- strategy version must be recorded for replay
        assert strategy_version >= 1, f"R23 violation: strategy_version must be >= 1, got {strategy_version}"
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
    """Pure functional core for posting (R14, R22, R23)."""

    def __init__(self, registry: StrategyRegistry | None = None):
        self._registry = registry or StrategyRegistry

    def propose(
        self,
        event: EventEnvelope,
        reference_data: ReferenceData,
        strategy_version: int | None = None,
    ) -> BookkeeperResult:
        """Transform an event into a proposed journal entry."""
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
        """Validate an event without creating a proposed entry."""
        result = self.propose(event, reference_data)
        return result.validation

    def can_handle(self, event_type: str) -> bool:
        """Check if a strategy is registered for this event type."""
        return self._registry.has_strategy(event_type)

    def get_strategy_version(self, event_type: str) -> int | None:
        """Get the latest strategy version for an event type."""
        try:
            return self._registry.get_latest_version(event_type)
        except StrategyNotFoundError:
            return None
