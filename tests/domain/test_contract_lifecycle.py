"""
End-to-end contract lifecycle integration test.

Business Narrative:
    Acme Defense Corp fulfills CPFF Contract W912345-24-C-0001 to manufacture
    10 radar assemblies. The lifecycle:

    Phase 1 — Procurement (PO Cycle):
        Receive steel → AP invoice → AP payment

    Phase 2 — Manufacturing (MO Cycle):
        Issue material to production → Regular labor → Overtime labor →
        Direct labor distribution → Indirect labor distribution

    Phase 3 — Contract Cost Accumulation:
        Charge direct labor → direct material → travel/T&E

    Phase 4 — Indirect Cost Allocation:
        Fringe @ 35% → Overhead @ 120% → G&A @ 15%

    Phase 5 — Billing & Revenue:
        Provisional billing → Fee accrual → AR invoice → AR payment

All pure domain tests — no database, no I/O.
"""

from datetime import date
from decimal import Decimal
from itertools import count
from typing import Any
from uuid import UUID, uuid4

import pytest

from finance_kernel.domain.event_validator import validate_payload_against_schema
from finance_kernel.domain.meaning_builder import MeaningBuilder, MeaningBuilderResult
from finance_kernel.domain.policy_selector import PolicySelector

# Schemas
from finance_kernel.domain.schemas.definitions.ap import (
    AP_INVOICE_RECEIVED_V1,
    AP_PAYMENT_V1,
)
from finance_kernel.domain.schemas.definitions.ar import (
    AR_INVOICE_ISSUED_V1,
    AR_PAYMENT_RECEIVED_V1,
)
from finance_kernel.domain.schemas.definitions.contract import (
    CONTRACT_BILLING_PROVISIONAL_V1,
    CONTRACT_COST_INCURRED_V1,
    CONTRACT_FEE_ACCRUAL_V1,
    CONTRACT_INDIRECT_ALLOCATION_V1,
)
from finance_kernel.domain.schemas.definitions.inventory import (
    INVENTORY_ISSUE_V1,
    INVENTORY_RECEIPT_V1,
)
from finance_kernel.domain.schemas.definitions.payroll import (
    PAYROLL_LABOR_DISTRIBUTION_V1,
    PAYROLL_TIMESHEET_V1,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry

# Profiles
from finance_modules.ap.profiles import (
    AP_INVOICE_PO_MATCHED,
    AP_PAYMENT,
)
from finance_modules.ar.profiles import (
    AR_INVOICE,
)
from finance_modules.ar.profiles import (
    AR_PAYMENT_RECEIVED as AR_PAYMENT,
)
from finance_modules.contracts.profiles import (
    CONTRACT_ALLOCATION_FRINGE,
    CONTRACT_ALLOCATION_GA,
    CONTRACT_ALLOCATION_OVERHEAD,
    CONTRACT_BILLING_COST_REIMB,
    CONTRACT_COST_DIRECT_LABOR,
    CONTRACT_COST_DIRECT_MATERIAL,
    CONTRACT_COST_TRAVEL,
    CONTRACT_FEE_FIXED,
)
from finance_modules.inventory.profiles import (
    INVENTORY_ISSUE_PRODUCTION,
    INVENTORY_RECEIPT,
)
from finance_modules.payroll.profiles import (
    LABOR_DISTRIBUTION_DIRECT,
    LABOR_DISTRIBUTION_INDIRECT,
    TIMESHEET_OVERTIME,
    TIMESHEET_REGULAR,
)

# =============================================================================
# STORY CONSTANTS
# =============================================================================

CONTRACT = "W912345-24-C-0001"
PO_NUMBER = "PO-2024-0100"
MO_ID = str(uuid4())  # Work order UUID for manufacturing order
SO_ID = str(uuid4())  # Sales order UUID
PROJECT = "PRJ-RADAR-001"
ORG_UNIT = "ORG-ACME-DEF"
COST_CENTER = "CC-MFG-001"
SUPPLIER = "SUP-STEEL-01"
CUSTOMER = "US-GOV-DOD"
EMPLOYEE = "EMP-JDOE"
DEPARTMENT = "DEPT-ENG"

EFFECTIVE_DATE = date(2024, 7, 15)

# Financial amounts (the story's numbers)
STEEL_QTY = "500"
STEEL_UNIT_COST = "20.00"
STEEL_TOTAL = "10000.00"

ISSUE_QTY = "200"
ISSUE_UNIT_COST = "20.00"
ISSUE_TOTAL = "4000.00"

REGULAR_HOURS = "8.00"
REGULAR_RATE = "75.00"
REGULAR_TOTAL = "600.00"

OT_HOURS = "4.00"
OT_RATE = "112.50"
OT_TOTAL = "450.00"

TOTAL_LABOR = "1050.00"
INDIRECT_LABOR = "200.00"

DIRECT_LABOR_COST = "1050.00"
DIRECT_MATERIAL_COST = "4000.00"
TRAVEL_COST = "1500.00"
TOTAL_DIRECT = "6550.00"

FRINGE_RATE = "0.35"
FRINGE_AMOUNT = "367.50"

OVERHEAD_RATE = "1.20"
OVERHEAD_AMOUNT = "1260.00"

GA_RATE = "0.15"
GA_AMOUNT = "982.50"

TOTAL_INDIRECT = "2610.00"
TOTAL_COST = "9160.00"
FEE_RATE = "0.08"
FEE_AMOUNT = "732.80"
TOTAL_BILLING = "9892.80"


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture(autouse=True, scope="module")
def _story_header():
    """Print the business narrative once at the start of the module."""
    global _event_counter
    _event_counter = count(1)
    W = 80
    print()
    print("#" * W)
    print("#" + " CONTRACT LIFECYCLE NARRATIVE ".center(W - 2) + "#")
    print("#" + f" {CONTRACT} ".center(W - 2) + "#")
    print("#" + " Acme Defense Corp — 10 Radar Assemblies (CPFF) ".center(W - 2) + "#")
    print("#" * W)
    print()
    print("  Story: Acme Defense Corp fulfills a CPFF contract to manufacture")
    print("  10 radar assemblies for the US DoD. This test traces every economic")
    print("  event from steel procurement through final payment collection.")
    print()
    print(f"  Contract:    {CONTRACT}")
    print(f"  PO:          {PO_NUMBER}")
    print(f"  Project:     {PROJECT}")
    print(f"  Customer:    {CUSTOMER}")
    print(f"  Supplier:    {SUPPLIER}")
    print(f"  Date:        {EFFECTIVE_DATE}")
    print()
    yield


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


def make_inventory_receipt(**overrides: Any) -> dict[str, Any]:
    """Steel received against PO-2024-0100."""
    payload: dict[str, Any] = {
        "receipt_id": str(uuid4()),
        "item_code": "RAW-STEEL-4140",
        "quantity": STEEL_QTY,
        "unit_cost": STEEL_UNIT_COST,
        "total_cost": STEEL_TOTAL,
        "currency": "USD",
        "warehouse_code": "WH-001",
        "po_number": PO_NUMBER,
        "supplier_party_code": SUPPLIER,
        "org_unit": ORG_UNIT,
        "cost_center": COST_CENTER,
    }
    payload.update(overrides)
    return payload


def make_ap_invoice_po(**overrides: Any) -> dict[str, Any]:
    """Supplier invoice matched to PO for steel."""
    payload: dict[str, Any] = {
        "invoice_id": str(uuid4()),
        "invoice_number": "INV-S-2024-001",
        "supplier_party_code": SUPPLIER,
        "invoice_date": "2024-07-16",
        "due_date": "2024-08-15",
        "gross_amount": STEEL_TOTAL,
        "tax_amount": "0.00",
        "net_amount": STEEL_TOTAL,
        "currency": "USD",
        "po_number": PO_NUMBER,
        "org_unit": ORG_UNIT,
        "cost_center": COST_CENTER,
    }
    payload.update(overrides)
    return payload


def make_ap_payment(**overrides: Any) -> dict[str, Any]:
    """Pay supplier for steel."""
    payload: dict[str, Any] = {
        "payment_id": str(uuid4()),
        "payment_reference": "PAY-2024-001",
        "supplier_party_code": SUPPLIER,
        "payment_date": "2024-08-10",
        "payment_amount": STEEL_TOTAL,
        "currency": "USD",
        "payment_method": "ACH",
        "bank_account_code": "BANK-OPS-001",
        "invoice_allocations": [
            {
                "invoice_id": str(uuid4()),
                "amount_applied": STEEL_TOTAL,
            }
        ],
        "org_unit": ORG_UNIT,
    }
    payload.update(overrides)
    return payload


def make_inventory_issue(**overrides: Any) -> dict[str, Any]:
    """Issue steel to production for MO."""
    payload: dict[str, Any] = {
        "issue_id": str(uuid4()),
        "item_code": "RAW-STEEL-4140",
        "quantity": ISSUE_QTY,
        "unit_cost": ISSUE_UNIT_COST,
        "total_cost": ISSUE_TOTAL,
        "currency": "USD",
        "warehouse_code": "WH-001",
        "issue_type": "PRODUCTION",
        "work_order_id": MO_ID,
        "org_unit": ORG_UNIT,
        "cost_center": COST_CENTER,
        "project": PROJECT,
    }
    payload.update(overrides)
    return payload


def make_timesheet_regular(**overrides: Any) -> dict[str, Any]:
    """Engineer logs 8 hours regular time on MO."""
    payload: dict[str, Any] = {
        "timesheet_id": str(uuid4()),
        "employee_party_code": EMPLOYEE,
        "work_date": "2024-07-15",
        "hours": REGULAR_HOURS,
        "pay_code": "REGULAR",
        "hourly_rate": REGULAR_RATE,
        "total_amount": REGULAR_TOTAL,
        "currency": "USD",
        "is_billable": True,
        "work_order_id": MO_ID,
        "org_unit": ORG_UNIT,
        "cost_center": COST_CENTER,
        "project": PROJECT,
        "department": DEPARTMENT,
    }
    payload.update(overrides)
    return payload


def make_timesheet_overtime(**overrides: Any) -> dict[str, Any]:
    """Engineer logs 4 hours overtime on MO."""
    payload: dict[str, Any] = {
        "timesheet_id": str(uuid4()),
        "employee_party_code": EMPLOYEE,
        "work_date": "2024-07-15",
        "hours": OT_HOURS,
        "pay_code": "OVERTIME",
        "hourly_rate": OT_RATE,
        "total_amount": OT_TOTAL,
        "currency": "USD",
        "is_billable": True,
        "work_order_id": MO_ID,
        "org_unit": ORG_UNIT,
        "cost_center": COST_CENTER,
        "project": PROJECT,
        "department": DEPARTMENT,
    }
    payload.update(overrides)
    return payload


def make_labor_dist_direct(**overrides: Any) -> dict[str, Any]:
    """Distribute $1,050 direct labor to WIP."""
    payload: dict[str, Any] = {
        "distribution_id": str(uuid4()),
        "pay_period_id": str(uuid4()),
        "employee_party_code": EMPLOYEE,
        "distribution_date": "2024-07-31",
        "labor_type": "DIRECT",
        "amount": TOTAL_LABOR,
        "currency": "USD",
        "work_order_id": MO_ID,
        "org_unit": ORG_UNIT,
        "cost_center": COST_CENTER,
        "project": PROJECT,
    }
    payload.update(overrides)
    return payload


def make_labor_dist_indirect(**overrides: Any) -> dict[str, Any]:
    """Distribute $200 indirect labor to overhead pool."""
    payload: dict[str, Any] = {
        "distribution_id": str(uuid4()),
        "pay_period_id": str(uuid4()),
        "employee_party_code": EMPLOYEE,
        "distribution_date": "2024-07-31",
        "labor_type": "INDIRECT",
        "amount": INDIRECT_LABOR,
        "currency": "USD",
        "org_unit": ORG_UNIT,
        "cost_center": COST_CENTER,
        "project": PROJECT,
    }
    payload.update(overrides)
    return payload


def make_contract_cost(cost_type: str, **overrides: Any) -> dict[str, Any]:
    """Charge cost to contract."""
    payload: dict[str, Any] = {
        "incurrence_id": str(uuid4()),
        "contract_number": CONTRACT,
        "clin_number": "0001",
        "incurrence_date": "2024-07-31",
        "cost_type": cost_type,
        "amount": "1000.00",
        "currency": "USD",
        "source_document_type": "TIMESHEET",
        "source_document_id": str(uuid4()),
        "org_unit": ORG_UNIT,
        "cost_center": COST_CENTER,
    }
    payload.update(overrides)
    return payload


def make_indirect_allocation(indirect_type: str, **overrides: Any) -> dict[str, Any]:
    """Allocate indirect costs to contract."""
    payload: dict[str, Any] = {
        "allocation_id": str(uuid4()),
        "contract_number": CONTRACT,
        "allocation_date": "2024-07-31",
        "period_start": "2024-07-01",
        "period_end": "2024-07-31",
        "indirect_type": indirect_type,
        "base_amount": "1050.00",
        "rate": "0.35",
        "allocated_amount": "367.50",
        "rate_type": "PROVISIONAL",
        "currency": "USD",
        "org_unit": ORG_UNIT,
        "cost_center": COST_CENTER,
    }
    payload.update(overrides)
    return payload


def make_billing_provisional(**overrides: Any) -> dict[str, Any]:
    """Bill government for period costs + fee."""
    payload: dict[str, Any] = {
        "billing_id": str(uuid4()),
        "invoice_number": "BILL-2024-07-001",
        "contract_number": CONTRACT,
        "billing_date": "2024-08-01",
        "period_start": "2024-07-01",
        "period_end": "2024-07-31",
        "billing_type": "COST_REIMBURSEMENT",
        "direct_labor_cost": DIRECT_LABOR_COST,
        "fringe_cost": FRINGE_AMOUNT,
        "overhead_cost": OVERHEAD_AMOUNT,
        "ga_cost": GA_AMOUNT,
        "material_cost": DIRECT_MATERIAL_COST,
        "subcontract_cost": "0.00",
        "travel_cost": TRAVEL_COST,
        "odc_cost": "0.00",
        "total_cost": TOTAL_COST,
        "fee_amount": FEE_AMOUNT,
        "total_billing": TOTAL_BILLING,
        "currency": "USD",
        "fringe_rate": FRINGE_RATE,
        "overhead_rate": OVERHEAD_RATE,
        "ga_rate": GA_RATE,
        "fee_rate": FEE_RATE,
        "customer_party_code": CUSTOMER,
        "org_unit": ORG_UNIT,
    }
    payload.update(overrides)
    return payload


def make_fee_accrual(**overrides: Any) -> dict[str, Any]:
    """Accrue fixed fee on CPFF contract."""
    payload: dict[str, Any] = {
        "accrual_id": str(uuid4()),
        "contract_number": CONTRACT,
        "accrual_date": "2024-07-31",
        "period_start": "2024-07-01",
        "period_end": "2024-07-31",
        "fee_type": "FIXED_FEE",
        "cost_base": TOTAL_COST,
        "fee_rate": FEE_RATE,
        "fee_amount": FEE_AMOUNT,
        "cumulative_fee": FEE_AMOUNT,
        "currency": "USD",
        "org_unit": ORG_UNIT,
    }
    payload.update(overrides)
    return payload


def make_ar_invoice(**overrides: Any) -> dict[str, Any]:
    """Issue AR invoice to government customer."""
    payload: dict[str, Any] = {
        "invoice_id": str(uuid4()),
        "invoice_number": "AR-2024-GOV-001",
        "customer_party_code": CUSTOMER,
        "invoice_date": "2024-08-01",
        "due_date": "2024-08-31",
        "gross_amount": TOTAL_BILLING,
        "tax_amount": "0.00",
        "net_amount": TOTAL_BILLING,
        "currency": "USD",
        "sales_order_id": SO_ID,
        "org_unit": ORG_UNIT,
        "cost_center": COST_CENTER,
        "project": PROJECT,
    }
    payload.update(overrides)
    return payload


def make_ar_payment(**overrides: Any) -> dict[str, Any]:
    """Government pays via wire transfer."""
    payload: dict[str, Any] = {
        "payment_id": str(uuid4()),
        "payment_reference": "RCPT-GOV-2024-001",
        "customer_party_code": CUSTOMER,
        "payment_date": "2024-08-25",
        "payment_amount": TOTAL_BILLING,
        "currency": "USD",
        "payment_method": "WIRE",
        "bank_account_code": "BANK-OPS-001",
        "invoice_allocations": [
            {
                "invoice_id": str(uuid4()),
                "amount_applied": TOTAL_BILLING,
            }
        ],
        "org_unit": ORG_UNIT,
    }
    payload.update(overrides)
    return payload


# =============================================================================
# NARRATIVE LOGGING
# =============================================================================

_event_counter = count(1)

# Key payload fields to display per event type (ordered for readability)
_PAYLOAD_DISPLAY: dict[str, list[str]] = {
    "inventory.receipt": [
        "item_code", "quantity", "unit_cost", "total_cost",
        "warehouse_code", "po_number", "supplier_party_code",
    ],
    "ap.invoice_received": [
        "invoice_number", "supplier_party_code", "gross_amount",
        "tax_amount", "net_amount", "po_number", "due_date",
    ],
    "ap.payment": [
        "payment_reference", "supplier_party_code", "payment_amount",
        "payment_method", "bank_account_code",
    ],
    "inventory.issue": [
        "item_code", "quantity", "unit_cost", "total_cost",
        "warehouse_code", "issue_type", "work_order_id",
    ],
    "payroll.timesheet": [
        "employee_party_code", "work_date", "hours", "pay_code",
        "hourly_rate", "total_amount", "work_order_id", "department",
    ],
    "payroll.labor_distribution": [
        "employee_party_code", "distribution_date", "labor_type",
        "amount", "work_order_id",
    ],
    "contract.cost_incurred": [
        "contract_number", "clin_number", "cost_type", "amount",
        "source_document_type", "labor_category",
    ],
    "contract.indirect_allocation": [
        "contract_number", "indirect_type", "base_amount",
        "rate", "allocated_amount", "rate_type",
    ],
    "contract.billing_provisional": [
        "invoice_number", "contract_number", "billing_type",
        "direct_labor_cost", "material_cost", "travel_cost",
        "fringe_cost", "overhead_cost", "ga_cost",
        "total_cost", "fee_amount", "total_billing",
    ],
    "contract.fee_accrual": [
        "contract_number", "fee_type", "cost_base",
        "fee_rate", "fee_amount",
    ],
    "ar.invoice_issued": [
        "invoice_number", "customer_party_code", "gross_amount",
        "tax_amount", "net_amount", "due_date",
    ],
    "ar.payment_received": [
        "payment_reference", "customer_party_code", "payment_amount",
        "payment_method", "bank_account_code",
    ],
}

# Dollar-formatted fields (show as $X,XXX.XX)
_MONEY_FIELDS = frozenset({
    "unit_cost", "total_cost", "gross_amount", "tax_amount", "net_amount",
    "payment_amount", "total_amount", "hourly_rate", "amount",
    "base_amount", "allocated_amount", "direct_labor_cost", "material_cost",
    "travel_cost", "fringe_cost", "overhead_cost", "ga_cost", "odc_cost",
    "subcontract_cost", "fee_amount", "total_billing",
    "cost_base", "cumulative_fee", "amount_applied",
})


def _fmt_money(val: str) -> str:
    """Format a numeric string as $X,XXX.XX."""
    try:
        d = Decimal(val)
        return f"${d:,.2f}"
    except Exception:
        return val


def _print_narrative(
    event_type: str,
    payload: dict[str, Any],
    profile: Any,
    result: "MeaningBuilderResult",
) -> None:
    """Print human-readable narrative of an economic event."""
    n = next(_event_counter)
    W = 80

    # Event header
    print()
    print(f"  Event {n}: {profile.description or profile.name} ".ljust(W, "\u2500"))
    print(f"    Profile:    {profile.name} (v{profile.version})")
    print(f"    Event:      {event_type}")
    print(f"    Economic:   {profile.meaning.economic_type}")
    if result.success and result.economic_event and result.economic_event.quantity is not None:
        print(f"    Quantity:   {result.economic_event.quantity}")
    print(f"    Date:       {EFFECTIVE_DATE}")
    print()

    # Payload details
    fields = _PAYLOAD_DISPLAY.get(event_type, list(payload.keys()))
    print("    Payload:")
    for key in fields:
        val = payload.get(key)
        if val is None:
            continue
        display = _fmt_money(str(val)) if key in _MONEY_FIELDS else str(val)
        label = key.replace("_", " ").replace("party code", "").strip()
        print(f"      {label:<28s} {display}")
    print()

    # Journal entries (ledger effects)
    print("    Journal Entries:")
    for eff in profile.ledger_effects:
        ledger = eff.ledger.upper()
        dr = eff.debit_role
        cr = eff.credit_role
        # Try to find the amount from the payload
        amt = _resolve_amount(event_type, eff, payload)
        amt_str = f"  {_fmt_money(amt)}" if amt else ""
        print(f"      {ledger:<12s} Dr {dr:<28s} / Cr {cr:<28s}{amt_str}")
    print()

    # Dimensions
    if result.success and result.economic_event and result.economic_event.dimensions:
        print("    Dimensions:")
        for k, v in result.economic_event.dimensions.items():
            print(f"      {k:<28s} {v}")
        print()

    # Guard / status
    if result.success:
        print("    Status: PASSED")
    elif result.guard_result and result.guard_result.rejected:
        print(f"    Status: REJECTED  reason={result.guard_result.reason_code}")
    elif result.guard_result and result.guard_result.blocked:
        print(f"    Status: BLOCKED  reason={result.guard_result.reason_code}")
    else:
        errs = ", ".join(e.code for e in result.validation_errors)
        print(f"    Status: VALIDATION FAILED  errors={errs}")
    print("  " + "\u2500" * (W - 2))


def _resolve_amount(
    event_type: str,
    effect: Any,
    payload: dict[str, Any],
) -> str | None:
    """Best-effort resolve the dollar amount for a ledger effect line."""
    # For billing with multiple effects, try to identify cost vs fee
    if event_type == "contract.billing_provisional":
        if "FEE" in effect.credit_role or "FEE" in effect.debit_role:
            return payload.get("fee_amount")
        if "WIP" in effect.credit_role:
            return payload.get("total_cost")
        if "BILLED" in effect.credit_role:
            return payload.get("total_cost")

    # Common amount fields in priority order
    for key in ("total_cost", "net_amount", "gross_amount", "payment_amount",
                "total_amount", "amount", "allocated_amount", "fee_amount",
                "total_billing"):
        val = payload.get(key)
        if val is not None:
            return str(val)
    return None


# =============================================================================
# HELPERS
# =============================================================================


def assert_schema_valid(payload: dict[str, Any], schema: Any) -> None:
    """Assert schema validation passes."""
    errors = validate_payload_against_schema(payload, schema)
    assert len(errors) == 0, f"Expected valid but got: {errors}"


def build_meaning(
    builder: MeaningBuilder,
    event_type: str,
    payload: dict[str, Any],
    profile: Any,
) -> MeaningBuilderResult:
    """Convenience wrapper for MeaningBuilder.build() with narrative output."""
    result = builder.build(
        event_id=uuid4(),
        event_type=event_type,
        payload=payload,
        effective_date=EFFECTIVE_DATE,
        profile=profile,
    )
    _print_narrative(event_type, payload, profile, result)
    return result


# =============================================================================
# PHASE 1: PROCUREMENT (PO CYCLE)
# =============================================================================


def _phase_banner(phase: int, title: str, detail: str) -> None:
    """Print a phase banner."""
    W = 80
    print()
    print("=" * W)
    print(f"  PHASE {phase}: {title}")
    print(f"  {detail}")
    print("=" * W)


class TestPhase1Procurement:
    """
    PO Cycle: Receive steel → Supplier invoice → Pay supplier.

    Acme purchases 500 units of 4140 steel at $20/unit from SUP-STEEL-01
    against PO-2024-0100.
    """

    @pytest.fixture(autouse=True)
    def _banner(self):
        _phase_banner(
            1,
            "PROCUREMENT (PO CYCLE)",
            "Acme buys 500 units of 4140 steel @ $20/unit from SUP-STEEL-01",
        )

    def test_receive_materials(self, builder: MeaningBuilder):
        """
        Event 1: Receive 500 units of 4140 steel at WH-001.
        Expect: INVENTORY_INCREASE, quantity=500,
                GL Dr:INVENTORY / Cr:GRNI,
                INVENTORY subledger Dr:STOCK_ON_HAND / Cr:IN_TRANSIT.
        """
        payload = make_inventory_receipt()
        assert_schema_valid(payload, INVENTORY_RECEIPT_V1)

        result = build_meaning(builder, "inventory.receipt", payload, INVENTORY_RECEIPT)

        assert result.success, f"Expected success, got: {result.guard_result}"
        assert result.economic_event.economic_type == "INVENTORY_INCREASE"
        assert result.economic_event.profile_id == "InventoryReceipt"
        assert result.economic_event.quantity == Decimal("500")

        # GL effect
        gl_effect = INVENTORY_RECEIPT.ledger_effects[0]
        assert gl_effect.debit_role == "INVENTORY"
        assert gl_effect.credit_role == "GRNI"

        # Inventory subledger
        inv_effect = INVENTORY_RECEIPT.ledger_effects[1]
        assert inv_effect.ledger == "INVENTORY"
        assert inv_effect.debit_role == "STOCK_ON_HAND"

        # Dimensions
        dims = result.economic_event.dimensions
        assert dims["org_unit"] == ORG_UNIT
        assert dims["cost_center"] == COST_CENTER

    def test_supplier_invoice(self, builder: MeaningBuilder):
        """
        Event 2: Supplier sends invoice INV-S-2024-001 for $10,000 matched to PO.
        Expect: LIABILITY_INCREASE,
                GL Dr:GRNI / Cr:ACCOUNTS_PAYABLE (clears receipt accrual),
                AP subledger Dr:INVOICE / Cr:SUPPLIER_BALANCE.
        """
        payload = make_ap_invoice_po()
        assert_schema_valid(payload, AP_INVOICE_RECEIVED_V1)

        result = build_meaning(
            builder, "ap.invoice_received", payload, AP_INVOICE_PO_MATCHED
        )

        assert result.success, f"Expected success, got: {result.guard_result}"
        assert result.economic_event.economic_type == "LIABILITY_INCREASE"
        assert result.economic_event.profile_id == "APInvoicePOMatched"

        # GL: Dr GRNI / Cr ACCOUNTS_PAYABLE
        gl_effect = AP_INVOICE_PO_MATCHED.ledger_effects[0]
        assert gl_effect.debit_role == "GRNI"
        assert gl_effect.credit_role == "ACCOUNTS_PAYABLE"

        # AP subledger
        ap_effect = AP_INVOICE_PO_MATCHED.ledger_effects[1]
        assert ap_effect.ledger == "AP"

        dims = result.economic_event.dimensions
        assert dims["org_unit"] == ORG_UNIT

    def test_supplier_payment(self, builder: MeaningBuilder):
        """
        Event 3: Pay supplier $10,000 via ACH.
        Expect: LIABILITY_DECREASE,
                GL Dr:ACCOUNTS_PAYABLE / Cr:CASH.
        """
        payload = make_ap_payment()
        assert_schema_valid(payload, AP_PAYMENT_V1)

        result = build_meaning(builder, "ap.payment", payload, AP_PAYMENT)

        assert result.success, f"Expected success, got: {result.guard_result}"
        assert result.economic_event.economic_type == "LIABILITY_DECREASE"
        assert result.economic_event.profile_id == "APPayment"

        gl_effect = AP_PAYMENT.ledger_effects[0]
        assert gl_effect.debit_role == "ACCOUNTS_PAYABLE"
        assert gl_effect.credit_role == "CASH"


# =============================================================================
# PHASE 2: MANUFACTURING (MO CYCLE)
# =============================================================================


class TestPhase2Manufacturing:
    """
    MO Cycle: Issue material → Regular labor → Overtime → Distribute labor.

    Manufacturing order MO-2024-0050 for radar assembly production.
    """

    @pytest.fixture(autouse=True)
    def _banner(self):
        _phase_banner(
            2,
            "MANUFACTURING (MO CYCLE)",
            "MO for radar assembly: issue material, record labor, distribute costs",
        )

    def test_issue_to_production(self, builder: MeaningBuilder):
        """
        Event 4: Issue 200 units of steel to production for MO.
        Expect: INVENTORY_TO_WIP, quantity=200,
                GL Dr:WIP / Cr:INVENTORY.
        """
        payload = make_inventory_issue()
        assert_schema_valid(payload, INVENTORY_ISSUE_V1)

        result = build_meaning(
            builder, "inventory.issue", payload, INVENTORY_ISSUE_PRODUCTION
        )

        assert result.success, f"Expected success, got: {result.guard_result}"
        assert result.economic_event.economic_type == "INVENTORY_TO_WIP"
        assert result.economic_event.quantity == Decimal("200")

        gl_effect = INVENTORY_ISSUE_PRODUCTION.ledger_effects[0]
        assert gl_effect.debit_role == "WIP"
        assert gl_effect.credit_role == "INVENTORY"

        dims = result.economic_event.dimensions
        assert dims["project"] == PROJECT

    def test_regular_labor(self, builder: MeaningBuilder):
        """
        Event 5: Engineer logs 8 hours regular time at $75/hr = $600.
        Expect: LABOR_ACCRUAL, quantity=8,
                GL Dr:WAGE_EXPENSE / Cr:ACCRUED_PAYROLL.
        """
        payload = make_timesheet_regular()
        assert_schema_valid(payload, PAYROLL_TIMESHEET_V1)

        result = build_meaning(
            builder, "timesheet.regular", payload, TIMESHEET_REGULAR
        )

        assert result.success
        assert result.economic_event.economic_type == "LABOR_ACCRUAL"
        assert result.economic_event.quantity == Decimal("8.00")

        gl_effect = TIMESHEET_REGULAR.ledger_effects[0]
        assert gl_effect.debit_role == "WAGE_EXPENSE"
        assert gl_effect.credit_role == "ACCRUED_PAYROLL"

        dims = result.economic_event.dimensions
        assert dims["project"] == PROJECT
        assert dims["department"] == DEPARTMENT

    def test_overtime_labor(self, builder: MeaningBuilder):
        """
        Event 6: Engineer logs 4 hours overtime at $112.50/hr = $450.
        Expect: LABOR_ACCRUAL, quantity=4,
                GL Dr:OVERTIME_EXPENSE / Cr:ACCRUED_PAYROLL.
        """
        payload = make_timesheet_overtime()
        assert_schema_valid(payload, PAYROLL_TIMESHEET_V1)

        result = build_meaning(
            builder, "timesheet.overtime", payload, TIMESHEET_OVERTIME
        )

        assert result.success
        assert result.economic_event.economic_type == "LABOR_ACCRUAL"
        assert result.economic_event.quantity == Decimal("4.00")

        gl_effect = TIMESHEET_OVERTIME.ledger_effects[0]
        assert gl_effect.debit_role == "OVERTIME_EXPENSE"
        assert gl_effect.credit_role == "ACCRUED_PAYROLL"

    def test_direct_labor_distribution(self, builder: MeaningBuilder):
        """
        Event 7: Distribute $1,050 direct labor to WIP for MO.
        Expect: LABOR_ALLOCATION,
                GL Dr:WIP / Cr:LABOR_CLEARING.
        """
        payload = make_labor_dist_direct()
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
        assert dims["project"] == PROJECT

    def test_indirect_labor_distribution(self, builder: MeaningBuilder):
        """
        Event 8: Distribute $200 indirect labor (supervision) to overhead pool.
        Expect: OVERHEAD_ALLOCATION,
                GL Dr:OVERHEAD_POOL / Cr:LABOR_CLEARING.
        """
        payload = make_labor_dist_indirect()
        assert_schema_valid(payload, PAYROLL_LABOR_DISTRIBUTION_V1)

        result = build_meaning(
            builder, "labor.distribution_indirect", payload, LABOR_DISTRIBUTION_INDIRECT
        )

        assert result.success
        assert result.economic_event.economic_type == "OVERHEAD_ALLOCATION"

        gl_effect = LABOR_DISTRIBUTION_INDIRECT.ledger_effects[0]
        assert gl_effect.debit_role == "OVERHEAD_POOL"
        assert gl_effect.credit_role == "LABOR_CLEARING"


# =============================================================================
# PHASE 3: CONTRACT COST ACCUMULATION
# =============================================================================


class TestPhase3ContractCosts:
    """
    Charge costs to Contract W912345-24-C-0001.

    Direct labor: $1,050 (CLIN 0001)
    Direct material: $4,000 (CLIN 0001)
    Travel/T&E: $1,500 (CLIN 0002)
    """

    @pytest.fixture(autouse=True)
    def _banner(self):
        _phase_banner(
            3,
            "CONTRACT COST ACCUMULATION",
            f"Charge direct costs to Contract {CONTRACT}",
        )

    def test_charge_direct_labor(self, builder: MeaningBuilder):
        """
        Event 9: Charge $1,050 direct labor to contract.
        Expect: CONTRACT_COST_INCURRENCE,
                GL Dr:WIP_DIRECT_LABOR / Cr:LABOR_CLEARING,
                CONTRACT subledger Dr:CONTRACT_COST_INCURRED / Cr:COST_CLEARING.
        """
        payload = make_contract_cost(
            "DIRECT_LABOR",
            amount=DIRECT_LABOR_COST,
            quantity=str(Decimal(REGULAR_HOURS) + Decimal(OT_HOURS)),
            unit_rate="87.50",  # blended rate
            labor_category="ENGINEER-SR",
            employee_party_code=EMPLOYEE,
        )
        assert_schema_valid(payload, CONTRACT_COST_INCURRED_V1)

        result = build_meaning(
            builder, "contract.cost_incurred", payload, CONTRACT_COST_DIRECT_LABOR
        )

        assert result.success, f"Expected success, got: {result.guard_result}"
        assert result.economic_event.economic_type == "CONTRACT_COST_INCURRENCE"
        assert result.economic_event.profile_id == "ContractCostDirectLabor"

        # GL effect
        gl_effect = CONTRACT_COST_DIRECT_LABOR.ledger_effects[0]
        assert gl_effect.debit_role == "WIP_DIRECT_LABOR"
        assert gl_effect.credit_role == "LABOR_CLEARING"

        # CONTRACT subledger
        contract_effect = CONTRACT_COST_DIRECT_LABOR.ledger_effects[1]
        assert contract_effect.ledger == "CONTRACT"
        assert contract_effect.debit_role == "CONTRACT_COST_INCURRED"

        # Dimensions
        dims = result.economic_event.dimensions
        assert dims["contract_number"] == CONTRACT
        assert dims["org_unit"] == ORG_UNIT
        assert dims["labor_category"] == "ENGINEER-SR"

    def test_charge_direct_material(self, builder: MeaningBuilder):
        """
        Event 10: Charge $4,000 direct material to contract.
        Expect: CONTRACT_COST_INCURRENCE,
                GL Dr:WIP_DIRECT_MATERIAL / Cr:MATERIAL_CLEARING.
        """
        payload = make_contract_cost(
            "DIRECT_MATERIAL",
            amount=DIRECT_MATERIAL_COST,
            source_document_type="MATERIAL_ISSUE",
            clin_number="0001",
        )
        assert_schema_valid(payload, CONTRACT_COST_INCURRED_V1)

        result = build_meaning(
            builder, "contract.cost_incurred", payload, CONTRACT_COST_DIRECT_MATERIAL
        )

        assert result.success
        assert result.economic_event.economic_type == "CONTRACT_COST_INCURRENCE"
        assert result.economic_event.profile_id == "ContractCostDirectMaterial"

        gl_effect = CONTRACT_COST_DIRECT_MATERIAL.ledger_effects[0]
        assert gl_effect.debit_role == "WIP_DIRECT_MATERIAL"
        assert gl_effect.credit_role == "MATERIAL_CLEARING"

        dims = result.economic_event.dimensions
        assert dims["contract_number"] == CONTRACT
        assert dims["clin_number"] == "0001"

    def test_charge_travel(self, builder: MeaningBuilder):
        """
        Event 11: Charge $1,500 T&E (site visit) to contract CLIN 0002.
        Expect: CONTRACT_COST_INCURRENCE,
                GL Dr:WIP_TRAVEL / Cr:EXPENSE_CLEARING.
        """
        payload = make_contract_cost(
            "TRAVEL",
            amount=TRAVEL_COST,
            source_document_type="EXPENSE_REPORT",
            clin_number="0002",
        )
        assert_schema_valid(payload, CONTRACT_COST_INCURRED_V1)

        result = build_meaning(
            builder, "contract.cost_incurred", payload, CONTRACT_COST_TRAVEL
        )

        assert result.success
        assert result.economic_event.economic_type == "CONTRACT_COST_INCURRENCE"
        assert result.economic_event.profile_id == "ContractCostTravel"

        gl_effect = CONTRACT_COST_TRAVEL.ledger_effects[0]
        assert gl_effect.debit_role == "WIP_TRAVEL"
        assert gl_effect.credit_role == "EXPENSE_CLEARING"

        dims = result.economic_event.dimensions
        assert dims["contract_number"] == CONTRACT
        assert dims["clin_number"] == "0002"


# =============================================================================
# PHASE 4: INDIRECT COST ALLOCATION
# =============================================================================


class TestPhase4IndirectAllocation:
    """
    Allocate indirect costs to the contract.

    Fringe @ 35% on $1,050 labor = $367.50
    Overhead @ 120% on $1,050 labor = $1,260.00
    G&A @ 15% on $6,550 total direct = $982.50
    """

    @pytest.fixture(autouse=True)
    def _banner(self):
        _phase_banner(
            4,
            "INDIRECT COST ALLOCATION",
            "Fringe 35% | Overhead 120% | G&A 15%",
        )

    def test_allocate_fringe(self, builder: MeaningBuilder):
        """
        Event 12: Allocate fringe benefits at 35% of direct labor.
        Expect: INDIRECT_ALLOCATION,
                GL Dr:WIP_FRINGE / Cr:FRINGE_POOL_APPLIED.
        """
        payload = make_indirect_allocation(
            "FRINGE",
            base_amount=DIRECT_LABOR_COST,
            rate=FRINGE_RATE,
            allocated_amount=FRINGE_AMOUNT,
        )
        assert_schema_valid(payload, CONTRACT_INDIRECT_ALLOCATION_V1)

        result = build_meaning(
            builder, "contract.indirect_allocation", payload, CONTRACT_ALLOCATION_FRINGE
        )

        assert result.success
        assert result.economic_event.economic_type == "INDIRECT_ALLOCATION"
        assert result.economic_event.profile_id == "ContractAllocationFringe"

        gl_effect = CONTRACT_ALLOCATION_FRINGE.ledger_effects[0]
        assert gl_effect.debit_role == "WIP_FRINGE"
        assert gl_effect.credit_role == "FRINGE_POOL_APPLIED"

        dims = result.economic_event.dimensions
        assert dims["contract_number"] == CONTRACT

    def test_allocate_overhead(self, builder: MeaningBuilder):
        """
        Event 13: Allocate overhead at 120% of direct labor.
        Expect: INDIRECT_ALLOCATION,
                GL Dr:WIP_OVERHEAD / Cr:OVERHEAD_POOL_APPLIED.
        """
        payload = make_indirect_allocation(
            "OVERHEAD",
            base_amount=DIRECT_LABOR_COST,
            rate=OVERHEAD_RATE,
            allocated_amount=OVERHEAD_AMOUNT,
        )
        assert_schema_valid(payload, CONTRACT_INDIRECT_ALLOCATION_V1)

        result = build_meaning(
            builder,
            "contract.indirect_allocation",
            payload,
            CONTRACT_ALLOCATION_OVERHEAD,
        )

        assert result.success
        assert result.economic_event.economic_type == "INDIRECT_ALLOCATION"
        assert result.economic_event.profile_id == "ContractAllocationOverhead"

        gl_effect = CONTRACT_ALLOCATION_OVERHEAD.ledger_effects[0]
        assert gl_effect.debit_role == "WIP_OVERHEAD"
        assert gl_effect.credit_role == "OVERHEAD_POOL_APPLIED"

    def test_allocate_ga(self, builder: MeaningBuilder):
        """
        Event 14: Allocate G&A at 15% of total direct costs ($6,550).
        Expect: INDIRECT_ALLOCATION,
                GL Dr:WIP_GA / Cr:GA_POOL_APPLIED.
        """
        payload = make_indirect_allocation(
            "G_AND_A",
            base_amount=TOTAL_DIRECT,
            rate=GA_RATE,
            allocated_amount=GA_AMOUNT,
        )
        assert_schema_valid(payload, CONTRACT_INDIRECT_ALLOCATION_V1)

        result = build_meaning(
            builder, "contract.indirect_allocation", payload, CONTRACT_ALLOCATION_GA
        )

        assert result.success
        assert result.economic_event.economic_type == "INDIRECT_ALLOCATION"
        assert result.economic_event.profile_id == "ContractAllocationGA"

        gl_effect = CONTRACT_ALLOCATION_GA.ledger_effects[0]
        assert gl_effect.debit_role == "WIP_GA"
        assert gl_effect.credit_role == "GA_POOL_APPLIED"


# =============================================================================
# PHASE 5: BILLING & REVENUE
# =============================================================================


class TestPhase5BillingAndRevenue:
    """
    Bill government and collect payment.

    Total cost: $9,160.00
    Fixed fee (8%): $732.80
    Total billing: $9,892.80
    """

    @pytest.fixture(autouse=True)
    def _banner(self):
        _phase_banner(
            5,
            "BILLING & REVENUE",
            f"Bill US-GOV-DOD: cost {_fmt_money(TOTAL_COST)} + fee {_fmt_money(FEE_AMOUNT)} = {_fmt_money(TOTAL_BILLING)}",
        )

    def test_bill_government(self, builder: MeaningBuilder):
        """
        Event 15: Provisional billing for cost-reimbursement contract.
        Expect: CONTRACT_BILLING,
                GL Dr:UNBILLED_AR / Cr:WIP_BILLED + DEFERRED_FEE_REVENUE,
                CONTRACT subledger Dr:BILLED / Cr:COST_BILLED.
        """
        payload = make_billing_provisional()
        assert_schema_valid(payload, CONTRACT_BILLING_PROVISIONAL_V1)

        result = build_meaning(
            builder,
            "contract.billing_provisional",
            payload,
            CONTRACT_BILLING_COST_REIMB,
        )

        assert result.success, f"Expected success, got: {result.guard_result}"
        assert result.economic_event.economic_type == "CONTRACT_BILLING"
        assert result.economic_event.profile_id == "ContractBillingCostReimbursement"

        # GL effect: costs + fee (fee line handled by module mappings)
        gl_effect = CONTRACT_BILLING_COST_REIMB.ledger_effects[0]
        assert gl_effect.ledger == "GL"
        assert gl_effect.debit_role == "UNBILLED_AR"
        assert gl_effect.credit_role == "WIP_BILLED"

        # CONTRACT subledger
        contract_effect = CONTRACT_BILLING_COST_REIMB.ledger_effects[1]
        assert contract_effect.ledger == "CONTRACT"

        dims = result.economic_event.dimensions
        assert dims["contract_number"] == CONTRACT

    def test_accrue_fee(self, builder: MeaningBuilder):
        """
        Event 16: Accrue fixed fee of $732.80 on CPFF contract.
        Expect: FEE_ACCRUAL,
                GL Dr:DEFERRED_FEE_REVENUE / Cr:FEE_REVENUE_EARNED.
        """
        payload = make_fee_accrual()
        assert_schema_valid(payload, CONTRACT_FEE_ACCRUAL_V1)

        result = build_meaning(
            builder, "contract.fee_accrual", payload, CONTRACT_FEE_FIXED
        )

        assert result.success, f"Expected success, got: {result.guard_result}"
        assert result.economic_event.economic_type == "FEE_ACCRUAL"
        assert result.economic_event.profile_id == "ContractFeeFixedAccrual"

        gl_effect = CONTRACT_FEE_FIXED.ledger_effects[0]
        assert gl_effect.debit_role == "DEFERRED_FEE_REVENUE"
        assert gl_effect.credit_role == "FEE_REVENUE_EARNED"

        dims = result.economic_event.dimensions
        assert dims["contract_number"] == CONTRACT

    def test_ar_invoice(self, builder: MeaningBuilder):
        """
        Event 17: Issue AR invoice to US-GOV-DOD for $9,892.80.
        Expect: REVENUE_RECOGNITION,
                GL Dr:ACCOUNTS_RECEIVABLE / Cr:REVENUE,
                AR subledger Dr:CUSTOMER_BALANCE / Cr:INVOICE.
        """
        payload = make_ar_invoice()
        assert_schema_valid(payload, AR_INVOICE_ISSUED_V1)

        result = build_meaning(
            builder, "ar.invoice", payload, AR_INVOICE
        )

        assert result.success, f"Expected success, got: {result.guard_result}"
        assert result.economic_event.economic_type == "REVENUE_RECOGNITION"
        assert result.economic_event.profile_id == "ARInvoice"

        gl_effect = AR_INVOICE.ledger_effects[0]
        assert gl_effect.debit_role == "ACCOUNTS_RECEIVABLE"
        assert gl_effect.credit_role == "REVENUE"

        ar_effect = AR_INVOICE.ledger_effects[1]
        assert ar_effect.ledger == "AR"

        dims = result.economic_event.dimensions
        assert dims["org_unit"] == ORG_UNIT
        assert dims["project"] == PROJECT

    def test_collect_payment(self, builder: MeaningBuilder):
        """
        Event 18: Government pays $9,892.80 via wire transfer.
        Expect: ASSET_INCREASE,
                GL Dr:CASH / Cr:ACCOUNTS_RECEIVABLE.
        """
        payload = make_ar_payment()
        assert_schema_valid(payload, AR_PAYMENT_RECEIVED_V1)

        result = build_meaning(
            builder, "ar.payment", payload, AR_PAYMENT
        )

        assert result.success, f"Expected success, got: {result.guard_result}"
        assert result.economic_event.economic_type == "ASSET_INCREASE"
        assert result.economic_event.profile_id == "ARPaymentReceived"

        gl_effect = AR_PAYMENT.ledger_effects[0]
        assert gl_effect.debit_role == "CASH"
        assert gl_effect.credit_role == "ACCOUNTS_RECEIVABLE"


# =============================================================================
# CROSS-PHASE VERIFICATION
# =============================================================================


class TestCrossPhaseVerification:
    """
    Verify the complete story holds together numerically and dimensionally.
    """

    @pytest.fixture(autouse=True)
    def _banner(self):
        _phase_banner(
            6,
            "CROSS-PHASE VERIFICATION",
            "Numeric consistency and dimension propagation checks",
        )

    def test_cost_accumulation_totals(self, builder: MeaningBuilder):
        """
        Run all cost-related events and verify the billing total
        equals the sum of all direct + indirect + fee components.

        Direct labor:    $1,050.00
        Direct material: $4,000.00
        Travel/T&E:      $1,500.00
        ---
        Total direct:    $6,550.00
        Fringe (35%):      $367.50
        Overhead (120%): $1,260.00
        G&A (15%):         $982.50
        ---
        Total cost:      $9,160.00
        Fixed fee (8%):    $732.80
        ---
        Total billing:   $9,892.80
        """
        # Verify the math
        direct_labor = Decimal(DIRECT_LABOR_COST)
        direct_material = Decimal(DIRECT_MATERIAL_COST)
        travel = Decimal(TRAVEL_COST)
        total_direct = direct_labor + direct_material + travel
        assert total_direct == Decimal(TOTAL_DIRECT)

        fringe = (direct_labor * Decimal(FRINGE_RATE)).quantize(Decimal("0.01"))
        assert fringe == Decimal(FRINGE_AMOUNT)

        overhead = (direct_labor * Decimal(OVERHEAD_RATE)).quantize(Decimal("0.01"))
        assert overhead == Decimal(OVERHEAD_AMOUNT)

        ga = (total_direct * Decimal(GA_RATE)).quantize(Decimal("0.01"))
        assert ga == Decimal(GA_AMOUNT)

        total_cost = total_direct + fringe + overhead + ga
        assert total_cost == Decimal(TOTAL_COST)

        fee = (total_cost * Decimal(FEE_RATE)).quantize(Decimal("0.01"))
        assert fee == Decimal(FEE_AMOUNT)

        total_billing = total_cost + fee
        assert total_billing == Decimal(TOTAL_BILLING)

        # Now verify the billing payload carries these exact amounts
        billing_payload = make_billing_provisional()
        assert Decimal(billing_payload["direct_labor_cost"]) == direct_labor
        assert Decimal(billing_payload["material_cost"]) == direct_material
        assert Decimal(billing_payload["travel_cost"]) == travel
        assert Decimal(billing_payload["fringe_cost"]) == fringe
        assert Decimal(billing_payload["overhead_cost"]) == overhead
        assert Decimal(billing_payload["ga_cost"]) == ga
        assert Decimal(billing_payload["total_cost"]) == total_cost
        assert Decimal(billing_payload["fee_amount"]) == fee
        assert Decimal(billing_payload["total_billing"]) == total_billing

        # Verify the billing event passes through the system
        assert_schema_valid(billing_payload, CONTRACT_BILLING_PROVISIONAL_V1)
        result = build_meaning(
            builder,
            "contract.billing_provisional",
            billing_payload,
            CONTRACT_BILLING_COST_REIMB,
        )
        assert result.success

    def test_all_events_reference_same_contract(self, builder: MeaningBuilder):
        """
        All contract-related events carry the same contract number
        in their dimensions.
        """
        contract_events = [
            (
                "contract.cost_incurred",
                make_contract_cost("DIRECT_LABOR", amount=DIRECT_LABOR_COST),
                CONTRACT_COST_DIRECT_LABOR,
            ),
            (
                "contract.cost_incurred",
                make_contract_cost(
                    "DIRECT_MATERIAL",
                    amount=DIRECT_MATERIAL_COST,
                    source_document_type="MATERIAL_ISSUE",
                ),
                CONTRACT_COST_DIRECT_MATERIAL,
            ),
            (
                "contract.cost_incurred",
                make_contract_cost(
                    "TRAVEL",
                    amount=TRAVEL_COST,
                    source_document_type="EXPENSE_REPORT",
                    clin_number="0002",
                ),
                CONTRACT_COST_TRAVEL,
            ),
            (
                "contract.indirect_allocation",
                make_indirect_allocation(
                    "FRINGE",
                    base_amount=DIRECT_LABOR_COST,
                    rate=FRINGE_RATE,
                    allocated_amount=FRINGE_AMOUNT,
                ),
                CONTRACT_ALLOCATION_FRINGE,
            ),
            (
                "contract.indirect_allocation",
                make_indirect_allocation(
                    "OVERHEAD",
                    base_amount=DIRECT_LABOR_COST,
                    rate=OVERHEAD_RATE,
                    allocated_amount=OVERHEAD_AMOUNT,
                ),
                CONTRACT_ALLOCATION_OVERHEAD,
            ),
            (
                "contract.indirect_allocation",
                make_indirect_allocation(
                    "G_AND_A",
                    base_amount=TOTAL_DIRECT,
                    rate=GA_RATE,
                    allocated_amount=GA_AMOUNT,
                ),
                CONTRACT_ALLOCATION_GA,
            ),
            (
                "contract.billing_provisional",
                make_billing_provisional(),
                CONTRACT_BILLING_COST_REIMB,
            ),
            (
                "contract.fee_accrual",
                make_fee_accrual(),
                CONTRACT_FEE_FIXED,
            ),
        ]

        for event_type, payload, profile in contract_events:
            result = build_meaning(builder, event_type, payload, profile)
            assert result.success, (
                f"{event_type}/{profile.name} failed: {result.guard_result}"
            )
            dims = result.economic_event.dimensions
            assert dims["contract_number"] == CONTRACT, (
                f"{profile.name} has contract_number={dims.get('contract_number')}, "
                f"expected {CONTRACT}"
            )

    def test_project_dimension_propagated(self, builder: MeaningBuilder):
        """
        Verify project code appears in dimensions for all events
        that include 'project' in their profile dimensions.
        """
        project_events = [
            # Inventory issue to production
            (
                "inventory.issue",
                make_inventory_issue(),
                INVENTORY_ISSUE_PRODUCTION,
            ),
            # Timesheets
            (
                "timesheet.regular",
                make_timesheet_regular(),
                TIMESHEET_REGULAR,
            ),
            (
                "timesheet.overtime",
                make_timesheet_overtime(),
                TIMESHEET_OVERTIME,
            ),
            # Direct labor distribution
            (
                "labor.distribution_direct",
                make_labor_dist_direct(),
                LABOR_DISTRIBUTION_DIRECT,
            ),
            # AR invoice
            (
                "ar.invoice",
                make_ar_invoice(),
                AR_INVOICE,
            ),
        ]

        for event_type, payload, profile in project_events:
            result = build_meaning(builder, event_type, payload, profile)
            assert result.success, (
                f"{event_type}/{profile.name} failed: {result.guard_result}"
            )
            dims = result.economic_event.dimensions
            assert dims.get("project") == PROJECT, (
                f"{profile.name} has project={dims.get('project')}, expected {PROJECT}"
            )
