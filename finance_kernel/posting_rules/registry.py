"""
Posting rule registry.

Manages registration and lookup of posting rules by event type.
"""

from typing import Dict, Type

from finance_kernel.models.event import Event
from finance_kernel.posting_rules.base import PostingRule
from finance_kernel.utils.rounding import LineSpec


class PostingRuleRegistry:
    """
    Registry for posting rules.

    Allows registration and lookup of rules by event type.
    Supports versioning for backward compatibility.
    """

    def __init__(self):
        """Initialize an empty registry."""
        # Map of event_type -> version -> rule
        self._rules: Dict[str, Dict[int, PostingRule]] = {}
        # Default version to use when not specified
        self._default_versions: Dict[str, int] = {}

    def register(
        self,
        rule: PostingRule,
        set_default: bool = True,
    ) -> None:
        """
        Register a posting rule.

        Args:
            rule: The posting rule to register.
            set_default: If True, set this as the default version.
        """
        event_type = rule.event_type
        version = rule.version

        if event_type not in self._rules:
            self._rules[event_type] = {}

        self._rules[event_type][version] = rule

        if set_default:
            self._default_versions[event_type] = version

    def get_rule(
        self,
        event_type: str,
        version: int | None = None,
    ) -> PostingRule | None:
        """
        Get a posting rule for an event type.

        Args:
            event_type: The event type to look up.
            version: Optional specific version. If None, uses default.

        Returns:
            PostingRule if found, None otherwise.
        """
        if event_type not in self._rules:
            return None

        if version is None:
            version = self._default_versions.get(event_type)
            if version is None:
                # Use highest version
                version = max(self._rules[event_type].keys())

        return self._rules[event_type].get(version)

    def compute_lines(
        self,
        event: Event,
        version: int | None = None,
    ) -> list[LineSpec]:
        """
        Compute journal lines for an event.

        Convenience method that looks up the rule and computes lines.

        Args:
            event: The event to process.
            version: Optional specific rule version.

        Returns:
            List of LineSpec.

        Raises:
            ValueError: If no rule found for event type.
        """
        rule = self.get_rule(event.event_type, version)

        if rule is None:
            raise ValueError(f"No posting rule found for event type: {event.event_type}")

        return rule.compute_lines(event)

    def list_event_types(self) -> list[str]:
        """
        List all registered event types.

        Returns:
            List of event type strings.
        """
        return list(self._rules.keys())

    def list_versions(self, event_type: str) -> list[int]:
        """
        List all versions for an event type.

        Args:
            event_type: The event type.

        Returns:
            List of version numbers.
        """
        if event_type not in self._rules:
            return []
        return sorted(self._rules[event_type].keys())


# Global default registry
_default_registry = PostingRuleRegistry()


def get_default_registry() -> PostingRuleRegistry:
    """Get the default posting rule registry."""
    return _default_registry


def register_rule(rule: PostingRule, set_default: bool = True) -> None:
    """
    Register a rule in the default registry.

    Args:
        rule: The posting rule to register.
        set_default: If True, set this as the default version.
    """
    _default_registry.register(rule, set_default)
