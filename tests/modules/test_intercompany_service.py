"""
Tests for Intercompany Module.

Validates:
- post_ic_transfer: posts Dr IC Due From / Cr IC Due To
- generate_eliminations: posts elimination entries
- reconcile_ic_balances: pure calculation
- consolidate: pure calculation
- get_ic_balance: pure query
- get_elimination_report: pure query
- post_transfer_pricing_adjustment: posts markup
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.intercompany.models import (
    ConsolidationResult,
    EliminationRule,
    ICReconciliationResult,
    ICTransaction,
    IntercompanyAgreement,
)
from finance_modules.intercompany.service import IntercompanyService
from tests.modules.conftest import TEST_IC_AGREEMENT_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def ic_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide IntercompanyService for integration testing."""
    return IntercompanyService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Model Tests
# =============================================================================


class TestIntercompanyModels:
    """Verify intercompany models are frozen dataclasses."""

    def test_agreement_creation(self):
        agreement = IntercompanyAgreement(
            id=uuid4(),
            entity_a="ENTITY_A",
            entity_b="ENTITY_B",
            effective_from=date(2024, 1, 1),
        )
        assert agreement.agreement_type == "transfer"
        assert agreement.markup_rate == Decimal("0")
        assert agreement.currency == "USD"

    def test_agreement_with_markup(self):
        agreement = IntercompanyAgreement(
            id=uuid4(),
            entity_a="ENTITY_A",
            entity_b="ENTITY_B",
            effective_from=date(2024, 1, 1),
            markup_rate=Decimal("0.10"),
        )
        assert agreement.markup_rate == Decimal("0.10")

    def test_ic_transaction_creation(self):
        txn = ICTransaction(
            id=uuid4(),
            from_entity="ENTITY_A",
            to_entity="ENTITY_B",
            amount=Decimal("50000.00"),
            transaction_date=date(2024, 1, 1),
        )
        assert txn.currency == "USD"
        assert txn.description == ""

    def test_elimination_rule_defaults(self):
        rule = EliminationRule(id=uuid4())
        assert rule.rule_type == "balance"
        assert rule.debit_role == "INTERCOMPANY_DUE_TO"
        assert rule.credit_role == "INTERCOMPANY_DUE_FROM"

    def test_consolidation_result(self):
        result = ConsolidationResult(
            entities=("A", "B", "C"),
            period="2024-Q1",
            total_debits=Decimal("300000"),
            total_credits=Decimal("300000"),
            elimination_amount=Decimal("50000"),
            is_balanced=True,
        )
        assert result.is_balanced is True
        assert len(result.entities) == 3

    def test_reconciliation_result(self):
        result = ICReconciliationResult(
            entity_a="A",
            entity_b="B",
            period="2024-Q1",
            entity_a_balance=Decimal("10000"),
            entity_b_balance=Decimal("10000"),
            difference=Decimal("0"),
            is_reconciled=True,
        )
        assert result.is_reconciled is True


# =============================================================================
# Integration Tests — Transfer
# =============================================================================


class TestICTransfer:
    """Tests for post_ic_transfer."""

    def test_transfer_posts(
        self, ic_service, current_period, test_actor_id, deterministic_clock,
    ):
        """IC transfer posts successfully."""
        txn, result = ic_service.post_ic_transfer(
            from_entity="ENTITY_A",
            to_entity="ENTITY_B",
            amount=Decimal("25000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(txn, ICTransaction)
        assert txn.amount == Decimal("25000.00")
        assert txn.from_entity == "ENTITY_A"
        assert txn.to_entity == "ENTITY_B"

    def test_transfer_with_description(
        self, ic_service, current_period, test_actor_id, deterministic_clock,
    ):
        """IC transfer with description."""
        txn, result = ic_service.post_ic_transfer(
            from_entity="ENTITY_A",
            to_entity="ENTITY_B",
            amount=Decimal("15000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            description="Management fee allocation",
        )
        assert result.status == ModulePostingStatus.POSTED
        assert txn.description == "Management fee allocation"


# =============================================================================
# Integration Tests — Eliminations
# =============================================================================


class TestICEliminations:
    """Tests for generate_eliminations."""

    def test_elimination_posts(
        self, ic_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Elimination entry posts successfully."""
        txn, result = ic_service.generate_eliminations(
            period="2024-Q1",
            entity_scope="CONSOLIDATED",
            elimination_amount=Decimal("50000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(txn, ICTransaction)
        assert txn.amount == Decimal("50000.00")


# =============================================================================
# Pure Calculation Tests — Reconciliation
# =============================================================================


class TestICReconciliation:
    """Tests for reconcile_ic_balances (pure calculation)."""

    def test_balanced_reconciliation(self, ic_service):
        """Balanced entities reconcile."""
        result = ic_service.reconcile_ic_balances(
            entity_a="A",
            entity_b="B",
            period="2024-Q1",
            entity_a_balance=Decimal("50000"),
            entity_b_balance=Decimal("50000"),
        )
        assert isinstance(result, ICReconciliationResult)
        assert result.is_reconciled is True
        assert result.difference == Decimal("0")

    def test_unbalanced_reconciliation(self, ic_service):
        """Unbalanced entities show difference."""
        result = ic_service.reconcile_ic_balances(
            entity_a="A",
            entity_b="B",
            period="2024-Q1",
            entity_a_balance=Decimal("50000"),
            entity_b_balance=Decimal("48000"),
        )
        assert result.is_reconciled is False
        assert result.difference == Decimal("2000")


# =============================================================================
# Pure Calculation Tests — Consolidation
# =============================================================================


class TestICConsolidation:
    """Tests for consolidate (pure calculation)."""

    def test_balanced_consolidation(self, ic_service):
        """Consolidation with balanced entities."""
        result = ic_service.consolidate(
            entities=("A", "B"),
            period="2024-Q1",
            entity_balances={
                "A": (Decimal("100000"), Decimal("100000")),
                "B": (Decimal("200000"), Decimal("200000")),
            },
        )
        assert isinstance(result, ConsolidationResult)
        assert result.total_debits == Decimal("300000")
        assert result.total_credits == Decimal("300000")
        assert result.is_balanced is True

    def test_unbalanced_consolidation(self, ic_service):
        """Consolidation with unbalanced entities."""
        result = ic_service.consolidate(
            entities=("A", "B"),
            period="2024-Q1",
            entity_balances={
                "A": (Decimal("100000"), Decimal("90000")),
                "B": (Decimal("80000"), Decimal("80000")),
            },
        )
        assert result.total_debits == Decimal("180000")
        assert result.total_credits == Decimal("170000")
        assert result.is_balanced is False


# =============================================================================
# Pure Query Tests — Balance & Reports
# =============================================================================


class TestICQueries:
    """Tests for get_ic_balance and get_elimination_report."""

    def test_ic_balance_net(self, ic_service):
        """Net balance between two entities."""
        transactions = [
            ICTransaction(id=uuid4(), from_entity="A", to_entity="B",
                          amount=Decimal("30000"), transaction_date=date(2024, 1, 1)),
            ICTransaction(id=uuid4(), from_entity="B", to_entity="A",
                          amount=Decimal("10000"), transaction_date=date(2024, 2, 1)),
            ICTransaction(id=uuid4(), from_entity="A", to_entity="B",
                          amount=Decimal("5000"), transaction_date=date(2024, 3, 1)),
        ]
        balance = ic_service.get_ic_balance("A", "B", transactions)
        # 30000 - 10000 + 5000 = 25000
        assert balance == Decimal("25000")

    def test_ic_balance_zero(self, ic_service):
        """No transactions = zero balance."""
        balance = ic_service.get_ic_balance("A", "B", [])
        assert balance == Decimal("0")

    def test_elimination_report(self, ic_service):
        """Elimination report generation."""
        eliminations = [
            ICTransaction(id=uuid4(), from_entity="A", to_entity="B",
                          amount=Decimal("20000"), transaction_date=date(2024, 1, 1),
                          description="IC balance elimination"),
            ICTransaction(id=uuid4(), from_entity="C", to_entity="D",
                          amount=Decimal("15000"), transaction_date=date(2024, 1, 1),
                          description="IC revenue elimination"),
        ]
        report = ic_service.get_elimination_report("2024-Q1", eliminations)
        assert report["period"] == "2024-Q1"
        assert report["total_eliminations"] == Decimal("35000")
        assert report["count"] == 2
        assert len(report["entries"]) == 2


# =============================================================================
# Integration Tests — Transfer Pricing
# =============================================================================


class TestTransferPricing:
    """Tests for post_transfer_pricing_adjustment."""

    def test_transfer_pricing_posts(
        self, ic_service, current_period, test_actor_id, deterministic_clock,
        test_ic_agreement,
    ):
        """Transfer pricing adjustment posts markup amount."""
        txn, result = ic_service.post_transfer_pricing_adjustment(
            agreement_id=TEST_IC_AGREEMENT_ID,
            from_entity="ENTITY_A",
            to_entity="ENTITY_B",
            base_amount=Decimal("100000.00"),
            markup_rate=Decimal("0.10"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert txn.amount == Decimal("10000.00")

    def test_transfer_pricing_high_markup(
        self, ic_service, current_period, test_actor_id, deterministic_clock,
        test_ic_agreement,
    ):
        """Higher markup rate calculates correct amount."""
        txn, result = ic_service.post_transfer_pricing_adjustment(
            agreement_id=TEST_IC_AGREEMENT_ID,
            from_entity="ENTITY_A",
            to_entity="ENTITY_B",
            base_amount=Decimal("200000.00"),
            markup_rate=Decimal("0.25"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert txn.amount == Decimal("50000.00")
