"""
Comprehensive event catalog for full-architecture testing.

Generates one test event per policy in the CompiledPolicyPack by:
  1. Introspecting each policy to derive its EventShape
  2. Generating a balanced, guard-satisfying payload from the shape
  3. Merging engine-specific payload fragments when engines are required

No hand-coded payloads — everything is derived from policy metadata.
When a new policy is added to the YAML configuration, this catalog
automatically produces a valid test event for it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from finance_config.compiler import CompiledPolicyPack

from tests.benchmarks.event_shape import introspect_policy, EventShape
from tests.benchmarks.event_generator import generate_event, TestEvent  # noqa: F401 re-exported


def build_event_catalog(
    pack: CompiledPolicyPack,
    *,
    amount: Decimal = Decimal("5000.00"),
    currency: str = "USD",
) -> list[TestEvent]:
    """Build one test event per policy from the compiled pack.

    Fully automatic: introspects each policy to derive its EventShape,
    then generates a valid, balanced test event from the shape.

    Args:
        pack: The compiled policy pack (possibly filtered to a tier).
        amount: Default primary posting amount.
        currency: ISO 4217 currency code.

    Returns:
        One TestEvent per policy in the pack.
    """
    events: list[TestEvent] = []

    for policy in pack.policies:
        shape = introspect_policy(policy)
        event = generate_event(shape, amount=amount, currency=currency)
        events.append(event)

    return events


def get_events_for_tier(pack: CompiledPolicyPack) -> list[TestEvent]:
    """Get test events for a tier's filtered pack.

    Returns one event per policy, suitable for full-architecture testing.
    """
    return build_event_catalog(pack)
