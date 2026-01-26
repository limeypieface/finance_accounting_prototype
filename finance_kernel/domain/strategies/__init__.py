"""
Posting strategies for different event types.

Each module in this package defines strategies for specific event types.
Strategies are automatically registered when imported.
"""

from finance_kernel.domain.strategies.generic_strategy import GenericPostingStrategy

__all__ = [
    "GenericPostingStrategy",
]
