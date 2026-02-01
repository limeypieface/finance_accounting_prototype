"""StrategyRegistry -- Event-type to PostingStrategy dispatch registry."""

from typing import ClassVar

from finance_kernel.domain.strategy import PostingStrategy, ReplayPolicy
from finance_kernel.exceptions import FinanceKernelError


class StrategyNotFoundError(FinanceKernelError):
    """Raised when no strategy is found for an event type (R18)."""

    code: str = "STRATEGY_NOT_FOUND"

    def __init__(self, event_type: str, version: int | None = None):
        self.event_type = event_type
        self.version = version
        msg = f"No strategy found for event type: {event_type}"
        if version is not None:
            msg += f" (version {version})"
        super().__init__(msg)


class StrategyVersionNotFoundError(FinanceKernelError):
    """Raised when a specific strategy version is not found (R18)."""

    code: str = "STRATEGY_VERSION_NOT_FOUND"

    def __init__(self, event_type: str, version: int, available_versions: list[int]):
        self.event_type = event_type
        self.version = version
        self.available_versions = available_versions
        super().__init__(
            f"Strategy version {version} not found for {event_type}. "
            f"Available versions: {available_versions}"
        )


class StrategyLifecycleError(FinanceKernelError):
    """Raised when strategy lifecycle validation fails (R23, R18)."""

    code: str = "STRATEGY_LIFECYCLE_ERROR"

    def __init__(self, event_type: str, version: int, reason: str):
        self.event_type = event_type
        self.version = version
        self.reason = reason
        super().__init__(
            f"Strategy lifecycle error for {event_type} v{version}: {reason}"
        )


class StrategyIncompatibleError(FinanceKernelError):
    """Raised when strategy is incompatible with current system version (R23, R18)."""

    code: str = "STRATEGY_INCOMPATIBLE"

    def __init__(
        self,
        event_type: str,
        strategy_version: int,
        system_version: int,
        supported_from: int,
        supported_to: int | None,
    ):
        self.event_type = event_type
        self.strategy_version = strategy_version
        self.system_version = system_version
        self.supported_from = supported_from
        self.supported_to = supported_to
        to_str = str(supported_to) if supported_to else "current"
        super().__init__(
            f"Strategy {event_type} v{strategy_version} is incompatible with "
            f"system version {system_version}. Supported range: [{supported_from}, {to_str}]"
        )


class StrategyRegistry:
    """Registry for posting strategies (R14, R23)."""

    # Class-level registry for all strategies
    _strategies: ClassVar[dict[str, dict[int, PostingStrategy]]] = {}

    @classmethod
    def register(cls, strategy: PostingStrategy) -> None:
        """Register a posting strategy, validating R23 lifecycle metadata."""
        event_type = strategy.event_type
        version = strategy.version

        # INVARIANT: R23 -- validate lifecycle metadata before admission
        cls._validate_lifecycle(strategy)

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
    def _validate_lifecycle(cls, strategy: PostingStrategy) -> None:
        """R23: Validate strategy lifecycle metadata."""
        event_type = strategy.event_type
        version = strategy.version

        # Version must be positive
        if version < 1:
            raise StrategyLifecycleError(
                event_type, version,
                f"Strategy version must be >= 1, got {version}"
            )

        # supported_from_version must be >= 1
        if strategy.supported_from_version < 1:
            raise StrategyLifecycleError(
                event_type, version,
                f"supported_from_version must be >= 1, got {strategy.supported_from_version}"
            )

        # supported_to_version must be >= supported_from_version if set
        if (
            strategy.supported_to_version is not None and
            strategy.supported_to_version < strategy.supported_from_version
        ):
            raise StrategyLifecycleError(
                event_type, version,
                f"supported_to_version ({strategy.supported_to_version}) must be >= "
                f"supported_from_version ({strategy.supported_from_version})"
            )

        # replay_policy must be valid enum value
        if not isinstance(strategy.replay_policy, ReplayPolicy):
            raise StrategyLifecycleError(
                event_type, version,
                f"replay_policy must be a ReplayPolicy enum, got {type(strategy.replay_policy)}"
            )

    @classmethod
    def get(
        cls,
        event_type: str,
        version: int | None = None,
    ) -> PostingStrategy:
        """Get a strategy for an event type, optionally by version."""
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
        """Get the latest version number for an event type."""
        if event_type not in cls._strategies:
            raise StrategyNotFoundError(event_type)

        versions = cls._strategies[event_type]
        if not versions:
            raise StrategyNotFoundError(event_type)

        return max(versions.keys())

    @classmethod
    def get_all_versions(cls, event_type: str) -> list[int]:
        """Get all registered versions for an event type."""
        if event_type not in cls._strategies:
            raise StrategyNotFoundError(event_type)

        return sorted(cls._strategies[event_type].keys())

    @classmethod
    def list_event_types(cls) -> list[str]:
        """List all registered event types."""
        return sorted(cls._strategies.keys())

    @classmethod
    def has_strategy(cls, event_type: str) -> bool:
        """Check if a strategy exists for an event type."""
        return event_type in cls._strategies and bool(cls._strategies[event_type])

    @classmethod
    def get_for_replay(
        cls,
        event_type: str,
        original_version: int,
        system_version: int,
    ) -> PostingStrategy:
        """Get a strategy for replay, respecting lifecycle and replay policy (R23)."""
        # Get the original strategy
        strategy = cls.get(event_type, original_version)

        # Check compatibility with current system version
        if not strategy.is_compatible_with_system_version(system_version):
            raise StrategyIncompatibleError(
                event_type=event_type,
                strategy_version=original_version,
                system_version=system_version,
                supported_from=strategy.supported_from_version,
                supported_to=strategy.supported_to_version,
            )

        # For STRICT policy, we must use exact same version
        if strategy.replay_policy == ReplayPolicy.STRICT:
            return strategy

        # For PERMISSIVE policy, we could use a newer compatible version
        # but for safety, we still use the original version
        # (Future enhancement: could find best compatible version)
        return strategy

    @classmethod
    def get_compatible_strategies(
        cls,
        event_type: str,
        system_version: int,
    ) -> list[PostingStrategy]:
        """Get all strategies compatible with a given system version (R23)."""
        if event_type not in cls._strategies:
            raise StrategyNotFoundError(event_type)

        compatible = []
        for version, strategy in cls._strategies[event_type].items():
            if strategy.is_compatible_with_system_version(system_version):
                compatible.append(strategy)

        return sorted(compatible, key=lambda s: s.version)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered strategies. For testing only."""
        cls._strategies.clear()

    @classmethod
    def unregister(cls, event_type: str, version: int | None = None) -> None:
        """Unregister a strategy."""
        if event_type not in cls._strategies:
            return

        if version is None:
            del cls._strategies[event_type]
        elif version in cls._strategies[event_type]:
            del cls._strategies[event_type][version]
            if not cls._strategies[event_type]:
                del cls._strategies[event_type]


def register_strategy(strategy: PostingStrategy) -> PostingStrategy:
    """Decorator to register a strategy class."""
    StrategyRegistry.register(strategy)
    return strategy


def strategy_for(event_type: str, version: int = 1):
    """Decorator factory to create and register a strategy."""

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
