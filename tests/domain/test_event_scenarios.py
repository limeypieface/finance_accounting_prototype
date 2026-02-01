"""
Event scenario tests: throw real business events at the system.

For each scenario we test three layers:
1. Schema validation — does the payload satisfy the schema?
2. Guard evaluation — does the MeaningBuilder reject or block?
3. Meaning building — is the economic event correct?

All pure domain tests — no database, no I/O.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from finance_kernel.domain.event_validator import validate_payload_against_schema
from finance_kernel.domain.meaning_builder import MeaningBuilder, MeaningBuilderResult
from finance_kernel.domain.policy_selector import (
    PolicyNotFoundError,
    PolicySelector,
)
from finance_kernel.domain.schemas.definitions.ap import (
    AP_INVOICE_RECEIVED_V1,
    AP_PAYMENT_V1,
)
from finance_kernel.domain.schemas.definitions.ar import (
    AR_CREDIT_MEMO_V1,
    AR_INVOICE_ISSUED_V1,
    AR_PAYMENT_RECEIVED_V1,
)
from finance_kernel.domain.schemas.definitions.inventory import (
    INVENTORY_ADJUSTMENT_V1,
    INVENTORY_ISSUE_V1,
    INVENTORY_RECEIPT_V1,
)
from finance_kernel.domain.schemas.definitions.payroll import (
    PAYROLL_LABOR_DISTRIBUTION_V1,
    PAYROLL_TIMESHEET_V1,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry
from finance_modules.ap.profiles import (
    AP_INVOICE_EXPENSE,
    AP_PAYMENT,
)
from finance_modules.ar.profiles import (
    AR_CREDIT_MEMO_RETURN,
    AR_INVOICE,
)
from finance_modules.ar.profiles import (
    AR_PAYMENT_RECEIVED as AR_PAYMENT,
)
from finance_modules.inventory.profiles import (
    INVENTORY_ISSUE_PRODUCTION,
    INVENTORY_ISSUE_SALE,
    INVENTORY_RECEIPT,
)
from finance_modules.payroll.profiles import (
    LABOR_DISTRIBUTION_DIRECT,
    LABOR_DISTRIBUTION_INDIRECT,
    TIMESHEET_OVERTIME,
    TIMESHEET_REGULAR,
)

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture(autouse=True)
def _clear_registries():
    """Save, clear, and restore registries for test isolation."""
    saved_schemas = {k: dict(v) for k, v in EventSchemaRegistry._schemas.items()}
    saved_profiles = {k: dict(v) for k, v in PolicySelector._profiles.items()}
    saved_by_event = {k: list(v) for k, v in PolicySelector._by_event_type.items()}
    EventSchemaRegistry.clear()
    PolicySelector.clear()
    yield
    EventSchemaRegistry.clear()
    PolicySelector.clear()
    EventSchemaRegistry._schemas.update(saved_schemas)
    PolicySelector._profiles.update(saved_profiles)
    PolicySelector._by_event_type.update(saved_by_event)


@pytest.fixture
def builder() -> MeaningBuilder:
    return MeaningBuilder()


# =============================================================================
# PAYLOAD FACTORIES
# =============================================================================


def make_ap_invoice(**overrides: Any) -> dict[str, Any]:
    """Valid AP invoice payload (direct expense, no PO)."""
    payload: dict[str, Any] = {
        "invoice_id": str(uuid4()),
        "invoice_number": "INV-2024-00123",
        "supplier_party_code": "SUP-ACME",
        "invoice_date": "2024-06-15",
        "due_date": "2024-07-15",
        "gross_amount": "5000.00",
        "tax_amount": "500.00",
        "net_amount": "4500.00",
        "currency": "USD",
        "org_unit": "ORG-US",
        "cost_center": "CC-ENG",
    }
    payload.update(overrides)
    return payload


def make_ap_payment(**overrides: Any) -> dict[str, Any]:
    """Valid AP payment payload."""
    payload: dict[str, Any] = {
        "payment_id": str(uuid4()),
        "payment_reference": "PAY-2024-00456",
        "supplier_party_code": "SUP-ACME",
        "payment_date": "2024-07-10",
        "payment_amount": "5000.00",
        "currency": "USD",
        "payment_method": "ACH",
        "bank_account_code": "BANK-MAIN",
        "invoice_allocations": [
            {
                "invoice_id": str(uuid4()),
                "amount_applied": "5000.00",
            }
        ],
        "org_unit": "ORG-US",
    }
    payload.update(overrides)
    return payload


def make_ar_invoice(**overrides: Any) -> dict[str, Any]:
    """Valid AR invoice payload."""
    payload: dict[str, Any] = {
        "invoice_id": str(uuid4()),
        "invoice_number": "AR-2024-00789",
        "customer_party_code": "CUST-BETA",
        "invoice_date": "2024-06-20",
        "due_date": "2024-07-20",
        "gross_amount": "12000.00",
        "tax_amount": "1200.00",
        "net_amount": "10800.00",
        "currency": "USD",
        "org_unit": "ORG-US",
        "cost_center": "CC-SALES",
        "project": "PRJ-2024-Q2",
    }
    payload.update(overrides)
    return payload


def make_ar_payment(**overrides: Any) -> dict[str, Any]:
    """Valid AR payment received payload."""
    payload: dict[str, Any] = {
        "payment_id": str(uuid4()),
        "payment_reference": "RCPT-2024-00321",
        "customer_party_code": "CUST-BETA",
        "payment_date": "2024-07-18",
        "payment_amount": "12000.00",
        "currency": "USD",
        "payment_method": "WIRE",
        "bank_account_code": "BANK-MAIN",
        "invoice_allocations": [
            {
                "invoice_id": str(uuid4()),
                "amount_applied": "12000.00",
            }
        ],
        "org_unit": "ORG-US",
    }
    payload.update(overrides)
    return payload


def make_inventory_receipt(**overrides: Any) -> dict[str, Any]:
    """Valid inventory receipt payload."""
    payload: dict[str, Any] = {
        "receipt_id": str(uuid4()),
        "item_code": "RAW-STEEL-001",
        "quantity": "500",
        "unit_cost": "12.75",
        "total_cost": "6375.00",
        "currency": "USD",
        "warehouse_code": "WH-MAIN",
        "po_number": "PO-2024-100",
        "supplier_party_code": "SUP-ACME",
        "org_unit": "ORG-US",
        "cost_center": "CC-MFG",
    }
    payload.update(overrides)
    return payload


def make_inventory_issue(issue_type: str = "SALE", **overrides: Any) -> dict[str, Any]:
    """Valid inventory issue payload."""
    payload: dict[str, Any] = {
        "issue_id": str(uuid4()),
        "item_code": "RAW-STEEL-001",
        "quantity": "100",
        "unit_cost": "12.75",
        "total_cost": "1275.00",
        "currency": "USD",
        "warehouse_code": "WH-MAIN",
        "issue_type": issue_type,
        "org_unit": "ORG-US",
        "cost_center": "CC-MFG",
    }
    payload.update(overrides)
    return payload


def make_timesheet(pay_code: str = "REGULAR", **overrides: Any) -> dict[str, Any]:
    """Valid payroll timesheet payload."""
    payload: dict[str, Any] = {
        "timesheet_id": str(uuid4()),
        "employee_party_code": "EMP-JSMITH",
        "work_date": "2024-06-15",
        "hours": "8.00",
        "pay_code": pay_code,
        "hourly_rate": "50.00",
        "total_amount": "400.00",
        "currency": "USD",
        "is_billable": True,
        "org_unit": "ORG-US",
        "cost_center": "CC-ENG",
        "project": "PRJ-2024-Q2",
        "department": "DEPT-SW",
    }
    payload.update(overrides)
    return payload


def make_labor_distribution(
    labor_type: str = "DIRECT", **overrides: Any
) -> dict[str, Any]:
    """Valid labor distribution payload."""
    payload: dict[str, Any] = {
        "distribution_id": str(uuid4()),
        "pay_period_id": str(uuid4()),
        "employee_party_code": "EMP-JSMITH",
        "distribution_date": "2024-06-30",
        "labor_type": labor_type,
        "amount": "8500.00",
        "currency": "USD",
        "org_unit": "ORG-US",
        "cost_center": "CC-ENG",
        "project": "PRJ-2024-Q2",
    }
    payload.update(overrides)
    return payload


# =============================================================================
# HELPERS
# =============================================================================

EFFECTIVE_DATE = date(2024, 6, 15)


def assert_schema_valid(payload: dict[str, Any], schema: Any) -> None:
    """Assert schema validation passes."""
    errors = validate_payload_against_schema(payload, schema)
    assert len(errors) == 0, f"Expected valid but got: {errors}"


def assert_schema_fails(
    payload: dict[str, Any], schema: Any, field_hint: str
) -> None:
    """Assert schema validation fails, with error referencing field_hint."""
    errors = validate_payload_against_schema(payload, schema)
    assert len(errors) > 0, "Expected schema failure but got none"
    assert any(
        field_hint in str(e) for e in errors
    ), f"Expected error about '{field_hint}', got: {errors}"


def build_meaning(
    builder: MeaningBuilder,
    event_type: str,
    payload: dict[str, Any],
    profile: Any,
) -> MeaningBuilderResult:
    """Convenience wrapper for MeaningBuilder.build()."""
    return builder.build(
        event_id=uuid4(),
        event_type=event_type,
        payload=payload,
        effective_date=EFFECTIVE_DATE,
        profile=profile,
    )


# =============================================================================
# TEST CLASS: AP Invoice Scenarios
# =============================================================================


class TestAPInvoiceScenarios:
    """Throw AP invoice events at the system."""

    def test_valid_expense_invoice(self, builder: MeaningBuilder):
        """
        Scenario: Receive a $5,000 invoice from supplier ACME for engineering services.
        Expect: LIABILITY_INCREASE, GL Dr:EXPENSE / Cr:ACCOUNTS_PAYABLE
        """
        payload = make_ap_invoice()
        assert_schema_valid(payload, AP_INVOICE_RECEIVED_V1)

        result = build_meaning(builder, "ap.invoice_received", payload, AP_INVOICE_EXPENSE)

        assert result.success, f"Expected success, got: {result.guard_result}"
        assert result.economic_event.economic_type == "LIABILITY_INCREASE"
        assert result.economic_event.profile_id == "APInvoiceExpense"
        assert result.economic_event.profile_version == 1

        # Verify profile ledger effects
        gl_effect = AP_INVOICE_EXPENSE.ledger_effects[0]
        assert gl_effect.ledger == "GL"
        assert gl_effect.debit_role == "EXPENSE"
        assert gl_effect.credit_role == "ACCOUNTS_PAYABLE"

        ap_effect = AP_INVOICE_EXPENSE.ledger_effects[1]
        assert ap_effect.ledger == "AP"
        assert ap_effect.debit_role == "INVOICE"
        assert ap_effect.credit_role == "SUPPLIER_BALANCE"

        # Verify dimensions
        dims = result.economic_event.dimensions
        assert dims["org_unit"] == "ORG-US"
        assert dims["cost_center"] == "CC-ENG"

    def test_reject_zero_amount_invoice(self, builder: MeaningBuilder):
        """
        Scenario: AP invoice arrives with gross_amount = 0.
        Expect: REJECT with reason_code INVALID_AMOUNT.
        """
        payload = make_ap_invoice(gross_amount="0")
        result = build_meaning(builder, "ap.invoice_received", payload, AP_INVOICE_EXPENSE)

        assert not result.success
        assert result.guard_result is not None
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "INVALID_AMOUNT"

    def test_reject_frozen_supplier(self, builder: MeaningBuilder):
        """
        Scenario: Invoice from a supplier who is frozen.
        We embed party.is_frozen=true in the payload to trigger the guard.
        Expect: REJECT with reason_code SUPPLIER_FROZEN.
        """
        payload = make_ap_invoice(party={"is_frozen": True})
        result = build_meaning(builder, "ap.invoice_received", payload, AP_INVOICE_EXPENSE)

        assert not result.success
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "SUPPLIER_FROZEN"

    def test_schema_fail_missing_invoice_number(self):
        """
        Scenario: AP invoice payload missing the required invoice_number.
        Expect: Schema validation error referencing invoice_number.
        """
        payload = make_ap_invoice()
        del payload["invoice_number"]
        assert_schema_fails(payload, AP_INVOICE_RECEIVED_V1, "invoice_number")

    def test_schema_fail_invalid_currency(self):
        """
        Scenario: AP invoice with currency = 'FAKE'.
        Expect: Schema validation error about currency.
        """
        payload = make_ap_invoice(currency="FAKE")
        assert_schema_fails(payload, AP_INVOICE_RECEIVED_V1, "currency")


# =============================================================================
# TEST CLASS: AP Payment Scenarios
# =============================================================================


class TestAPPaymentScenarios:
    """Throw AP payment events at the system."""

    def test_valid_ach_payment(self, builder: MeaningBuilder):
        """
        Scenario: Pay supplier $5,000 via ACH.
        Expect: LIABILITY_DECREASE, GL Dr:ACCOUNTS_PAYABLE / Cr:CASH
        """
        payload = make_ap_payment()
        assert_schema_valid(payload, AP_PAYMENT_V1)

        result = build_meaning(builder, "ap.payment", payload, AP_PAYMENT)

        assert result.success
        assert result.economic_event.economic_type == "LIABILITY_DECREASE"
        assert result.economic_event.profile_id == "APPayment"

        gl_effect = AP_PAYMENT.ledger_effects[0]
        assert gl_effect.debit_role == "ACCOUNTS_PAYABLE"
        assert gl_effect.credit_role == "CASH"

        dims = result.economic_event.dimensions
        assert dims["org_unit"] == "ORG-US"

    def test_reject_zero_payment(self, builder: MeaningBuilder):
        """
        Scenario: Payment with amount = 0.
        Expect: REJECT with reason_code INVALID_AMOUNT.
        """
        payload = make_ap_payment(payment_amount="0")
        result = build_meaning(builder, "ap.payment", payload, AP_PAYMENT)

        assert not result.success
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "INVALID_AMOUNT"

    def test_schema_fail_invalid_payment_method(self):
        """
        Scenario: Payment with method = 'BITCOIN' (not in allowed_values).
        Expect: Schema validation error about allowed values.
        """
        payload = make_ap_payment(payment_method="BITCOIN")
        assert_schema_fails(payload, AP_PAYMENT_V1, "payment_method")


# =============================================================================
# TEST CLASS: AR Invoice Scenarios
# =============================================================================


class TestARInvoiceScenarios:
    """Throw AR invoice events at the system."""

    def test_valid_customer_invoice(self, builder: MeaningBuilder):
        """
        Scenario: Issue $12,000 invoice to customer BETA for services.
        Expect: REVENUE_RECOGNITION, GL Dr:ACCOUNTS_RECEIVABLE / Cr:REVENUE
        """
        payload = make_ar_invoice()
        assert_schema_valid(payload, AR_INVOICE_ISSUED_V1)

        result = build_meaning(builder, "ar.invoice", payload, AR_INVOICE)

        assert result.success
        assert result.economic_event.economic_type == "REVENUE_RECOGNITION"
        assert result.economic_event.profile_id == "ARInvoice"

        gl_effect = AR_INVOICE.ledger_effects[0]
        assert gl_effect.debit_role == "ACCOUNTS_RECEIVABLE"
        assert gl_effect.credit_role == "REVENUE"

        ar_effect = AR_INVOICE.ledger_effects[1]
        assert ar_effect.ledger == "AR"
        assert ar_effect.debit_role == "CUSTOMER_BALANCE"
        assert ar_effect.credit_role == "INVOICE"

        dims = result.economic_event.dimensions
        assert dims["org_unit"] == "ORG-US"
        assert dims["cost_center"] == "CC-SALES"
        assert dims["project"] == "PRJ-2024-Q2"

    def test_reject_zero_amount_invoice(self, builder: MeaningBuilder):
        """
        Scenario: AR invoice with gross_amount = 0.
        Expect: REJECT with reason_code INVALID_AMOUNT.
        """
        payload = make_ar_invoice(gross_amount="0")
        result = build_meaning(builder, "ar.invoice", payload, AR_INVOICE)

        assert not result.success
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "INVALID_AMOUNT"

    def test_reject_frozen_customer(self, builder: MeaningBuilder):
        """
        Scenario: Issue invoice to a frozen customer.
        Expect: REJECT with reason_code CUSTOMER_FROZEN.
        """
        payload = make_ar_invoice(party={"is_frozen": True})
        result = build_meaning(builder, "ar.invoice", payload, AR_INVOICE)

        assert not result.success
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "CUSTOMER_FROZEN"

    def test_schema_fail_missing_customer_party_code(self):
        """
        Scenario: AR invoice missing customer_party_code.
        Expect: Schema validation error.
        """
        payload = make_ar_invoice()
        del payload["customer_party_code"]
        assert_schema_fails(payload, AR_INVOICE_ISSUED_V1, "customer_party_code")


# =============================================================================
# TEST CLASS: AR Payment Scenarios
# =============================================================================


class TestARPaymentScenarios:
    """Throw AR payment events at the system."""

    def test_valid_customer_payment(self, builder: MeaningBuilder):
        """
        Scenario: Receive $12,000 wire payment from customer BETA.
        Expect: ASSET_INCREASE, GL Dr:CASH / Cr:ACCOUNTS_RECEIVABLE
        """
        payload = make_ar_payment()
        assert_schema_valid(payload, AR_PAYMENT_RECEIVED_V1)

        result = build_meaning(builder, "ar.payment", payload, AR_PAYMENT)

        assert result.success
        assert result.economic_event.economic_type == "ASSET_INCREASE"
        assert result.economic_event.profile_id == "ARPaymentReceived"

        gl_effect = AR_PAYMENT.ledger_effects[0]
        assert gl_effect.debit_role == "CASH"
        assert gl_effect.credit_role == "ACCOUNTS_RECEIVABLE"

        dims = result.economic_event.dimensions
        assert dims["org_unit"] == "ORG-US"

    def test_reject_zero_payment(self, builder: MeaningBuilder):
        """
        Scenario: Customer payment of $0.
        Expect: REJECT with reason_code INVALID_AMOUNT.
        """
        payload = make_ar_payment(payment_amount="0")
        result = build_meaning(builder, "ar.payment", payload, AR_PAYMENT)

        assert not result.success
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "INVALID_AMOUNT"


# =============================================================================
# TEST CLASS: Inventory Scenarios
# =============================================================================


class TestInventoryScenarios:
    """Throw inventory events at the system."""

    def test_receive_500_units_of_steel(self, builder: MeaningBuilder):
        """
        Scenario: Receive 500 units of RAW-STEEL-001 at $12.75/unit.
        Expect: INVENTORY_INCREASE, quantity=500, GL Dr:INVENTORY / Cr:GRNI,
                plus INVENTORY subledger Dr:STOCK_ON_HAND / Cr:IN_TRANSIT.
        """
        payload = make_inventory_receipt()
        assert_schema_valid(payload, INVENTORY_RECEIPT_V1)

        result = build_meaning(builder, "inventory.receipt", payload, INVENTORY_RECEIPT)

        assert result.success
        assert result.economic_event.economic_type == "INVENTORY_INCREASE"
        assert result.economic_event.quantity == Decimal("500")

        # GL effect
        gl_effect = INVENTORY_RECEIPT.ledger_effects[0]
        assert gl_effect.debit_role == "INVENTORY"
        assert gl_effect.credit_role == "GRNI"

        # Inventory subledger effect
        inv_effect = INVENTORY_RECEIPT.ledger_effects[1]
        assert inv_effect.ledger == "INVENTORY"
        assert inv_effect.debit_role == "STOCK_ON_HAND"
        assert inv_effect.credit_role == "IN_TRANSIT"

        dims = result.economic_event.dimensions
        assert dims["org_unit"] == "ORG-US"
        assert dims["cost_center"] == "CC-MFG"

    def test_reject_zero_quantity_receipt(self, builder: MeaningBuilder):
        """
        Scenario: Inventory receipt with quantity = 0.
        Expect: REJECT with reason_code INVALID_QUANTITY.
        """
        payload = make_inventory_receipt(quantity="0")
        result = build_meaning(builder, "inventory.receipt", payload, INVENTORY_RECEIPT)

        assert not result.success
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "INVALID_QUANTITY"

    def test_issue_100_units_for_sale(self, builder: MeaningBuilder):
        """
        Scenario: Issue 100 units for a customer sale.
        Expect: INVENTORY_DECREASE, quantity=100, GL Dr:COGS / Cr:INVENTORY.
        """
        payload = make_inventory_issue("SALE", quantity="100")
        assert_schema_valid(payload, INVENTORY_ISSUE_V1)

        result = build_meaning(builder, "inventory.issue", payload, INVENTORY_ISSUE_SALE)

        assert result.success
        assert result.economic_event.economic_type == "INVENTORY_DECREASE"
        assert result.economic_event.quantity == Decimal("100")

        gl_effect = INVENTORY_ISSUE_SALE.ledger_effects[0]
        assert gl_effect.debit_role == "COGS"
        assert gl_effect.credit_role == "INVENTORY"

    def test_issue_50_units_to_production(self, builder: MeaningBuilder):
        """
        Scenario: Issue 50 units of raw material to production floor.
        Expect: INVENTORY_TO_WIP, GL Dr:WIP / Cr:INVENTORY.
        """
        payload = make_inventory_issue("PRODUCTION", quantity="50", project="PRJ-2024-Q2")
        assert_schema_valid(payload, INVENTORY_ISSUE_V1)

        result = build_meaning(
            builder, "inventory.issue", payload, INVENTORY_ISSUE_PRODUCTION
        )

        assert result.success
        assert result.economic_event.economic_type == "INVENTORY_TO_WIP"
        assert result.economic_event.quantity == Decimal("50")

        gl_effect = INVENTORY_ISSUE_PRODUCTION.ledger_effects[0]
        assert gl_effect.debit_role == "WIP"
        assert gl_effect.credit_role == "INVENTORY"

        dims = result.economic_event.dimensions
        assert dims["project"] == "PRJ-2024-Q2"

    def test_reject_zero_quantity_issue(self, builder: MeaningBuilder):
        """
        Scenario: Inventory issue with quantity = 0.
        Expect: REJECT with reason_code INVALID_QUANTITY.
        """
        payload = make_inventory_issue("SALE", quantity="0")
        result = build_meaning(builder, "inventory.issue", payload, INVENTORY_ISSUE_SALE)

        assert not result.success
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "INVALID_QUANTITY"

    def test_schema_fail_missing_warehouse_code(self):
        """
        Scenario: Inventory receipt without warehouse_code.
        Expect: Schema validation error.
        """
        payload = make_inventory_receipt()
        del payload["warehouse_code"]
        assert_schema_fails(payload, INVENTORY_RECEIPT_V1, "warehouse_code")


# =============================================================================
# TEST CLASS: Payroll Scenarios
# =============================================================================


class TestPayrollScenarios:
    """Throw payroll events at the system."""

    def test_8_hours_regular_time(self, builder: MeaningBuilder):
        """
        Scenario: Engineer logs 8 hours of regular time at $50/hr.
        Expect: LABOR_ACCRUAL, quantity=8, GL Dr:WAGE_EXPENSE / Cr:ACCRUED_PAYROLL.
        """
        payload = make_timesheet("REGULAR", hours="8.00", hourly_rate="50.00", total_amount="400.00")
        assert_schema_valid(payload, PAYROLL_TIMESHEET_V1)

        result = build_meaning(builder, "timesheet.regular", payload, TIMESHEET_REGULAR)

        assert result.success
        assert result.economic_event.economic_type == "LABOR_ACCRUAL"
        assert result.economic_event.quantity == Decimal("8.00")
        assert result.economic_event.profile_id == "TimesheetRegular"

        gl_effect = TIMESHEET_REGULAR.ledger_effects[0]
        assert gl_effect.debit_role == "WAGE_EXPENSE"
        assert gl_effect.credit_role == "ACCRUED_PAYROLL"

        dims = result.economic_event.dimensions
        assert dims["org_unit"] == "ORG-US"
        assert dims["cost_center"] == "CC-ENG"
        assert dims["project"] == "PRJ-2024-Q2"
        assert dims["department"] == "DEPT-SW"

    def test_4_hours_overtime(self, builder: MeaningBuilder):
        """
        Scenario: Engineer logs 4 hours overtime at $75/hr.
        Expect: LABOR_ACCRUAL, GL Dr:OVERTIME_EXPENSE / Cr:ACCRUED_PAYROLL.
        """
        payload = make_timesheet(
            "OVERTIME", hours="4.00", hourly_rate="75.00", total_amount="300.00"
        )
        assert_schema_valid(payload, PAYROLL_TIMESHEET_V1)

        result = build_meaning(builder, "timesheet.overtime", payload, TIMESHEET_OVERTIME)

        assert result.success
        assert result.economic_event.economic_type == "LABOR_ACCRUAL"
        assert result.economic_event.quantity == Decimal("4.00")

        gl_effect = TIMESHEET_OVERTIME.ledger_effects[0]
        assert gl_effect.debit_role == "OVERTIME_EXPENSE"
        assert gl_effect.credit_role == "ACCRUED_PAYROLL"

    def test_reject_zero_hours(self, builder: MeaningBuilder):
        """
        Scenario: Timesheet with 0 hours.
        Expect: REJECT with reason_code INVALID_HOURS.
        """
        payload = make_timesheet("REGULAR", hours="0")
        result = build_meaning(builder, "timesheet.regular", payload, TIMESHEET_REGULAR)

        assert not result.success
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "INVALID_HOURS"

    def test_reject_25_hours(self, builder: MeaningBuilder):
        """
        Scenario: Timesheet with 25 hours (exceeds 24-hour limit).
        Expect: REJECT with reason_code EXCESSIVE_HOURS.
        """
        payload = make_timesheet("REGULAR", hours="25")
        result = build_meaning(builder, "timesheet.regular", payload, TIMESHEET_REGULAR)

        assert not result.success
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "EXCESSIVE_HOURS"

    def test_direct_labor_distribution(self, builder: MeaningBuilder):
        """
        Scenario: Distribute $8,500 direct labor to WIP for project PRJ-2024-Q2.
        Expect: LABOR_ALLOCATION, GL Dr:WIP / Cr:LABOR_CLEARING.
        """
        payload = make_labor_distribution("DIRECT")
        assert_schema_valid(payload, PAYROLL_LABOR_DISTRIBUTION_V1)

        result = build_meaning(
            builder, "labor.distribution_direct", payload, LABOR_DISTRIBUTION_DIRECT
        )

        assert result.success
        assert result.economic_event.economic_type == "LABOR_ALLOCATION"
        assert result.economic_event.profile_id == "LaborDistributionDirect"

        gl_effect = LABOR_DISTRIBUTION_DIRECT.ledger_effects[0]
        assert gl_effect.debit_role == "WIP"
        assert gl_effect.credit_role == "LABOR_CLEARING"

        dims = result.economic_event.dimensions
        assert dims["org_unit"] == "ORG-US"
        assert dims["cost_center"] == "CC-ENG"
        assert dims["project"] == "PRJ-2024-Q2"

    def test_indirect_labor_distribution(self, builder: MeaningBuilder):
        """
        Scenario: Distribute $3,200 indirect labor to overhead pool.
        Expect: OVERHEAD_ALLOCATION, GL Dr:OVERHEAD_POOL / Cr:LABOR_CLEARING.
        """
        payload = make_labor_distribution("INDIRECT", amount="3200.00")
        assert_schema_valid(payload, PAYROLL_LABOR_DISTRIBUTION_V1)

        result = build_meaning(
            builder, "labor.distribution_indirect", payload, LABOR_DISTRIBUTION_INDIRECT
        )

        assert result.success
        assert result.economic_event.economic_type == "OVERHEAD_ALLOCATION"

        gl_effect = LABOR_DISTRIBUTION_INDIRECT.ledger_effects[0]
        assert gl_effect.debit_role == "OVERHEAD_POOL"
        assert gl_effect.credit_role == "LABOR_CLEARING"

    def test_reject_zero_distribution_amount(self, builder: MeaningBuilder):
        """
        Scenario: Labor distribution with amount = 0.
        Expect: REJECT with reason_code INVALID_AMOUNT.
        """
        payload = make_labor_distribution("DIRECT", amount="0")
        result = build_meaning(
            builder, "labor.distribution_direct", payload, LABOR_DISTRIBUTION_DIRECT
        )

        assert not result.success
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "INVALID_AMOUNT"

    def test_schema_fail_invalid_pay_code(self):
        """
        Scenario: Timesheet with pay_code = 'TRIPLE_TIME' (not allowed).
        Expect: Schema validation error.
        """
        payload = make_timesheet("TRIPLE_TIME")
        assert_schema_fails(payload, PAYROLL_TIMESHEET_V1, "pay_code")


# =============================================================================
# TEST CLASS: Cross-Cutting Scenarios
# =============================================================================


class TestCrossCuttingScenarios:
    """Scenarios that test cross-profile and registry behavior."""

    def test_same_event_different_profiles(self, builder: MeaningBuilder):
        """
        Scenario: Two timesheets — REGULAR and OVERTIME — same employee, same day.
        Both should pass but route to different GL debit roles.
        """
        regular_payload = make_timesheet("REGULAR", hours="8.00")
        overtime_payload = make_timesheet("OVERTIME", hours="4.00")

        regular_result = build_meaning(
            builder, "timesheet.regular", regular_payload, TIMESHEET_REGULAR
        )
        overtime_result = build_meaning(
            builder, "timesheet.overtime", overtime_payload, TIMESHEET_OVERTIME
        )

        assert regular_result.success
        assert overtime_result.success

        # Same economic type
        assert regular_result.economic_event.economic_type == "LABOR_ACCRUAL"
        assert overtime_result.economic_event.economic_type == "LABOR_ACCRUAL"

        # Different debit roles
        assert TIMESHEET_REGULAR.ledger_effects[0].debit_role == "WAGE_EXPENSE"
        assert TIMESHEET_OVERTIME.ledger_effects[0].debit_role == "OVERTIME_EXPENSE"

    def test_no_profile_registered_for_event(self):
        """
        Scenario: Submit an event type that has no profile registered.
        Expect: PolicyNotFoundError from the registry.
        """
        # Registry is empty (cleared by fixture)
        with pytest.raises(PolicyNotFoundError):
            PolicySelector.find_for_event(
                "widget.created", date(2024, 6, 15)
            )

    def test_event_before_profile_effective_date(self):
        """
        Scenario: Submit inventory.receipt event dated 2023-06-15,
        but the InventoryReceipt profile is only effective from 2024-01-01.
        Expect: PolicyNotFoundError (no effective profile).
        """
        # Register the real profile
        PolicySelector.register(INVENTORY_RECEIPT)

        with pytest.raises(PolicyNotFoundError):
            PolicySelector.find_for_event(
                "inventory.receipt", date(2023, 6, 15)
            )
