"""Declarative base classes for all SQLAlchemy ORM models."""

from datetime import datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID as PyUUID
from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, Numeric, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UUIDString(TypeDecorator):
    """UUID type stored as String(36) for cross-database portability."""

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return str(value)
        return None

    def process_result_value(self, value, dialect):
        if value is not None:
            return PyUUID(value)
        return None


class Base(DeclarativeBase):
    """Declarative base with UUID primary key and financial-grade type map."""

    # Map Python types to SQLAlchemy column types
    type_annotation_map: ClassVar[dict] = {
        # Financial precision: 38 digits total, 9 decimal places
        Decimal: Numeric(38, 9),
        # Timestamps with timezone awareness
        datetime: DateTime(timezone=True),
        # UUIDs stored as string
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
    """Abstract base with audit timestamp and actor tracking."""

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
