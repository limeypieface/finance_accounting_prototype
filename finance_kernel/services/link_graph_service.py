"""Graph operations on EconomicLinks with L3 acyclicity enforcement."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, literal, literal_column, select, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance_kernel.domain.economic_link import (
    LINK_TYPE_SPECS,
    ArtifactRef,
    ArtifactType,
    EconomicLink,
    LinkPath,
    LinkQuery,
    LinkType,
)
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import (
    DuplicateLinkError,
    LinkCycleError,
    MaxChildrenExceededError,
)
from finance_kernel.logging_config import get_logger
from finance_kernel.models.economic_link import EconomicLinkModel

logger = get_logger("services.link_graph")


@dataclass(frozen=True)
class LinkEstablishResult:
    """Result of establishing a link."""

    link: EconomicLink
    was_duplicate: bool = False

    @classmethod
    def success(cls, link: EconomicLink) -> LinkEstablishResult:
        return cls(link=link)

    @classmethod
    def already_exists(cls, link: EconomicLink) -> LinkEstablishResult:
        return cls(link=link, was_duplicate=True)


@dataclass(frozen=True)
class UnconsumedValue:
    """Remaining value after child allocations."""

    artifact_ref: ArtifactRef
    original_amount: Money
    consumed_amount: Money
    remaining_amount: Money
    child_count: int

    @property
    def is_fully_consumed(self) -> bool:
        return self.remaining_amount.is_zero

    @property
    def consumption_percentage(self) -> Decimal:
        if self.original_amount.is_zero:
            return Decimal("100")
        return (self.consumed_amount.amount / self.original_amount.amount) * 100


class LinkGraphService:
    """Persists economic links and enforces L3 acyclicity."""

    # Link types that must be acyclic (prevent infinite loops)
    ACYCLIC_LINK_TYPES: frozenset[LinkType] = frozenset({
        LinkType.FULFILLED_BY,
        LinkType.SOURCED_FROM,
        LinkType.DERIVED_FROM,
        LinkType.CONSUMED_BY,
        LinkType.CORRECTED_BY,
    })

    def __init__(self, session: Session):
        self.session = session

    # =========================================================================
    # Link Creation
    # =========================================================================

    def establish_link(
        self,
        link: EconomicLink,
        allow_duplicate: bool = False,
    ) -> LinkEstablishResult:
        """Persist a link after validating acyclicity, duplicates, and max-children."""
        # Check for existing link (duplicate)
        existing = self._find_existing_link(link)
        if existing:
            if allow_duplicate:
                return LinkEstablishResult.already_exists(existing.to_domain())
            raise DuplicateLinkError(
                link_type=link.link_type.value,
                parent_ref=str(link.parent_ref),
                child_ref=str(link.child_ref),
            )

        # INVARIANT: L3 -- Acyclic enforcement for directed link types
        if link.link_type in self.ACYCLIC_LINK_TYPES:
            cycle_path = self._detect_cycle(link)
            if cycle_path:
                logger.error(
                    "cycle_detected_in_link_graph",
                    extra={
                        "link_type": link.link_type.value,
                        "path": [str(ref) for ref in cycle_path],
                    },
                )
                raise LinkCycleError(
                    link_type=link.link_type.value,
                    path=[str(ref) for ref in cycle_path],
                )

        # Check max children constraint
        spec = LINK_TYPE_SPECS.get(link.link_type)
        if spec and spec.max_children is not None:
            current_count = self._count_children(link.parent_ref, link.link_type)
            if current_count >= spec.max_children:
                raise MaxChildrenExceededError(
                    link_type=link.link_type.value,
                    parent_ref=str(link.parent_ref),
                    max_children=spec.max_children,
                    current_children=current_count,
                )

        # Persist the link
        try:
            orm_link = EconomicLinkModel.from_domain(link)
            self.session.add(orm_link)
            self.session.flush()
            logger.info(
                "economic_link_created",
                extra={
                    "link_id": str(link.link_id),
                    "link_type": link.link_type.value,
                    "source_ref": str(link.parent_ref),
                    "target_ref": str(link.child_ref),
                },
            )
            return LinkEstablishResult.success(link)
        except IntegrityError as e:
            # Race condition: another process created the link
            self.session.rollback()
            existing = self._find_existing_link(link)
            if existing and allow_duplicate:
                return LinkEstablishResult.already_exists(existing.to_domain())
            raise DuplicateLinkError(
                link_type=link.link_type.value,
                parent_ref=str(link.parent_ref),
                child_ref=str(link.child_ref),
            ) from e

    def establish_links(
        self,
        links: Sequence[EconomicLink],
        allow_duplicates: bool = False,
    ) -> list[LinkEstablishResult]:
        """Establish multiple links atomically."""
        results: list[LinkEstablishResult] = []
        for link in links:
            result = self.establish_link(link, allow_duplicate=allow_duplicates)
            results.append(result)
        return results

    # =========================================================================
    # Graph Traversal
    # =========================================================================

    def walk_path(self, query: LinkQuery) -> list[LinkPath]:
        """Recursively traverse the graph based on the query using CTE."""
        if query.max_depth < 1:
            return [LinkPath(artifacts=(query.starting_ref,), links=())]

        # Fetch all reachable links using recursive CTE
        reachable_links = self._fetch_reachable_links(query)

        # Build paths from the link results
        return self._build_paths(
            query.starting_ref,
            reachable_links,
            query.max_depth,
            query.direction,
        )

    def get_children(
        self,
        parent_ref: ArtifactRef,
        link_types: frozenset[LinkType] | None = None,
    ) -> list[EconomicLink]:
        """Get all direct children of an artifact."""
        query = (
            select(EconomicLinkModel)
            .where(
                and_(
                    EconomicLinkModel.parent_artifact_type == parent_ref.artifact_type.value,
                    EconomicLinkModel.parent_artifact_id == parent_ref.artifact_id,
                )
            )
        )

        if link_types:
            query = query.where(
                EconomicLinkModel.link_type.in_([lt.value for lt in link_types])
            )

        result = self.session.execute(query)
        return [row.to_domain() for row in result.scalars().all()]

    def get_parents(
        self,
        child_ref: ArtifactRef,
        link_types: frozenset[LinkType] | None = None,
    ) -> list[EconomicLink]:
        """Get all direct parents of an artifact."""
        query = (
            select(EconomicLinkModel)
            .where(
                and_(
                    EconomicLinkModel.child_artifact_type == child_ref.artifact_type.value,
                    EconomicLinkModel.child_artifact_id == child_ref.artifact_id,
                )
            )
        )

        if link_types:
            query = query.where(
                EconomicLinkModel.link_type.in_([lt.value for lt in link_types])
            )

        result = self.session.execute(query)
        return [row.to_domain() for row in result.scalars().all()]

    def get_link(
        self,
        parent_ref: ArtifactRef,
        child_ref: ArtifactRef,
        link_type: LinkType,
    ) -> EconomicLink | None:
        """Get a specific link by its relationship."""
        result = self._find_existing_link_by_refs(parent_ref, child_ref, link_type)
        return result.to_domain() if result else None

    # =========================================================================
    # Value Calculations
    # =========================================================================

    def get_unconsumed_value(
        self,
        parent_ref: ArtifactRef,
        original_amount: Money,
        link_types: frozenset[LinkType] | None = None,
        amount_metadata_key: str = "amount_applied",
    ) -> UnconsumedValue:
        """Calculate remaining value after child allocations."""
        if link_types is None:
            link_types = frozenset({
                LinkType.PAID_BY,
                LinkType.ALLOCATED_TO,
                LinkType.CONSUMED_BY,
            })

        # Get all child links
        children = self.get_children(parent_ref, link_types)

        # Sum up consumed amounts from metadata
        consumed_total = Decimal("0")
        for link in children:
            if link.metadata and amount_metadata_key in link.metadata:
                try:
                    amount = Decimal(str(link.metadata[amount_metadata_key]))
                    consumed_total += amount
                except (ValueError, TypeError):
                    logger.debug(
                        "skipping_invalid_metadata",
                        extra={
                            "link_id": str(link.link_id),
                            "metadata_key": amount_metadata_key,
                            "raw_value": str(link.metadata.get(amount_metadata_key)),
                        },
                    )

        consumed_amount = Money.of(consumed_total, original_amount.currency)
        remaining_amount = original_amount - consumed_amount

        return UnconsumedValue(
            artifact_ref=parent_ref,
            original_amount=original_amount,
            consumed_amount=consumed_amount,
            remaining_amount=remaining_amount,
            child_count=len(children),
        )

    def get_total_allocated(
        self,
        child_ref: ArtifactRef,
        link_types: frozenset[LinkType] | None = None,
        amount_metadata_key: str = "amount_applied",
    ) -> Decimal:
        """Calculate total amount allocated TO a child from all parents."""
        if link_types is None:
            link_types = frozenset({
                LinkType.ALLOCATED_TO,
                LinkType.APPLIED_TO,
            })

        parents = self.get_parents(child_ref, link_types)

        total = Decimal("0")
        for link in parents:
            if link.metadata and amount_metadata_key in link.metadata:
                try:
                    amount = Decimal(str(link.metadata[amount_metadata_key]))
                    total += amount
                except (ValueError, TypeError):
                    logger.debug(
                        "skipping_invalid_metadata",
                        extra={
                            "link_id": str(link.link_id),
                            "metadata_key": amount_metadata_key,
                            "raw_value": str(link.metadata.get(amount_metadata_key)),
                        },
                    )

        return total

    # =========================================================================
    # Reversal Support
    # =========================================================================

    def find_reversal(self, artifact_ref: ArtifactRef) -> EconomicLink | None:
        """Find the REVERSED_BY link for an artifact, if any."""
        children = self.get_children(artifact_ref, frozenset({LinkType.REVERSED_BY}))
        return children[0] if children else None

    def is_reversed(self, artifact_ref: ArtifactRef) -> bool:
        """Check if an artifact has been reversed."""
        return self.find_reversal(artifact_ref) is not None

    def find_correction(self, artifact_ref: ArtifactRef) -> EconomicLink | None:
        """Find the CORRECTED_BY link for an artifact, if any."""
        children = self.get_children(artifact_ref, frozenset({LinkType.CORRECTED_BY}))
        return children[0] if children else None

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _find_existing_link(self, link: EconomicLink) -> EconomicLinkModel | None:
        """Find existing link by relationship."""
        return self._find_existing_link_by_refs(
            link.parent_ref, link.child_ref, link.link_type
        )

    def _find_existing_link_by_refs(
        self,
        parent_ref: ArtifactRef,
        child_ref: ArtifactRef,
        link_type: LinkType,
    ) -> EconomicLinkModel | None:
        """Find existing link by refs and type."""
        return self.session.execute(
            select(EconomicLinkModel)
            .where(
                and_(
                    EconomicLinkModel.link_type == link_type.value,
                    EconomicLinkModel.parent_artifact_type == parent_ref.artifact_type.value,
                    EconomicLinkModel.parent_artifact_id == parent_ref.artifact_id,
                    EconomicLinkModel.child_artifact_type == child_ref.artifact_type.value,
                    EconomicLinkModel.child_artifact_id == child_ref.artifact_id,
                )
            )
        ).scalar_one_or_none()

    def _count_children(
        self,
        parent_ref: ArtifactRef,
        link_type: LinkType,
    ) -> int:
        """Count children for a parent with a specific link type."""
        result = self.session.execute(
            select(func.count())
            .select_from(EconomicLinkModel)
            .where(
                and_(
                    EconomicLinkModel.link_type == link_type.value,
                    EconomicLinkModel.parent_artifact_type == parent_ref.artifact_type.value,
                    EconomicLinkModel.parent_artifact_id == parent_ref.artifact_id,
                )
            )
        )
        return result.scalar() or 0

    def _detect_cycle(self, new_link: EconomicLink) -> list[ArtifactRef] | None:
        """Detect if adding a link would create a cycle via DFS."""
        visited: set[str] = set()
        path: list[ArtifactRef] = []

        def can_reach(
            current: ArtifactRef,
            target: ArtifactRef,
        ) -> bool:
            """DFS to check if current can reach target."""
            ref_str = str(current)
            if ref_str in visited:
                return False
            visited.add(ref_str)
            path.append(current)

            if current == target:
                return True

            # Get children of current with same link type
            children = self.get_children(current, frozenset({new_link.link_type}))
            for link in children:
                if can_reach(link.child_ref, target):
                    return True

            path.pop()
            return False

        # Check if child can reach parent (would create cycle)
        if can_reach(new_link.child_ref, new_link.parent_ref):
            path.append(new_link.parent_ref)
            return path

        return None

    def _fetch_reachable_links(self, query: LinkQuery) -> list[EconomicLinkModel]:
        """Fetch all links reachable from starting point using recursive CTE."""
        starting_ref = query.starting_ref
        direction = query.direction
        max_depth = query.max_depth
        link_types = query.link_types

        # Build base case: direct links from starting point
        if direction in ("children", "both"):
            children_base = (
                select(
                    EconomicLinkModel.id,
                    EconomicLinkModel.link_type,
                    EconomicLinkModel.parent_artifact_type,
                    EconomicLinkModel.parent_artifact_id,
                    EconomicLinkModel.child_artifact_type,
                    EconomicLinkModel.child_artifact_id,
                    EconomicLinkModel.creating_event_id,
                    EconomicLinkModel.created_at,
                    EconomicLinkModel.link_metadata,
                    literal(1).label("depth"),
                )
                .where(
                    and_(
                        EconomicLinkModel.parent_artifact_type == starting_ref.artifact_type.value,
                        EconomicLinkModel.parent_artifact_id == starting_ref.artifact_id,
                    )
                )
            )
            if link_types:
                children_base = children_base.where(
                    EconomicLinkModel.link_type.in_([lt.value for lt in link_types])
                )
        else:
            children_base = None

        if direction in ("parents", "both"):
            parents_base = (
                select(
                    EconomicLinkModel.id,
                    EconomicLinkModel.link_type,
                    EconomicLinkModel.parent_artifact_type,
                    EconomicLinkModel.parent_artifact_id,
                    EconomicLinkModel.child_artifact_type,
                    EconomicLinkModel.child_artifact_id,
                    EconomicLinkModel.creating_event_id,
                    EconomicLinkModel.created_at,
                    EconomicLinkModel.link_metadata,
                    literal(1).label("depth"),
                )
                .where(
                    and_(
                        EconomicLinkModel.child_artifact_type == starting_ref.artifact_type.value,
                        EconomicLinkModel.child_artifact_id == starting_ref.artifact_id,
                    )
                )
            )
            if link_types:
                parents_base = parents_base.where(
                    EconomicLinkModel.link_type.in_([lt.value for lt in link_types])
                )
        else:
            parents_base = None

        # Combine base cases
        if children_base is not None and parents_base is not None:
            base_query = union_all(children_base, parents_base)
        elif children_base is not None:
            base_query = children_base
        elif parents_base is not None:
            base_query = parents_base
        else:
            return []

        # For max_depth=1, just execute the base query
        if max_depth == 1:
            result = self.session.execute(
                select(EconomicLinkModel).where(
                    EconomicLinkModel.id.in_(
                        select(literal_column("id")).select_from(base_query.subquery())
                    )
                )
            )
            return list(result.scalars().all())

        # For deeper traversal, use iterative approach
        return self._iterative_traversal(query)

    def _iterative_traversal(self, query: LinkQuery) -> list[EconomicLinkModel]:
        """Iterative graph traversal for depth > 1."""
        visited_ids: set[UUID] = set()
        all_links: list[EconomicLinkModel] = []
        current_refs: set[str] = {str(query.starting_ref)}

        for depth in range(query.max_depth):
            # Collect all links at this depth
            depth_links: list[EconomicLinkModel] = []

            for ref_str in current_refs:
                ref = ArtifactRef.parse(ref_str)

                if query.direction in ("children", "both"):
                    children_query = (
                        select(EconomicLinkModel)
                        .where(
                            and_(
                                EconomicLinkModel.parent_artifact_type == ref.artifact_type.value,
                                EconomicLinkModel.parent_artifact_id == ref.artifact_id,
                                ~EconomicLinkModel.id.in_(visited_ids) if visited_ids else True,
                            )
                        )
                    )
                    if query.link_types:
                        children_query = children_query.where(
                            EconomicLinkModel.link_type.in_([lt.value for lt in query.link_types])
                        )
                    result = self.session.execute(children_query)
                    depth_links.extend(result.scalars().all())

                if query.direction in ("parents", "both"):
                    parents_query = (
                        select(EconomicLinkModel)
                        .where(
                            and_(
                                EconomicLinkModel.child_artifact_type == ref.artifact_type.value,
                                EconomicLinkModel.child_artifact_id == ref.artifact_id,
                                ~EconomicLinkModel.id.in_(visited_ids) if visited_ids else True,
                            )
                        )
                    )
                    if query.link_types:
                        parents_query = parents_query.where(
                            EconomicLinkModel.link_type.in_([lt.value for lt in query.link_types])
                        )
                    result = self.session.execute(parents_query)
                    depth_links.extend(result.scalars().all())

            if not depth_links:
                break

            # Update tracking
            for link in depth_links:
                if link.id not in visited_ids:
                    visited_ids.add(link.id)
                    all_links.append(link)

            # Prepare next iteration
            current_refs = set()
            for link in depth_links:
                if query.direction in ("children", "both"):
                    current_refs.add(link.child_ref_str)
                if query.direction in ("parents", "both"):
                    current_refs.add(link.parent_ref_str)

        return all_links

    def _build_paths(
        self,
        starting_ref: ArtifactRef,
        links: list[EconomicLinkModel],
        max_depth: int,
        direction: str = "children",
    ) -> list[LinkPath]:
        """Build LinkPath objects from traversal results."""
        if not links:
            return [LinkPath(artifacts=(starting_ref,), links=())]

        # Build adjacency maps for both directions
        children_map: dict[str, list[EconomicLinkModel]] = {}
        parents_map: dict[str, list[EconomicLinkModel]] = {}

        for link in links:
            parent_key = link.parent_ref_str
            if parent_key not in children_map:
                children_map[parent_key] = []
            children_map[parent_key].append(link)

            child_key = link.child_ref_str
            if child_key not in parents_map:
                parents_map[child_key] = []
            parents_map[child_key].append(link)

        # DFS to build paths
        paths: list[LinkPath] = []

        def build_path(
            current: ArtifactRef,
            path_artifacts: list[ArtifactRef],
            path_links: list[EconomicLink],
            depth: int,
        ) -> None:
            current_str = str(current)

            if direction == "children":
                adjacency_map = children_map
                get_next = lambda link: link.child_ref
            elif direction == "parents":
                adjacency_map = parents_map
                get_next = lambda link: link.parent_ref
            else:  # "both"
                adjacency_map = children_map
                get_next = lambda link: link.child_ref

            if depth >= max_depth or current_str not in adjacency_map:
                paths.append(LinkPath(
                    artifacts=tuple(path_artifacts),
                    links=tuple(path_links),
                ))
                return

            for orm_link in adjacency_map[current_str]:
                domain_link = orm_link.to_domain()
                next_ref = get_next(domain_link)
                new_artifacts = path_artifacts + [next_ref]
                new_links = path_links + [domain_link]
                build_path(
                    domain_link.child_ref,
                    new_artifacts,
                    new_links,
                    depth + 1,
                )

        build_path(starting_ref, [starting_ref], [], 0)

        return paths if paths else [LinkPath(artifacts=(starting_ref,), links=())]
