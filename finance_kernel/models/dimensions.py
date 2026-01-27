"""
Dimension models for multi-dimensional accounting.

Phase 1 baseline dimension set:
- org_unit_id (or legal_entity_id)
- project_id (nullable but typed)
- contract_id (nullable but typed)

Hard invariants:
- Dimension keys are fixed identifiers, not free text
- Dimension values are stable IDs; names are immutable once created
- DimensionValue must reference an existing Dimension (FK enforced)
- Inactive dimensions cannot be used in postings
- Required dimensions are enforced by posting rules
"""

from sqlalchemy import Boolean, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase


class Dimension(TrackedBase):
    """
    Dimension definition.

    Defines the available dimensions for multi-dimensional accounting.

    Invariants:
    - code is unique and immutable
    - is_active=False prevents use in new postings
    """

    __tablename__ = "dimensions"

    __table_args__ = (
        UniqueConstraint("code", name="uq_dimension_code"),
    )

    # Dimension identifier (e.g., "org_unit", "project", "contract")
    code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
    )

    # Human-readable name
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # Description
    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # Whether this dimension is required on all postings
    is_required: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # Whether this dimension is active
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Dimension {self.code}>"


class DimensionValue(TrackedBase):
    """
    Value for a dimension.

    Represents a specific value that can be assigned to a dimension
    (e.g., a specific project, org unit, or contract).

    Invariants:
    - dimension_code must reference an existing Dimension.code (FK enforced)
    - code is immutable once created
    - name is immutable once created (stable for audit trail)
    - is_active=False prevents use in new postings
    """

    __tablename__ = "dimension_values"

    __table_args__ = (
        UniqueConstraint("dimension_code", "code", name="uq_dimension_value"),
        ForeignKeyConstraint(
            ["dimension_code"],
            ["dimensions.code"],
            name="fk_dimension_value_dimension",
            ondelete="RESTRICT",  # Prevent deleting dimensions with values
        ),
        Index("idx_dimval_dimension", "dimension_code"),
        Index("idx_dimval_active", "is_active"),
    )

    # Which dimension this value belongs to (FK to dimensions.code)
    dimension_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # Relationship to parent Dimension
    dimension: Mapped["Dimension"] = relationship(
        "Dimension",
        primaryjoin="DimensionValue.dimension_code == foreign(Dimension.code)",
        lazy="joined",
    )

    # Value identifier (stable, never changes)
    code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # Human-readable name (can change without affecting history)
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # Description
    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # Whether this value is active for new postings
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<DimensionValue {self.dimension_code}:{self.code}>"
