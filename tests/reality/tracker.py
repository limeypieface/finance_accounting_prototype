"""
Tracks which boundaries were crossed during a test.

Used by the dependency reality detector: tests marked @pytest.mark.system
must touch real DB connection, at least one config load, and at least one
persistence write.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class BoundaryTracker:
    """Per-test record of which boundaries were crossed."""

    db_connection: bool = False
    config_load: bool = False
    sequence_allocator: bool = False
    persistence_write: bool = False

    def touch_db_connection(self) -> None:
        self.db_connection = True

    def touch_config_load(self) -> None:
        self.config_load = True

    def touch_sequence_allocator(self) -> None:
        self.sequence_allocator = True

    def touch_persistence_write(self) -> None:
        self.persistence_write = True

    def satisfies_reality_check(self) -> tuple[bool, list[str]]:
        """Required for system: real DB, config load, persistence write."""
        missing = []
        if not self.db_connection:
            missing.append("real DB connection")
        if not self.config_load:
            missing.append("at least one config load")
        if not self.persistence_write:
            missing.append("at least one persistence write")
        return (len(missing) == 0, missing)


# Thread-local so parallel tests don't mix (pytest usually runs one test at a time per worker).
_local = threading.local()


def get_tracker() -> BoundaryTracker | None:
    """Return the current test's tracker, or None if not in a reality-checked test."""
    return getattr(_local, "tracker", None)


def boundary_tracker() -> BoundaryTracker:
    """Get or create the current test's tracker."""
    if not hasattr(_local, "tracker"):
        _local.tracker = BoundaryTracker()
    return _local.tracker


def reset_tracker() -> None:
    """Clear the current tracker (call at test start)."""
    if hasattr(_local, "tracker"):
        del _local.tracker
