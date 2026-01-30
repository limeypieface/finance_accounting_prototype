"""
Configuration lifecycle status.

Configuration sets are append-only. Each version declares its predecessor.
Only PUBLISHED configs can be used for posting. Superseded configs remain
for replay/audit.
"""

from enum import Enum, unique


@unique
class ConfigStatus(str, Enum):
    """Lifecycle status for a configuration set."""

    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


# Allowed status transitions (from → set of valid next states)
ALLOWED_TRANSITIONS: dict[ConfigStatus, frozenset[ConfigStatus]] = {
    ConfigStatus.DRAFT: frozenset({ConfigStatus.REVIEWED}),
    ConfigStatus.REVIEWED: frozenset({ConfigStatus.APPROVED, ConfigStatus.DRAFT}),
    ConfigStatus.APPROVED: frozenset({ConfigStatus.PUBLISHED, ConfigStatus.DRAFT}),
    ConfigStatus.PUBLISHED: frozenset({ConfigStatus.SUPERSEDED}),
    ConfigStatus.SUPERSEDED: frozenset(),  # Terminal
}


def validate_transition(current: ConfigStatus, target: ConfigStatus) -> bool:
    """Check if a status transition is valid."""
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())
