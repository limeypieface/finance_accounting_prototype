"""
Event schema data structures.

Provides immutable, hashable schema definitions for event payload validation.
This is part of the functional core - no I/O, no ORM.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


class EventFieldType(str, Enum):
    """Supported field types in event schemas."""

    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"  # ISO 8601 date (YYYY-MM-DD)
    DATETIME = "datetime"  # ISO 8601 datetime
    UUID = "uuid"
    CURRENCY = "currency"  # ISO 4217 code
    OBJECT = "object"  # Nested object
    ARRAY = "array"  # Array of items


@dataclass(frozen=True)
class EventFieldSchema:
    """
    Schema definition for a single field.

    Immutable and hashable for use in frozen dataclasses.
    """

    name: str
    field_type: EventFieldType
    required: bool = True
    nullable: bool = False
    description: str | None = None

    # For OBJECT type - nested field definitions
    nested_fields: tuple["EventFieldSchema", ...] | None = None

    # For ARRAY type
    item_type: EventFieldType | None = None
    item_schema: tuple["EventFieldSchema", ...] | None = None  # For array of objects

    # Validation constraints for numeric types
    min_value: Decimal | int | None = None
    max_value: Decimal | int | None = None

    # Validation constraints for string types
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None  # Regex pattern

    # Enum-like constraint
    allowed_values: frozenset[str] | None = None

    def __post_init__(self) -> None:
        """Validate field schema configuration."""
        if self.field_type == EventFieldType.OBJECT and not self.nested_fields:
            raise ValueError(
                f"Field '{self.name}' of type OBJECT must have nested_fields"
            )

        if self.field_type == EventFieldType.ARRAY:
            if not self.item_type and not self.item_schema:
                raise ValueError(
                    f"Field '{self.name}' of type ARRAY must have item_type or item_schema"
                )
            if self.item_type == EventFieldType.OBJECT and not self.item_schema:
                raise ValueError(
                    f"Field '{self.name}' with item_type OBJECT must have item_schema"
                )


@dataclass(frozen=True)
class EventSchema:
    """
    Complete schema definition for an event type.

    Immutable and hashable for deterministic validation.
    """

    event_type: str
    version: int
    fields: tuple[EventFieldSchema, ...]
    description: str = ""

    # Governance metadata
    deprecated: bool = False
    deprecated_message: str | None = None
    superseded_by_version: int | None = None

    # Private cache for field paths (computed lazily)
    _field_paths_cache: frozenset[str] = field(
        default=frozenset(), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        """Validate schema configuration."""
        if not self.event_type:
            raise ValueError("event_type is required")
        if self.version < 1:
            raise ValueError("version must be >= 1")
        if "." not in self.event_type:
            raise ValueError(
                f"event_type must be namespaced (contain a dot): {self.event_type}"
            )

    @property
    def schema_key(self) -> str:
        """Unique key for this schema."""
        return f"{self.event_type}:v{self.version}"

    def _iterate_field_paths(
        self,
        fields: tuple[EventFieldSchema, ...],
        prefix: str = "",
    ) -> "Iterator[str]":
        """
        Recursively iterate all field paths.

        Args:
            fields: Fields to iterate.
            prefix: Current path prefix.

        Yields:
            Field paths in dot notation.
        """
        for f in fields:
            path = f"{prefix}{f.name}" if prefix else f.name
            yield path

            if f.field_type == EventFieldType.OBJECT and f.nested_fields:
                yield from self._iterate_field_paths(f.nested_fields, f"{path}.")

            if f.field_type == EventFieldType.ARRAY and f.item_schema:
                # Array items use [*] wildcard notation
                yield from self._iterate_field_paths(f.item_schema, f"{path}[*].")

    def all_field_paths(self) -> frozenset[str]:
        """
        Get all valid field paths in this schema.

        Returns:
            Frozen set of all field paths in dot notation.
        """
        # Use object.__setattr__ to bypass frozen dataclass
        if not self._field_paths_cache:
            paths = frozenset(self._iterate_field_paths(self.fields))
            object.__setattr__(self, "_field_paths_cache", paths)
        return self._field_paths_cache

    def has_field(self, path: str) -> bool:
        """
        Check if a field path exists in this schema.

        Args:
            path: Field path in dot notation (e.g., "amount", "items[*].quantity")

        Returns:
            True if the field path exists.
        """
        return path in self.all_field_paths()

    def get_field(self, path: str) -> EventFieldSchema | None:
        """
        Get field schema by dot-notation path.

        Args:
            path: Field path (e.g., "amount", "items[*].quantity")

        Returns:
            EventFieldSchema if found, None otherwise.
        """
        if not self.has_field(path):
            return None

        parts = path.replace("[*]", "").split(".")
        current_fields = self.fields

        for i, part in enumerate(parts):
            for f in current_fields:
                if f.name == part:
                    if i == len(parts) - 1:
                        return f
                    # Navigate deeper
                    if f.field_type == EventFieldType.OBJECT and f.nested_fields:
                        current_fields = f.nested_fields
                        break
                    elif f.field_type == EventFieldType.ARRAY and f.item_schema:
                        current_fields = f.item_schema
                        break
            else:
                return None

        return None

    def get_fields_dict(self) -> dict[str, EventFieldSchema]:
        """
        Get top-level fields as a dictionary by name.

        Returns:
            Dictionary mapping field names to schemas.
        """
        return {f.name: f for f in self.fields}
