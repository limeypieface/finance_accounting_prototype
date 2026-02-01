"""
Module: finance_kernel.models.dimensions
Responsibility: ORM persistence for the multi-dimensional accounting dimension
    schema.  Dimension defines the axis (e.g., "org_unit", "project"); DimensionValue
    defines the members of that axis (e.g., "PROJ-100", "DIV-EAST").
Architecture position: Kernel > Models.  May import from db/base.py only.
    MUST NOT import from services/, selectors/, domain/, or outer layers.

Invariants enforced:
    R10 -- Dimension.code is immutable once dimension values exist (ORM listener
           in db/immutability.py + DB trigger 07_dimension.sql).
    R10 -- DimensionValue.code, .name, and .dimension_code are immutable after
           creation (ORM listener + DB trigger).
    R21 -- dimension_schema_version on JournalEntry records the dimension schema
           state at posting time for deterministic replay.

Failure modes:
    - ImmutabilityViolationError on UPDATE of Dimension.code when values exist.
    - ImmutabilityViolationError on UPDATE of DimensionValue immutable fields
      (code, name, dimension_code).
    - IntegrityError on INSERT of DimensionValue with non-existent dimension_code
      (FK RESTRICT).

Audit relevance:
    Dimensions partition journal lines into reportable segments (cost center,
    project, contract).  Stability of dimension codes and names ensures that
    historical journal dimension JSON maps remain interpretable across time.
    Changing a dimension value code would orphan all journal lines that reference it.
"""

from sqlalchemy import Boolean, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase


class Dimension(TrackedBase):
    """
    Dimension definition -- one axis of the multi-dimensional chart.

    Contract:
        Each Dimension defines one named axis (e.g., "org_unit", "project")
        identified by a unique, immutable code.  The code becomes the key
        in JournalLine.dimensions JSON maps and MUST NOT change once any
        DimensionValue exists under it (R10).

    Guarantees:
        - code is globally unique (uq_dimension_code constraint).
        - code is immutable once child DimensionValues exist (ORM + DB trigger).
        - is_active=False prevents this dimension from being used in new postings.
        - is_required=True causes posting validation to reject lines without it.

    Non-goals:
        - This model does NOT enforce is_required at the ORM level; enforcement
          lives in the posting pipeline (JournalWriter / AccountingIntent).
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
    Member value for a dimension axis.

    Contract:
        Each DimensionValue represents one selectable member within a Dimension
        (e.g., project "PROJ-100" under the "project" dimension).  Its code,
        name, and dimension_code are immutable after creation (R10).

    Guarantees:
        - (dimension_code, code) is unique (uq_dimension_value constraint).
        - dimension_code references an existing Dimension.code (FK RESTRICT).
        - code, name, dimension_code are immutable once created (ORM + DB trigger).
        - is_active=False prevents this value from being used in new postings.

    Non-goals:
        - This model does NOT cascade-delete when its parent Dimension is
          removed; the FK uses ON DELETE RESTRICT to prevent orphaning.
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
