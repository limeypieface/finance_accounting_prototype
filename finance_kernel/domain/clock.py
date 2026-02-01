"""
Clock -- Deterministic time abstraction.

Responsibility:
    Provides an injectable clock interface so that domain, engine, and service
    code never call ``datetime.now()`` or ``date.today()`` directly.

Architecture position:
    Kernel > Domain -- pure functional core, zero I/O (except SystemClock,
    which is the one sanctioned I/O boundary for time).

Invariants enforced:
    (none directly -- enables deterministic replay for L4 and R21)

Failure modes:
    - SequentialClock raises RuntimeError if exhausted and no fallback time.

Audit relevance:
    Deterministic clocks are critical for replay verification (L4).  Every
    timestamp recorded in journal entries and audit events is traceable to an
    injected Clock instance.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Iterator


class Clock(ABC):
    """
    Abstract clock interface.

    Contract:
        All services that need current time must receive a Clock instance
        via constructor injection.  Domain and engine code must NEVER import
        ``datetime.now()`` or ``date.today()`` directly.

    Guarantees:
        - ``now()`` returns a timezone-aware ``datetime``.
        - ``now_utc()`` returns a UTC-normalized ``datetime``.
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

    Contract:
        The sole sanctioned I/O boundary for time in the kernel.

    Guarantees:
        Returns timezone-aware UTC ``datetime`` instances.

    Non-goals:
        Not suitable for deterministic replay or testing.
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

    Contract:
        Used in tests and replay scenarios for deterministic behavior (L4).

    Guarantees:
        - ``now()`` returns the same value on repeated calls until ``advance()``
          or ``set_time()`` is called.
        - ``tick()`` advances by exactly 1 second and returns the new time.
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

    Contract:
        Initialized with a non-empty list of ``datetime`` values.  After
        exhaustion, repeats the last value.

    Guarantees:
        Returns times in the exact order supplied.

    Raises:
        ValueError: If initialized with an empty list.
        RuntimeError: If exhausted with no recorded last time.
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
