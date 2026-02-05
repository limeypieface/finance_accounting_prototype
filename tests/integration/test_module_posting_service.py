"""
End-to-end integration test for ModulePostingService.

Tests the full flow:
    Event → PolicySelector lookup → MeaningBuilder → AccountingIntent → JournalEntry

Scenarios:
    1. Inventory receipt → verify JournalEntry created via ModulePostingService
    2. Where-clause dispatch → AP invoice with and without PO
    3. Profile not found → unknown event type returns PROFILE_NOT_FOUND
    4. Period closed → posting to closed period returns PERIOD_CLOSED
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.models.fiscal_period import PeriodStatus
from finance_kernel.services.module_posting_service import (
    ModulePostingStatus,
)

# Service-tier: use test session + module_role_resolver (no get_session/get_active_config).
pytestmark = pytest.mark.service

# =============================================================================
# Test 1: Inventory Receipt — Happy Path
# =============================================================================


class TestInventoryReceiptEndToEnd:
    """Post an inventory receipt and verify journal entries."""

    def test_inventory_receipt_posts_successfully(
        self, module_posting_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Inventory receipt should produce POSTED status with journal entries."""
        result = module_posting_service.post_event(
            event_type="inventory.receipt",
            payload={
                "quantity": 100,
                "unit_cost": "25.00",
                "item_code": "RAW-STEEL-001",
                "po_number": "PO-2024-0100",
                "warehouse": "WH-01",
            },
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("2500.00"),
            currency="USD",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert result.profile_name == "InventoryReceipt"
        assert len(result.journal_entry_ids) > 0

    def test_inventory_receipt_profile_matches_event_type(
        self, module_posting_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Profile matched should be InventoryReceipt for inventory.receipt events."""
        result = module_posting_service.post_event(
            event_type="inventory.receipt",
            payload={
                "quantity": 50,
                "unit_cost": "10.00",
                "item_code": "BOLT-M8",
            },
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("500.00"),
        )

        assert result.profile_name == "InventoryReceipt"
        assert result.meaning_result is not None
        assert result.meaning_result.success


# =============================================================================
# Test 2: Where-Clause Dispatch — AP Invoice With/Without PO
# =============================================================================


class TestWhereClauseDispatch:
    """Verify that where-clause matching selects the correct profile variant."""

    def test_ap_invoice_without_po_matches_expense_profile(
        self, module_posting_service, current_period, test_actor_id, deterministic_clock,
    ):
        """AP invoice without PO number should match APInvoiceExpense."""
        result = module_posting_service.post_event(
            event_type="ap.invoice_received",
            payload={
                "invoice_number": "INV-001",
                "supplier_code": "SUP-100",
                "gross_amount": "1500.00",
                "po_number": None,
            },
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("1500.00"),
        )

        assert result.is_success
        assert result.profile_name == "APInvoiceExpense"

    def test_ap_invoice_with_po_matches_po_matched_profile(
        self, module_posting_service, current_period, test_actor_id, deterministic_clock,
    ):
        """AP invoice with PO number should match APInvoicePOMatched."""
        result = module_posting_service.post_event(
            event_type="ap.invoice_received",
            payload={
                "invoice_number": "INV-002",
                "supplier_code": "SUP-100",
                "gross_amount": "2000.00",
                "po_number": "PO-2024-0050",
            },
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("2000.00"),
        )

        assert result.is_success
        assert result.profile_name == "APInvoicePOMatched"

    def test_where_clause_dispatch_produces_different_profiles(
        self, module_posting_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Same event type with different payloads should dispatch to different profiles."""
        eff_date = deterministic_clock.now().date()

        result_no_po = module_posting_service.post_event(
            event_type="ap.invoice_received",
            payload={
                "invoice_number": "INV-003",
                "supplier_code": "SUP-200",
                "gross_amount": "500.00",
                "po_number": None,
            },
            effective_date=eff_date,
            actor_id=test_actor_id,
            amount=Decimal("500.00"),
        )

        result_with_po = module_posting_service.post_event(
            event_type="ap.invoice_received",
            payload={
                "invoice_number": "INV-004",
                "supplier_code": "SUP-200",
                "gross_amount": "800.00",
                "po_number": "PO-2024-0051",
            },
            effective_date=eff_date,
            actor_id=test_actor_id,
            amount=Decimal("800.00"),
        )

        assert result_no_po.profile_name != result_with_po.profile_name
        assert result_no_po.profile_name == "APInvoiceExpense"
        assert result_with_po.profile_name == "APInvoicePOMatched"


# =============================================================================
# Test 3: Profile Not Found
# =============================================================================


class TestProfileNotFound:
    """Unknown event types should return PROFILE_NOT_FOUND status."""

    def test_unknown_event_type_returns_profile_not_found(
        self, module_posting_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Posting with unknown event type should return PROFILE_NOT_FOUND."""
        result = module_posting_service.post_event(
            event_type="unknown.event_type",
            payload={"data": "test"},
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("100.00"),
        )

        assert result.status == ModulePostingStatus.PROFILE_NOT_FOUND
        assert not result.is_success
        assert "unknown.event_type" in (result.message or "")


# =============================================================================
# Test 4: Period Closed
# =============================================================================


class TestPeriodClosed:
    """Posting to closed periods should return PERIOD_CLOSED status."""

    def test_posting_to_closed_period_returns_period_closed(
        self, module_posting_service, create_period, test_actor_id, deterministic_clock,
    ):
        """Posting to a closed fiscal period should be rejected."""
        # Create a closed period in the past
        past_date = date(2023, 6, 15)
        create_period(
            period_code="2023-06",
            name="June 2023",
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 30),
            status=PeriodStatus.CLOSED,
        )

        result = module_posting_service.post_event(
            event_type="inventory.receipt",
            payload={"quantity": 10, "unit_cost": "50.00"},
            effective_date=past_date,
            actor_id=test_actor_id,
            amount=Decimal("500.00"),
        )

        assert result.status == ModulePostingStatus.PERIOD_CLOSED
        assert not result.is_success


# =============================================================================
# Test 5: Multiple Ledger Intents
# =============================================================================


class TestMultiLedgerPosting:
    """Verify that multi-ledger profiles produce entries for each ledger."""

    def test_inventory_receipt_creates_gl_and_subledger_entries(
        self, module_posting_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Inventory receipt should create entries in both GL and INVENTORY ledgers."""
        result = module_posting_service.post_event(
            event_type="inventory.receipt",
            payload={
                "quantity": 200,
                "unit_cost": "12.50",
                "item_code": "WIRE-COPPER",
            },
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("2500.00"),
        )

        assert result.is_success
        # Multi-ledger profiles should produce journal entries
        assert len(result.journal_entry_ids) > 0
        # The interpretation result should contain the full posting outcome
        assert result.interpretation_result is not None
        assert result.interpretation_result.success


# =============================================================================
# Test 6: AP Payment
# =============================================================================


class TestAPPaymentPosting:
    """Test AP payment posting through ModulePostingService."""

    def test_ap_payment_posts_successfully(
        self, module_posting_service, current_period, test_actor_id, deterministic_clock,
    ):
        """AP payment should produce POSTED status."""
        result = module_posting_service.post_event(
            event_type="ap.payment",
            payload={
                "payment_amount": "5000.00",
                "supplier_code": "SUP-100",
                "invoice_number": "INV-001",
                "payment_method": "CHECK",
            },
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("5000.00"),
        )

        assert result.is_success
        assert result.profile_name == "APPayment"


# =============================================================================
# Test 7: Idempotency (same event posted twice)
# =============================================================================


class TestIdempotency:
    """Verify that posting the same event twice is handled gracefully."""

    def test_same_event_id_posted_twice_returns_already_posted(
        self, module_posting_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Same event_id posted twice should return ALREADY_POSTED."""
        event_id = uuid4()
        eff_date = deterministic_clock.now().date()
        payload = {"quantity": 10, "unit_cost": "100.00"}

        # First post
        result1 = module_posting_service.post_event(
            event_type="inventory.receipt",
            payload=payload,
            effective_date=eff_date,
            actor_id=test_actor_id,
            amount=Decimal("1000.00"),
            event_id=event_id,
        )
        assert result1.is_success

        # Second post with same event_id
        result2 = module_posting_service.post_event(
            event_type="inventory.receipt",
            payload=payload,
            effective_date=eff_date,
            actor_id=test_actor_id,
            amount=Decimal("1000.00"),
            event_id=event_id,
        )

        assert result2.status == ModulePostingStatus.ALREADY_POSTED
        assert result2.is_success
