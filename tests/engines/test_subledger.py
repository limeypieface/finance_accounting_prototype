"""
Tests for Subledger Pattern.

Covers:
- SubledgerEntry creation
- Balance calculation
- Reconciliation
- Entry validation
- Edge cases and error handling
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_engines.subledger import (
    EntryDirection,
    ReconciliationResult,
    ReconciliationStatus,
    SubledgerBalance,
    SubledgerEntry,
    create_credit_entry,
    create_debit_entry,
)
from finance_kernel.domain.subledger_control import SubledgerType
from finance_kernel.domain.values import Money
from finance_services.subledger_service import SubledgerService


class TestSubledgerEntryCreation:
    """Tests for SubledgerEntry value object."""

    def test_create_debit_entry(self):
        """Creates a debit entry."""
        entry = SubledgerEntry(
            subledger_type="AP",
            entity_id="vendor-1",
            source_document_type="invoice",
            source_document_id="inv-123",
            debit=Money.of("1000.00", "USD"),
            credit=None,
            effective_date=date(2024, 1, 15),
        )

        assert entry.direction == EntryDirection.DEBIT
        assert entry.amount == Money.of("1000.00", "USD")
        assert entry.signed_amount == Money.of("1000.00", "USD")
        assert entry.currency == "USD"

    def test_create_credit_entry(self):
        """Creates a credit entry."""
        entry = SubledgerEntry(
            subledger_type="AP",
            entity_id="vendor-1",
            source_document_type="payment",
            source_document_id="pmt-456",
            debit=None,
            credit=Money.of("1000.00", "USD"),
            effective_date=date(2024, 1, 20),
        )

        assert entry.direction == EntryDirection.CREDIT
        assert entry.amount == Money.of("1000.00", "USD")
        assert entry.signed_amount.amount == Decimal("-1000.00")

    def test_entry_requires_debit_or_credit(self):
        """Raises error if neither debit nor credit provided."""
        with pytest.raises(ValueError, match="must have either"):
            SubledgerEntry(
                subledger_type="AP",
                entity_id="vendor-1",
                source_document_type="invoice",
                source_document_id="inv-123",
                debit=None,
                credit=None,
            )

    def test_entry_cannot_have_both(self):
        """Raises error if both debit and credit provided."""
        with pytest.raises(ValueError, match="cannot have both"):
            SubledgerEntry(
                subledger_type="AP",
                entity_id="vendor-1",
                source_document_type="invoice",
                source_document_id="inv-123",
                debit=Money.of("100.00", "USD"),
                credit=Money.of("100.00", "USD"),
            )

    def test_entry_immutable(self):
        """SubledgerEntry is immutable."""
        entry = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        with pytest.raises(AttributeError):
            entry.debit = Money.of("200.00", "USD")


class TestEntryOpenStatus:
    """Tests for entry open/reconciled status."""

    def test_new_entry_is_open(self):
        """New entries are open by default."""
        entry = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        assert entry.is_open
        assert not entry.is_reconciled
        assert entry.reconciliation_status == ReconciliationStatus.OPEN

    def test_open_amount_equals_amount_when_no_reconciliation(self):
        """Open amount equals full amount when not reconciled."""
        entry = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        assert entry.open_amount == Money.of("100.00", "USD")


class TestEntryReconciliation:
    """Tests for entry reconciliation."""

    def test_with_reconciliation(self):
        """Creates new entry with reconciliation applied."""
        entry = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        reconciled = entry.with_reconciliation(
            reconciled_amount=Money.of("50.00", "USD"),
            reconciled_to_id="pmt-1",
        )

        # Original unchanged
        assert entry.reconciliation_status == ReconciliationStatus.OPEN

        # New entry has partial reconciliation
        assert reconciled.reconciliation_status == ReconciliationStatus.PARTIAL
        assert reconciled.reconciled_amount == Money.of("50.00", "USD")
        assert reconciled.open_amount == Money.of("50.00", "USD")
        assert "pmt-1" in reconciled.reconciled_to_ids

    def test_full_reconciliation(self):
        """Full reconciliation changes status to RECONCILED."""
        entry = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        reconciled = entry.with_reconciliation(
            reconciled_amount=Money.of("100.00", "USD"),
            reconciled_to_id="pmt-1",
        )

        assert reconciled.reconciliation_status == ReconciliationStatus.RECONCILED
        assert reconciled.is_reconciled
        assert reconciled.open_amount.is_zero


class TestConvenienceFactories:
    """Tests for convenience factory functions."""

    def test_create_debit_entry_factory(self):
        """create_debit_entry convenience function."""
        entry = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
            memo="Test invoice",
            dimensions={"cost_center": "SALES"},
        )

        assert entry.direction == EntryDirection.DEBIT
        assert entry.memo == "Test invoice"
        assert entry.dimensions["cost_center"] == "SALES"

    def test_create_credit_entry_factory(self):
        """create_credit_entry convenience function."""
        entry = create_credit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="payment",
            source_document_id="pmt-1",
            effective_date=date(2024, 1, 15),
        )

        assert entry.direction == EntryDirection.CREDIT


class TestSubledgerServiceBase:
    """Tests for SubledgerService base class."""

    def setup_method(self):
        # Create a concrete implementation for testing
        class TestSubledgerService(SubledgerService):
            subledger_type = SubledgerType.AP

            def post(self, entry, gl_entry_id):
                return entry  # Simple passthrough for testing

            def get_balance(self, entity_id, as_of_date=None, currency=None):
                return SubledgerBalance(
                    entity_id=entity_id,
                    subledger_type=self.subledger_type.value,
                    as_of_date=as_of_date or date(2026, 1, 30),
                    debit_total=Money.of("0", "USD"),
                    credit_total=Money.of("0", "USD"),
                    balance=Money.of("0", "USD"),
                    open_item_count=0,
                    currency="USD",
                )

            def get_open_items(self, entity_id, currency=None):
                return []

        self.service = TestSubledgerService()

    def test_validate_entry_valid(self):
        """Validates a complete entry."""
        entry = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        errors = self.service.validate_entry(entry)

        assert len(errors) == 0

    def test_validate_entry_missing_fields(self):
        """Catches missing required fields."""
        entry = SubledgerEntry(
            subledger_type="",  # Missing
            entity_id="",  # Missing
            source_document_type="",  # Missing
            source_document_id="",  # Missing
            debit=Money.of("0", "USD"),  # Zero amount
        )

        errors = self.service.validate_entry(entry)

        assert len(errors) >= 4
        assert any("type" in e.lower() for e in errors)
        assert any("entity" in e.lower() for e in errors)


class TestReconciliation:
    """Tests for reconciliation operations."""

    def setup_method(self):
        class TestSubledgerService(SubledgerService):
            subledger_type = SubledgerType.AP

            def post(self, entry, gl_entry_id):
                return entry

            def get_balance(self, entity_id, as_of_date=None, currency=None):
                pass

            def get_open_items(self, entity_id, currency=None):
                return []

        self.service = TestSubledgerService()

    def test_reconcile_matching_entries(self):
        """Reconciles matching debit and credit entries."""
        debit = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        credit = create_credit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="payment",
            source_document_id="pmt-1",
            effective_date=date(2024, 1, 15),
        )

        result = self.service.reconcile(debit, credit)

        assert isinstance(result, ReconciliationResult)
        assert result.reconciled_amount == Money.of("100.00", "USD")
        assert result.is_full_match

    def test_reconcile_partial(self):
        """Partial reconciliation when amounts differ."""
        debit = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        credit = create_credit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("60.00", "USD"),
            source_document_type="payment",
            source_document_id="pmt-1",
            effective_date=date(2024, 1, 15),
        )

        result = self.service.reconcile(debit, credit)

        assert result.reconciled_amount == Money.of("60.00", "USD")
        assert not result.is_full_match

    def test_reconcile_with_explicit_amount(self):
        """Reconciles specific amount."""
        debit = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        credit = create_credit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="payment",
            source_document_id="pmt-1",
            effective_date=date(2024, 1, 15),
        )

        result = self.service.reconcile(
            debit,
            credit,
            amount=Money.of("50.00", "USD"),
        )

        assert result.reconciled_amount == Money.of("50.00", "USD")
        assert not result.is_full_match

    def test_reconcile_different_subledger_raises(self):
        """Raises error for different subledger types."""
        debit = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        credit = create_credit_entry(
            subledger_type="AR",  # Different!
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="payment",
            source_document_id="pmt-1",
            effective_date=date(2024, 1, 15),
        )

        with pytest.raises(ValueError, match="different subledgers"):
            self.service.reconcile(debit, credit)

    def test_reconcile_different_entity_raises(self):
        """Raises error for different entities."""
        debit = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        credit = create_credit_entry(
            subledger_type="AP",
            entity_id="vendor-2",  # Different!
            amount=Money.of("100.00", "USD"),
            source_document_type="payment",
            source_document_id="pmt-1",
            effective_date=date(2024, 1, 15),
        )

        with pytest.raises(ValueError, match="different entities"):
            self.service.reconcile(debit, credit)

    def test_reconcile_wrong_directions_raises(self):
        """Raises error if directions are wrong."""
        debit1 = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        debit2 = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-2",
            effective_date=date(2024, 1, 15),
        )

        with pytest.raises(ValueError, match="credit"):
            self.service.reconcile(debit1, debit2)

    def test_reconcile_currency_mismatch_raises(self):
        """Raises error for different currencies."""
        debit = create_debit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "USD"),
            source_document_type="invoice",
            source_document_id="inv-1",
            effective_date=date(2024, 1, 15),
        )

        credit = create_credit_entry(
            subledger_type="AP",
            entity_id="vendor-1",
            amount=Money.of("100.00", "EUR"),  # Different currency
            source_document_type="payment",
            source_document_id="pmt-1",
            effective_date=date(2024, 1, 15),
        )

        with pytest.raises(ValueError, match="different currencies"):
            self.service.reconcile(debit, credit)


class TestBalanceCalculation:
    """Tests for balance calculation."""

    def setup_method(self):
        class TestSubledgerService(SubledgerService):
            subledger_type = SubledgerType.AP

            def post(self, entry, gl_entry_id):
                return entry

            def get_balance(self, entity_id, as_of_date=None, currency=None):
                pass

            def get_open_items(self, entity_id, currency=None):
                return []

        self.service = TestSubledgerService()

    def test_calculate_balance_ap(self):
        """AP balance is credit - debit (liability normal balance)."""
        entries = [
            create_credit_entry(
                subledger_type="AP",
                entity_id="vendor-1",
                amount=Money.of("1000.00", "USD"),
                source_document_type="invoice",
                source_document_id="inv-1",
                effective_date=date(2024, 1, 15),
            ),
            create_debit_entry(
                subledger_type="AP",
                entity_id="vendor-1",
                amount=Money.of("400.00", "USD"),
                source_document_type="payment",
                source_document_id="pmt-1",
                effective_date=date(2024, 1, 15),
            ),
        ]

        balance = self.service.calculate_balance(entries, as_of_date=date(2026, 1, 30))

        assert balance.credit_total == Money.of("1000.00", "USD")
        assert balance.debit_total == Money.of("400.00", "USD")
        # AP: credit - debit
        assert balance.balance == Money.of("600.00", "USD")

    def test_calculate_balance_tracks_open_items(self):
        """Counts open items in balance."""
        entries = [
            create_credit_entry(
                subledger_type="AP",
                entity_id="vendor-1",
                amount=Money.of("100.00", "USD"),
                source_document_type="invoice",
                source_document_id="inv-1",
                effective_date=date(2024, 1, 15),
            ),
            create_credit_entry(
                subledger_type="AP",
                entity_id="vendor-1",
                amount=Money.of("200.00", "USD"),
                source_document_type="invoice",
                source_document_id="inv-2",
                effective_date=date(2024, 1, 15),
            ),
        ]

        balance = self.service.calculate_balance(entries, as_of_date=date(2026, 1, 30))

        assert balance.open_item_count == 2

    def test_calculate_balance_empty_raises(self):
        """Raises error for empty entries."""
        with pytest.raises(ValueError, match="empty"):
            self.service.calculate_balance([], as_of_date=date(2026, 1, 30))


class TestSubledgerTypes:
    """Tests for subledger type enums (now canonical from kernel domain)."""

    def test_subledger_types(self):
        """All expected subledger types exist with canonical uppercase values."""
        assert SubledgerType.AP == "AP"
        assert SubledgerType.AR == "AR"
        assert SubledgerType.BANK == "BANK"
        assert SubledgerType.INVENTORY == "INVENTORY"
        assert SubledgerType.FIXED_ASSETS == "FIXED_ASSETS"
        assert SubledgerType.INTERCOMPANY == "INTERCOMPANY"
        assert SubledgerType.PAYROLL == "PAYROLL"
        assert SubledgerType.WIP == "WIP"
