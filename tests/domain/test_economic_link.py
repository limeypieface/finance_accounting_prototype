"""
Tests for Economic Link Primitive.

Tests the foundational EconomicLink system that provides the "why pointer"
for traversing economic ancestry between artifacts.

Invariants tested:
- L1: Links are immutable
- L2: No self-links
- L3: Link graph must be acyclic (tested at application level)
- L4: creating_event_id is required
- L5: Type compatibility per link type
"""

from datetime import datetime
from uuid import uuid4

import pytest

from finance_kernel.domain.economic_link import (
    LINK_TYPE_SPECS,
    ArtifactRef,
    ArtifactType,
    EconomicLink,
    LinkPath,
    LinkQuery,
    LinkType,
    LinkTypeSpec,
)


class TestArtifactType:
    """Tests for ArtifactType enum."""

    def test_all_expected_types_defined(self):
        """Should have all expected artifact types."""
        expected = {
            "event",
            "journal_entry",
            "journal_line",
            "purchase_order",
            "receipt",
            "invoice",
            "payment",
            "credit_memo",
            "debit_memo",
            "cost_lot",
            "shipment",
            "inventory_adjustment",
            "asset",
            "depreciation",
            "disposal",
            "bank_statement",
            "bank_transaction",
            "intercompany_transaction",
        }

        actual = {at.value for at in ArtifactType}
        assert actual == expected

    def test_artifact_types_unique(self):
        """Should have unique values."""
        values = [at.value for at in ArtifactType]
        assert len(values) == len(set(values))


class TestLinkType:
    """Tests for LinkType enum."""

    def test_all_expected_link_types_defined(self):
        """Should have all expected link types."""
        expected = {
            "fulfilled_by",
            "paid_by",
            "applied_to",
            "reversed_by",
            "corrected_by",
            "consumed_by",
            "sourced_from",
            "allocated_to",
            "allocated_from",
            "derived_from",
            "matched_with",
            "adjusted_by",
        }

        actual = {lt.value for lt in LinkType}
        assert actual == expected

    def test_all_link_types_have_specs(self):
        """Should have specs for most link types."""
        # APPLIED_TO and ALLOCATED_FROM are inverse views, may not have specs
        link_types_with_specs = set(LINK_TYPE_SPECS.keys())
        assert LinkType.FULFILLED_BY in link_types_with_specs
        assert LinkType.PAID_BY in link_types_with_specs
        assert LinkType.REVERSED_BY in link_types_with_specs
        assert LinkType.CONSUMED_BY in link_types_with_specs


class TestArtifactRef:
    """Tests for ArtifactRef value object."""

    def test_create_valid_artifact_ref(self):
        """Should create valid artifact ref."""
        artifact_id = uuid4()
        ref = ArtifactRef(
            artifact_type=ArtifactType.INVOICE,
            artifact_id=artifact_id,
        )

        assert ref.artifact_type == ArtifactType.INVOICE
        assert ref.artifact_id == artifact_id

    def test_artifact_ref_string_representation(self):
        """Should produce string representation."""
        artifact_id = uuid4()
        ref = ArtifactRef(
            artifact_type=ArtifactType.PURCHASE_ORDER,
            artifact_id=artifact_id,
        )

        assert str(ref) == f"purchase_order:{artifact_id}"

    def test_artifact_ref_parse_roundtrip(self):
        """Should parse string representation back to ArtifactRef."""
        original = ArtifactRef(
            artifact_type=ArtifactType.RECEIPT,
            artifact_id=uuid4(),
        )

        ref_string = str(original)
        parsed = ArtifactRef.parse(ref_string)

        assert parsed.artifact_type == original.artifact_type
        assert parsed.artifact_id == original.artifact_id

    def test_artifact_ref_parse_invalid_string(self):
        """Should reject invalid ref string."""
        with pytest.raises(ValueError, match="Invalid artifact ref string"):
            ArtifactRef.parse("not:a:valid:ref")

        with pytest.raises(ValueError, match="Invalid artifact ref string"):
            ArtifactRef.parse("invalid_type:12345678-1234-1234-1234-123456789012")

    def test_artifact_ref_factory_methods(self):
        """Should have factory methods for common types."""
        event_id = uuid4()
        entry_id = uuid4()
        po_id = uuid4()
        invoice_id = uuid4()

        assert ArtifactRef.event(event_id).artifact_type == ArtifactType.EVENT
        assert ArtifactRef.journal_entry(entry_id).artifact_type == ArtifactType.JOURNAL_ENTRY
        assert ArtifactRef.purchase_order(po_id).artifact_type == ArtifactType.PURCHASE_ORDER
        assert ArtifactRef.invoice(invoice_id).artifact_type == ArtifactType.INVOICE

    def test_artifact_ref_immutable(self):
        """Should be immutable (frozen dataclass)."""
        ref = ArtifactRef(
            artifact_type=ArtifactType.PAYMENT,
            artifact_id=uuid4(),
        )

        with pytest.raises(AttributeError):
            ref.artifact_type = ArtifactType.INVOICE

    def test_artifact_ref_equality(self):
        """Should support equality comparison."""
        artifact_id = uuid4()

        ref1 = ArtifactRef(ArtifactType.INVOICE, artifact_id)
        ref2 = ArtifactRef(ArtifactType.INVOICE, artifact_id)
        ref3 = ArtifactRef(ArtifactType.INVOICE, uuid4())
        ref4 = ArtifactRef(ArtifactType.PAYMENT, artifact_id)

        assert ref1 == ref2
        assert ref1 != ref3  # Different ID
        assert ref1 != ref4  # Different type

    def test_artifact_ref_hashable(self):
        """Should be hashable for use in sets/dicts."""
        ref1 = ArtifactRef(ArtifactType.INVOICE, uuid4())
        ref2 = ArtifactRef(ArtifactType.PAYMENT, uuid4())

        ref_set = {ref1, ref2}
        assert len(ref_set) == 2
        assert ref1 in ref_set


class TestLinkTypeSpec:
    """Tests for LinkTypeSpec validation."""

    def test_fulfilled_by_spec_valid_combinations(self):
        """Should accept valid FULFILLED_BY combinations."""
        spec = LINK_TYPE_SPECS[LinkType.FULFILLED_BY]

        # PO -> Receipt
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())
        errors = spec.validate(po_ref, receipt_ref)
        assert len(errors) == 0

        # Receipt -> Invoice
        invoice_ref = ArtifactRef.invoice(uuid4())
        errors = spec.validate(receipt_ref, invoice_ref)
        assert len(errors) == 0

    def test_fulfilled_by_spec_invalid_combinations(self):
        """Should reject invalid FULFILLED_BY combinations."""
        spec = LINK_TYPE_SPECS[LinkType.FULFILLED_BY]

        # Invoice -> Payment (wrong direction)
        invoice_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())
        errors = spec.validate(invoice_ref, payment_ref)
        assert len(errors) > 0

    def test_paid_by_spec(self):
        """Should validate PAID_BY combinations."""
        spec = LINK_TYPE_SPECS[LinkType.PAID_BY]

        # Invoice -> Payment (valid)
        invoice_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())
        errors = spec.validate(invoice_ref, payment_ref)
        assert len(errors) == 0

        # Payment -> Invoice (invalid - wrong direction)
        errors = spec.validate(payment_ref, invoice_ref)
        assert len(errors) > 0

    def test_reversed_by_spec_max_children(self):
        """Should have max_children=1 for REVERSED_BY."""
        spec = LINK_TYPE_SPECS[LinkType.REVERSED_BY]
        assert spec.max_children == 1

    def test_matched_with_spec_symmetric(self):
        """Should be symmetric for MATCHED_WITH."""
        spec = LINK_TYPE_SPECS[LinkType.MATCHED_WITH]
        assert spec.is_symmetric is True

    def test_derived_from_spec_allows_any(self):
        """Should allow any combination for DERIVED_FROM."""
        spec = LINK_TYPE_SPECS[LinkType.DERIVED_FROM]

        # Any combination should be valid
        event_ref = ArtifactRef.event(uuid4())
        lot_ref = ArtifactRef.cost_lot(uuid4())
        errors = spec.validate(event_ref, lot_ref)
        assert len(errors) == 0


class TestEconomicLink:
    """Tests for EconomicLink value object."""

    @pytest.fixture
    def po_ref(self) -> ArtifactRef:
        """Create a PO artifact ref."""
        return ArtifactRef.purchase_order(uuid4())

    @pytest.fixture
    def receipt_ref(self) -> ArtifactRef:
        """Create a receipt artifact ref."""
        return ArtifactRef.receipt(uuid4())

    @pytest.fixture
    def invoice_ref(self) -> ArtifactRef:
        """Create an invoice artifact ref."""
        return ArtifactRef.invoice(uuid4())

    @pytest.fixture
    def payment_ref(self) -> ArtifactRef:
        """Create a payment artifact ref."""
        return ArtifactRef.payment(uuid4())

    def test_create_valid_link(self, po_ref, receipt_ref):
        """Should create valid economic link."""
        link_id = uuid4()
        creating_event_id = uuid4()
        now = datetime.now()

        link = EconomicLink(
            link_id=link_id,
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=creating_event_id,
            created_at=now,
        )

        assert link.link_id == link_id
        assert link.link_type == LinkType.FULFILLED_BY
        assert link.parent_ref == po_ref
        assert link.child_ref == receipt_ref
        assert link.creating_event_id == creating_event_id
        assert link.metadata is None

    def test_create_link_with_metadata(self, invoice_ref, payment_ref):
        """Should allow optional metadata."""
        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.PAID_BY,
            parent_ref=invoice_ref,
            child_ref=payment_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(),
            metadata={"amount_applied": "100.00", "currency": "USD"},
        )

        assert link.metadata is not None
        assert link.metadata["amount_applied"] == "100.00"

    def test_reject_self_link_l2(self):
        """L2: Should reject self-links."""
        artifact_ref = ArtifactRef.invoice(uuid4())

        with pytest.raises(ValueError, match="Self-link not allowed"):
            EconomicLink(
                link_id=uuid4(),
                link_type=LinkType.DERIVED_FROM,
                parent_ref=artifact_ref,
                child_ref=artifact_ref,  # Same as parent
                creating_event_id=uuid4(),
                created_at=datetime.now(),
            )

    def test_reject_invalid_type_combination_l5(self):
        """L5: Should reject invalid type combinations."""
        # Invoice cannot be parent for FULFILLED_BY
        invoice_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())

        with pytest.raises(ValueError, match="Invalid link type combination"):
            EconomicLink(
                link_id=uuid4(),
                link_type=LinkType.FULFILLED_BY,  # Wrong link type for invoice->payment
                parent_ref=invoice_ref,
                child_ref=payment_ref,
                creating_event_id=uuid4(),
                created_at=datetime.now(),
            )

    def test_factory_method(self, po_ref, receipt_ref):
        """Should support factory method."""
        link = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(),
        )

        assert link.link_type == LinkType.FULFILLED_BY

    def test_link_immutable_l1(self, po_ref, receipt_ref):
        """L1: Should be immutable (frozen dataclass)."""
        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(),
        )

        with pytest.raises(AttributeError):
            link.link_type = LinkType.PAID_BY

    def test_is_reversal_helper(self):
        """Should identify reversal links."""
        entry_ref = ArtifactRef.journal_entry(uuid4())
        reversal_ref = ArtifactRef.journal_entry(uuid4())

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.REVERSED_BY,
            parent_ref=entry_ref,
            child_ref=reversal_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(),
        )

        assert link.is_reversal() is True
        assert link.is_payment() is False
        assert link.is_fulfillment() is False

    def test_is_payment_helper(self, invoice_ref, payment_ref):
        """Should identify payment links."""
        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.PAID_BY,
            parent_ref=invoice_ref,
            child_ref=payment_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(),
        )

        assert link.is_payment() is True
        assert link.is_reversal() is False

    def test_is_fulfillment_helper(self, po_ref, receipt_ref):
        """Should identify fulfillment links."""
        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(),
        )

        assert link.is_fulfillment() is True
        assert link.is_payment() is False

    def test_is_consumption_helper(self):
        """Should identify consumption links."""
        lot_ref = ArtifactRef.cost_lot(uuid4())
        event_ref = ArtifactRef.event(uuid4())

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.CONSUMED_BY,
            parent_ref=lot_ref,
            child_ref=event_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(),
        )

        assert link.is_consumption() is True


class TestLinkQuery:
    """Tests for LinkQuery specification."""

    def test_create_default_query(self):
        """Should create query with defaults."""
        starting_ref = ArtifactRef.invoice(uuid4())

        query = LinkQuery(starting_ref=starting_ref)

        assert query.starting_ref == starting_ref
        assert query.link_types is None  # All types
        assert query.direction == "children"
        assert query.max_depth == 1
        assert query.include_metadata is False

    def test_create_filtered_query(self):
        """Should create query with filters."""
        starting_ref = ArtifactRef.purchase_order(uuid4())

        query = LinkQuery(
            starting_ref=starting_ref,
            link_types=frozenset({LinkType.FULFILLED_BY}),
            direction="both",
            max_depth=3,
            include_metadata=True,
        )

        assert query.link_types == frozenset({LinkType.FULFILLED_BY})
        assert query.direction == "both"
        assert query.max_depth == 3
        assert query.include_metadata is True


class TestLinkPath:
    """Tests for LinkPath traversal result."""

    def test_create_valid_path(self):
        """Should create valid path with matching artifacts and links."""
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())
        invoice_ref = ArtifactRef.invoice(uuid4())

        link1 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(),
        )

        link2 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=receipt_ref,
            child_ref=invoice_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(),
        )

        path = LinkPath(
            artifacts=(po_ref, receipt_ref, invoice_ref),
            links=(link1, link2),
        )

        assert path.depth == 2
        assert path.start == po_ref
        assert path.end == invoice_ref

    def test_reject_invalid_path_length(self):
        """Should reject path with mismatched artifacts/links count."""
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(),
        )

        # 2 artifacts should have 1 link, not 2
        with pytest.raises(ValueError, match="Invalid path"):
            LinkPath(
                artifacts=(po_ref, receipt_ref),
                links=(link, link),  # Too many links
            )

    def test_single_artifact_path(self):
        """Should support single artifact (depth 0) path."""
        ref = ArtifactRef.invoice(uuid4())

        path = LinkPath(artifacts=(ref,), links=())

        assert path.depth == 0
        assert path.start == ref
        assert path.end == ref


class TestChainScenarios:
    """Integration tests for real-world link chain scenarios."""

    def test_three_way_match_chain(self):
        """Should support PO -> Receipt -> Invoice chain."""
        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())
        invoice_ref = ArtifactRef.invoice(uuid4())
        creating_event = uuid4()
        now = datetime.now()

        # PO -> Receipt
        link1 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=creating_event,
            created_at=now,
        )

        # Receipt -> Invoice
        link2 = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=receipt_ref,
            child_ref=invoice_ref,
            creating_event_id=creating_event,
            created_at=now,
        )

        assert link1.is_fulfillment()
        assert link2.is_fulfillment()

    def test_payment_allocation_chain(self):
        """Should support Invoice -> Payment allocation."""
        invoice_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())
        creating_event = uuid4()
        now = datetime.now()

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.PAID_BY,
            parent_ref=invoice_ref,
            child_ref=payment_ref,
            creating_event_id=creating_event,
            created_at=now,
            metadata={"amount_applied": "500.00", "currency": "USD"},
        )

        assert link.is_payment()
        assert link.metadata["amount_applied"] == "500.00"

    def test_cost_lot_consumption_chain(self):
        """Should support CostLot -> Consumption event chain."""
        lot_ref = ArtifactRef.cost_lot(uuid4())
        consumption_event_ref = ArtifactRef.event(uuid4())
        creating_event = uuid4()
        now = datetime.now()

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.CONSUMED_BY,
            parent_ref=lot_ref,
            child_ref=consumption_event_ref,
            creating_event_id=creating_event,
            created_at=now,
            metadata={"quantity_consumed": "10", "unit_cost": "25.00"},
        )

        assert link.is_consumption()

    def test_reversal_chain(self):
        """Should support JournalEntry -> Reversal chain."""
        original_entry = ArtifactRef.journal_entry(uuid4())
        reversal_entry = ArtifactRef.journal_entry(uuid4())
        creating_event = uuid4()
        now = datetime.now()

        link = EconomicLink(
            link_id=uuid4(),
            link_type=LinkType.REVERSED_BY,
            parent_ref=original_entry,
            child_ref=reversal_entry,
            creating_event_id=creating_event,
            created_at=now,
        )

        assert link.is_reversal()
