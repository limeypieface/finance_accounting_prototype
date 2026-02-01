"""
Tests for LinkGraphService.

Tests the graph operations for EconomicLinks including:
- L3 (Acyclic) enforcement
- Graph traversal with walk_path
- Unconsumed value calculations
- Reversal and correction detection
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.economic_link import (
    ArtifactRef,
    ArtifactType,
    EconomicLink,
    LinkQuery,
    LinkType,
)
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import (
    DuplicateLinkError,
    LinkCycleError,
    MaxChildrenExceededError,
)
from finance_kernel.services.link_graph_service import (
    LinkEstablishResult,
    LinkGraphService,
    UnconsumedValue,
)


@pytest.fixture
def link_graph_service(session):
    """Provide a LinkGraphService instance."""
    return LinkGraphService(session)


@pytest.fixture
def creating_event_id():
    """Provide a consistent creating event ID."""
    return uuid4()


class TestEstablishLink:
    """Tests for establishing links."""

    def test_establish_valid_link(self, link_graph_service, creating_event_id):
        """Should establish a valid link."""
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )

        result = link_graph_service.establish_link(link)

        assert result.link == link
        assert result.was_duplicate is False

    def test_establish_link_with_metadata(self, link_graph_service, creating_event_id):
        """Should establish link with metadata."""
        invoice_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.PAID_BY,
            parent_ref=invoice_ref,
            child_ref=payment_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
            metadata={"amount_applied": "500.00", "currency": "USD"},
        )

        result = link_graph_service.establish_link(link)

        assert result.link.metadata["amount_applied"] == "500.00"

    def test_reject_duplicate_link(self, link_graph_service, creating_event_id):
        """Should reject duplicate links."""
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )

        # First link succeeds
        link_graph_service.establish_link(link)

        # Second link with same relationship fails
        duplicate = EconomicLink(
            link_id=uuid4(),  # Different ID
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )

        with pytest.raises(DuplicateLinkError):
            link_graph_service.establish_link(duplicate)

    def test_allow_duplicate_returns_existing(self, link_graph_service, creating_event_id):
        """Should return existing link when allow_duplicate=True."""
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())

        original = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )

        link_graph_service.establish_link(original)

        # Second link with allow_duplicate
        duplicate = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )

        result = link_graph_service.establish_link(duplicate, allow_duplicate=True)

        assert result.was_duplicate is True


class TestCycleDetection:
    """Tests for L3 (Acyclic) enforcement."""

    def test_detect_simple_cycle(self, link_graph_service, creating_event_id):
        """Should detect A -> B -> A cycle."""
        # Use events since DERIVED_FROM allows any type combination
        ref_a = ArtifactRef.event(uuid4())
        ref_b = ArtifactRef.event(uuid4())

        # Create A -> B with DERIVED_FROM (which is acyclic)
        link_ab = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.DERIVED_FROM,
            parent_ref=ref_a,
            child_ref=ref_b,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_ab)

        # Attempt B -> A with SAME link type (would create cycle)
        link_ba = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.DERIVED_FROM,  # Same link type as above
            parent_ref=ref_b,
            child_ref=ref_a,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )

        with pytest.raises(LinkCycleError):
            link_graph_service.establish_link(link_ba)

    def test_detect_transitive_cycle(self, link_graph_service, creating_event_id):
        """Should detect A -> B -> C -> A cycle."""
        ref_a = ArtifactRef.event(uuid4())
        ref_b = ArtifactRef.event(uuid4())
        ref_c = ArtifactRef.event(uuid4())

        # Create A -> B
        link_ab = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.DERIVED_FROM,
            parent_ref=ref_a,
            child_ref=ref_b,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_ab)

        # Create B -> C
        link_bc = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.DERIVED_FROM,
            parent_ref=ref_b,
            child_ref=ref_c,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_bc)

        # Attempt C -> A (would create cycle)
        link_ca = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.DERIVED_FROM,
            parent_ref=ref_c,
            child_ref=ref_a,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )

        with pytest.raises(LinkCycleError):
            link_graph_service.establish_link(link_ca)

    def test_allow_non_acyclic_link_type(self, link_graph_service, creating_event_id):
        """Should allow cycles for non-acyclic link types like MATCHED_WITH."""
        ref_a = ArtifactRef.invoice(uuid4())
        ref_b = ArtifactRef.invoice(uuid4())

        # MATCHED_WITH is symmetric and doesn't require acyclicity
        # Create A matched with B
        link_ab = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.MATCHED_WITH,
            parent_ref=ref_a,
            child_ref=ref_b,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_ab)

        # Create B matched with A (not a cycle concern for MATCHED_WITH)
        link_ba = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.MATCHED_WITH,
            parent_ref=ref_b,
            child_ref=ref_a,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )

        # This should NOT raise - MATCHED_WITH is not in ACYCLIC_LINK_TYPES
        result = link_graph_service.establish_link(link_ba)
        assert result.was_duplicate is False


class TestMaxChildrenConstraint:
    """Tests for max children constraint (e.g., REVERSED_BY has max=1)."""

    def test_enforce_max_children_on_reversed_by(self, link_graph_service, creating_event_id):
        """Should enforce max_children=1 for REVERSED_BY."""
        original_entry = ArtifactRef.journal_entry(uuid4())
        reversal_1 = ArtifactRef.journal_entry(uuid4())
        reversal_2 = ArtifactRef.journal_entry(uuid4())

        # First reversal succeeds
        link_1 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.REVERSED_BY,
            parent_ref=original_entry,
            child_ref=reversal_1,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_1)

        # Second reversal fails
        link_2 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.REVERSED_BY,
            parent_ref=original_entry,
            child_ref=reversal_2,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )

        with pytest.raises(MaxChildrenExceededError) as exc_info:
            link_graph_service.establish_link(link_2)

        assert exc_info.value.max_children == 1
        assert exc_info.value.current_children == 1


class TestGetChildrenAndParents:
    """Tests for getting direct children and parents."""

    def test_get_children(self, link_graph_service, creating_event_id):
        """Should get all direct children."""
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_1 = ArtifactRef.receipt(uuid4())
        receipt_2 = ArtifactRef.receipt(uuid4())

        # Create PO -> Receipt1
        link_1 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_1,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_1)

        # Create PO -> Receipt2
        link_2 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_2,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_2)

        children = link_graph_service.get_children(po_ref)

        assert len(children) == 2
        child_refs = {link.child_ref for link in children}
        assert receipt_1 in child_refs
        assert receipt_2 in child_refs

    def test_get_children_filtered_by_type(self, link_graph_service, creating_event_id):
        """Should filter children by link type."""
        invoice_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())
        credit_memo_ref = ArtifactRef.credit_memo(uuid4())

        # Create Invoice -> Payment (PAID_BY)
        link_1 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.PAID_BY,
            parent_ref=invoice_ref,
            child_ref=payment_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_1)

        # Create Invoice -> CreditMemo (CORRECTED_BY)
        link_2 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.CORRECTED_BY,
            parent_ref=invoice_ref,
            child_ref=credit_memo_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_2)

        # Get only PAID_BY children
        paid_children = link_graph_service.get_children(
            invoice_ref,
            link_types=frozenset({LinkType.PAID_BY}),
        )

        assert len(paid_children) == 1
        assert paid_children[0].child_ref == payment_ref

    def test_get_parents(self, link_graph_service, creating_event_id):
        """Should get all direct parents."""
        receipt_ref = ArtifactRef.receipt(uuid4())
        po_1 = ArtifactRef.purchase_order(uuid4())
        po_2 = ArtifactRef.purchase_order(uuid4())

        # Create PO1 -> Receipt
        link_1 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_1,
            child_ref=receipt_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_1)

        # Create PO2 -> Receipt
        link_2 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_2,
            child_ref=receipt_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_2)

        parents = link_graph_service.get_parents(receipt_ref)

        assert len(parents) == 2
        parent_refs = {link.parent_ref for link in parents}
        assert po_1 in parent_refs
        assert po_2 in parent_refs


class TestWalkPath:
    """Tests for graph traversal."""

    def test_walk_path_single_depth(self, link_graph_service, creating_event_id):
        """Should traverse one level deep."""
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link)

        query = LinkQuery(starting_ref=po_ref, max_depth=1)
        paths = link_graph_service.walk_path(query)

        assert len(paths) == 1
        assert paths[0].depth == 1
        assert paths[0].start == po_ref
        assert paths[0].end == receipt_ref

    def test_walk_path_multi_depth(self, link_graph_service, creating_event_id):
        """Should traverse multiple levels."""
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())
        invoice_ref = ArtifactRef.invoice(uuid4())

        # PO -> Receipt
        link_1 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_1)

        # Receipt -> Invoice
        link_2 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=receipt_ref,
            child_ref=invoice_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link_2)

        query = LinkQuery(starting_ref=po_ref, max_depth=2)
        paths = link_graph_service.walk_path(query)

        # Should have path to invoice
        end_refs = {path.end for path in paths}
        assert invoice_ref in end_refs

    def test_walk_path_parents_direction(self, link_graph_service, creating_event_id):
        """Should traverse up to parents."""
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link)

        query = LinkQuery(starting_ref=receipt_ref, direction="parents", max_depth=1)
        paths = link_graph_service.walk_path(query)

        assert len(paths) >= 1
        # One path should end at PO
        end_refs = {path.end for path in paths}
        assert po_ref in end_refs


class TestUnconsumedValue:
    """Tests for unconsumed value calculation."""

    def test_fully_unconsumed(self, link_graph_service, creating_event_id):
        """Should show full amount when no children."""
        invoice_ref = ArtifactRef.invoice(uuid4())
        original_amount = Money.of("1000.00", "USD")

        result = link_graph_service.get_unconsumed_value(
            invoice_ref, original_amount
        )

        assert result.original_amount == original_amount
        assert result.consumed_amount.amount == Decimal("0")
        assert result.remaining_amount == original_amount
        assert result.child_count == 0
        assert not result.is_fully_consumed

    def test_partially_consumed(self, link_graph_service, creating_event_id):
        """Should calculate partial consumption."""
        invoice_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())
        original_amount = Money.of("1000.00", "USD")

        # Create partial payment link
        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.PAID_BY,
            parent_ref=invoice_ref,
            child_ref=payment_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
            metadata={"amount_applied": "400.00"},
        )
        link_graph_service.establish_link(link)

        result = link_graph_service.get_unconsumed_value(
            invoice_ref, original_amount
        )

        assert result.consumed_amount.amount == Decimal("400.00")
        assert result.remaining_amount.amount == Decimal("600.00")
        assert result.child_count == 1
        assert result.consumption_percentage == Decimal("40")

    def test_fully_consumed(self, link_graph_service, creating_event_id):
        """Should show fully consumed when payments equal invoice."""
        invoice_ref = ArtifactRef.invoice(uuid4())
        payment_1 = ArtifactRef.payment(uuid4())
        payment_2 = ArtifactRef.payment(uuid4())
        original_amount = Money.of("1000.00", "USD")

        # Create two payment links totaling the invoice
        link_1 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.PAID_BY,
            parent_ref=invoice_ref,
            child_ref=payment_1,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
            metadata={"amount_applied": "600.00"},
        )
        link_graph_service.establish_link(link_1)

        link_2 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.PAID_BY,
            parent_ref=invoice_ref,
            child_ref=payment_2,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
            metadata={"amount_applied": "400.00"},
        )
        link_graph_service.establish_link(link_2)

        result = link_graph_service.get_unconsumed_value(
            invoice_ref, original_amount
        )

        assert result.consumed_amount.amount == Decimal("1000.00")
        assert result.remaining_amount.is_zero
        assert result.child_count == 2
        assert result.is_fully_consumed


class TestReversalDetection:
    """Tests for reversal and correction detection."""

    def test_find_reversal(self, link_graph_service, creating_event_id):
        """Should find reversal link."""
        original_entry = ArtifactRef.journal_entry(uuid4())
        reversal_entry = ArtifactRef.journal_entry(uuid4())

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.REVERSED_BY,
            parent_ref=original_entry,
            child_ref=reversal_entry,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link)

        reversal = link_graph_service.find_reversal(original_entry)

        assert reversal is not None
        assert reversal.child_ref == reversal_entry

    def test_is_reversed(self, link_graph_service, creating_event_id):
        """Should detect if artifact is reversed."""
        original_entry = ArtifactRef.journal_entry(uuid4())
        reversal_entry = ArtifactRef.journal_entry(uuid4())

        # Before reversal
        assert link_graph_service.is_reversed(original_entry) is False

        # Create reversal
        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.REVERSED_BY,
            parent_ref=original_entry,
            child_ref=reversal_entry,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link)

        # After reversal
        assert link_graph_service.is_reversed(original_entry) is True

    def test_find_correction(self, link_graph_service, creating_event_id):
        """Should find correction link."""
        original_invoice = ArtifactRef.invoice(uuid4())
        corrected_invoice = ArtifactRef.invoice(uuid4())

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.CORRECTED_BY,
            parent_ref=original_invoice,
            child_ref=corrected_invoice,
            creating_event_id=creating_event_id,
            created_at=datetime.now(),
        )
        link_graph_service.establish_link(link)

        correction = link_graph_service.find_correction(original_invoice)

        assert correction is not None
        assert correction.child_ref == corrected_invoice


class TestEstablishMultipleLinks:
    """Tests for establishing multiple links at once."""

    def test_establish_multiple_links(self, link_graph_service, creating_event_id):
        """Should establish multiple links atomically."""
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_1 = ArtifactRef.receipt(uuid4())
        receipt_2 = ArtifactRef.receipt(uuid4())

        links = [
            EconomicLink(
                link_id=uuid4(),
                link_type=LinkType.FULFILLED_BY,
                parent_ref=po_ref,
                child_ref=receipt_1,
                creating_event_id=creating_event_id,
                created_at=datetime.now(),
            ),
            EconomicLink(
                link_id=uuid4(),
                link_type=LinkType.FULFILLED_BY,
                parent_ref=po_ref,
                child_ref=receipt_2,
                creating_event_id=creating_event_id,
                created_at=datetime.now(),
            ),
        ]

        results = link_graph_service.establish_links(links)

        assert len(results) == 2
        assert all(not r.was_duplicate for r in results)

        # Verify persisted
        children = link_graph_service.get_children(po_ref)
        assert len(children) == 2
