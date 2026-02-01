"""
Tests for Subledger Control Contract.

Tests the enforcement of the fundamental invariant that subledger
balances must reconcile with GL control accounts.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.subledger_control import (
    ControlAccountBinding,
    ReconciliationResult,
    ReconciliationTiming,
    ReconciliationTolerance,
    ReconciliationViolation,
    SubledgerControlContract,
    SubledgerControlRegistry,
    SubledgerReconciler,
    SubledgerType,
    ToleranceType,
    create_ap_control_contract,
    create_ar_control_contract,
    create_bank_control_contract,
    create_default_control_registry,
    create_inventory_control_contract,
)
from finance_kernel.domain.values import Currency, Money


class TestSubledgerType:
    """Tests for SubledgerType enum."""

    def test_all_subledger_types_defined(self):
        """Should have all expected subledger types (canonical uppercase values)."""
        expected = {
            "AP", "AR", "INVENTORY", "FIXED_ASSETS", "BANK",
            "PAYROLL", "WIP", "INTERCOMPANY",
        }
        actual = {st.value for st in SubledgerType}
        assert actual == expected


class TestReconciliationTolerance:
    """Tests for ReconciliationTolerance."""

    def test_zero_tolerance(self):
        """Should reject any variance."""
        tolerance = ReconciliationTolerance.zero()

        assert tolerance.is_within_tolerance(Decimal("0"), Decimal("1000"))
        assert not tolerance.is_within_tolerance(Decimal("0.01"), Decimal("1000"))
        assert not tolerance.is_within_tolerance(Decimal("-0.01"), Decimal("1000"))

    def test_pennies_tolerance(self):
        """Should allow small absolute variance."""
        tolerance = ReconciliationTolerance.pennies(Decimal("0.05"))

        assert tolerance.is_within_tolerance(Decimal("0"), Decimal("1000"))
        assert tolerance.is_within_tolerance(Decimal("0.05"), Decimal("1000"))
        assert tolerance.is_within_tolerance(Decimal("-0.05"), Decimal("1000"))
        assert not tolerance.is_within_tolerance(Decimal("0.06"), Decimal("1000"))

    def test_percentage_tolerance(self):
        """Should allow percentage-based variance."""
        tolerance = ReconciliationTolerance.percent(Decimal("1"))  # 1%

        # 1% of 1000 = 10
        assert tolerance.is_within_tolerance(Decimal("10"), Decimal("1000"))
        assert tolerance.is_within_tolerance(Decimal("-10"), Decimal("1000"))
        assert not tolerance.is_within_tolerance(Decimal("11"), Decimal("1000"))

    def test_percentage_with_cap(self):
        """Should respect max cap on percentage tolerance."""
        tolerance = ReconciliationTolerance.percent(
            Decimal("1"),  # 1%
            max_cap=Decimal("5"),  # But capped at $5
        )

        # For balance of 1000: 1% = 10, but capped at 5
        assert tolerance.is_within_tolerance(Decimal("5"), Decimal("1000"))
        assert not tolerance.is_within_tolerance(Decimal("6"), Decimal("1000"))

        # For balance of 100: 1% = 1, no cap needed
        assert tolerance.is_within_tolerance(Decimal("1"), Decimal("100"))
        assert not tolerance.is_within_tolerance(Decimal("1.5"), Decimal("100"))


class TestControlAccountBinding:
    """Tests for ControlAccountBinding."""

    def test_create_binding(self):
        """Should create control account binding."""
        binding = ControlAccountBinding(
            subledger_type=SubledgerType.AP,
            control_account_role="AP_CONTROL",
            control_account_code="2100",
            is_debit_normal=False,
            currency="USD",
        )

        assert binding.subledger_type == SubledgerType.AP
        assert binding.control_account_role == "AP_CONTROL"
        assert binding.control_account_code == "2100"
        assert not binding.is_debit_normal
        assert binding.currency == "USD"

    def test_expected_sign_credit_normal(self):
        """Should return -1 for credit normal accounts."""
        binding = ControlAccountBinding(
            subledger_type=SubledgerType.AP,
            control_account_role="AP_CONTROL",
            control_account_code="2100",
            is_debit_normal=False,  # Liability
            currency="USD",
        )

        assert binding.expected_sign() == -1

    def test_expected_sign_debit_normal(self):
        """Should return 1 for debit normal accounts."""
        binding = ControlAccountBinding(
            subledger_type=SubledgerType.AR,
            control_account_role="AR_CONTROL",
            control_account_code="1200",
            is_debit_normal=True,  # Asset
            currency="USD",
        )

        assert binding.expected_sign() == 1


class TestSubledgerControlContract:
    """Tests for SubledgerControlContract."""

    @pytest.fixture
    def ap_contract(self) -> SubledgerControlContract:
        """Create AP control contract."""
        return create_ap_control_contract()

    def test_contract_properties(self, ap_contract):
        """Should expose binding properties."""
        assert ap_contract.subledger_type == SubledgerType.AP
        assert ap_contract.control_account_role == "AP_CONTROL"

    def test_real_time_enforcement(self, ap_contract):
        """Should enforce on every post."""
        assert ap_contract.timing == ReconciliationTiming.REAL_TIME
        assert ap_contract.enforce_on_post
        assert ap_contract.enforce_on_close


class TestSubledgerReconciler:
    """Tests for SubledgerReconciler."""

    @pytest.fixture
    def reconciler(self) -> SubledgerReconciler:
        """Create reconciler instance."""
        return SubledgerReconciler()

    @pytest.fixture
    def ap_contract(self) -> SubledgerControlContract:
        """Create AP control contract."""
        return create_ap_control_contract()

    def test_reconcile_balanced(self, reconciler, ap_contract):
        """Should report balanced when amounts match."""
        result = reconciler.reconcile(
            contract=ap_contract,
            subledger_balance=Money.of("5000.00", "USD"),
            control_account_balance=Money.of("5000.00", "USD"),
            as_of_date=date(2026, 1, 30),
            checked_at=datetime(2026, 1, 30, 12, 0, 0),
        )

        assert result.is_balanced
        assert result.is_reconciled
        assert result.is_within_tolerance
        assert result.variance.is_zero

    def test_reconcile_with_variance(self, reconciler, ap_contract):
        """Should calculate variance correctly."""
        result = reconciler.reconcile(
            contract=ap_contract,
            subledger_balance=Money.of("5000.50", "USD"),
            control_account_balance=Money.of("5000.00", "USD"),
            as_of_date=date(2026, 1, 30),
            checked_at=datetime(2026, 1, 30, 12, 0, 0),
        )

        assert not result.is_balanced
        assert not result.is_reconciled
        assert result.variance_amount == Decimal("0.50")

    def test_reconcile_within_tolerance(self, reconciler, ap_contract):
        """Should accept variance within tolerance."""
        result = reconciler.reconcile(
            contract=ap_contract,
            subledger_balance=Money.of("5000.01", "USD"),
            control_account_balance=Money.of("5000.00", "USD"),
            as_of_date=date(2026, 1, 30),
            checked_at=datetime(2026, 1, 30, 12, 0, 0),
        )

        assert not result.is_balanced
        assert not result.is_reconciled
        assert result.is_within_tolerance
        assert result.variance_amount == Decimal("0.01")

    def test_reconcile_exceeds_tolerance(self, reconciler, ap_contract):
        """Should reject variance exceeding tolerance."""
        result = reconciler.reconcile(
            contract=ap_contract,
            subledger_balance=Money.of("5000.02", "USD"),
            control_account_balance=Money.of("5000.00", "USD"),
            as_of_date=date(2026, 1, 30),
            checked_at=datetime(2026, 1, 30, 12, 0, 0),
        )

        assert not result.is_balanced
        assert not result.is_reconciled
        assert not result.is_within_tolerance

    def test_reconcile_currency_mismatch_raises(self, reconciler, ap_contract):
        """Should reject mismatched currencies."""
        with pytest.raises(ValueError, match="Currency mismatch"):
            reconciler.reconcile(
                contract=ap_contract,
                subledger_balance=Money.of("5000.00", "USD"),
                control_account_balance=Money.of("5000.00", "EUR"),
                as_of_date=date(2026, 1, 30),
                checked_at=datetime(2026, 1, 30, 12, 0, 0),
            )

    def test_validate_post_success(self, reconciler, ap_contract):
        """Should pass when post maintains balance."""
        violations = reconciler.validate_post(
            contract=ap_contract,
            subledger_balance_before=Money.of("5000.00", "USD"),
            subledger_balance_after=Money.of("6000.00", "USD"),
            control_balance_before=Money.of("5000.00", "USD"),
            control_balance_after=Money.of("6000.00", "USD"),
            as_of_date=date.today(),
        )

        assert len(violations) == 0

    def test_validate_post_failure(self, reconciler, ap_contract):
        """Should fail when post causes imbalance."""
        violations = reconciler.validate_post(
            contract=ap_contract,
            subledger_balance_before=Money.of("5000.00", "USD"),
            subledger_balance_after=Money.of("6000.00", "USD"),
            control_balance_before=Money.of("5000.00", "USD"),
            control_balance_after=Money.of("5500.00", "USD"),  # Mismatch!
            as_of_date=date.today(),
        )

        assert len(violations) == 1
        assert violations[0].violation_type == "out_of_balance"
        assert violations[0].blocking

    def test_validate_post_skipped_when_disabled(self, reconciler):
        """Should skip validation when enforce_on_post=False."""
        contract = SubledgerControlContract(
            binding=ControlAccountBinding(
                subledger_type=SubledgerType.INVENTORY,
                control_account_role="INV_CONTROL",
                control_account_code="1400",
                is_debit_normal=True,
                currency="USD",
            ),
            timing=ReconciliationTiming.PERIOD_END,
            tolerance=ReconciliationTolerance.zero(),
            enforce_on_post=False,  # Disabled
            enforce_on_close=True,
        )

        violations = reconciler.validate_post(
            contract=contract,
            subledger_balance_before=Money.of("5000.00", "USD"),
            subledger_balance_after=Money.of("6000.00", "USD"),
            control_balance_before=Money.of("5000.00", "USD"),
            control_balance_after=Money.of("5500.00", "USD"),  # Would be imbalanced
            as_of_date=date.today(),
        )

        assert len(violations) == 0  # Not checked

    def test_validate_period_close_success(self, reconciler, ap_contract):
        """Should pass when balanced at period close."""
        violations = reconciler.validate_period_close(
            contract=ap_contract,
            subledger_balance=Money.of("5000.00", "USD"),
            control_account_balance=Money.of("5000.00", "USD"),
            period_end_date=date.today(),
        )

        assert len(violations) == 0

    def test_validate_period_close_blocked(self, reconciler, ap_contract):
        """Should block close when out of tolerance."""
        violations = reconciler.validate_period_close(
            contract=ap_contract,
            subledger_balance=Money.of("5100.00", "USD"),
            control_account_balance=Money.of("5000.00", "USD"),
            period_end_date=date.today(),
        )

        assert len(violations) == 1
        assert violations[0].violation_type == "period_close_blocked"
        assert violations[0].blocking

    def test_validate_period_close_warning(self, reconciler, ap_contract):
        """Should warn (not block) when within tolerance but not balanced."""
        violations = reconciler.validate_period_close(
            contract=ap_contract,
            subledger_balance=Money.of("5000.01", "USD"),
            control_account_balance=Money.of("5000.00", "USD"),
            period_end_date=date.today(),
        )

        assert len(violations) == 1
        assert violations[0].violation_type == "tolerance_warning"
        assert violations[0].severity == "warning"
        assert not violations[0].blocking


class TestSubledgerControlRegistry:
    """Tests for SubledgerControlRegistry."""

    def test_register_and_get(self):
        """Should register and retrieve contracts."""
        registry = SubledgerControlRegistry()
        contract = create_ap_control_contract()

        registry.register(contract)

        retrieved = registry.get(SubledgerType.AP)
        assert retrieved is not None
        assert retrieved.subledger_type == SubledgerType.AP

    def test_get_missing(self):
        """Should return None for unregistered type."""
        registry = SubledgerControlRegistry()
        assert registry.get(SubledgerType.AP) is None

    def test_get_by_control_account(self):
        """Should retrieve by control account role."""
        registry = SubledgerControlRegistry()
        registry.register(create_ap_control_contract())

        contract = registry.get_by_control_account("AP_CONTROL")
        assert contract is not None
        assert contract.subledger_type == SubledgerType.AP

    def test_get_all(self):
        """Should retrieve all registered contracts."""
        registry = SubledgerControlRegistry()
        registry.register(create_ap_control_contract())
        registry.register(create_ar_control_contract())

        all_contracts = registry.get_all()
        assert len(all_contracts) == 2


class TestStandardContracts:
    """Tests for standard contract factory functions."""

    def test_ap_contract(self):
        """Should create valid AP contract."""
        contract = create_ap_control_contract()

        assert contract.subledger_type == SubledgerType.AP
        assert contract.control_account_role == "AP_CONTROL"
        assert contract.binding.control_account_code == "2100"
        assert not contract.binding.is_debit_normal  # Liability
        assert contract.timing == ReconciliationTiming.REAL_TIME
        assert contract.enforce_on_post
        assert contract.enforce_on_close

    def test_ar_contract(self):
        """Should create valid AR contract."""
        contract = create_ar_control_contract()

        assert contract.subledger_type == SubledgerType.AR
        assert contract.control_account_role == "AR_CONTROL"
        assert contract.binding.control_account_code == "1200"
        assert contract.binding.is_debit_normal  # Asset

    def test_inventory_contract(self):
        """Should create valid Inventory contract."""
        contract = create_inventory_control_contract()

        assert contract.subledger_type == SubledgerType.INVENTORY
        assert contract.control_account_role == "INVENTORY_CONTROL"
        assert contract.timing == ReconciliationTiming.PERIOD_END
        assert not contract.enforce_on_post  # Not real-time
        assert contract.enforce_on_close

    def test_bank_contract(self):
        """Should create valid Bank contract with zero tolerance."""
        contract = create_bank_control_contract()

        assert contract.subledger_type == SubledgerType.BANK
        assert contract.tolerance.tolerance_type == ToleranceType.NONE
        assert contract.timing == ReconciliationTiming.DAILY


class TestDefaultControlRegistry:
    """Tests for default control registry."""

    def test_has_all_standard_contracts(self):
        """Should include all standard subledger contracts."""
        registry = create_default_control_registry()

        assert registry.get(SubledgerType.AP) is not None
        assert registry.get(SubledgerType.AR) is not None
        assert registry.get(SubledgerType.INVENTORY) is not None
        assert registry.get(SubledgerType.BANK) is not None

    def test_all_contracts_have_valid_bindings(self):
        """All contracts should have valid control account bindings."""
        registry = create_default_control_registry()

        for contract in registry.get_all():
            assert contract.binding is not None
            assert contract.binding.control_account_code
            assert contract.binding.control_account_role
            assert contract.binding.currency == "USD"


class TestReconciliationResult:
    """Tests for ReconciliationResult."""

    def test_result_properties(self):
        """Should expose all result properties."""
        result = ReconciliationResult(
            subledger_type=SubledgerType.AP,
            as_of_date=date.today(),
            subledger_balance=Money.of("5000.50", "USD"),
            control_account_balance=Money.of("5000.00", "USD"),
            variance=Money.of("0.50", "USD"),
            is_reconciled=False,
            is_within_tolerance=False,
            tolerance_used=ReconciliationTolerance.pennies(),
            checked_at=datetime(2026, 1, 30, 12, 0, 0),
            entries_checked=100,
        )

        assert result.subledger_type == SubledgerType.AP
        assert result.variance_amount == Decimal("0.50")
        assert not result.is_balanced
        assert result.entries_checked == 100
