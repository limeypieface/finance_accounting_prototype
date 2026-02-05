"""Dependency reality detector: ensure tests marked system touch DB, config, persistence."""

from tests.reality.tracker import (
    boundary_tracker,
    get_tracker,
    reset_tracker,
)

__all__ = [
    "boundary_tracker",
    "get_tracker",
    "reset_tracker",
]
