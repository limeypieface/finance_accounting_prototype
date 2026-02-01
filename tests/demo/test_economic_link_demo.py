"""
Demonstration test for EconomicLink - shows the graph in action with verbose logging.

Run with:
    python -m pytest tests/demo/test_economic_link_demo.py -v -s

The -s flag is important to see all the print output!

Requirements:
    - PostgreSQL must be running (uses the standard test fixtures from conftest.py)
    - Database 'finance_kernel_test' must exist
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from finance_kernel.domain.economic_link import (
    ArtifactRef,
    ArtifactType,
    EconomicLink,
    LinkQuery,
    LinkType,
)
from finance_kernel.domain.values import Money
from finance_kernel.services.link_graph_service import LinkGraphService

# =============================================================================
# PRETTY PRINTING HELPERS
# =============================================================================

def banner(title: str) -> None:
    """Print a banner for visual separation."""
    width = 80
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def section(title: str) -> None:
    """Print a section header."""
    print()
    print(f"--- {title} ---")
    print()


def log_artifact(name: str, ref: ArtifactRef) -> None:
    """Log an artifact reference."""
    print(f"  {name}: {ref.artifact_type.value}({str(ref.artifact_id)[:8]}...)")


def log_link(link: EconomicLink) -> None:
    """Log an economic link."""
    print(f"  LINK: {link.parent_ref.artifact_type.value}({str(link.parent_ref.artifact_id)[:8]}...) "
          f"--[{link.link_type.value}]--> "
          f"{link.child_ref.artifact_type.value}({str(link.child_ref.artifact_id)[:8]}...)")
    if link.metadata:
        print(f"         metadata: {link.metadata}")


def log_path(path, indent: int = 0) -> None:
    """Log a traversal path."""
    prefix = "  " * indent
    artifacts = path.artifacts
    print(f"{prefix}PATH (depth={path.depth}):")
    for i, ref in enumerate(artifacts):
        connector = "  └─► " if i == len(artifacts) - 1 else "  ├─► "
        print(f"{prefix}{connector}{ref.artifact_type.value}({str(ref.artifact_id)[:8]}...)")


# =============================================================================
# TEST FIXTURES
# =============================================================================

# Note: Uses PostgreSQL fixtures from conftest.py:
# - engine: PostgreSQL database engine
# - tables: Creates/drops tables with immutability listeners
# - session: Database session with auto-commit/rollback


@pytest.fixture
def link_service(session: Session) -> LinkGraphService:
    """Create a LinkGraphService instance."""
    return LinkGraphService(session)


# =============================================================================
# DEMO TEST: FULL AP WORKFLOW
# =============================================================================

class TestEconomicLinkDemo:
    """
    Demonstration of EconomicLink tracking a complete AP workflow:

    PO-001 ($10,000)
       │
       ├──[FULFILLED_BY]──► RECEIPT-001 ($6,000)
       │                         │
       │                         └──[FULFILLED_BY]──► INVOICE-001 ($6,000)
       │                                                    │
       │                                                    └──[PAID_BY]──► PAYMENT-001 ($6,000)
       │
       └──[FULFILLED_BY]──► RECEIPT-002 ($4,000)
                                │
                                └──[FULFILLED_BY]──► INVOICE-002 ($4,000)
                                                          │
                                                          └──[REVERSED_BY]──► CREDIT-MEMO-001 ($4,000)
    """

    def test_full_ap_workflow_with_tracing(self, session: Session, link_service: LinkGraphService):
        """
        Walk through a complete AP workflow demonstrating all EconomicLink features.
        """

        banner("ECONOMIC LINK DEMONSTRATION")
        print("""
        This demo shows EconomicLink tracking a complete AP workflow:
        - A Purchase Order for $10,000
        - Two partial receipts ($6,000 and $4,000)
        - Two invoices matching the receipts
        - One payment ($6,000)
        - One credit memo reversing the second invoice
        """)

        # =====================================================================
        # STEP 1: CREATE ARTIFACTS
        # =====================================================================

        section("STEP 1: Creating Artifacts")

        # Create artifact references (these are just typed pointers - the actual
        # documents would live in the AP/Purchasing modules)

        po_id = uuid4()
        receipt_1_id = uuid4()
        receipt_2_id = uuid4()
        invoice_1_id = uuid4()
        invoice_2_id = uuid4()
        payment_1_id = uuid4()
        credit_memo_id = uuid4()

        # Event references (for audit trail)
        event_receipt_1 = uuid4()
        event_receipt_2 = uuid4()
        event_invoice_1 = uuid4()
        event_invoice_2 = uuid4()
        event_payment_1 = uuid4()
        event_credit_memo = uuid4()

        po_ref = ArtifactRef.purchase_order(po_id)
        receipt_1_ref = ArtifactRef.receipt(receipt_1_id)
        receipt_2_ref = ArtifactRef.receipt(receipt_2_id)
        invoice_1_ref = ArtifactRef.invoice(invoice_1_id)
        invoice_2_ref = ArtifactRef.invoice(invoice_2_id)
        payment_1_ref = ArtifactRef.payment(payment_1_id)
        credit_memo_ref = ArtifactRef.credit_memo(credit_memo_id)

        print("Created artifact references:")
        log_artifact("PO-001      ($10,000)", po_ref)
        log_artifact("RECEIPT-001 ($6,000)", receipt_1_ref)
        log_artifact("RECEIPT-002 ($4,000)", receipt_2_ref)
        log_artifact("INVOICE-001 ($6,000)", invoice_1_ref)
        log_artifact("INVOICE-002 ($4,000)", invoice_2_ref)
        log_artifact("PAYMENT-001 ($6,000)", payment_1_ref)
        log_artifact("CREDIT-MEMO ($4,000)", credit_memo_ref)

        # =====================================================================
        # STEP 2: ESTABLISH LINKS - RECEIPTS TO PO
        # =====================================================================

        section("STEP 2: Establishing Links - Receipts fulfill PO")

        # Receipt 1 fulfills PO (partial - $6,000 of $10,000)
        link_po_receipt1 = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_1_ref,
            creating_event_id=event_receipt_1,
            created_at=datetime.now(),
            metadata={"amount": "6000.00", "currency": "USD", "quantity": "60"},
        )

        result = link_service.establish_link(link_po_receipt1)
        print("Establishing link: PO --[FULFILLED_BY]--> Receipt-1")
        log_link(link_po_receipt1)
        print(f"  Result: {'SUCCESS'} (new_link={not result.was_duplicate})")

        # Receipt 2 fulfills PO (remainder - $4,000 of $10,000)
        link_po_receipt2 = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_2_ref,
            creating_event_id=event_receipt_2,
            created_at=datetime.now(),
            metadata={"amount": "4000.00", "currency": "USD", "quantity": "40"},
        )

        result = link_service.establish_link(link_po_receipt2)
        print("\nEstablishing link: PO --[FULFILLED_BY]--> Receipt-2")
        log_link(link_po_receipt2)
        print(f"  Result: {'SUCCESS'} (new_link={not result.was_duplicate})")

        session.commit()

        # =====================================================================
        # STEP 3: CHECK UNCONSUMED VALUE ON PO
        # =====================================================================

        section("STEP 3: Checking Unconsumed Value on PO")

        po_amount = Money.of(Decimal("10000.00"), "USD")

        unconsumed = link_service.get_unconsumed_value(
            parent_ref=po_ref,
            original_amount=po_amount,
            link_types=frozenset({LinkType.FULFILLED_BY}),
            amount_metadata_key="amount",
        )

        print(f"PO Original Amount:   {po_amount}")
        print(f"Total Consumed:       {unconsumed.consumed_amount}")
        print(f"Unconsumed Remaining: {unconsumed.remaining_amount}")
        print(f"Fully Consumed?       {unconsumed.is_fully_consumed}")
        print(f"Number of children:   {unconsumed.child_count}")

        # =====================================================================
        # STEP 4: ESTABLISH LINKS - INVOICES TO RECEIPTS
        # =====================================================================

        section("STEP 4: Establishing Links - Invoices fulfill Receipts")

        # Invoice 1 fulfills Receipt 1
        link_receipt1_invoice1 = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=receipt_1_ref,
            child_ref=invoice_1_ref,
            creating_event_id=event_invoice_1,
            created_at=datetime.now(),
            metadata={"amount": "6000.00", "currency": "USD"},
        )

        result = link_service.establish_link(link_receipt1_invoice1)
        print("Establishing link: Receipt-1 --[FULFILLED_BY]--> Invoice-1")
        log_link(link_receipt1_invoice1)
        print(f"  Result: {'SUCCESS'}")

        # Invoice 2 fulfills Receipt 2
        link_receipt2_invoice2 = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=receipt_2_ref,
            child_ref=invoice_2_ref,
            creating_event_id=event_invoice_2,
            created_at=datetime.now(),
            metadata={"amount": "4000.00", "currency": "USD"},
        )

        result = link_service.establish_link(link_receipt2_invoice2)
        print("\nEstablishing link: Receipt-2 --[FULFILLED_BY]--> Invoice-2")
        log_link(link_receipt2_invoice2)
        print(f"  Result: {'SUCCESS'}")

        session.commit()

        # =====================================================================
        # STEP 5: ESTABLISH LINKS - PAYMENT TO INVOICE
        # =====================================================================

        section("STEP 5: Establishing Links - Payment pays Invoice-1")

        link_invoice1_payment = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.PAID_BY,
            parent_ref=invoice_1_ref,
            child_ref=payment_1_ref,
            creating_event_id=event_payment_1,
            created_at=datetime.now(),
            metadata={"amount": "6000.00", "currency": "USD"},
        )

        result = link_service.establish_link(link_invoice1_payment)
        print("Establishing link: Invoice-1 --[PAID_BY]--> Payment-1")
        log_link(link_invoice1_payment)
        print(f"  Result: {'SUCCESS'}")

        session.commit()

        # =====================================================================
        # STEP 6: ESTABLISH LINKS - CREDIT MEMO REVERSES INVOICE-2
        # =====================================================================

        section("STEP 6: Establishing Links - Credit Memo corrects Invoice-2")

        # Note: CORRECTED_BY is the right link type for Invoice→CreditMemo
        # REVERSED_BY is for JournalEntry→JournalEntry reversals
        link_invoice2_credit = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.CORRECTED_BY,
            parent_ref=invoice_2_ref,
            child_ref=credit_memo_ref,
            creating_event_id=event_credit_memo,
            created_at=datetime.now(),
            metadata={"reason": "Goods returned", "amount": "4000.00"},
        )

        result = link_service.establish_link(link_invoice2_credit)
        print("Establishing link: Invoice-2 --[CORRECTED_BY]--> Credit-Memo")
        log_link(link_invoice2_credit)
        print(f"  Result: {'SUCCESS'}")

        session.commit()

        # =====================================================================
        # STEP 7: WALK THE GRAPH FROM PO
        # =====================================================================

        section("STEP 7: Walking the Graph from PO (children direction)")

        query = LinkQuery(
            starting_ref=po_ref,
            link_types=None,  # All link types
            max_depth=10,
            direction="children",
        )

        print("Query: Starting from PO, walk all children up to depth 10")
        print("       Link types: ALL")
        print("       Direction: children")
        print()

        paths = link_service.walk_path(query)

        print(f"Found {len(paths)} paths from PO:")
        print()
        for i, path in enumerate(paths, 1):
            print(f"  Path {i}:")
            log_path(path, indent=2)
            print()

        # =====================================================================
        # STEP 8: WALK THE GRAPH FROM PAYMENT (parents direction)
        # =====================================================================

        section("STEP 8: Walking the Graph from Payment (parents direction)")

        query = LinkQuery(
            starting_ref=payment_1_ref,
            link_types=None,
            max_depth=10,
            direction="parents",
        )

        print("Query: Starting from Payment, walk all parents up to depth 10")
        print("       This traces back the money flow!")
        print()

        paths = link_service.walk_path(query)

        print(f"Found {len(paths)} paths leading to Payment:")
        print()
        for i, path in enumerate(paths, 1):
            print(f"  Path {i} (tracing back to origin):")
            log_path(path, indent=2)
            print()

        # =====================================================================
        # STEP 9: CHECK CORRECTION/REVERSAL STATUS
        # =====================================================================

        section("STEP 9: Checking Correction Status")

        # Note: is_reversed() checks for REVERSED_BY links (JournalEntry reversals)
        # For document corrections (Invoice→CreditMemo), we use CORRECTED_BY
        # Let's check both using get_children

        # Check Invoice 1 (not corrected - it was paid)
        inv1_corrections = link_service.get_children(invoice_1_ref, link_types=frozenset({LinkType.CORRECTED_BY}))

        print(f"Invoice-1 corrected? {len(inv1_corrections) > 0}")
        if inv1_corrections:
            print("  Correction found!")
        else:
            print("  No correction found (Invoice-1 was PAID, not corrected)")

        print()

        # Check Invoice 2 (corrected by credit memo)
        inv2_corrections = link_service.get_children(invoice_2_ref, link_types=frozenset({LinkType.CORRECTED_BY}))

        print(f"Invoice-2 corrected? {len(inv2_corrections) > 0}")
        if inv2_corrections:
            print("  Correction found!")
            log_link(inv2_corrections[0])

        # Also demonstrate the is_reversed() API (for journal entry reversals)
        print()
        print("Note: is_reversed() is specifically for REVERSED_BY links")
        print("      (JournalEntry→JournalEntry reversals, not document corrections)")

        # =====================================================================
        # STEP 10: CHECK UNCONSUMED VALUE ON INVOICES
        # =====================================================================

        section("STEP 10: Checking Payment Status on Invoices")

        # Invoice 1 - fully paid
        inv1_amount = Money.of(Decimal("6000.00"), "USD")
        unconsumed_inv1 = link_service.get_unconsumed_value(
            parent_ref=invoice_1_ref,
            original_amount=inv1_amount,
            link_types=frozenset({LinkType.PAID_BY}),
            amount_metadata_key="amount",
        )

        print("Invoice-1:")
        print(f"  Original Amount: {inv1_amount}")
        print(f"  Paid Amount:     {unconsumed_inv1.consumed_amount}")
        print(f"  Open Balance:    {unconsumed_inv1.remaining_amount}")
        print(f"  Fully Paid?      {unconsumed_inv1.is_fully_consumed}")

        print()

        # Invoice 2 - not paid (reversed instead)
        inv2_amount = Money.of(Decimal("4000.00"), "USD")
        unconsumed_inv2 = link_service.get_unconsumed_value(
            parent_ref=invoice_2_ref,
            original_amount=inv2_amount,
            link_types=frozenset({LinkType.PAID_BY}),
            amount_metadata_key="amount",
        )

        print("Invoice-2:")
        print(f"  Original Amount: {inv2_amount}")
        print(f"  Paid Amount:     {unconsumed_inv2.consumed_amount}")
        print(f"  Open Balance:    {unconsumed_inv2.remaining_amount}")
        print(f"  Fully Paid?      {unconsumed_inv2.is_fully_consumed}")
        print("  (Note: This invoice was REVERSED, not paid)")

        # =====================================================================
        # STEP 11: GET DIRECT CHILDREN/PARENTS
        # =====================================================================

        section("STEP 11: Get Direct Children and Parents")

        # Get all children of PO
        po_children = link_service.get_children(po_ref)
        print(f"Direct children of PO ({len(po_children)} found):")
        for link in po_children:
            log_link(link)

        print()

        # Get all parents of Invoice-1
        inv1_parents = link_service.get_parents(invoice_1_ref)
        print(f"Direct parents of Invoice-1 ({len(inv1_parents)} found):")
        for link in inv1_parents:
            log_link(link)

        # =====================================================================
        # STEP 12: FILTERED QUERIES
        # =====================================================================

        section("STEP 12: Filtered Queries (only FULFILLED_BY links)")

        query = LinkQuery(
            starting_ref=po_ref,
            link_types=(LinkType.FULFILLED_BY,),
            max_depth=10,
            direction="children",
        )

        print("Query: From PO, follow only FULFILLED_BY links")
        print()

        paths = link_service.walk_path(query)

        print(f"Found {len(paths)} fulfillment paths:")
        for i, path in enumerate(paths, 1):
            print(f"  Path {i}:")
            log_path(path, indent=2)
            print()

        # =====================================================================
        # SUMMARY
        # =====================================================================

        banner("DEMONSTRATION COMPLETE")

        print("""
        What we demonstrated:

        1. ARTIFACT REFERENCES
           - Typed pointers to documents (PO, Receipt, Invoice, Payment, Credit Memo)
           - Self-describing (know their own type)

        2. LINK ESTABLISHMENT
           - Links track relationships with metadata (amounts, quantities)
           - Every link has an event_ref for audit trail
           - L3 invariant prevents cycles within same link type

        3. GRAPH TRAVERSAL
           - walk_path() traverses the entire graph
           - Works in both directions (children and parents)
           - Can filter by link type

        4. UNCONSUMED VALUE
           - Tracks how much of a parent artifact is "consumed" by children
           - Essential for partial receipts, partial payments

        5. REVERSAL DETECTION
           - is_reversed() and find_reversal() for quick checks
           - REVERSED_BY link type enforces max_children=1 (only one reversal)

        This graph structure enables:
           - 3-way matching (PO → Receipt → Invoice)
           - Payment allocation tracking
           - Reversal/correction audit trails
           - "Drill-down" from any document to its origins
        """)

        # Assertions to make pytest happy
        assert len(paths) > 0
        assert unconsumed_inv1.is_fully_consumed
        assert not unconsumed_inv2.is_fully_consumed
        assert len(inv2_corrections) > 0  # Invoice-2 was corrected
        assert len(inv1_corrections) == 0  # Invoice-1 was not corrected


class TestCycleDetectionDemo:
    """Demonstrate L3 (acyclic) enforcement."""

    def test_cycle_detection_in_action(self, session: Session, link_service: LinkGraphService):
        """Show that cycles are detected and rejected."""

        banner("CYCLE DETECTION DEMONSTRATION")

        section("Setup: Three events that could form a cycle")

        event_a = uuid4()
        event_b = uuid4()
        event_c = uuid4()

        ref_a = ArtifactRef.event(event_a)
        ref_b = ArtifactRef.event(event_b)
        ref_c = ArtifactRef.event(event_c)

        log_artifact("Event A", ref_a)
        log_artifact("Event B", ref_b)
        log_artifact("Event C", ref_c)

        section("Step 1: A --[DERIVED_FROM]--> B (allowed)")

        link_ab = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.DERIVED_FROM,
            parent_ref=ref_a,
            child_ref=ref_b,
            creating_event_id=event_a,
            created_at=datetime.now(),
        )

        result = link_service.establish_link(link_ab)
        print(f"Result: {'SUCCESS'}")
        log_link(link_ab)
        session.commit()

        section("Step 2: B --[DERIVED_FROM]--> C (allowed)")

        link_bc = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.DERIVED_FROM,
            parent_ref=ref_b,
            child_ref=ref_c,
            creating_event_id=event_b,
            created_at=datetime.now(),
        )

        result = link_service.establish_link(link_bc)
        print(f"Result: {'SUCCESS'}")
        log_link(link_bc)
        session.commit()

        section("Step 3: C --[DERIVED_FROM]--> A (CYCLE - should be rejected!)")

        print("Attempting to create link that would form a cycle:")
        print("  A → B → C → A  (this is circular!)")
        print()

        link_ca = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.DERIVED_FROM,
            parent_ref=ref_c,
            child_ref=ref_a,
            creating_event_id=event_c,
            created_at=datetime.now(),
        )

        from finance_kernel.exceptions import LinkCycleError

        try:
            result = link_service.establish_link(link_ca)
            print("Result: UNEXPECTED SUCCESS (this should have failed!)")
            assert False, "Cycle should have been detected"
        except LinkCycleError as e:
            print("Result: REJECTED (as expected)")
            print(f"  Error: {e}")
            print(f"  Error code: {e.code}")

        banner("CYCLE DETECTION WORKING CORRECTLY")

        print("""
        The L3 invariant (acyclic links) prevents:
        - Infinite loops during graph traversal
        - Logical inconsistencies (A derived from itself?)
        - Corrupted audit trails

        Note: REFERENCES link type is NOT acyclic (allows general associations)
        """)


class TestMaxChildrenDemo:
    """Demonstrate max_children enforcement for REVERSED_BY."""

    def test_single_reversal_enforcement(self, session: Session, link_service: LinkGraphService):
        """Show that an artifact can only be reversed once."""

        banner("MAX CHILDREN (SINGLE REVERSAL) DEMONSTRATION")

        section("Setup: A journal entry that will be reversed")

        # Note: REVERSED_BY only allows JOURNAL_ENTRY or EVENT types
        original_entry_id = uuid4()
        reversal_1_id = uuid4()
        reversal_2_id = uuid4()

        original_ref = ArtifactRef.journal_entry(original_entry_id)
        reversal_1_ref = ArtifactRef.journal_entry(reversal_1_id)
        reversal_2_ref = ArtifactRef.journal_entry(reversal_2_id)

        log_artifact("Original Entry", original_ref)
        log_artifact("Reversal-1", reversal_1_ref)
        log_artifact("Reversal-2", reversal_2_ref)

        section("Step 1: Original --[REVERSED_BY]--> Reversal-1 (allowed)")

        link_1 = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.REVERSED_BY,
            parent_ref=original_ref,
            child_ref=reversal_1_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(),
        )

        result = link_service.establish_link(link_1)
        print(f"Result: {'SUCCESS'}")
        log_link(link_1)
        session.commit()

        section("Step 2: Original --[REVERSED_BY]--> Reversal-2 (should be rejected!)")

        print("Attempting to create a second reversal:")
        print("  REVERSED_BY has max_children=1 (you can only reverse once)")
        print()

        link_2 = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.REVERSED_BY,
            parent_ref=original_ref,
            child_ref=reversal_2_ref,
            creating_event_id=uuid4(),
            created_at=datetime.now(),
        )

        from finance_kernel.exceptions import MaxChildrenExceededError

        try:
            result = link_service.establish_link(link_2)
            print("Result: UNEXPECTED SUCCESS")
            assert False, "Second reversal should have been rejected"
        except MaxChildrenExceededError as e:
            print("Result: REJECTED (as expected)")
            print(f"  Error: {e}")
            print(f"  Error code: {e.code}")

        banner("MAX CHILDREN ENFORCEMENT WORKING")

        print("""
        The REVERSED_BY link type has max_children=1, which means:
        - An artifact can only be reversed ONCE
        - Prevents double-reversal errors
        - Forces corrections to go through proper channels

        Similarly, CORRECTED_BY also has max_children=1.
        """)
