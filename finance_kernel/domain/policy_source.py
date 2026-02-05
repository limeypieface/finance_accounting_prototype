"""PolicySource -- Abstract source of AccountingPolicy for the posting pipeline.

When a CompiledPolicyPack is present, the pack (via PackPolicySource in
finance_services) is the source of truth so that guards, trigger, and
meaning come from config. When no pack is present, SelectorPolicySource
delegates to PolicySelector (Python-registered profiles).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol, runtime_checkable

from finance_kernel.domain.accounting_policy import AccountingPolicy
from finance_kernel.domain.policy_selector import PolicyNotFoundError, PolicySelector
from finance_kernel.logging_config import get_logger

logger = get_logger("domain.policy_source")


@runtime_checkable
class PolicySource(Protocol):
    """Protocol for resolving the AccountingPolicy for an event.

    Implementations: SelectorPolicySource (PolicySelector), PackPolicySource (pack).
    """

    def get_profile(
        self,
        event_type: str,
        effective_date: date,
        payload: dict[str, Any] | None = None,
        scope_value: str = "*",
    ) -> AccountingPolicy:
        """Return the matching AccountingPolicy for the event.

        Raises:
            PolicyNotFoundError: When no profile matches.
        """
        ...


class SelectorPolicySource:
    """PolicySource that delegates to PolicySelector (Python-registered profiles)."""

    def get_profile(
        self,
        event_type: str,
        effective_date: date,
        payload: dict[str, Any] | None = None,
        scope_value: str = "*",
    ) -> AccountingPolicy:
        """Return the profile from PolicySelector.find_for_event."""
        return PolicySelector.find_for_event(
            event_type=event_type,
            effective_date=effective_date,
            scope_value=scope_value,
            payload=payload,
        )
