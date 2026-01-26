"""
Strategy registry for event type to posting strategy mapping.

The registry provides:
- Registration of strategies by event type
- Version tracking for replay
- Strategy lookup by event type and optional version

R18 Compliance: All exceptions have machine-readable error codes.
"""

from typing import ClassVar

from finance_kernel.domain.strategy import PostingStrategy
from finance_kernel.exceptions import FinanceKernelError


class StrategyNotFoundError(FinanceKernelError):
    """
    Raised when no strategy is found for an event type.

    R18 Compliance: Has machine-readable code attribute.
    """

    code: str = "STRATEGY_NOT_FOUND"

    def __init__(self, event_type: str, version: int | None = None):
        self.event_type = event_type
        self.version = version
        msg = f"No strategy found for event type: {event_type}"
        if version is not None:
            msg += f" (version {version})"
        super().__init__(msg)


class StrategyVersionNotFoundError(FinanceKernelError):
    """
    Raised when a specific strategy version is not found.

    R18 Compliance: Has machine-readable code attribute.
    """

    code: str = "STRATEGY_VERSION_NOT_FOUND"

    def __init__(self, event_type: str, version: int, available_versions: list[int]):
        self.event_type = event_type
        self.version = version
        self.available_versions = available_versions
        super().__init__(
            f"Strategy version {version} not found for {event_type}. "
            f"Available versions: {available_versions}"
        )


class StrategyRegistry:
    """
    Registry for posting strategies.

    Strategies are registered by event type and version.
    Multiple versions can exist for replay purposes.
    """

    # Class-level registry for all strategies
    _strategies: ClassVar[dict[str, dict[int, PostingStrategy]]] = {}

    @classmethod
    def register(cls, strategy: PostingStrategy) -> None:
        """
        Register a posting strategy.

        Args:
            strategy: The strategy to register.

        Raises:
            ValueError: If a strategy with the same event_type and version
                       is already registered.
        """
        event_type = strategy.event_type
        version = strategy.version

        if event_type not in cls._strategies:
            cls._strategies[event_type] = {}

        if version in cls._strategies[event_type]:
            existing = cls._strategies[event_type][version]
            raise ValueError(
                f"Strategy already registered for {event_type} v{version}: "
                f"{existing.__class__.__name__}"
            )

        cls._strategies[event_type][version] = strategy

    @classmethod
    def get(
        cls,
        event_type: str,
        version: int | None = None,
    ) -> PostingStrategy:
        """
        Get a strategy for an event type.

        Args:
            event_type: The event type to look up.
            version: Optional specific version. If None, returns latest.

        Returns:
            The posting strategy.

        Raises:
            StrategyNotFoundError: If no strategy exists for the event type.
            StrategyVersionNotFoundError: If the specific version doesn't exist.
        """
        if event_type not in cls._strategies:
            raise StrategyNotFoundError(event_type)

        versions = cls._strategies[event_type]

        if not versions:
            raise StrategyNotFoundError(event_type)

        if version is None:
            # Return latest version
            latest_version = max(versions.keys())
            return versions[latest_version]

        if version not in versions:
            raise StrategyVersionNotFoundError(
                event_type, version, sorted(versions.keys())
            )

        return versions[version]

    @classmethod
    def get_latest_version(cls, event_type: str) -> int:
        """
        Get the latest version number for an event type.

        Args:
            event_type: The event type.

        Returns:
            The latest version number.

        Raises:
            StrategyNotFoundError: If no strategy exists for the event type.
        """
        if event_type not in cls._strategies:
            raise StrategyNotFoundError(event_type)

        versions = cls._strategies[event_type]
        if not versions:
            raise StrategyNotFoundError(event_type)

        return max(versions.keys())

    @classmethod
    def get_all_versions(cls, event_type: str) -> list[int]:
        """
        Get all registered versions for an event type.

        Args:
            event_type: The event type.

        Returns:
            List of version numbers (sorted ascending).

        Raises:
            StrategyNotFoundError: If no strategy exists for the event type.
        """
        if event_type not in cls._strategies:
            raise StrategyNotFoundError(event_type)

        return sorted(cls._strategies[event_type].keys())

    @classmethod
    def list_event_types(cls) -> list[str]:
        """
        List all registered event types.

        Returns:
            List of event type strings.
        """
        return sorted(cls._strategies.keys())

    @classmethod
    def has_strategy(cls, event_type: str) -> bool:
        """
        Check if a strategy exists for an event type.

        Args:
            event_type: The event type.

        Returns:
            True if a strategy exists, False otherwise.
        """
        return event_type in cls._strategies and bool(cls._strategies[event_type])

    @classmethod
    def clear(cls) -> None:
        """
        Clear all registered strategies.

        Use this in tests to reset the registry.
        """
        cls._strategies.clear()

    @classmethod
    def unregister(cls, event_type: str, version: int | None = None) -> None:
        """
        Unregister a strategy.

        Args:
            event_type: The event type.
            version: Specific version to unregister, or None for all versions.
        """
        if event_type not in cls._strategies:
            return

        if version is None:
            del cls._strategies[event_type]
        elif version in cls._strategies[event_type]:
            del cls._strategies[event_type][version]
            if not cls._strategies[event_type]:
                del cls._strategies[event_type]


def register_strategy(strategy: PostingStrategy) -> PostingStrategy:
    """
    Decorator to register a strategy class.

    Usage:
        @register_strategy
        class MyStrategy(BasePostingStrategy):
            ...
    """
    StrategyRegistry.register(strategy)
    return strategy


def strategy_for(event_type: str, version: int = 1):
    """
    Decorator factory to create and register a strategy.

    Usage:
        @strategy_for("inventory.receipt", version=1)
        class InventoryReceiptStrategy(BasePostingStrategy):
            ...
    """

    def decorator(cls):
        # The class should define _compute_line_specs
        # We instantiate it and register
        instance = cls()
        # Override event_type and version
        instance._event_type = event_type
        instance._version = version
        StrategyRegistry.register(instance)
        return cls

    return decorator
