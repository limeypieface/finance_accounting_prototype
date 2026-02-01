"""
Module: finance_kernel.db.base
Responsibility: Declarative base classes for all SQLAlchemy ORM models.  Provides
    the UUID primary key convention, type annotation map for consistent column
    types, and the TrackedBase mixin for audit timestamps.
Architecture position: Kernel > DB.  This is the lowest-level import target
    within the kernel.  ALL model files import from here.  This module MUST NOT
    import from models/, services/, selectors/, domain/, or outer layers.

Invariants enforced:
    - UUID primary keys: Every model inherits a uuid4-generated primary key,
      ensuring globally unique, non-sequential identifiers.
    - Decimal precision: type_annotation_map maps Python Decimal to
      Numeric(38, 9), enforcing financial-grade precision system-wide.
      NEVER use float for monetary amounts.
    - Audit timestamps: TrackedBase provides created_at, updated_at,
      created_by_id, and updated_by_id for audit trail completeness.

Failure modes:
    - IntegrityError if a model attempts to INSERT a duplicate UUID (astronomically
      unlikely with uuid4 but protected by PK constraint).

Audit relevance:
    TrackedBase.created_at, updated_at, created_by_id, and updated_by_id form
    the basic audit metadata for every tracked entity.  The updated_at/updated_by_id
    fields are explicitly allowed to change even on immutable records (they are
    audit metadata, not financial data -- see db/immutability.py design decision #1).
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
    UUID type stored as String(36) for cross-database portability.

    Contract:
        Transparently converts between Python UUID objects and their 36-character
        string representation (e.g., "550e8400-e29b-41d4-a716-446655440000").

    Guarantees:
        - process_bind_param: UUID -> str on INSERT/UPDATE.
        - process_result_value: str -> UUID on SELECT.
        - cache_ok=True enables SQLAlchemy statement caching.
    """

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Convert UUID to string when storing.

        Preconditions: value is a UUID or None.
        Postconditions: Returns str(value) or None.
        """
        if value is not None:
            return str(value)
        return None

    def process_result_value(self, value, dialect):
        """Convert string back to UUID when loading.

        Preconditions: value is a 36-character UUID string or None.
        Postconditions: Returns UUID(value) or None.
        """
        if value is not None:
            return PyUUID(value)
        return None


class Base(DeclarativeBase):
    """
    Declarative base for all SQLAlchemy models.

    Contract:
        Every ORM model in the system inherits from Base (or TrackedBase).
        Base provides a UUID primary key and a type_annotation_map that
        enforces consistent column types across the entire schema.

    Guarantees:
        - id is always a uuid4-generated UUID stored as String(36).
        - Decimal maps to Numeric(38, 9) -- financial-grade precision.
        - datetime maps to DateTime(timezone=True) -- always timezone-aware.
        - int maps to BigInteger -- safe for monotonic sequences.
    """

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
    """
    Abstract base with audit timestamp and actor tracking.

    Contract:
        Every model that inherits TrackedBase automatically records who
        created and last modified the row, and when.  These fields are
        considered audit metadata, NOT financial data, so they are
        explicitly allowed to change even on otherwise-immutable records
        (see db/immutability.py design decision #1).

    Guarantees:
        - created_at is set to server NOW() on INSERT and never changes.
        - updated_at is set to server NOW() on INSERT and auto-updates on
          every UPDATE (via onupdate=func.now()).
        - created_by_id is required (NOT NULL) -- every record has a creator.
        - updated_by_id is nullable (may not be set on initial creation).
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
