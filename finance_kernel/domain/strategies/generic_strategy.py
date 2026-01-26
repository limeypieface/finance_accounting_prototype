"""
Generic posting strategy for simple events.

This strategy handles events that have their line specifications
directly in the payload. It's useful for:
- Testing
- Simple events without complex transformation logic
- Events pre-computed by external systems

Payload format:
{
    "lines": [
        {
            "account_code": "1000",
            "side": "debit",
            "amount": "100.00",
            "currency": "USD",
            "memo": "optional",
            "dimensions": {"project": "P001"}  # optional
        },
        ...
    ],
    "description": "optional description",
    "metadata": {}  # optional
}
"""

from decimal import Decimal

from finance_kernel.domain.dtos import (
    EventEnvelope,
    LineSide,
    LineSpec,
    ReferenceData,
)
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry


class GenericPostingStrategy(BasePostingStrategy):
    """
    Generic strategy that reads line specs from the event payload.

    This is a flexible strategy that can handle any event type
    whose payload contains pre-computed line specifications.
    """

    def __init__(self, event_type: str = "generic.posting", version: int = 1):
        """
        Initialize the generic strategy.

        Args:
            event_type: The event type this strategy handles.
            version: Version of this strategy.
        """
        self._event_type = event_type
        self._version = version

    @property
    def event_type(self) -> str:
        return self._event_type

    @property
    def version(self) -> int:
        return self._version

    def _compute_line_specs(
        self,
        event: EventEnvelope,
        reference_data: ReferenceData,
    ) -> list[LineSpec]:
        """Extract line specs from the event payload."""
        payload = event.payload
        lines_data = payload.get("lines", [])

        if not lines_data:
            raise ValueError("Payload must contain 'lines' array")

        line_specs = []
        for line_data in lines_data:
            # Parse side
            side_str = line_data.get("side", "").lower()
            if side_str == "debit":
                side = LineSide.DEBIT
            elif side_str == "credit":
                side = LineSide.CREDIT
            else:
                raise ValueError(f"Invalid side: {side_str}")

            # Parse amount
            amount_str = line_data.get("amount")
            if amount_str is None:
                raise ValueError("Line must have 'amount'")
            amount = Decimal(str(amount_str))

            # Get account code
            account_code = line_data.get("account_code")
            if not account_code:
                raise ValueError("Line must have 'account_code'")

            # Get currency
            currency = line_data.get("currency")
            if not currency:
                raise ValueError("Line must have 'currency'")

            # Optional fields
            memo = line_data.get("memo")
            dimensions = line_data.get("dimensions")
            is_rounding = line_data.get("is_rounding", False)

            line_specs.append(
                LineSpec.create(
                    account_code=account_code,
                    side=side,
                    amount=amount,
                    currency=currency,
                    memo=memo,
                    dimensions=dimensions,
                    is_rounding=is_rounding,
                )
            )

        return line_specs

    def _get_description(self, event: EventEnvelope) -> str | None:
        """Get description from payload."""
        return event.payload.get("description")

    def _get_metadata(self, event: EventEnvelope) -> dict | None:
        """Get metadata from payload."""
        return event.payload.get("metadata")


# Register the generic strategy
_generic_strategy = GenericPostingStrategy()
StrategyRegistry.register(_generic_strategy)


def create_strategy_for_event_type(
    event_type: str,
    version: int = 1,
) -> GenericPostingStrategy:
    """
    Factory to create and register a generic strategy for an event type.

    Useful for dynamically supporting new event types.

    Args:
        event_type: The event type to handle.
        version: Strategy version.

    Returns:
        The registered strategy.
    """
    strategy = GenericPostingStrategy(event_type=event_type, version=version)
    StrategyRegistry.register(strategy)
    return strategy
