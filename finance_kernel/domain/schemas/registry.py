"""
Event schema registry.

Provides registration and lookup of event schemas by event type and version.
This is part of the functional core - no I/O, no ORM.
"""

from typing import ClassVar

from finance_kernel.domain.schemas.base import EventSchema
from finance_kernel.logging_config import get_logger

logger = get_logger("domain.schema_registry")


class SchemaNotFoundError(Exception):
    """No schema registered for event type/version."""

    code: str = "SCHEMA_NOT_FOUND"

    def __init__(self, event_type: str, version: int | None = None):
        self.event_type = event_type
        self.version = version
        msg = f"No schema registered for event type: {event_type}"
        if version is not None:
            msg += f" (version {version})"
        super().__init__(msg)


class SchemaAlreadyRegisteredError(Exception):
    """Schema already registered for event type/version."""

    code: str = "SCHEMA_ALREADY_REGISTERED"

    def __init__(self, event_type: str, version: int):
        self.event_type = event_type
        self.version = version
        super().__init__(
            f"Schema already registered for {event_type} version {version}"
        )


class EventSchemaRegistry:
    """
    Registry for event schemas.

    Provides:
    - Schema registration by event type and version
    - Schema lookup by event type and version
    - Version management (latest, all versions)

    This is a pure domain component - no I/O, no ORM.

    Usage:
        # Register a schema
        EventSchemaRegistry.register(my_schema)

        # Get specific version
        schema = EventSchemaRegistry.get("inventory.receipt", version=1)

        # Get latest version
        schema = EventSchemaRegistry.get("inventory.receipt")

        # Check if schema exists
        if EventSchemaRegistry.has_schema("inventory.receipt"):
            ...
    """

    # Class-level registry: event_type -> {version -> schema}
    _schemas: ClassVar[dict[str, dict[int, EventSchema]]] = {}

    @classmethod
    def register(cls, schema: EventSchema) -> None:
        """
        Register an event schema.

        Args:
            schema: The schema to register.

        Raises:
            SchemaAlreadyRegisteredError: If schema is already registered.
        """
        if schema.event_type not in cls._schemas:
            cls._schemas[schema.event_type] = {}

        if schema.version in cls._schemas[schema.event_type]:
            logger.warning(
                "schema_already_registered",
                extra={
                    "event_type": schema.event_type,
                    "version": schema.version,
                },
            )
            raise SchemaAlreadyRegisteredError(schema.event_type, schema.version)

        cls._schemas[schema.event_type][schema.version] = schema
        logger.info(
            "schema_registered",
            extra={
                "event_type": schema.event_type,
                "version": schema.version,
                "field_count": len(schema.fields),
            },
        )

    @classmethod
    def get(
        cls,
        event_type: str,
        version: int | None = None,
    ) -> EventSchema:
        """
        Get schema for event type.

        Args:
            event_type: The event type.
            version: Specific version, or None for latest.

        Returns:
            The EventSchema.

        Raises:
            SchemaNotFoundError: If not found.
        """
        if event_type not in cls._schemas:
            logger.warning(
                "schema_not_found",
                extra={
                    "event_type": event_type,
                    "version": version,
                },
            )
            raise SchemaNotFoundError(event_type, version)

        versions = cls._schemas[event_type]

        if version is not None:
            if version not in versions:
                logger.warning(
                    "schema_not_found",
                    extra={
                        "event_type": event_type,
                        "version": version,
                        "available_versions": sorted(versions.keys()),
                    },
                )
                raise SchemaNotFoundError(event_type, version)
            logger.debug(
                "schema_lookup_hit",
                extra={
                    "event_type": event_type,
                    "version": version,
                },
            )
            return versions[version]

        # Return latest version
        if not versions:
            logger.warning(
                "schema_not_found",
                extra={
                    "event_type": event_type,
                    "version": version,
                },
            )
            raise SchemaNotFoundError(event_type)

        latest_version = max(versions.keys())
        logger.debug(
            "schema_lookup_hit",
            extra={
                "event_type": event_type,
                "version": latest_version,
                "resolved": "latest",
            },
        )
        return versions[latest_version]

    @classmethod
    def has_schema(cls, event_type: str, version: int | None = None) -> bool:
        """
        Check if schema exists.

        Args:
            event_type: The event type.
            version: Specific version, or None for any version.

        Returns:
            True if schema exists.
        """
        if event_type not in cls._schemas:
            return False

        if version is None:
            return len(cls._schemas[event_type]) > 0

        return version in cls._schemas[event_type]

    @classmethod
    def get_latest_version(cls, event_type: str) -> int:
        """
        Get latest schema version for event type.

        Args:
            event_type: The event type.

        Returns:
            The latest version number.

        Raises:
            SchemaNotFoundError: If no schemas registered.
        """
        if event_type not in cls._schemas or not cls._schemas[event_type]:
            raise SchemaNotFoundError(event_type)

        return max(cls._schemas[event_type].keys())

    @classmethod
    def get_all_versions(cls, event_type: str) -> list[int]:
        """
        Get all registered versions for event type.

        Args:
            event_type: The event type.

        Returns:
            List of version numbers (sorted ascending).
        """
        if event_type not in cls._schemas:
            return []

        return sorted(cls._schemas[event_type].keys())

    @classmethod
    def list_event_types(cls) -> list[str]:
        """
        List all registered event types.

        Returns:
            List of event type names (sorted).
        """
        return sorted(cls._schemas.keys())

    @classmethod
    def clear(cls) -> None:
        """
        Clear all registered schemas.

        WARNING: This should only be used in tests.
        """
        cls._schemas.clear()

    @classmethod
    def unregister(cls, event_type: str, version: int | None = None) -> None:
        """
        Unregister a schema.

        WARNING: This should only be used in tests.

        Args:
            event_type: The event type.
            version: Specific version, or None to remove all versions.
        """
        if event_type not in cls._schemas:
            return

        if version is None:
            del cls._schemas[event_type]
        elif version in cls._schemas[event_type]:
            del cls._schemas[event_type][version]
            if not cls._schemas[event_type]:
                del cls._schemas[event_type]
