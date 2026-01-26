"""
Base posting rule protocol.

Posting rules transform events into journal lines deterministically.
"""

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from finance_kernel.models.event import Event
from finance_kernel.utils.rounding import LineSpec


@runtime_checkable
class PostingRule(Protocol):
    """
    Protocol for posting rules.

    Posting rules define how events are transformed into journal lines.
    Each rule is:
    - Deterministic: Same event always produces same lines
    - Versioned: Rule version is recorded on JournalEntry
    - Stateless: No side effects during computation
    """

    @property
    def event_type(self) -> str:
        """Event type this rule handles."""
        ...

    @property
    def version(self) -> int:
        """Version of this rule."""
        ...

    def compute_lines(self, event: Event) -> list[LineSpec]:
        """
        Compute journal lines from an event.

        Args:
            event: The event to transform.

        Returns:
            List of LineSpec describing the journal lines.
        """
        ...


class BasePostingRule(ABC):
    """
    Abstract base class for posting rules.

    Provides common functionality for posting rules.
    """

    @property
    @abstractmethod
    def event_type(self) -> str:
        """Event type this rule handles."""
        pass

    @property
    @abstractmethod
    def version(self) -> int:
        """Version of this rule."""
        pass

    @abstractmethod
    def compute_lines(self, event: Event) -> list[LineSpec]:
        """
        Compute journal lines from an event.

        Args:
            event: The event to transform.

        Returns:
            List of LineSpec describing the journal lines.
        """
        pass

    def validate_event(self, event: Event) -> None:
        """
        Validate that the event is suitable for this rule.

        Override in subclasses to add validation.

        Args:
            event: The event to validate.

        Raises:
            ValueError: If event is invalid.
        """
        if event.event_type != self.event_type:
            raise ValueError(
                f"Event type mismatch: expected {self.event_type}, "
                f"got {event.event_type}"
            )
