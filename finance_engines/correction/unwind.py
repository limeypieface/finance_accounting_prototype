"""
Correction Engine Domain Objects.

Immutable value objects representing unwind plans, affected artifacts,
and correction results. These are pure domain objects with no I/O dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Mapping, Any, Sequence
from uuid import UUID

from finance_kernel.domain.values import Money
from finance_kernel.domain.economic_link import ArtifactRef, EconomicLink
from finance_kernel.logging_config import get_logger

logger = get_logger("engines.correction.unwind")


class CorrectionType(str, Enum):
    """Types of corrections."""

    VOID = "void"               # Cancel document completely
    ADJUST = "adjust"           # Partial adjustment
    RECLASS = "reclass"         # Account reclassification
    PERIOD_CORRECT = "period"   # Prior period correction


class UnwindStrategy(str, Enum):
    """How to handle downstream artifacts during unwind."""

    CASCADE = "cascade"           # Unwind all downstream automatically
    STOP_AT_POSTED = "stop"       # Stop at already-posted documents
    SELECTIVE = "selective"       # User picks which to unwind
    DRY_RUN = "dry_run"          # Calculate but don't execute


@dataclass(frozen=True, slots=True)
class CompensatingLine:
    """
    A single line in a compensating journal entry.

    Represents the reversal of an original posting line.
    """

    account_id: str
    amount: Money
    is_debit: bool
    original_line_id: UUID | None = None  # The line being reversed
    dimension_overrides: Mapping[str, str] | None = None  # Cost center, etc.
    memo: str | None = None

    @property
    def is_credit(self) -> bool:
        """True if this is a credit line."""
        return not self.is_debit

    @classmethod
    def reverse_line(
        cls,
        original_account_id: str,
        original_amount: Money,
        original_is_debit: bool,
        original_line_id: UUID,
        memo: str | None = None,
    ) -> CompensatingLine:
        """Create a line that reverses the original."""
        return cls(
            account_id=original_account_id,
            amount=original_amount,
            is_debit=not original_is_debit,  # Flip debit/credit
            original_line_id=original_line_id,
            memo=memo or f"Reversal of line {original_line_id}",
        )


@dataclass(frozen=True, slots=True)
class CompensatingEntry:
    """
    A journal entry that compensates (reverses) an original entry.

    Contains the lines needed to reverse the GL impact of an affected artifact.
    """

    artifact_ref: ArtifactRef       # What we're reversing
    original_entry_id: UUID         # The original journal entry
    lines: tuple[CompensatingLine, ...]
    posting_date: date
    effective_date: date
    memo: str
    correction_type: CorrectionType
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.lines:
            logger.error("compensating_entry_no_lines", extra={
                "artifact_ref": str(self.artifact_ref),
                "original_entry_id": str(self.original_entry_id),
            })
            raise ValueError("Compensating entry must have at least one line")
        # Validate debits = credits
        total_debits = sum(
            line.amount.amount for line in self.lines if line.is_debit
        )
        total_credits = sum(
            line.amount.amount for line in self.lines if line.is_credit
        )
        if total_debits != total_credits:
            logger.critical("compensating_entry_unbalanced", extra={
                "artifact_ref": str(self.artifact_ref),
                "original_entry_id": str(self.original_entry_id),
                "total_debits": str(total_debits),
                "total_credits": str(total_credits),
            })
            raise ValueError(
                f"Compensating entry unbalanced: debits={total_debits}, credits={total_credits}"
            )

    @property
    def total_amount(self) -> Money:
        """Total debit (or credit) amount."""
        total = sum(line.amount.amount for line in self.lines if line.is_debit)
        # Use currency from first line
        return Money.of(total, self.lines[0].amount.currency.code)

    @property
    def line_count(self) -> int:
        """Number of lines in entry."""
        return len(self.lines)

    @classmethod
    def create_reversal(
        cls,
        artifact_ref: ArtifactRef,
        original_entry_id: UUID,
        original_lines: Sequence[tuple[str, Money, bool, UUID]],  # account, amount, is_debit, line_id
        posting_date: date,
        effective_date: date,
        correction_type: CorrectionType = CorrectionType.VOID,
        memo: str | None = None,
    ) -> CompensatingEntry:
        """Create a reversal entry from original lines."""
        logger.info("compensating_reversal_created", extra={
            "artifact_ref": str(artifact_ref),
            "original_entry_id": str(original_entry_id),
            "original_line_count": len(original_lines),
            "correction_type": correction_type.value,
            "posting_date": posting_date.isoformat(),
        })

        reversed_lines = tuple(
            CompensatingLine.reverse_line(
                original_account_id=account_id,
                original_amount=amount,
                original_is_debit=is_debit,
                original_line_id=line_id,
            )
            for account_id, amount, is_debit, line_id in original_lines
        )

        return cls(
            artifact_ref=artifact_ref,
            original_entry_id=original_entry_id,
            lines=reversed_lines,
            posting_date=posting_date,
            effective_date=effective_date,
            memo=memo or f"Reversal of {artifact_ref}",
            correction_type=correction_type,
        )


@dataclass(frozen=True, slots=True)
class AffectedArtifact:
    """
    An artifact affected by a correction cascade.

    Tracks what needs to be unwound and its position in the graph.
    """

    ref: ArtifactRef
    depth: int                      # Distance from root (0 = root)
    path_from_root: tuple[ArtifactRef, ...]  # How we got here
    original_gl_entries: tuple[UUID, ...]    # Journal entries to reverse
    link_type_followed: str | None = None    # What link brought us here
    can_unwind: bool = True         # False if already corrected or locked
    block_reason: str | None = None  # Why can't unwind

    @property
    def is_root(self) -> bool:
        """True if this is the root artifact being corrected."""
        return self.depth == 0

    @property
    def has_gl_impact(self) -> bool:
        """True if this artifact has journal entries to reverse."""
        return len(self.original_gl_entries) > 0

    @property
    def is_blocked(self) -> bool:
        """True if artifact cannot be unwound."""
        return not self.can_unwind

    @classmethod
    def root(
        cls,
        ref: ArtifactRef,
        gl_entries: tuple[UUID, ...],
    ) -> AffectedArtifact:
        """Create the root artifact being corrected."""
        return cls(
            ref=ref,
            depth=0,
            path_from_root=(ref,),
            original_gl_entries=gl_entries,
            link_type_followed=None,
        )

    @classmethod
    def downstream(
        cls,
        ref: ArtifactRef,
        parent: AffectedArtifact,
        link_type: str,
        gl_entries: tuple[UUID, ...],
        can_unwind: bool = True,
        block_reason: str | None = None,
    ) -> AffectedArtifact:
        """Create a downstream affected artifact."""
        return cls(
            ref=ref,
            depth=parent.depth + 1,
            path_from_root=parent.path_from_root + (ref,),
            original_gl_entries=gl_entries,
            link_type_followed=link_type,
            can_unwind=can_unwind,
            block_reason=block_reason,
        )


@dataclass(frozen=True, slots=True)
class UnwindPlan:
    """
    Complete plan for unwinding a correction cascade.

    Contains all affected artifacts and the compensating entries needed.
    Built by traversing the EconomicLink graph from the root artifact.
    """

    root_ref: ArtifactRef
    strategy: UnwindStrategy
    correction_type: CorrectionType
    affected_artifacts: tuple[AffectedArtifact, ...]
    compensating_entries: tuple[CompensatingEntry, ...]
    created_at: datetime
    max_depth_reached: int
    warnings: tuple[str, ...] = ()
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.affected_artifacts:
            logger.error("unwind_plan_empty", extra={
                "root_ref": str(self.root_ref),
            })
            raise ValueError("Unwind plan must have at least one affected artifact")
        # Root should be first
        if self.affected_artifacts[0].ref != self.root_ref:
            logger.error("unwind_plan_root_mismatch", extra={
                "root_ref": str(self.root_ref),
                "first_artifact_ref": str(self.affected_artifacts[0].ref),
            })
            raise ValueError("First affected artifact must be the root")

    @property
    def artifact_count(self) -> int:
        """Total number of affected artifacts."""
        return len(self.affected_artifacts)

    @property
    def entry_count(self) -> int:
        """Total number of compensating entries needed."""
        return len(self.compensating_entries)

    @property
    def total_gl_reversals(self) -> int:
        """Total number of original GL entries being reversed."""
        return sum(
            len(a.original_gl_entries) for a in self.affected_artifacts
        )

    @property
    def blocked_artifacts(self) -> tuple[AffectedArtifact, ...]:
        """Artifacts that cannot be unwound."""
        return tuple(a for a in self.affected_artifacts if a.is_blocked)

    @property
    def has_blocked_artifacts(self) -> bool:
        """True if any artifacts are blocked from unwinding."""
        return len(self.blocked_artifacts) > 0

    @property
    def can_execute(self) -> bool:
        """True if plan can be executed (no critical blocks)."""
        # Can execute if strategy is cascade and no blocks, or selective
        if self.strategy == UnwindStrategy.DRY_RUN:
            return False
        if self.strategy == UnwindStrategy.CASCADE and self.has_blocked_artifacts:
            return False
        return True

    @property
    def is_dry_run(self) -> bool:
        """True if this is a dry run (analysis only)."""
        return self.strategy == UnwindStrategy.DRY_RUN

    def artifacts_at_depth(self, depth: int) -> tuple[AffectedArtifact, ...]:
        """Get all artifacts at a specific depth."""
        return tuple(a for a in self.affected_artifacts if a.depth == depth)

    @classmethod
    def create(
        cls,
        root_ref: ArtifactRef,
        strategy: UnwindStrategy,
        correction_type: CorrectionType,
        affected: list[AffectedArtifact],
        entries: list[CompensatingEntry],
        warnings: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> UnwindPlan:
        """Create an unwind plan."""
        max_depth = max(a.depth for a in affected) if affected else 0
        return cls(
            root_ref=root_ref,
            strategy=strategy,
            correction_type=correction_type,
            affected_artifacts=tuple(affected),
            compensating_entries=tuple(entries),
            created_at=datetime.now(timezone.utc),
            max_depth_reached=max_depth,
            warnings=tuple(warnings or []),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class CorrectionResult:
    """
    Result of executing a correction.

    Contains the executed plan plus all created journal entries and links.
    """

    plan: UnwindPlan
    journal_entries_created: tuple[UUID, ...]
    links_created: tuple[EconomicLink, ...]
    executed_at: datetime
    actor_id: str
    execution_event_id: UUID

    @property
    def entry_count(self) -> int:
        """Number of journal entries created."""
        return len(self.journal_entries_created)

    @property
    def link_count(self) -> int:
        """Number of links created."""
        return len(self.links_created)

    @property
    def artifacts_corrected(self) -> int:
        """Number of artifacts that were corrected."""
        return len([
            a for a in self.plan.affected_artifacts
            if a.can_unwind and a.has_gl_impact
        ])

    @classmethod
    def create(
        cls,
        plan: UnwindPlan,
        journal_entries: list[UUID],
        links: list[EconomicLink],
        actor_id: str,
        execution_event_id: UUID,
    ) -> CorrectionResult:
        """Create a correction result."""
        return cls(
            plan=plan,
            journal_entries_created=tuple(journal_entries),
            links_created=tuple(links),
            executed_at=datetime.now(timezone.utc),
            actor_id=actor_id,
            execution_event_id=execution_event_id,
        )
