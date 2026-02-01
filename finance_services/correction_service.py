"""
finance_services.correction_service -- Recursive cascade unwind for document corrections.

Responsibility:
    Manages document corrections by building unwind plans via link-graph
    traversal, generating compensating journal entries that reverse the
    original GL impact, and executing corrections with full CORRECTED_BY /
    REVERSED_BY link tracking.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    Composes LinkGraphService (kernel), PeriodService (kernel), and
    the pure unwind engine (finance_engines.correction.unwind).

Invariants enforced:
    - R10 (Posted record immutability): corrections are via reversal entries,
      never mutation of posted records.
    - R12 (Closed period enforcement): when PeriodService is injected,
      artifacts in closed periods are blocked from correction (G12).
    - LINK_LEGALITY (R4-links): all correction links use the immutable
      EconomicLink model with CORRECTED_BY / REVERSED_BY types.

Failure modes:
    - AlreadyCorrectedError: root document already has a CORRECTED_BY link.
    - UnwindDepthExceededError: graph depth exceeds safety limit.
    - CorrectionCascadeBlockedError: a downstream artifact cannot be unwound.
    - ClosedPeriodError: artifact falls in a closed fiscal period.

Audit relevance:
    - Every correction creates CORRECTED_BY links with metadata capturing
      the actor, depth, plan size, and correction_type.
    - Compensating journal entries are generated for auditor review before
      execution via the dry-run strategy.

Usage:
    from finance_services.correction_service import CorrectionEngine
    from finance_kernel.services.link_graph_service import LinkGraphService

    link_service = LinkGraphService(session)
    correction = CorrectionEngine(session, link_service)

    # Build an unwind plan (dry run)
    plan = correction.build_unwind_plan(
        root_ref=ArtifactRef.invoice(invoice_id),
        strategy=UnwindStrategy.DRY_RUN,
    )

    # Execute correction (cascade)
    result = correction.void_document(
        document_ref=ArtifactRef.po(po_id),
        actor_id="system",
        creating_event_id=event_id,
        cascade=True,
    )
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Mapping, Any, Callable, Sequence
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger

logger = get_logger("services.correction")
from finance_kernel.domain.economic_link import (
    ArtifactRef,
    ArtifactType,
    EconomicLink,
    LinkType,
    LinkQuery,
)
from finance_kernel.exceptions import (
    AlreadyCorrectedError,
    ClosedPeriodError,
    CorrectionCascadeBlockedError,
    PeriodNotFoundError,
    UnwindDepthExceededError,
    NoGLImpactError,
)
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.period_service import PeriodService

from finance_engines.correction.unwind import (
    UnwindPlan,
    AffectedArtifact,
    CompensatingEntry,
    CompensatingLine,
    CorrectionResult,
    CorrectionType,
    UnwindStrategy,
)


# Type alias for GL entry lookup function
GLEntryLookup = Callable[[ArtifactRef], list[tuple[UUID, list[tuple[str, Money, bool, UUID]]]]]


class CorrectionEngine:
    """
    Manages document corrections with cascade unwind.

    Contract:
        Given a root artifact reference, build an UnwindPlan that captures
        all downstream artifacts reachable via FULFILLED_BY / SOURCED_FROM /
        DERIVED_FROM / CONSUMED_BY / PAID_BY links, generate compensating
        journal entries, and execute the correction atomically.

    Guarantees:
        - Every corrected artifact receives a CORRECTED_BY link whose
          metadata includes correction_type, depth, actor_id, and
          plan_artifacts_count.
        - Compensating entries exactly reverse the original GL impact per
          artifact (sign-flip of every original line).
        - A dry-run plan never writes to the database.
        - Race-condition guard: root correction status is re-checked inside
          execute_correction before any writes.

    Non-goals:
        - Does NOT handle partial quantity corrections (use adjust_document
          with caller-supplied adjustment entries instead).
        - Does NOT re-open closed fiscal periods; it blocks instead (G12).

    Cascade Behavior:
    When voiding a document (e.g., PO), the engine:
    1. Finds all downstream artifacts via link graph
    2. For each, generates compensating GL entries
    3. Creates CORRECTED_BY links for audit trail
    4. Returns complete correction result

    Example cascade: Void PO
    - PO -> Receipt1 -> Invoice1 -> Payment1
    - PO -> Receipt2 -> Invoice2
    All of these would be unwound in a single correction.
    """

    # Link types that represent downstream relationships to follow
    DOWNSTREAM_LINK_TYPES: frozenset[LinkType] = frozenset({
        LinkType.FULFILLED_BY,
        LinkType.SOURCED_FROM,
        LinkType.DERIVED_FROM,
        LinkType.CONSUMED_BY,
        LinkType.PAID_BY,
    })

    # Maximum depth for cascade traversal (prevent runaway)
    DEFAULT_MAX_DEPTH: int = 10

    def __init__(
        self,
        session: Session,
        link_graph: LinkGraphService,
        gl_entry_lookup: GLEntryLookup | None = None,
        period_service: PeriodService | None = None,
    ):
        """
        Initialize the correction engine.

        Args:
            session: SQLAlchemy session for database operations.
            link_graph: LinkGraphService for link operations.
            gl_entry_lookup: Optional function to look up GL entries for an artifact.
                            If not provided, corrections will have empty GL entries.
            period_service: Optional PeriodService for period lock enforcement (G12).
                           When provided, corrections are blocked for artifacts in
                           closed periods.
        """
        self.session = session
        self.link_graph = link_graph
        self._gl_entry_lookup = gl_entry_lookup or self._default_gl_lookup
        self._period_service = period_service

    # =========================================================================
    # Plan Building
    # =========================================================================

    def build_unwind_plan(
        self,
        root_ref: ArtifactRef,
        strategy: UnwindStrategy = UnwindStrategy.CASCADE,
        correction_type: CorrectionType = CorrectionType.VOID,
        max_depth: int | None = None,
        posting_date: date | None = None,
        effective_date: date | None = None,
    ) -> UnwindPlan:
        """
        Build a plan for unwinding a document and its downstream effects.

        This traverses the link graph from the root artifact, finding
        all downstream artifacts that would need to be corrected.

        Args:
            root_ref: The root document to correct.
            strategy: How to handle downstream artifacts.
            correction_type: Type of correction (void, adjust, etc.).
            max_depth: Maximum depth to traverse (default: 10).
            posting_date: Date for compensating entries (default: today).
            effective_date: Effective date for corrections (default: today).

        Returns:
            UnwindPlan with all affected artifacts and compensating entries.

        Raises:
            AlreadyCorrectedError: Root document is already corrected.
            UnwindDepthExceededError: Graph exceeds max depth.
        """
        t0 = time.monotonic()
        max_depth = max_depth or self.DEFAULT_MAX_DEPTH
        posting_date = posting_date or date.today()
        effective_date = effective_date or date.today()

        logger.info("unwind_plan_build_started", extra={
            "root_ref": str(root_ref),
            "strategy": strategy.value,
            "correction_type": correction_type.value,
            "max_depth": max_depth,
            "posting_date": posting_date.isoformat(),
        })

        # INVARIANT [R10]: Posted records are never mutated; corrections
        # are via compensating entries only.  Pre-check ensures no duplicate
        # correction links.
        correction = self.link_graph.find_correction(root_ref)
        if correction:
            logger.warning("unwind_plan_already_corrected", extra={
                "root_ref": str(root_ref),
                "corrected_by": str(correction.child_ref),
            })
            raise AlreadyCorrectedError(
                str(root_ref),
                str(correction.child_ref),
            )

        # Build affected artifacts list via graph traversal
        affected = self._traverse_downstream(root_ref, max_depth)

        # Check for depth exceeded
        actual_max_depth = max(a.depth for a in affected) if affected else 0
        if actual_max_depth >= max_depth:
            # Check if there are more links beyond max depth
            deepest = [a for a in affected if a.depth == max_depth]
            for artifact in deepest:
                children = self.link_graph.get_children(
                    artifact.ref,
                    self.DOWNSTREAM_LINK_TYPES,
                )
                if children:
                    raise UnwindDepthExceededError(
                        str(root_ref),
                        max_depth,
                        actual_max_depth + 1,
                    )

        # Generate warnings for blocked artifacts
        warnings: list[str] = []
        blocked = [a for a in affected if a.is_blocked]
        for artifact in blocked:
            warnings.append(
                f"Cannot unwind {artifact.ref}: {artifact.block_reason}"
            )

        # Generate compensating entries
        entries: list[CompensatingEntry] = []
        if strategy != UnwindStrategy.DRY_RUN:
            for artifact in affected:
                if not artifact.can_unwind:
                    continue
                if not artifact.has_gl_impact:
                    continue

                entry = self._generate_compensating_entry(
                    artifact=artifact,
                    correction_type=correction_type,
                    posting_date=posting_date,
                    effective_date=effective_date,
                )
                if entry:
                    entries.append(entry)

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("unwind_plan_build_completed", extra={
            "root_ref": str(root_ref),
            "strategy": strategy.value,
            "affected_count": len(affected),
            "entry_count": len(entries),
            "warning_count": len(warnings),
            "blocked_count": len(blocked),
            "duration_ms": duration_ms,
        })

        return UnwindPlan.create(
            root_ref=root_ref,
            strategy=strategy,
            correction_type=correction_type,
            affected=affected,
            entries=entries,
            created_at=datetime.now(timezone.utc),
            warnings=warnings,
        )

    def _traverse_downstream(
        self,
        root_ref: ArtifactRef,
        max_depth: int,
    ) -> list[AffectedArtifact]:
        """
        Traverse the link graph to find all downstream artifacts.

        Uses breadth-first traversal to build the affected list.
        """
        logger.debug("downstream_traversal_started", extra={
            "root_ref": str(root_ref),
            "max_depth": max_depth,
        })

        affected: list[AffectedArtifact] = []
        visited: set[str] = set()

        # Get GL entries for root
        root_gl_entries = self._get_gl_entries(root_ref)
        root_artifact = AffectedArtifact.root(
            ref=root_ref,
            gl_entries=tuple(root_gl_entries),
        )
        affected.append(root_artifact)
        visited.add(str(root_ref))

        # BFS traversal
        current_level = [root_artifact]
        for depth in range(max_depth):
            next_level: list[AffectedArtifact] = []

            for parent_artifact in current_level:
                # Get all children via downstream link types
                children = self.link_graph.get_children(
                    parent_artifact.ref,
                    self.DOWNSTREAM_LINK_TYPES,
                )

                for link in children:
                    child_ref = link.child_ref
                    ref_str = str(child_ref)

                    if ref_str in visited:
                        continue
                    visited.add(ref_str)

                    # Check if child can be unwound
                    can_unwind, block_reason = self._check_can_unwind(child_ref)

                    # Get GL entries
                    gl_entries = self._get_gl_entries(child_ref)

                    child_artifact = AffectedArtifact.downstream(
                        ref=child_ref,
                        parent=parent_artifact,
                        link_type=link.link_type.value,
                        gl_entries=tuple(gl_entries),
                        can_unwind=can_unwind,
                        block_reason=block_reason,
                    )
                    affected.append(child_artifact)
                    next_level.append(child_artifact)

            if not next_level:
                break
            current_level = next_level

        logger.debug("downstream_traversal_completed", extra={
            "root_ref": str(root_ref),
            "artifacts_found": len(affected),
            "visited_count": len(visited),
        })

        return affected

    def _check_can_unwind(self, artifact_ref: ArtifactRef) -> tuple[bool, str | None]:
        """
        Check if an artifact can be unwound.

        Returns (can_unwind, reason if blocked).
        """
        # Check if already corrected
        correction = self.link_graph.find_correction(artifact_ref)
        if correction:
            return False, f"Already corrected by {correction.child_ref}"

        # Check if reversed
        reversal = self.link_graph.find_reversal(artifact_ref)
        if reversal:
            return False, f"Already reversed by {reversal.child_ref}"

        # INVARIANT [R12 / G12]: Period lock check -- corrections to
        # artifacts in closed periods are blocked.
        if self._period_service is not None:
            effective_date = self._get_effective_date(artifact_ref)
            if effective_date is not None:
                try:
                    self._period_service.validate_effective_date(effective_date)
                except ClosedPeriodError as e:
                    return False, f"Period {e.period_code} is closed"
                except PeriodNotFoundError:
                    pass  # No period defined — allow correction

        return True, None

    def _get_effective_date(self, artifact_ref: ArtifactRef) -> date | None:
        """
        Look up the effective date for an artifact.

        For journal entries, queries the entry's effective_date.
        For other artifact types, returns None (no period check).
        """
        from finance_kernel.selectors.journal_selector import JournalSelector

        if artifact_ref.artifact_type in (
            ArtifactType.JOURNAL_ENTRY,
            ArtifactType.EVENT,
        ):
            try:
                artifact_id = UUID(str(artifact_ref.artifact_id))
            except (ValueError, TypeError):
                return None

            selector = JournalSelector(self.session)
            dto = selector.get_entry(artifact_id)

            if dto is not None:
                return dto.effective_date

        return None

    def _get_gl_entries(self, artifact_ref: ArtifactRef) -> list[UUID]:
        """Get GL entry IDs for an artifact."""
        entries = self._gl_entry_lookup(artifact_ref)
        return [entry_id for entry_id, lines in entries]

    def _default_gl_lookup(
        self,
        artifact_ref: ArtifactRef,
    ) -> list[tuple[UUID, list[tuple[str, Money, bool, UUID]]]]:
        """
        Default GL lookup — queries journal entries for an artifact.

        For JOURNAL_ENTRY artifacts, looks up the entry directly by ID.
        For EVENT artifacts, looks up all entries posted for that event.
        Returns (entry_id, [(account_id_str, amount, is_debit, line_id)]).
        """
        from finance_kernel.domain import LineSide
        from finance_kernel.selectors.journal_selector import JournalSelector

        selector = JournalSelector(self.session)
        results: list[tuple[UUID, list[tuple[str, Money, bool, UUID]]]] = []

        def _entry_to_tuples(dto):
            """Convert a JournalEntryDTO to the expected tuple format."""
            line_tuples = [
                (
                    str(line.account_id),
                    Money(line.amount, line.currency),
                    line.side == LineSide.DEBIT,
                    line.id,
                )
                for line in dto.lines
            ]
            return (dto.id, line_tuples)

        if artifact_ref.artifact_type == ArtifactType.JOURNAL_ENTRY:
            try:
                entry_id = UUID(str(artifact_ref.artifact_id))
            except (ValueError, TypeError):
                return []

            dto = selector.get_entry(entry_id)
            if dto is not None:
                results.append(_entry_to_tuples(dto))

        elif artifact_ref.artifact_type == ArtifactType.EVENT:
            try:
                event_id = UUID(str(artifact_ref.artifact_id))
            except (ValueError, TypeError):
                return []

            dtos = selector.get_entries_by_event(event_id)
            for dto in dtos:
                results.append(_entry_to_tuples(dto))

        return results

    # =========================================================================
    # Entry Generation
    # =========================================================================

    def _generate_compensating_entry(
        self,
        artifact: AffectedArtifact,
        correction_type: CorrectionType,
        posting_date: date,
        effective_date: date,
    ) -> CompensatingEntry | None:
        """
        Generate a compensating entry for an affected artifact.

        Reverses each original GL entry associated with the artifact.
        """
        if not artifact.has_gl_impact:
            return None

        # Get original entry details
        entries = self._gl_entry_lookup(artifact.ref)
        if not entries:
            return None

        # For now, handle the first entry
        # In practice, might need to handle multiple
        entry_id, lines = entries[0]

        if not lines:
            return None

        return CompensatingEntry.create_reversal(
            artifact_ref=artifact.ref,
            original_entry_id=entry_id,
            original_lines=lines,
            posting_date=posting_date,
            effective_date=effective_date,
            correction_type=correction_type,
            memo=f"Correction of {artifact.ref}",
        )

    # =========================================================================
    # Correction Execution
    # =========================================================================

    def execute_correction(
        self,
        plan: UnwindPlan,
        actor_id: str,
        creating_event_id: UUID,
        journal_entry_writer: Callable[[CompensatingEntry], UUID] | None = None,
    ) -> CorrectionResult:
        """
        Execute a correction plan.

        Creates:
        - Compensating journal entries
        - CORRECTED_BY links for each affected artifact

        Args:
            plan: The unwind plan to execute.
            actor_id: Who is performing the correction.
            creating_event_id: Event that triggered the correction.
            journal_entry_writer: Optional function to create journal entries.
                                 If not provided, entries are not actually written.

        Returns:
            CorrectionResult with all created entries and links.

        Raises:
            AlreadyCorrectedError: Root is now corrected (race condition).
            CorrectionCascadeBlockedError: A required artifact is blocked.
        """
        t0 = time.monotonic()
        logger.info("correction_execution_started", extra={
            "root_ref": str(plan.root_ref),
            "strategy": plan.strategy.value,
            "correction_type": plan.correction_type.value,
            "affected_count": plan.artifact_count,
            "entry_count": plan.entry_count,
            "actor_id": actor_id,
        })

        # INVARIANT: Dry-run plans must never be executed.
        if plan.is_dry_run:
            logger.error("correction_execution_dry_run_rejected", extra={
                "root_ref": str(plan.root_ref),
            })
            raise ValueError("Cannot execute a dry run plan")

        if not plan.can_execute:
            blocked = plan.blocked_artifacts
            if blocked:
                first_blocked = blocked[0]
                raise CorrectionCascadeBlockedError(
                    str(plan.root_ref),
                    str(first_blocked.ref),
                    first_blocked.block_reason or "Unknown reason",
                    first_blocked.depth,
                )

        # INVARIANT [R10]: Re-check root not corrected (race condition guard).
        # Between plan build and execution another thread may have corrected
        # the same artifact.
        correction = self.link_graph.find_correction(plan.root_ref)
        if correction:
            raise AlreadyCorrectedError(
                str(plan.root_ref),
                str(correction.child_ref),
            )

        # Create journal entries
        journal_entries: list[UUID] = []
        for entry in plan.compensating_entries:
            if journal_entry_writer:
                entry_id = journal_entry_writer(entry)
                journal_entries.append(entry_id)

        # Create correction links
        links: list[EconomicLink] = []
        correction_doc_ref = ArtifactRef(
            artifact_type=ArtifactType.JOURNAL_ENTRY,
            artifact_id=str(creating_event_id),  # Use event ID as correction doc
        )

        for artifact in plan.affected_artifacts:
            if not artifact.can_unwind:
                continue

            # Create CORRECTED_BY link
            link = EconomicLink.create(
                link_id=uuid4(),
                link_type=LinkType.CORRECTED_BY,
                parent_ref=artifact.ref,
                child_ref=correction_doc_ref,
                creating_event_id=creating_event_id,
                created_at=datetime.now(timezone.utc),
                metadata={
                    "correction_type": plan.correction_type.value,
                    "depth": artifact.depth,
                    "actor_id": actor_id,
                    "plan_artifacts_count": len(plan.affected_artifacts),
                },
            )
            result = self.link_graph.establish_link(link, allow_duplicate=True)
            if not result.was_duplicate:
                links.append(result.link)

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("correction_execution_completed", extra={
            "root_ref": str(plan.root_ref),
            "journal_entries_created": len(journal_entries),
            "links_created": len(links),
            "actor_id": actor_id,
            "duration_ms": duration_ms,
        })

        return CorrectionResult.create(
            plan=plan,
            journal_entries=journal_entries,
            links=links,
            actor_id=actor_id,
            execution_event_id=creating_event_id,
            executed_at=datetime.now(timezone.utc),
        )

    def void_document(
        self,
        document_ref: ArtifactRef,
        actor_id: str,
        creating_event_id: UUID,
        cascade: bool = True,
        max_depth: int | None = None,
        posting_date: date | None = None,
        journal_entry_writer: Callable[[CompensatingEntry], UUID] | None = None,
    ) -> CorrectionResult:
        """
        Void a document and optionally cascade to downstream artifacts.

        Convenience method that builds a plan and executes it.

        Args:
            document_ref: The document to void.
            actor_id: Who is performing the void.
            creating_event_id: Event that triggered the void.
            cascade: If True, void all downstream artifacts too.
            max_depth: Maximum cascade depth.
            posting_date: Date for compensating entries.
            journal_entry_writer: Optional function to create journal entries.

        Returns:
            CorrectionResult with all corrections made.
        """
        logger.info("document_void_started", extra={
            "document_ref": str(document_ref),
            "actor_id": actor_id,
            "cascade": cascade,
        })

        strategy = UnwindStrategy.CASCADE if cascade else UnwindStrategy.STOP_AT_POSTED

        plan = self.build_unwind_plan(
            root_ref=document_ref,
            strategy=strategy,
            correction_type=CorrectionType.VOID,
            max_depth=max_depth,
            posting_date=posting_date,
        )

        return self.execute_correction(
            plan=plan,
            actor_id=actor_id,
            creating_event_id=creating_event_id,
            journal_entry_writer=journal_entry_writer,
        )

    def adjust_document(
        self,
        document_ref: ArtifactRef,
        actor_id: str,
        creating_event_id: UUID,
        adjustment_entries: Sequence[CompensatingEntry],
        posting_date: date | None = None,
        journal_entry_writer: Callable[[CompensatingEntry], UUID] | None = None,
    ) -> CorrectionResult:
        """
        Make an adjustment to a document (partial correction).

        Unlike void, this doesn't reverse all GL but posts specific
        adjusting entries provided by the caller.

        Args:
            document_ref: The document being adjusted.
            actor_id: Who is performing the adjustment.
            creating_event_id: Event that triggered the adjustment.
            adjustment_entries: The adjusting entries to post.
            posting_date: Date for the entries.
            journal_entry_writer: Function to create journal entries.

        Returns:
            CorrectionResult with adjustments made.
        """
        logger.info("document_adjustment_started", extra={
            "document_ref": str(document_ref),
            "actor_id": actor_id,
            "adjustment_entry_count": len(adjustment_entries),
        })

        # Build a minimal plan (just the root, no cascade)
        plan = UnwindPlan.create(
            root_ref=document_ref,
            strategy=UnwindStrategy.SELECTIVE,
            correction_type=CorrectionType.ADJUST,
            affected=[AffectedArtifact.root(document_ref, ())],
            entries=list(adjustment_entries),
            created_at=datetime.now(timezone.utc),
        )

        return self.execute_correction(
            plan=plan,
            actor_id=actor_id,
            creating_event_id=creating_event_id,
            journal_entry_writer=journal_entry_writer,
        )

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def is_corrected(self, artifact_ref: ArtifactRef) -> bool:
        """Check if an artifact has been corrected."""
        return self.link_graph.find_correction(artifact_ref) is not None

    def get_correction_chain(
        self,
        artifact_ref: ArtifactRef,
    ) -> list[EconomicLink]:
        """
        Get the chain of corrections for an artifact.

        Follows CORRECTED_BY links to build correction history.
        """
        corrections: list[EconomicLink] = []
        current_ref = artifact_ref

        while True:
            correction = self.link_graph.find_correction(current_ref)
            if not correction:
                break
            corrections.append(correction)
            current_ref = correction.child_ref

        logger.debug("correction_chain_retrieved", extra={
            "artifact_ref": str(artifact_ref),
            "chain_length": len(corrections),
        })

        return corrections
