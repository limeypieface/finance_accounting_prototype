"""
Module: finance_kernel.models.economic_link
Responsibility: ORM persistence for economic links -- the "why pointer"
    connecting financial artifacts across the system.
Architecture position: Kernel > Models.  May import from db/base.py and
    exceptions.py only.

Invariants enforced:
    R4/L1 -- Links are immutable after creation (ORM before_update/before_delete
             listeners + DB trigger).  No UPDATE, no DELETE.
    L2   -- No self-links (parent_ref != child_ref).  Enforced by
            LinkGraphService at creation time.
    L3   -- Unique constraint on (link_type, parent_ref, child_ref).
    L4   -- creating_event_id is required (NOT NULL) -- provenance tracking.

Failure modes:
    - ImmutabilityViolationError on UPDATE or DELETE attempt (L1).
    - IntegrityError on duplicate link (L3).
    - SelfLinkError on self-referential link attempt (L2).

Audit relevance:
    EconomicLinks form the traversable graph of financial cause-and-effect.
    Every link records the creating_event_id so auditors can trace why any
    two artifacts are related.  Immutability ensures the historical graph
    cannot be retroactively altered.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Index,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import Base, UUIDString
from finance_kernel.exceptions import ImmutabilityViolationError

if TYPE_CHECKING:
    from finance_kernel.domain.economic_link import (
        ArtifactRef,
        EconomicLink,
        LinkType,
    )


class EconomicLinkModel(Base):
    """
    Persistent storage for economic links.

    Contract:
        Once INSERTed, an EconomicLinkModel row is immutable -- no UPDATE,
        no DELETE.  To "undo" a link, create a compensating relationship
        (e.g., REVERSED_BY).

    Guarantees:
        - (link_type, parent_ref, child_ref) is unique (L3).
        - creating_event_id is always populated (L4).
        - ORM listeners raise ImmutabilityViolationError on mutation (L1).

    Non-goals:
        - Self-link prevention (L2) is enforced by LinkGraphService, not
          this model.
        - Cycle detection is performed by LinkGraphService at creation time.
    """

    __tablename__ = "economic_links"

    __table_args__ = (
        # L3: Unique constraint on relationship
        UniqueConstraint(
            "link_type",
            "parent_artifact_type",
            "parent_artifact_id",
            "child_artifact_type",
            "child_artifact_id",
            name="uq_economic_link_relationship",
        ),
        # Query patterns
        Index("idx_link_parent", "parent_artifact_type", "parent_artifact_id"),
        Index("idx_link_child", "child_artifact_type", "child_artifact_id"),
        Index("idx_link_type", "link_type"),
        Index("idx_link_creating_event", "creating_event_id"),
        Index("idx_link_created_at", "created_at"),
        # Composite for traversal queries
        Index(
            "idx_link_type_parent",
            "link_type",
            "parent_artifact_type",
            "parent_artifact_id",
        ),
        Index(
            "idx_link_type_child",
            "link_type",
            "child_artifact_type",
            "child_artifact_id",
        ),
    )

    # Semantic relationship type
    link_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # Parent artifact (the "from" side)
    parent_artifact_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    parent_artifact_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        nullable=False,
    )

    # Child artifact (the "to" side)
    child_artifact_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    child_artifact_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        nullable=False,
    )

    # L4: Provenance - which event created this link
    creating_event_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        nullable=False,
    )

    # When the link was created
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Optional metadata (e.g., allocation percentage, matched amount)
    link_metadata: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<EconomicLink {self.link_type}: "
            f"{self.parent_artifact_type}:{self.parent_artifact_id} → "
            f"{self.child_artifact_type}:{self.child_artifact_id}>"
        )

    @property
    def parent_ref_str(self) -> str:
        """String representation of parent ref."""
        return f"{self.parent_artifact_type}:{self.parent_artifact_id}"

    @property
    def child_ref_str(self) -> str:
        """String representation of child ref."""
        return f"{self.child_artifact_type}:{self.child_artifact_id}"

    @classmethod
    def from_domain(cls, link: EconomicLink) -> EconomicLinkModel:
        """Create ORM model from domain object.

        Preconditions: link is a valid EconomicLink domain object.
        Postconditions: Returns a new EconomicLinkModel ready for session.add().
        """
        return cls(
            id=link.link_id,
            link_type=link.link_type.value,
            parent_artifact_type=link.parent_ref.artifact_type.value,
            parent_artifact_id=link.parent_ref.artifact_id,
            child_artifact_type=link.child_ref.artifact_type.value,
            child_artifact_id=link.child_ref.artifact_id,
            creating_event_id=link.creating_event_id,
            created_at=link.created_at,
            link_metadata=dict(link.metadata) if link.metadata else None,
        )

    def to_domain(self) -> EconomicLink:
        """Convert ORM model to domain object.

        Postconditions: Returns an EconomicLink frozen dataclass.
        Raises: ValueError if stored link_type or artifact_type is not a
            valid enum member.
        """
        # Import here to avoid circular imports
        from finance_kernel.domain.economic_link import (
            ArtifactRef,
            ArtifactType,
            EconomicLink,
            LinkType,
        )

        return EconomicLink(
            link_id=self.id,
            link_type=LinkType(self.link_type),
            parent_ref=ArtifactRef(
                artifact_type=ArtifactType(self.parent_artifact_type),
                artifact_id=self.parent_artifact_id,
            ),
            child_ref=ArtifactRef(
                artifact_type=ArtifactType(self.child_artifact_type),
                artifact_id=self.child_artifact_id,
            ),
            creating_event_id=self.creating_event_id,
            created_at=self.created_at,
            metadata=self.link_metadata,
        )

    @property
    def parent_ref(self) -> ArtifactRef:
        """Get parent as ArtifactRef."""
        from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType

        return ArtifactRef(
            artifact_type=ArtifactType(self.parent_artifact_type),
            artifact_id=self.parent_artifact_id,
        )

    @property
    def child_ref(self) -> ArtifactRef:
        """Get child as ArtifactRef."""
        from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType

        return ArtifactRef(
            artifact_type=ArtifactType(self.child_artifact_type),
            artifact_id=self.child_artifact_id,
        )


# =============================================================================
# ORM-Level Immutability Protection (L1 Compliance)
# =============================================================================
# Links are immutable once created. No modifications allowed.
# The database trigger provides a second layer of defense for raw SQL.
# =============================================================================


@event.listens_for(EconomicLinkModel, "before_update")
def prevent_link_update(mapper, connection, target):
    """Prevent any updates to EconomicLink records.

    Preconditions: Called by SQLAlchemy before any UPDATE flush.
    Raises: ImmutabilityViolationError always -- INVARIANT L1: Links are
        immutable once created.
    """
    raise ImmutabilityViolationError(
        entity_type="EconomicLink",
        entity_id=str(target.id),
        reason="L1 Violation: Economic links are immutable - cannot modify link",
    )


@event.listens_for(EconomicLinkModel, "before_delete")
def prevent_link_delete(mapper, connection, target):
    """Prevent deletion of EconomicLink records.

    Preconditions: Called by SQLAlchemy before any DELETE flush.
    Raises: ImmutabilityViolationError always -- INVARIANT L1: Links are
        immutable.  Use compensating links (e.g., REVERSED_BY) instead.
    """
    raise ImmutabilityViolationError(
        entity_type="EconomicLink",
        entity_id=str(target.id),
        reason="L1 Violation: Economic links are immutable - cannot delete link",
    )
