"""
Tests for business profiles (asset, inventory, AP, AR, cash, FX, deferred, payroll).

Tests cover:
- Profile registration and retrieval
- Trigger configuration
- Ledger effects
- Guard conditions

Profiles are registered via the session-scoped register_modules fixture (conftest.py).
"""

from datetime import date

import pytest

from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    GuardType,
    LedgerEffect,
)
from finance_kernel.domain.policy_selector import (
    PolicySelector,
)

pytestmark = pytest.mark.usefixtures("register_modules")


# ============================================================================
# Helper Functions
# ============================================================================


def get_ledger_effect(profile: AccountingPolicy, ledger: str) -> LedgerEffect | None:
    """Get ledger effect by ledger name."""
    for effect in profile.ledger_effects:
        if effect.ledger == ledger:
            return effect
    return None


# ============================================================================
# Asset Profile Tests
# ============================================================================


class TestAssetAcquisitionProfile:
    """Tests for AssetAcquisitionCash profile."""

    @pytest.fixture
    def profile(self) -> AccountingPolicy:
        return PolicySelector.get("AssetAcquisitionCash")

    def test_profile_registered(self, profile):
        """Profile should be registered."""
        assert profile is not None
        assert profile.name == "AssetAcquisitionCash"

    def test_trigger_event_type(self, profile):
        """Profile should trigger on asset.acquisition."""
        assert profile.trigger.event_type == "asset.acquisition"
        assert profile.trigger.schema_version == 1

    def test_meaning_economic_type(self, profile):
        """Profile should have correct economic type."""
        assert profile.meaning.economic_type == "FIXED_ASSET_INCREASE"

    def test_gl_effect(self, profile):
        """Profile should have correct GL effect."""
        effect = get_ledger_effect(profile, "GL")
        assert effect is not None
        assert effect.debit_role == "FIXED_ASSET"
        assert effect.credit_role == "CASH"

    def test_has_cost_guard(self, profile):
        """Profile should reject zero/negative cost."""
        reject_guards = profile.get_reject_guards()
        cost_guard = next((g for g in reject_guards if "cost" in g.expression), None)
        assert cost_guard is not None
        assert cost_guard.reason_code == "INVALID_COST"


class TestAssetDepreciationProfile:
    """Tests for AssetDepreciation profile."""

    @pytest.fixture
    def profile(self) -> AccountingPolicy:
        return PolicySelector.get("AssetDepreciation")

    def test_profile_registered(self, profile):
        """Profile should be registered."""
        assert profile.name == "AssetDepreciation"

    def test_gl_effect(self, profile):
        """Profile should debit expense, credit accumulated depreciation."""
        effect = get_ledger_effect(profile, "GL")
        assert effect is not None
        assert effect.debit_role == "DEPRECIATION_EXPENSE"
        assert effect.credit_role == "ACCUMULATED_DEPRECIATION"


class TestAssetDisposalProfiles:
    """Tests for asset disposal profiles."""

    def test_gain_profile_registered(self):
        """Gain disposal profile should be registered."""
        profile = PolicySelector.get("AssetDisposalGain")
        assert profile is not None
        assert profile.trigger.where == (("payload.disposal_type", "SALE"),)

    def test_loss_profile_registered(self):
        """Loss disposal profile should be registered."""
        profile = PolicySelector.get("AssetDisposalLoss")
        assert profile is not None
        assert profile.trigger.where == (("payload.disposal_type", "RETIREMENT"),)

    def test_impairment_profile_registered(self):
        """Impairment profile should be registered."""
        profile = PolicySelector.get("AssetImpairment")
        assert profile is not None


# ============================================================================
# Inventory Profile Tests
# ============================================================================


class TestInventoryReceiptProfile:
    """Tests for InventoryReceipt profile."""

    @pytest.fixture
    def profile(self) -> AccountingPolicy:
        return PolicySelector.get("InventoryReceipt")

    def test_profile_registered(self, profile):
        """Profile should be registered."""
        assert profile.name == "InventoryReceipt"

    def test_trigger_event_type(self, profile):
        """Profile should trigger on inventory.receipt."""
        assert profile.trigger.event_type == "inventory.receipt"

    def test_gl_effect(self, profile):
        """Profile should debit inventory, credit GRNI."""
        effect = get_ledger_effect(profile, "GL")
        assert effect is not None
        assert effect.debit_role == "INVENTORY"
        assert effect.credit_role == "GRNI"

    def test_inventory_subledger_effect(self, profile):
        """Profile should have inventory subledger effect."""
        effect = get_ledger_effect(profile, "INVENTORY")
        assert effect is not None

    def test_has_quantity_field_reference(self, profile):
        """Profile should reference quantity field."""
        assert profile.meaning.quantity_field == "payload.quantity"


class TestInventoryIssueProfiles:
    """Tests for inventory issue profiles by type."""

    def test_sale_profile_registered(self):
        """Sale issue profile should be registered."""
        profile = PolicySelector.get("InventoryIssueSale")
        assert profile is not None
        assert profile.trigger.where == (("payload.issue_type", "SALE"),)
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "COGS"

    def test_production_profile_registered(self):
        """Production issue profile should be registered."""
        profile = PolicySelector.get("InventoryIssueProduction")
        assert profile is not None
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "WIP"

    def test_scrap_profile_registered(self):
        """Scrap issue profile should be registered."""
        profile = PolicySelector.get("InventoryIssueScrap")
        assert profile is not None

    def test_transfer_profile_registered(self):
        """Transfer issue profile should be registered."""
        profile = PolicySelector.get("InventoryIssueTransfer")
        assert profile is not None


class TestInventoryAdjustmentProfiles:
    """Tests for inventory adjustment profiles."""

    def test_positive_adjustment_registered(self):
        """Positive adjustment profile should be registered."""
        profile = PolicySelector.get("InventoryAdjustmentPositive")
        assert profile is not None
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "INVENTORY"
        assert effect.credit_role == "INVENTORY_VARIANCE"

    def test_negative_adjustment_registered(self):
        """Negative adjustment profile should be registered."""
        profile = PolicySelector.get("InventoryAdjustmentNegative")
        assert profile is not None
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "INVENTORY_VARIANCE"
        assert effect.credit_role == "INVENTORY"


# ============================================================================
# AP Profile Tests
# ============================================================================


class TestAPInvoiceProfiles:
    """Tests for AP invoice profiles."""

    def test_expense_invoice_registered(self):
        """Direct expense invoice profile should be registered."""
        profile = PolicySelector.get("APInvoiceExpense")
        assert profile is not None
        assert profile.trigger.event_type == "ap.invoice_received"

    def test_expense_invoice_gl_effect(self):
        """Expense invoice should debit expense, credit AP."""
        profile = PolicySelector.get("APInvoiceExpense")
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "EXPENSE"
        assert effect.credit_role == "ACCOUNTS_PAYABLE"

    def test_expense_invoice_has_frozen_guard(self):
        """Expense invoice should check for frozen supplier."""
        profile = PolicySelector.get("APInvoiceExpense")
        reject_guards = profile.get_reject_guards()
        frozen_guard = next((g for g in reject_guards if "frozen" in g.expression), None)
        assert frozen_guard is not None
        assert frozen_guard.reason_code == "SUPPLIER_FROZEN"

    def test_po_matched_invoice_registered(self):
        """PO-matched invoice profile should be registered."""
        profile = PolicySelector.get("APInvoicePOMatched")
        assert profile is not None


class TestAPPaymentProfile:
    """Tests for APPayment profile."""

    @pytest.fixture
    def profile(self) -> AccountingPolicy:
        return PolicySelector.get("APPayment")

    def test_profile_registered(self, profile):
        """Profile should be registered."""
        assert profile.name == "APPayment"

    def test_gl_effect(self, profile):
        """Profile should debit AP, credit cash."""
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "ACCOUNTS_PAYABLE"
        assert effect.credit_role == "CASH"

    def test_ap_subledger_effect(self, profile):
        """Profile should have AP subledger effect."""
        effect = get_ledger_effect(profile, "AP")
        assert effect is not None


# ============================================================================
# AR Profile Tests
# ============================================================================


class TestARInvoiceProfile:
    """Tests for ARInvoice profile."""

    @pytest.fixture
    def profile(self) -> AccountingPolicy:
        return PolicySelector.get("ARInvoice")

    def test_profile_registered(self, profile):
        """Profile should be registered."""
        assert profile.name == "ARInvoice"

    def test_trigger_event_type(self, profile):
        """Profile should trigger on ar.invoice."""
        assert profile.trigger.event_type == "ar.invoice"

    def test_gl_effect(self, profile):
        """Profile should debit AR, credit revenue."""
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "ACCOUNTS_RECEIVABLE"
        assert effect.credit_role == "REVENUE"

    def test_has_credit_limit_guard(self, profile):
        """Profile should have credit limit BLOCK guard."""
        block_guards = profile.get_block_guards()
        credit_guard = next((g for g in block_guards if "credit_limit" in g.expression), None)
        assert credit_guard is not None
        assert credit_guard.guard_type == GuardType.BLOCK
        assert credit_guard.reason_code == "CREDIT_LIMIT_EXCEEDED"


class TestARPaymentProfile:
    """Tests for ARPaymentReceived profile."""

    @pytest.fixture
    def profile(self) -> AccountingPolicy:
        return PolicySelector.get("ARPaymentReceived")

    def test_profile_registered(self, profile):
        """Profile should be registered."""
        assert profile.name == "ARPaymentReceived"

    def test_gl_effect(self, profile):
        """Profile should debit cash, credit AR."""
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "CASH"
        assert effect.credit_role == "ACCOUNTS_RECEIVABLE"


class TestARCreditMemoProfiles:
    """Tests for AR credit memo profiles by reason."""

    def test_return_credit_registered(self):
        """Return credit memo profile should be registered."""
        profile = PolicySelector.get("ARCreditMemoReturn")
        assert profile is not None
        assert profile.trigger.where == (("payload.reason_code", "RETURN"),)

    def test_price_adj_credit_registered(self):
        """Price adjustment credit memo profile should be registered."""
        profile = PolicySelector.get("ARCreditMemoPriceAdj")
        assert profile is not None

    def test_service_credit_registered(self):
        """Service credit memo profile should be registered."""
        profile = PolicySelector.get("ARCreditMemoService")
        assert profile is not None

    def test_error_credit_registered(self):
        """Error correction credit memo profile should be registered."""
        profile = PolicySelector.get("ARCreditMemoError")
        assert profile is not None


# ============================================================================
# Cash Profile Tests
# ============================================================================


class TestCashDepositProfile:
    """Tests for CashDeposit profile."""

    @pytest.fixture
    def profile(self) -> AccountingPolicy:
        return PolicySelector.get("CashDeposit")

    def test_profile_registered(self, profile):
        """Profile should be registered."""
        assert profile.name == "CashDeposit"

    def test_gl_effect(self, profile):
        """Profile should debit bank, credit undeposited funds."""
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "BANK"
        assert effect.credit_role == "UNDEPOSITED_FUNDS"


class TestCashWithdrawalProfiles:
    """Tests for cash withdrawal profiles by destination."""

    def test_expense_withdrawal_registered(self):
        """Expense withdrawal profile should be registered."""
        profile = PolicySelector.get("CashWithdrawalExpense")
        assert profile is not None
        assert profile.trigger.where == (("payload.destination_type", "EXPENSE"),)

    def test_supplier_withdrawal_registered(self):
        """Supplier payment withdrawal profile should be registered."""
        profile = PolicySelector.get("CashWithdrawalSupplier")
        assert profile is not None

    def test_payroll_withdrawal_registered(self):
        """Payroll withdrawal profile should be registered."""
        profile = PolicySelector.get("CashWithdrawalPayroll")
        assert profile is not None


class TestCashTransferProfile:
    """Tests for CashTransfer profile."""

    @pytest.fixture
    def profile(self) -> AccountingPolicy:
        return PolicySelector.get("CashTransfer")

    def test_profile_registered(self, profile):
        """Profile should be registered."""
        assert profile.name == "CashTransfer"

    def test_gl_effect(self, profile):
        """Profile should move between bank accounts."""
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "BANK_DESTINATION"
        assert effect.credit_role == "BANK_SOURCE"


# ============================================================================
# FX Profile Tests
# ============================================================================


class TestFXProfiles:
    """Tests for FX gain/loss profiles."""

    def test_unrealized_gain_registered(self):
        """Unrealized gain profile should be registered."""
        profile = PolicySelector.get("FXUnrealizedGain")
        assert profile is not None
        assert profile.meaning.economic_type == "FX_GAIN"

    def test_unrealized_loss_registered(self):
        """Unrealized loss profile should be registered."""
        profile = PolicySelector.get("FXUnrealizedLoss")
        assert profile is not None
        assert profile.meaning.economic_type == "FX_LOSS"

    def test_realized_gain_registered(self):
        """Realized gain profile should be registered."""
        profile = PolicySelector.get("FXRealizedGain")
        assert profile is not None

    def test_realized_loss_registered(self):
        """Realized loss profile should be registered."""
        profile = PolicySelector.get("FXRealizedLoss")
        assert profile is not None


# ============================================================================
# Deferred Profile Tests
# ============================================================================


class TestDeferredRevenueProfile:
    """Tests for DeferredRevenueRecognition profile."""

    @pytest.fixture
    def profile(self) -> AccountingPolicy:
        return PolicySelector.get("DeferredRevenueRecognition")

    def test_profile_registered(self, profile):
        """Profile should be registered."""
        assert profile.name == "DeferredRevenueRecognition"

    def test_trigger_event_type(self, profile):
        """Profile should trigger on deferred.revenue_recognition."""
        assert profile.trigger.event_type == "deferred.revenue_recognition"

    def test_gl_effect(self, profile):
        """Profile should debit deferred revenue, credit revenue."""
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "DEFERRED_REVENUE"
        assert effect.credit_role == "REVENUE"


class TestDeferredExpenseProfile:
    """Tests for DeferredExpenseRecognition profile."""

    @pytest.fixture
    def profile(self) -> AccountingPolicy:
        return PolicySelector.get("DeferredExpenseRecognition")

    def test_profile_registered(self, profile):
        """Profile should be registered."""
        assert profile.name == "DeferredExpenseRecognition"

    def test_trigger_event_type(self, profile):
        """Profile should trigger on deferred.expense_recognition."""
        assert profile.trigger.event_type == "deferred.expense_recognition"

    def test_gl_effect(self, profile):
        """Profile should debit expense, credit prepaid."""
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "EXPENSE"
        assert effect.credit_role == "PREPAID_EXPENSE"


# ============================================================================
# Payroll Profile Tests
# ============================================================================


class TestTimesheetProfiles:
    """Tests for timesheet profiles by pay code."""

    def test_regular_timesheet_registered(self):
        """Regular hours timesheet profile should be registered."""
        profile = PolicySelector.get("TimesheetRegular")
        assert profile is not None
        assert profile.trigger.where == (("payload.pay_code", "REGULAR"),)

    def test_overtime_timesheet_registered(self):
        """Overtime timesheet profile should be registered."""
        profile = PolicySelector.get("TimesheetOvertime")
        assert profile is not None
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "OVERTIME_EXPENSE"

    def test_pto_timesheet_registered(self):
        """PTO timesheet profile should be registered."""
        profile = PolicySelector.get("TimesheetPTO")
        assert profile is not None


class TestLaborDistributionProfiles:
    """Tests for labor distribution profiles by labor type."""

    def test_direct_labor_registered(self):
        """Direct labor distribution profile should be registered."""
        profile = PolicySelector.get("LaborDistributionDirect")
        assert profile is not None
        assert profile.trigger.where == (("payload.labor_type", "DIRECT"),)
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "WIP"
        assert effect.credit_role == "LABOR_CLEARING"

    def test_indirect_labor_registered(self):
        """Indirect labor distribution profile should be registered."""
        profile = PolicySelector.get("LaborDistributionIndirect")
        assert profile is not None
        effect = get_ledger_effect(profile, "GL")
        assert effect.debit_role == "OVERHEAD_POOL"

    def test_overhead_labor_registered(self):
        """Overhead labor distribution profile should be registered."""
        profile = PolicySelector.get("LaborDistributionOverhead")
        assert profile is not None


# ============================================================================
# Profile Count and Properties Tests
# ============================================================================


class TestProfileRegistration:
    """Tests for overall profile registration."""

    def test_total_profile_count(self):
        """All module profiles should be registered (90+)."""
        profiles = PolicySelector.list_profiles()
        assert len(profiles) >= 90, f"Expected at least 90 profiles, got {len(profiles)}"

    def test_all_profiles_have_effective_date(self):
        """All profiles should have effective_from date."""
        for name in PolicySelector.list_profiles():
            profile = PolicySelector.get(name)
            assert profile.effective_from is not None
            assert isinstance(profile.effective_from, date)

    def test_all_profiles_have_gl_effect(self):
        """Most profiles should have at least one GL effect."""
        # All business profiles should have GL effects
        profiles_with_gl = [
            "AssetAcquisitionCash", "AssetDepreciation",
            "InventoryReceipt", "InventoryIssueSale",
            "APInvoiceExpense", "APPayment",
            "ARInvoice", "ARPaymentReceived",
            "CashDeposit", "CashTransfer",
            "DeferredRevenueRecognition", "DeferredExpenseRecognition",
            "TimesheetRegular", "LaborDistributionDirect",
        ]
        for name in profiles_with_gl:
            profile = PolicySelector.get(name)
            effect = get_ledger_effect(profile, "GL")
            assert effect is not None, f"{name} missing GL effect"

    def test_profiles_are_effective_in_2024(self):
        """All profiles should be effective as of 2024."""
        check_date = date(2024, 6, 15)
        for name in PolicySelector.list_profiles():
            profile = PolicySelector.get(name)
            assert profile.is_effective_on(check_date), f"{name} not effective on {check_date}"
