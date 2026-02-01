"""
Tests for CorrectionEngine - Recursive cascade unwind.

Tests cover:
- Unwind plan building
- Graph traversal
- Already corrected detection
- Cascade correction execution
- Depth limiting
- Blocked artifact handling
"""

from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from finance_engines.correction import (
    CompensatingEntry,
    CompensatingLine,
    CorrectionType,
    UnwindPlan,
    UnwindStrategy,
)
from finance_kernel.domain.economic_link import (
    ArtifactRef,
    ArtifactType,
    EconomicLink,
    LinkType,
)
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import (
    AlreadyCorrectedError,
    CorrectionCascadeBlockedError,
    UnwindDepthExceededError,
)
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_services.correction_service import CorrectionEngine


class TestUnwindPlanBuilding:
    """Tests for building unwind plans."""

    def test_build_plan_for_single_artifact(self, session: Session):
        """Building plan for artifact with no children."""
        link_graph = LinkGraphService(session)
        engine = CorrectionEngine(session, link_graph)

        po_ref = ArtifactRef.purchase_order(uuid4())

        plan = engine.build_unwind_plan(
            root_ref=po_ref,
            strategy=UnwindStrategy.CASCADE,
        )

        assert plan.root_ref == po_ref
        assert plan.artifact_count == 1
        assert plan.affected_artifacts[0].is_root
        assert plan.affected_artifacts[0].depth == 0

    def test_build_plan_traverses_downstream(self, session: Session):
        """Plan should include all downstream artifacts."""
        link_graph = LinkGraphService(session)
        engine = CorrectionEngine(session, link_graph)

        # Create chain: PO -> Receipt -> Invoice
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())
        invoice_ref = ArtifactRef.invoice(uuid4())

        # Create links
        link1 = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(UTC),
        )
        link_graph.establish_link(link1)

        link2 = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=receipt_ref,
            child_ref=invoice_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(UTC),
        )
        link_graph.establish_link(link2)

        plan = engine.build_unwind_plan(
            root_ref=po_ref,
            strategy=UnwindStrategy.CASCADE,
        )

        assert plan.artifact_count == 3
        assert plan.max_depth_reached == 2

        # Check depths
        depths = {str(a.ref): a.depth for a in plan.affected_artifacts}
        assert depths[str(po_ref)] == 0
        assert depths[str(receipt_ref)] == 1
        assert depths[str(invoice_ref)] == 2

    def test_dry_run_does_not_create_entries(self, session: Session):
        """Dry run should not generate compensating entries."""
        link_graph = LinkGraphService(session)
        engine = CorrectionEngine(session, link_graph)

        po_ref = ArtifactRef.purchase_order(uuid4())

        plan = engine.build_unwind_plan(
            root_ref=po_ref,
            strategy=UnwindStrategy.DRY_RUN,
        )

        assert plan.is_dry_run
        assert plan.entry_count == 0
        assert not plan.can_execute


class TestAlreadyCorrected:
    """Tests for already-corrected detection."""

    def test_already_corrected_error(self, session: Session):
        """Should raise error when root already corrected."""
        link_graph = LinkGraphService(session)
        engine = CorrectionEngine(session, link_graph)

        po_ref = ArtifactRef.purchase_order(uuid4())
        correction_ref = ArtifactRef.journal_entry(uuid4())

        # Mark as corrected
        correction_link = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.CORRECTED_BY,
            parent_ref=po_ref,
            child_ref=correction_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(UTC),
        )
        link_graph.establish_link(correction_link)

        with pytest.raises(AlreadyCorrectedError) as exc_info:
            engine.build_unwind_plan(root_ref=po_ref)

        assert exc_info.value.document_ref == str(po_ref)
        assert exc_info.value.correction_ref == str(correction_ref)

    def test_is_corrected_returns_true_when_corrected(self, session: Session):
        """is_corrected should return True for corrected artifacts."""
        link_graph = LinkGraphService(session)
        engine = CorrectionEngine(session, link_graph)

        po_ref = ArtifactRef.purchase_order(uuid4())
        correction_ref = ArtifactRef.journal_entry(uuid4())

        # Not corrected yet
        assert not engine.is_corrected(po_ref)

        # Mark as corrected
        correction_link = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.CORRECTED_BY,
            parent_ref=po_ref,
            child_ref=correction_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(UTC),
        )
        link_graph.establish_link(correction_link)

        # Now corrected
        assert engine.is_corrected(po_ref)


class TestDepthLimiting:
    """Tests for cascade depth limiting."""

    def test_depth_limit_prevents_runaway(self, session: Session):
        """Should stop at max depth and raise error if more exists."""
        link_graph = LinkGraphService(session)
        engine = CorrectionEngine(session, link_graph)

        # Create deep chain: A -> B -> C -> D -> E
        refs = [ArtifactRef.invoice(uuid4()) for _ in range(5)]

        for i in range(len(refs) - 1):
            link = EconomicLink.create(
                link_id=uuid4(),
                link_type=LinkType.DERIVED_FROM,
                parent_ref=refs[i],
                child_ref=refs[i + 1],
                creating_event_id=uuid4(),
                created_at=datetime.now(UTC),
            )
            link_graph.establish_link(link)

        # With max_depth=2, should raise error because there's more
        with pytest.raises(UnwindDepthExceededError) as exc_info:
            engine.build_unwind_plan(
                root_ref=refs[0],
                max_depth=2,
            )

        assert exc_info.value.max_depth == 2

    def test_plan_respects_max_depth_when_no_overflow(self, session: Session):
        """Should build plan up to max_depth when graph doesn't exceed it."""
        link_graph = LinkGraphService(session)
        engine = CorrectionEngine(session, link_graph)

        # Create chain: A -> B -> C
        refs = [ArtifactRef.invoice(uuid4()) for _ in range(3)]

        for i in range(len(refs) - 1):
            link = EconomicLink.create(
                link_id=uuid4(),
                link_type=LinkType.DERIVED_FROM,
                parent_ref=refs[i],
                child_ref=refs[i + 1],
                creating_event_id=uuid4(),
                created_at=datetime.now(UTC),
            )
            link_graph.establish_link(link)

        # With max_depth=5, should succeed (chain is only 3 deep)
        plan = engine.build_unwind_plan(
            root_ref=refs[0],
            max_depth=5,
        )

        assert plan.artifact_count == 3
        assert plan.max_depth_reached == 2


class TestBlockedArtifacts:
    """Tests for handling blocked artifacts in cascade."""

    def test_blocked_artifact_in_cascade(self, session: Session):
        """Already-corrected downstream should be marked as blocked."""
        link_graph = LinkGraphService(session)
        engine = CorrectionEngine(session, link_graph)

        # Chain: PO -> Receipt (corrected) -> Invoice
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())
        invoice_ref = ArtifactRef.invoice(uuid4())

        # Create chain
        link1 = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(UTC),
        )
        link_graph.establish_link(link1)

        link2 = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=receipt_ref,
            child_ref=invoice_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(UTC),
        )
        link_graph.establish_link(link2)

        # Mark receipt as already corrected
        correction_link = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.CORRECTED_BY,
            parent_ref=receipt_ref,
            child_ref=ArtifactRef.journal_entry(uuid4()),
            creating_event_id=uuid4(),
            created_at=datetime.now(UTC),
        )
        link_graph.establish_link(correction_link)

        # Build plan
        plan = engine.build_unwind_plan(
            root_ref=po_ref,
            strategy=UnwindStrategy.CASCADE,
        )

        # Receipt should be blocked
        receipt_artifact = next(
            a for a in plan.affected_artifacts if str(a.ref) == str(receipt_ref)
        )
        assert receipt_artifact.is_blocked
        assert not receipt_artifact.can_unwind
        assert plan.has_blocked_artifacts


class TestCorrectionExecution:
    """Tests for executing corrections."""

    def test_execute_creates_corrected_by_links(self, session: Session):
        """Execution should create CORRECTED_BY links for all artifacts."""
        link_graph = LinkGraphService(session)
        engine = CorrectionEngine(session, link_graph)

        # Simple chain: PO -> Receipt
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())

        link = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(UTC),
        )
        link_graph.establish_link(link)

        # Build and execute
        plan = engine.build_unwind_plan(root_ref=po_ref)
        event_id = uuid4()
        result = engine.execute_correction(
            plan=plan,
            actor_id="test-user",
            creating_event_id=event_id,
        )

        assert result.link_count == 2  # Both PO and Receipt corrected
        # artifacts_corrected only counts those with GL entries to reverse
        # Since we didn't add GL entries, check the plan instead
        assert result.plan.artifact_count == 2

        # Check links
        assert engine.is_corrected(po_ref)
        assert engine.is_corrected(receipt_ref)

    def test_void_document_convenience_method(self, session: Session):
        """void_document should build plan and execute."""
        link_graph = LinkGraphService(session)
        engine = CorrectionEngine(session, link_graph)

        po_ref = ArtifactRef.purchase_order(uuid4())

        result = engine.void_document(
            document_ref=po_ref,
            actor_id="test-user",
            creating_event_id=uuid4(),
        )

        assert result.plan.correction_type == CorrectionType.VOID
        assert engine.is_corrected(po_ref)

    def test_execute_dry_run_fails(self, session: Session):
        """Cannot execute a dry run plan."""
        link_graph = LinkGraphService(session)
        engine = CorrectionEngine(session, link_graph)

        po_ref = ArtifactRef.purchase_order(uuid4())

        plan = engine.build_unwind_plan(
            root_ref=po_ref,
            strategy=UnwindStrategy.DRY_RUN,
        )

        with pytest.raises(ValueError, match="Cannot execute a dry run"):
            engine.execute_correction(
                plan=plan,
                actor_id="test-user",
                creating_event_id=uuid4(),
            )


class TestCorrectionChain:
    """Tests for correction chain tracking."""

    def test_get_correction_chain_returns_history(self, session: Session):
        """Should return full correction history."""
        link_graph = LinkGraphService(session)
        engine = CorrectionEngine(session, link_graph)

        doc_ref = ArtifactRef.invoice(uuid4())
        correction1_ref = ArtifactRef.journal_entry(uuid4())
        correction2_ref = ArtifactRef.journal_entry(uuid4())

        # Create correction chain: doc -> correction1 -> correction2
        link1 = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.CORRECTED_BY,
            parent_ref=doc_ref,
            child_ref=correction1_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(UTC),
        )
        link_graph.establish_link(link1)

        link2 = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.CORRECTED_BY,
            parent_ref=correction1_ref,
            child_ref=correction2_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(UTC),
        )
        link_graph.establish_link(link2)

        chain = engine.get_correction_chain(doc_ref)

        assert len(chain) == 2
        assert chain[0].child_ref == correction1_ref
        assert chain[1].child_ref == correction2_ref


class TestCompensatingEntries:
    """Tests for compensating entry generation."""

    def test_compensating_entry_reverses_lines(self):
        """Compensating entry should flip debit/credit."""
        original_lines = [
            ("1100-CASH", Money.of("1000.00", "USD"), True, uuid4()),  # Debit
            ("4000-REVENUE", Money.of("1000.00", "USD"), False, uuid4()),  # Credit
        ]

        entry = CompensatingEntry.create_reversal(
            artifact_ref=ArtifactRef.invoice(uuid4()),
            original_entry_id=uuid4(),
            original_lines=original_lines,
            posting_date=date.today(),
            effective_date=date.today(),
        )

        assert entry.line_count == 2

        # Find the cash line
        cash_line = next(l for l in entry.lines if l.account_id == "1100-CASH")
        assert cash_line.is_credit  # Was debit, now credit

        # Find the revenue line
        revenue_line = next(l for l in entry.lines if l.account_id == "4000-REVENUE")
        assert revenue_line.is_debit  # Was credit, now debit

    def test_compensating_entry_must_balance(self):
        """Compensating entry must have balanced debits/credits."""
        # Unbalanced lines should fail
        unbalanced_lines = [
            ("1100-CASH", Money.of("1000.00", "USD"), True, uuid4()),
            ("4000-REVENUE", Money.of("900.00", "USD"), False, uuid4()),  # Wrong amount
        ]

        with pytest.raises(ValueError, match="unbalanced"):
            CompensatingEntry.create_reversal(
                artifact_ref=ArtifactRef.invoice(uuid4()),
                original_entry_id=uuid4(),
                original_lines=unbalanced_lines,
                posting_date=date.today(),
                effective_date=date.today(),
            )
