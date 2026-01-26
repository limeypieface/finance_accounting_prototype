"""
Base classes for all SQLAlchemy models.

Follows ironflow/sindri patterns:
- Base: Standard declarative base with UUID primary keys
- TrackedBase: Adds audit timestamps (created_at, updated_at, created_by_id)
"""

from datetime import datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID as PyUUID, uuid4

from sqlalchemy import BigInteger, DateTime, Numeric, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UUIDString(TypeDecorator):
    """
    Platform-independent UUID type.

    Uses String(36) as storage, converting UUID objects to/from strings.
    This ensures compatibility with SQLite which doesn't have native UUID support.
    """

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Convert UUID to string when storing."""
        if value is not None:
            return str(value)
        return None

    def process_result_value(self, value, dialect):
        """Convert string back to UUID when loading."""
        if value is not None:
            return PyUUID(value)
        return None


class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy models.

    Features:
    - UUID primary keys (generated via uuid4)
    - Type annotation map for consistent column types
    """

    # Map Python types to SQLAlchemy column types
    type_annotation_map: ClassVar[dict] = {
        # Financial precision: 38 digits total, 9 decimal places
        Decimal: Numeric(38, 9),
        # Timestamps with timezone awareness
        datetime: DateTime(timezone=True),
        # UUIDs stored as string (SQLite compatibility)
        PyUUID: UUIDString(),
        # Large integers for sequences
        int: BigInteger,
    }

    # Default UUID primary key for all models
    id: Mapped[PyUUID] = mapped_column(
        UUIDString(),
        primary_key=True,
        default=uuid4,
    )


class TrackedBase(Base):
    """
    Abstract base class for models that track creation and modification.

    Adds:
    - created_at: When the record was created
    - updated_at: When the record was last modified
    - created_by_id: UUID of the user/actor who created the record
    - updated_by_id: UUID of the user/actor who last modified the record
    """

    __abstract__ = True

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    created_by_id: Mapped[PyUUID] = mapped_column(
        UUIDString(),
        nullable=False,
    )

    updated_by_id: Mapped[PyUUID | None] = mapped_column(
        UUIDString(),
        nullable=True,
    )


# Re-export UUID for convenience
UUID = PyUUID
