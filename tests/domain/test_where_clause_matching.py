"""
Tests for PolicySelector where-clause evaluation.

Verifies that find_for_event() correctly dispatches to variant profiles
based on payload field matching via where-clauses on PolicyTrigger.
"""

from datetime import date

import pytest

from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    GuardCondition,
    GuardType,
    LedgerEffect,
    PolicyMeaning,
    PolicyTrigger,
)
from finance_kernel.domain.policy_selector import (
    PolicyNotFoundError,
    PolicySelector,
)


@pytest.fixture(autouse=True)
def clear_registry():
    """Save, clear, and restore registry for test isolation."""
    saved_profiles = {k: dict(v) for k, v in PolicySelector._profiles.items()}
    saved_by_event = {k: list(v) for k, v in PolicySelector._by_event_type.items()}
    PolicySelector.clear()
    yield
    PolicySelector.clear()
    PolicySelector._profiles.update(saved_profiles)
    PolicySelector._by_event_type.update(saved_by_event)


def _make_profile(
    name: str,
    event_type: str,
    where: tuple = (),
    economic_type: str = "TEST",
) -> AccountingPolicy:
    """Helper to create a minimal profile for testing."""
    return AccountingPolicy(
        name=name,
        version=1,
        trigger=PolicyTrigger(event_type=event_type, where=where),
        meaning=PolicyMeaning(economic_type=economic_type),
        ledger_effects=(
            LedgerEffect(ledger="GL", debit_role="DEBIT", credit_role="CREDIT"),
        ),
        effective_from=date(2024, 1, 1),
    )


class TestWhereClauseEquality:
    """Test where-clause equality matching."""

    def test_single_where_clause_matches(self):
        sale = _make_profile(
            "IssueSale", "inventory.issue",
            where=(("payload.issue_type", "SALE"),),
        )
        PolicySelector.register(sale)

        result = PolicySelector.find_for_event(
            "inventory.issue", date(2024, 6, 1),
            payload={"issue_type": "SALE"},
        )
        assert result.name == "IssueSale"

    def test_wrong_value_does_not_match(self):
        sale = _make_profile(
            "IssueSale", "inventory.issue",
            where=(("payload.issue_type", "SALE"),),
        )
        PolicySelector.register(sale)

        with pytest.raises(PolicyNotFoundError):
            PolicySelector.find_for_event(
                "inventory.issue", date(2024, 6, 1),
                payload={"issue_type": "PRODUCTION"},
            )

    def test_multiple_variants_dispatch_correctly(self):
        sale = _make_profile(
            "IssueSale", "inventory.issue",
            where=(("payload.issue_type", "SALE"),),
        )
        production = _make_profile(
            "IssueProduction", "inventory.issue",
            where=(("payload.issue_type", "PRODUCTION"),),
        )
        scrap = _make_profile(
            "IssueScrap", "inventory.issue",
            where=(("payload.issue_type", "SCRAP"),),
        )
        PolicySelector.register(sale)
        PolicySelector.register(production)
        PolicySelector.register(scrap)

        result = PolicySelector.find_for_event(
            "inventory.issue", date(2024, 6, 1),
            payload={"issue_type": "SALE"},
        )
        assert result.name == "IssueSale"

        result = PolicySelector.find_for_event(
            "inventory.issue", date(2024, 6, 1),
            payload={"issue_type": "PRODUCTION"},
        )
        assert result.name == "IssueProduction"

        result = PolicySelector.find_for_event(
            "inventory.issue", date(2024, 6, 1),
            payload={"issue_type": "SCRAP"},
        )
        assert result.name == "IssueScrap"

    def test_multi_field_where_all_must_match(self):
        profile = _make_profile(
            "SpecificCost", "contract.cost",
            where=(
                ("payload.cost_type", "DIRECT_LABOR"),
                ("payload.contract_type", "CPFF"),
            ),
        )
        PolicySelector.register(profile)

        # Both fields match
        result = PolicySelector.find_for_event(
            "contract.cost", date(2024, 6, 1),
            payload={"cost_type": "DIRECT_LABOR", "contract_type": "CPFF"},
        )
        assert result.name == "SpecificCost"

        # One field doesn't match
        with pytest.raises(PolicyNotFoundError):
            PolicySelector.find_for_event(
                "contract.cost", date(2024, 6, 1),
                payload={"cost_type": "DIRECT_LABOR", "contract_type": "FFP"},
            )


class TestWhereClauseAbsence:
    """Test where-clause None (absence) matching."""

    def test_none_value_matches_absent_field(self):
        no_po = _make_profile(
            "InvoiceNoPO", "ap.invoice_received",
            where=(("payload.po_number", None),),
        )
        PolicySelector.register(no_po)

        result = PolicySelector.find_for_event(
            "ap.invoice_received", date(2024, 6, 1),
            payload={"amount": "1000"},
        )
        assert result.name == "InvoiceNoPO"

    def test_none_value_rejects_present_field(self):
        no_po = _make_profile(
            "InvoiceNoPO", "ap.invoice_received",
            where=(("payload.po_number", None),),
        )
        PolicySelector.register(no_po)

        with pytest.raises(PolicyNotFoundError):
            PolicySelector.find_for_event(
                "ap.invoice_received", date(2024, 6, 1),
                payload={"po_number": "PO-001", "amount": "1000"},
            )


class TestWhereClauseExpressions:
    """Test where-clause comparison expressions."""

    def test_greater_than_expression(self):
        positive = _make_profile(
            "AdjustPositive", "inventory.adjustment",
            where=(("payload.quantity_change > 0", True),),
        )
        PolicySelector.register(positive)

        result = PolicySelector.find_for_event(
            "inventory.adjustment", date(2024, 6, 1),
            payload={"quantity_change": "5"},
        )
        assert result.name == "AdjustPositive"

    def test_less_than_expression(self):
        negative = _make_profile(
            "AdjustNegative", "inventory.adjustment",
            where=(("payload.quantity_change < 0", True),),
        )
        PolicySelector.register(negative)

        result = PolicySelector.find_for_event(
            "inventory.adjustment", date(2024, 6, 1),
            payload={"quantity_change": "-3"},
        )
        assert result.name == "AdjustNegative"

    def test_expression_dispatch_positive_vs_negative(self):
        positive = _make_profile(
            "AdjustPositive", "inventory.adjustment",
            where=(("payload.quantity_change > 0", True),),
        )
        negative = _make_profile(
            "AdjustNegative", "inventory.adjustment",
            where=(("payload.quantity_change < 0", True),),
        )
        PolicySelector.register(positive)
        PolicySelector.register(negative)

        result = PolicySelector.find_for_event(
            "inventory.adjustment", date(2024, 6, 1),
            payload={"quantity_change": "10"},
        )
        assert result.name == "AdjustPositive"

        result = PolicySelector.find_for_event(
            "inventory.adjustment", date(2024, 6, 1),
            payload={"quantity_change": "-7"},
        )
        assert result.name == "AdjustNegative"


class TestWhereClauseFallback:
    """Test fallback behavior when no where-clause matches."""

    def test_fallback_to_generic_profile(self):
        """When no where-clause profile matches, fall back to generic."""
        specific = _make_profile(
            "IssueSale", "inventory.issue",
            where=(("payload.issue_type", "SALE"),),
        )
        generic = _make_profile(
            "IssueGeneric", "inventory.issue",
            where=(),
        )
        PolicySelector.register(specific)
        PolicySelector.register(generic)

        # Specific match works
        result = PolicySelector.find_for_event(
            "inventory.issue", date(2024, 6, 1),
            payload={"issue_type": "SALE"},
        )
        assert result.name == "IssueSale"

        # Unknown variant falls back to generic
        result = PolicySelector.find_for_event(
            "inventory.issue", date(2024, 6, 1),
            payload={"issue_type": "UNKNOWN"},
        )
        assert result.name == "IssueGeneric"

    def test_no_payload_excludes_where_clause_profiles(self):
        """Without payload, only generic profiles are returned."""
        specific = _make_profile(
            "IssueSale", "inventory.issue",
            where=(("payload.issue_type", "SALE"),),
        )
        generic = _make_profile(
            "IssueGeneric", "inventory.issue",
            where=(),
        )
        PolicySelector.register(specific)
        PolicySelector.register(generic)

        result = PolicySelector.find_for_event(
            "inventory.issue", date(2024, 6, 1),
        )
        assert result.name == "IssueGeneric"

    def test_no_payload_no_generic_raises(self):
        """Without payload and no generic profile, raises error."""
        specific = _make_profile(
            "IssueSale", "inventory.issue",
            where=(("payload.issue_type", "SALE"),),
        )
        PolicySelector.register(specific)

        with pytest.raises(PolicyNotFoundError):
            PolicySelector.find_for_event(
                "inventory.issue", date(2024, 6, 1),
            )


class TestWhereClauseWithExistingFeatures:
    """Test where-clause works with effective dates, scope, and precedence."""

    def test_where_clause_with_effective_date_filter(self):
        old = _make_profile(
            "IssueOld", "inventory.issue",
            where=(("payload.issue_type", "SALE"),),
        )
        # Make old profile expire
        old = AccountingPolicy(
            name="IssueOld",
            version=1,
            trigger=PolicyTrigger(
                event_type="inventory.issue",
                where=(("payload.issue_type", "SALE"),),
            ),
            meaning=PolicyMeaning(economic_type="TEST"),
            ledger_effects=(
                LedgerEffect(ledger="GL", debit_role="DEBIT", credit_role="CREDIT"),
            ),
            effective_from=date(2024, 1, 1),
            effective_to=date(2024, 6, 30),
        )
        new = AccountingPolicy(
            name="IssueNew",
            version=1,
            trigger=PolicyTrigger(
                event_type="inventory.issue",
                where=(("payload.issue_type", "SALE"),),
            ),
            meaning=PolicyMeaning(economic_type="TEST_V2"),
            ledger_effects=(
                LedgerEffect(ledger="GL", debit_role="DEBIT", credit_role="CREDIT"),
            ),
            effective_from=date(2024, 7, 1),
        )
        PolicySelector.register(old)
        PolicySelector.register(new)

        result = PolicySelector.find_for_event(
            "inventory.issue", date(2024, 3, 1),
            payload={"issue_type": "SALE"},
        )
        assert result.name == "IssueOld"

        result = PolicySelector.find_for_event(
            "inventory.issue", date(2024, 9, 1),
            payload={"issue_type": "SALE"},
        )
        assert result.name == "IssueNew"

    def test_profile_without_where_still_works(self):
        """Profiles without where-clauses continue to work as before."""
        simple = _make_profile("SimpleReceipt", "inventory.receipt")
        PolicySelector.register(simple)

        # Works with payload
        result = PolicySelector.find_for_event(
            "inventory.receipt", date(2024, 6, 1),
            payload={"quantity": "10"},
        )
        assert result.name == "SimpleReceipt"

        # Works without payload
        result = PolicySelector.find_for_event(
            "inventory.receipt", date(2024, 6, 1),
        )
        assert result.name == "SimpleReceipt"
