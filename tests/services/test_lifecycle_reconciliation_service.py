"""
Tests for LifecycleReconciliationService -- GAP-REC Phase 4.

Covers chain building from DB, journal entry R21 metadata population,
end-to-end check_artifact and check_artifacts, and edge cases.

Uses PostgreSQL via the standard session fixture with automatic rollback.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.domain.economic_link import (
    ArtifactRef,
    ArtifactType,
    EconomicLink,
    LinkType,
)
from finance_kernel.domain.values import Money
from finance_kernel.models.event import Event
from finance_kernel.models.journal import (
    JournalEntry,
    JournalEntryStatus,
)
from finance_kernel.services.link_graph_service import LinkGraphService

from finance_engines.reconciliation.lifecycle_types import (
    CheckStatus,
    LifecycleChain,
)
from finance_services.lifecycle_reconciliation_service import (
    LifecycleReconciliationService,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_event(
    session: Session,
    event_id,
    actor_id,
    clock: DeterministicClock,
    effective_date=None,
    event_type="test.event",
):
    """Create a source Event row required by JournalEntry FK."""
    from finance_kernel.utils.hashing import hash_payload

    effective = effective_date or clock.now().date()
    payload = {"test": "data"}
    evt = Event(
        event_id=event_id,
        event_type=event_type,
        occurred_at=clock.now(),
        effective_date=effective,
        actor_id=actor_id,
        producer="test",
        payload=payload,
        payload_hash=hash_payload(payload),
        schema_version=1,
        ingested_at=clock.now(),
    )
    session.add(evt)
    session.flush()
    return evt


def _make_posted_entry(
    session: Session,
    source_event_id,
    actor_id,
    clock: DeterministicClock,
    *,
    effective_date=None,
    event_type="test.event",
    coa_version=1,
    dimension_schema_version=1,
    rounding_policy_version=1,
    currency_registry_version=1,
    posting_rule_version=1,
):
    """Create a posted JournalEntry with R21 columns populated."""
    effective = effective_date or clock.now().date()
    entry = JournalEntry(
        source_event_id=source_event_id,
        source_event_type=event_type,
        occurred_at=clock.now(),
        effective_date=effective,
        posted_at=clock.now(),
        actor_id=actor_id,
        status=JournalEntryStatus.POSTED,
        idempotency_key=f"test:{event_type}:{source_event_id}",
        posting_rule_version=posting_rule_version,
        coa_version=coa_version,
        dimension_schema_version=dimension_schema_version,
        rounding_policy_version=rounding_policy_version,
        currency_registry_version=currency_registry_version,
        created_by_id=actor_id,
    )
    session.add(entry)
    session.flush()
    return entry


def _make_link(
    session: Session,
    link_graph: LinkGraphService,
    link_type: LinkType,
    parent_ref: ArtifactRef,
    child_ref: ArtifactRef,
    creating_event_id=None,
    metadata=None,
):
    """Create an economic link via LinkGraphService."""
    link = EconomicLink(
        link_id=uuid4(),
        link_type=link_type,
        parent_ref=parent_ref,
        child_ref=child_ref,
        creating_event_id=creating_event_id or uuid4(),
        created_at=datetime.now(UTC),
        metadata=metadata,
    )
    result = link_graph.establish_link(link)
    return result.link


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def link_graph(session):
    return LinkGraphService(session)


@pytest.fixture
def clock():
    return DeterministicClock(datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def service(session, link_graph, clock):
    return LifecycleReconciliationService(
        session=session,
        link_graph=link_graph,
        clock=clock,
    )


# =============================================================================
# Chain Building
# =============================================================================


class TestBuildChain:
    """Tests for build_chain: walks the link graph and populates nodes."""

    def test_single_node_chain(self, session, service, clock, test_actor_id):
        """A root with no children yields a chain with one node."""
        po_id = uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)

        _make_event(session, po_id, test_actor_id, clock)
        _make_posted_entry(session, po_id, test_actor_id, clock)

        chain = service.build_chain(po_ref)

        assert chain.root_ref == po_ref
        assert chain.node_count == 1
        assert chain.edge_count == 0
        node = chain.get_node(po_ref)
        assert node is not None
        assert node.has_journal_entry
        assert node.coa_version == 1

    def test_two_node_chain(self, session, service, link_graph, clock, test_actor_id):
        """PO -> Receipt chain builds two nodes and one edge."""
        po_id = uuid4()
        receipt_id = uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)
        receipt_ref = ArtifactRef.receipt(receipt_id)

        # Create events + entries
        _make_event(session, po_id, test_actor_id, clock)
        _make_posted_entry(session, po_id, test_actor_id, clock)
        _make_event(session, receipt_id, test_actor_id, clock, event_type="test.receipt")
        _make_posted_entry(
            session, receipt_id, test_actor_id, clock,
            event_type="test.receipt",
        )

        # Link PO -> Receipt
        _make_link(session, link_graph, LinkType.FULFILLED_BY, po_ref, receipt_ref)

        chain = service.build_chain(po_ref)

        assert chain.node_count == 2
        assert chain.edge_count == 1
        assert chain.get_node(po_ref) is not None
        assert chain.get_node(receipt_ref) is not None

    def test_three_node_chain(self, session, service, link_graph, clock, test_actor_id):
        """PO -> Receipt -> Invoice builds full chain."""
        po_id, receipt_id, invoice_id = uuid4(), uuid4(), uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)
        receipt_ref = ArtifactRef.receipt(receipt_id)
        invoice_ref = ArtifactRef.invoice(invoice_id)

        for eid, etype in [
            (po_id, "test.po"),
            (receipt_id, "test.receipt"),
            (invoice_id, "test.invoice"),
        ]:
            _make_event(session, eid, test_actor_id, clock, event_type=etype)
            _make_posted_entry(session, eid, test_actor_id, clock, event_type=etype)

        _make_link(session, link_graph, LinkType.FULFILLED_BY, po_ref, receipt_ref)
        _make_link(session, link_graph, LinkType.FULFILLED_BY, receipt_ref, invoice_ref)

        chain = service.build_chain(po_ref)

        assert chain.node_count == 3
        assert chain.edge_count == 2

    def test_node_without_journal_entry(self, session, service, link_graph, clock, test_actor_id):
        """Orphaned child (no journal entry) still appears as a node."""
        po_id = uuid4()
        receipt_id = uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)
        receipt_ref = ArtifactRef.receipt(receipt_id)

        # Only the PO has an event + entry; receipt has nothing
        _make_event(session, po_id, test_actor_id, clock)
        _make_posted_entry(session, po_id, test_actor_id, clock)

        _make_link(session, link_graph, LinkType.FULFILLED_BY, po_ref, receipt_ref)

        chain = service.build_chain(po_ref)

        assert chain.node_count == 2
        receipt_node = chain.get_node(receipt_ref)
        assert receipt_node is not None
        assert not receipt_node.has_journal_entry
        assert receipt_node.coa_version is None

    def test_r21_metadata_populated(self, session, service, clock, test_actor_id):
        """Node correctly captures R21 columns from journal entry."""
        po_id = uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)

        _make_event(session, po_id, test_actor_id, clock)
        _make_posted_entry(
            session, po_id, test_actor_id, clock,
            coa_version=3,
            dimension_schema_version=2,
            rounding_policy_version=1,
            currency_registry_version=4,
            posting_rule_version=5,
        )

        chain = service.build_chain(po_ref)
        node = chain.get_node(po_ref)

        assert node.coa_version == 3
        assert node.dimension_schema_version == 2
        assert node.rounding_policy_version == 1
        assert node.currency_registry_version == 4
        assert node.posting_rule_version == 5

    def test_edge_amount_from_metadata(self, session, service, link_graph, clock, test_actor_id):
        """Edge link_amount is extracted from link metadata."""
        po_id, receipt_id = uuid4(), uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)
        receipt_ref = ArtifactRef.receipt(receipt_id)

        for eid, etype in [(po_id, "test.po"), (receipt_id, "test.receipt")]:
            _make_event(session, eid, test_actor_id, clock, event_type=etype)
            _make_posted_entry(session, eid, test_actor_id, clock, event_type=etype)

        _make_link(
            session, link_graph, LinkType.FULFILLED_BY, po_ref, receipt_ref,
            metadata={"amount_applied": "5000.00", "currency": "USD"},
        )

        chain = service.build_chain(po_ref)

        assert chain.edge_count == 1
        edge = chain.edges[0]
        assert edge.link_amount is not None
        assert edge.link_amount == Money.of("5000.00", "USD")

    def test_edge_without_amount_metadata(self, session, service, link_graph, clock, test_actor_id):
        """Edge with no amount in metadata yields link_amount=None."""
        po_id, receipt_id = uuid4(), uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)
        receipt_ref = ArtifactRef.receipt(receipt_id)

        for eid, etype in [(po_id, "test.po"), (receipt_id, "test.receipt")]:
            _make_event(session, eid, test_actor_id, clock, event_type=etype)
            _make_posted_entry(session, eid, test_actor_id, clock, event_type=etype)

        _make_link(session, link_graph, LinkType.FULFILLED_BY, po_ref, receipt_ref)

        chain = service.build_chain(po_ref)
        assert chain.edges[0].link_amount is None

    def test_branching_chain(self, session, service, link_graph, clock, test_actor_id):
        """PO with two receipts builds branching chain."""
        po_id, r1_id, r2_id = uuid4(), uuid4(), uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)
        r1_ref = ArtifactRef.receipt(r1_id)
        r2_ref = ArtifactRef.receipt(r2_id)

        for eid, etype in [
            (po_id, "test.po"),
            (r1_id, "test.receipt"),
            (r2_id, "test.receipt2"),
        ]:
            _make_event(session, eid, test_actor_id, clock, event_type=etype)
            _make_posted_entry(session, eid, test_actor_id, clock, event_type=etype)

        _make_link(session, link_graph, LinkType.FULFILLED_BY, po_ref, r1_ref)
        _make_link(session, link_graph, LinkType.FULFILLED_BY, po_ref, r2_ref)

        chain = service.build_chain(po_ref)

        assert chain.node_count == 3
        assert chain.edge_count == 2


# =============================================================================
# Check Operations
# =============================================================================


class TestCheckArtifact:
    """Tests for check_artifact: build chain + run all checks."""

    def test_clean_lifecycle(self, session, service, link_graph, clock, test_actor_id):
        """Consistent PO -> Receipt -> Invoice passes all checks."""
        po_id, receipt_id, invoice_id = uuid4(), uuid4(), uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)
        receipt_ref = ArtifactRef.receipt(receipt_id)
        invoice_ref = ArtifactRef.invoice(invoice_id)

        for eid, etype, eff_date in [
            (po_id, "test.po", date(2026, 1, 10)),
            (receipt_id, "test.receipt", date(2026, 1, 12)),
            (invoice_id, "test.invoice", date(2026, 1, 14)),
        ]:
            _make_event(
                session, eid, test_actor_id, clock,
                effective_date=eff_date, event_type=etype,
            )
            _make_posted_entry(
                session, eid, test_actor_id, clock,
                effective_date=eff_date, event_type=etype,
            )

        _make_link(session, link_graph, LinkType.FULFILLED_BY, po_ref, receipt_ref)
        _make_link(session, link_graph, LinkType.FULFILLED_BY, receipt_ref, invoice_ref)

        result = service.check_artifact(po_ref, as_of_date=date(2026, 1, 15))

        # No orphans, no temporal violations, consistent policy
        assert result.status in (CheckStatus.PASSED, CheckStatus.WARNING)
        assert result.nodes_checked == 3
        assert result.edges_checked == 2

    def test_orphaned_child_detected(self, session, service, link_graph, clock, test_actor_id):
        """Child without journal entry yields ORPHANED_LINK finding."""
        po_id = uuid4()
        receipt_id = uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)
        receipt_ref = ArtifactRef.receipt(receipt_id)

        _make_event(session, po_id, test_actor_id, clock)
        _make_posted_entry(session, po_id, test_actor_id, clock)

        _make_link(session, link_graph, LinkType.FULFILLED_BY, po_ref, receipt_ref)

        result = service.check_artifact(po_ref, as_of_date=date(2026, 1, 15))

        assert result.status == CheckStatus.FAILED
        orphan_findings = [f for f in result.findings if f.code == "ORPHANED_LINK"]
        assert len(orphan_findings) >= 1

    def test_policy_drift_detected(self, session, service, link_graph, clock, test_actor_id):
        """Different R21 versions across linked nodes yields POLICY_REGIME_DRIFT."""
        po_id, receipt_id = uuid4(), uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)
        receipt_ref = ArtifactRef.receipt(receipt_id)

        _make_event(session, po_id, test_actor_id, clock, event_type="test.po")
        _make_posted_entry(
            session, po_id, test_actor_id, clock,
            event_type="test.po",
            coa_version=1,
        )

        _make_event(session, receipt_id, test_actor_id, clock, event_type="test.receipt")
        _make_posted_entry(
            session, receipt_id, test_actor_id, clock,
            event_type="test.receipt",
            coa_version=2,  # Different from PO
        )

        _make_link(session, link_graph, LinkType.FULFILLED_BY, po_ref, receipt_ref)

        result = service.check_artifact(po_ref, as_of_date=date(2026, 1, 15))

        drift_findings = [f for f in result.findings if f.code == "POLICY_REGIME_DRIFT"]
        assert len(drift_findings) >= 1

    def test_explicit_as_of_date(self, session, service, clock, test_actor_id):
        """as_of_date parameter is used instead of clock."""
        po_id = uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)

        _make_event(session, po_id, test_actor_id, clock)
        _make_posted_entry(session, po_id, test_actor_id, clock)

        result = service.check_artifact(po_ref, as_of_date=date(2026, 6, 1))
        assert result.as_of_date == date(2026, 6, 1)

    def test_default_as_of_date_uses_clock(self, session, service, clock, test_actor_id):
        """When as_of_date is None, uses the injected clock."""
        po_id = uuid4()
        po_ref = ArtifactRef.purchase_order(po_id)

        _make_event(session, po_id, test_actor_id, clock)
        _make_posted_entry(session, po_id, test_actor_id, clock)

        result = service.check_artifact(po_ref)
        assert result.as_of_date == date(2026, 1, 15)


class TestCheckArtifacts:
    """Tests for check_artifacts: batch check multiple roots."""

    def test_batch_check(self, session, service, clock, test_actor_id):
        """check_artifacts returns one result per root."""
        refs = []
        for _ in range(3):
            eid = uuid4()
            ref = ArtifactRef.purchase_order(eid)
            _make_event(session, eid, test_actor_id, clock, event_type=f"test.po.{eid}")
            _make_posted_entry(
                session, eid, test_actor_id, clock, event_type=f"test.po.{eid}",
            )
            refs.append(ref)

        results = service.check_artifacts(refs, as_of_date=date(2026, 1, 15))

        assert len(results) == 3
        for i, result in enumerate(results):
            assert result.root_ref == refs[i]

    def test_empty_batch(self, service):
        """Empty list returns empty results."""
        results = service.check_artifacts([])
        assert results == []
