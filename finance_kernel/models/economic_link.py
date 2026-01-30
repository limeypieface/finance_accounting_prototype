"""
EconomicLink ORM Model.

Persistence layer for economic links with immutability protection.

Hard invariants:
- L1: Links are immutable after creation (no UPDATE, no DELETE)
- L2: No self-links (parent_ref != child_ref)
- L3: Unique constraint on (link_type, parent_ref, child_ref)
- L4: creating_event_id is required (NOT NULL)
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

    Maps the EconomicLink domain object to the database.
    Enforces immutability via ORM event listeners.
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
        """
        Create ORM model from domain object.

        Used by LinkGraphService to persist links.
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
        """
        Convert ORM model to domain object.

        Used by LinkGraphService for query results.
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
    """
    Prevent any updates to EconomicLink records.

    L1 Violation: Links are immutable once created.
    """
    raise ImmutabilityViolationError(
        entity_type="EconomicLink",
        entity_id=str(target.id),
        reason="L1 Violation: Economic links are immutable - cannot modify link",
    )


@event.listens_for(EconomicLinkModel, "before_delete")
def prevent_link_delete(mapper, connection, target):
    """
    Prevent deletion of EconomicLink records.

    L1 Violation: Links are immutable - use compensating links (e.g., REVERSED_BY)
    instead of deletion.
    """
    raise ImmutabilityViolationError(
        entity_type="EconomicLink",
        entity_id=str(target.id),
        reason="L1 Violation: Economic links are immutable - cannot delete link",
    )
