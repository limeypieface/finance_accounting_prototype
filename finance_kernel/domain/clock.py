"""
Deterministic clock abstraction.

The domain layer never calls datetime.now() directly.
All time is injected via a Clock interface.

This enables:
- Deterministic testing
- Replay of historical events
- Time-travel debugging
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Iterator


class Clock(ABC):
    """
    Abstract clock interface.

    All services that need current time must receive a Clock instance.
    """

    @abstractmethod
    def now(self) -> datetime:
        """Get the current time."""
        ...

    @abstractmethod
    def now_utc(self) -> datetime:
        """Get the current UTC time."""
        ...


class SystemClock(Clock):
    """
    Production clock that returns actual system time.

    Use this in production code.
    """

    def now(self) -> datetime:
        """Get current system time with timezone."""
        return datetime.now(timezone.utc)

    def now_utc(self) -> datetime:
        """Get current UTC time."""
        return datetime.now(timezone.utc)


class DeterministicClock(Clock):
    """
    Test clock with controlled time.

    Use this in tests for deterministic behavior.
    """

    def __init__(self, fixed_time: datetime | None = None):
        """
        Initialize with optional fixed time.

        Args:
            fixed_time: If provided, clock always returns this time.
                       If None, uses a default epoch time.
        """
        self._fixed_time = fixed_time or datetime(
            2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc
        )
        self._advance_seconds = 0

    def now(self) -> datetime:
        """Get the fixed/controlled time."""
        from datetime import timedelta

        return self._fixed_time + timedelta(seconds=self._advance_seconds)

    def now_utc(self) -> datetime:
        """Get the fixed/controlled UTC time."""
        return self.now().astimezone(timezone.utc)

    def set_time(self, time: datetime) -> None:
        """Set the clock to a specific time."""
        self._fixed_time = time
        self._advance_seconds = 0

    def advance(self, seconds: int = 1) -> None:
        """Advance the clock by the specified seconds."""
        self._advance_seconds += seconds

    def tick(self) -> datetime:
        """Advance by 1 second and return new time."""
        self.advance(1)
        return self.now()


class SequentialClock(Clock):
    """
    Clock that returns sequential times from a predefined list.

    Useful for testing specific time sequences.
    """

    def __init__(self, times: list[datetime]):
        """
        Initialize with a list of times.

        Args:
            times: List of times to return in sequence.
        """
        if not times:
            raise ValueError("SequentialClock requires at least one time")
        self._times: Iterator[datetime] = iter(times)
        self._last_time: datetime | None = None
        self._exhausted = False

    def now(self) -> datetime:
        """Get the next time in sequence."""
        if self._exhausted:
            if self._last_time is None:
                raise RuntimeError("SequentialClock has no times")
            return self._last_time

        try:
            self._last_time = next(self._times)
            return self._last_time
        except StopIteration:
            self._exhausted = True
            if self._last_time is None:
                raise RuntimeError("SequentialClock exhausted with no times")
            return self._last_time

    def now_utc(self) -> datetime:
        """Get the next time in sequence as UTC."""
        return self.now().astimezone(timezone.utc)
