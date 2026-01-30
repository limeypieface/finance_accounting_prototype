"""
Comprehensive tests for contract billing schemas and profiles.

Tests Phase C: Contract/Billing Schemas and Profiles for DCAA compliance.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

# Import to register schemas
import finance_kernel.domain.schemas.definitions.contract  # noqa: F401

# Contract profiles now live in finance_modules.contracts
from finance_modules.contracts.profiles import register as register_contracts
register_contracts()

from finance_kernel.domain.accounting_policy import AccountingPolicy, LedgerEffect
from finance_kernel.domain.schemas.registry import EventSchemaRegistry
from finance_kernel.domain.policy_selector import PolicySelector
from finance_kernel.domain.event_validator import validate_payload_against_schema


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
# Test Fixtures
# ============================================================================


@pytest.fixture
def cost_incurred_payload():
    """Valid contract.cost_incurred event payload."""
    return {
        "incurrence_id": str(uuid4()),
        "contract_number": "FA8106-21-C-0001",
        "clin_number": "0001",
        "incurrence_date": "2024-03-15",
        "cost_type": "DIRECT_LABOR",
        "amount": Decimal("5000.00"),
        "currency": "USD",
        "quantity": Decimal("40.00"),
        "unit_rate": Decimal("125.00"),
        "source_document_type": "TIMESHEET",
        "source_document_id": str(uuid4()),
        "labor_category": "Engineer III",
        "employee_party_code": "EMP-001",
        "org_unit": "ORG-US",
        "cost_center": "CC-ENG",
    }


@pytest.fixture
def billing_provisional_payload():
    """Valid contract.billing_provisional event payload."""
    return {
        "billing_id": str(uuid4()),
        "invoice_number": "INV-2024-001",
        "contract_number": "FA8106-21-C-0001",
        "billing_date": "2024-03-31",
        "period_start": "2024-03-01",
        "period_end": "2024-03-31",
        "billing_type": "COST_REIMBURSEMENT",
        "direct_labor_cost": Decimal("50000.00"),
        "fringe_cost": Decimal("15000.00"),
        "overhead_cost": Decimal("35000.00"),
        "ga_cost": Decimal("10000.00"),
        "material_cost": Decimal("5000.00"),
        "subcontract_cost": Decimal("20000.00"),
        "travel_cost": Decimal("2500.00"),
        "odc_cost": Decimal("1500.00"),
        "total_cost": Decimal("139000.00"),
        "fee_amount": Decimal("11120.00"),
        "total_billing": Decimal("150120.00"),
        "currency": "USD",
        "fringe_rate": Decimal("0.30"),
        "overhead_rate": Decimal("0.70"),
        "ga_rate": Decimal("0.10"),
        "fee_rate": Decimal("0.08"),
        "customer_party_code": "USAF-AFMC",
        "org_unit": "ORG-US",
    }


@pytest.fixture
def fee_accrual_payload():
    """Valid contract.fee_accrual event payload."""
    return {
        "accrual_id": str(uuid4()),
        "contract_number": "FA8106-21-C-0001",
        "accrual_date": "2024-03-31",
        "period_start": "2024-03-01",
        "period_end": "2024-03-31",
        "fee_type": "FIXED_FEE",
        "cost_base": Decimal("139000.00"),
        "fee_rate": Decimal("0.08"),
        "fee_amount": Decimal("11120.00"),
        "cumulative_fee": Decimal("45000.00"),
        "ceiling_fee": Decimal("100000.00"),
        "currency": "USD",
        "org_unit": "ORG-US",
    }


@pytest.fixture
def indirect_allocation_payload():
    """Valid contract.indirect_allocation event payload."""
    return {
        "allocation_id": str(uuid4()),
        "contract_number": "FA8106-21-C-0001",
        "allocation_date": "2024-03-31",
        "period_start": "2024-03-01",
        "period_end": "2024-03-31",
        "indirect_type": "OVERHEAD",
        "base_amount": Decimal("50000.00"),
        "rate": Decimal("0.70"),
        "allocated_amount": Decimal("35000.00"),
        "rate_type": "PROVISIONAL",
        "currency": "USD",
        "org_unit": "ORG-US",
        "cost_center": "CC-ENG",
    }


@pytest.fixture
def rate_adjustment_payload():
    """Valid contract.rate_adjustment event payload."""
    return {
        "adjustment_id": str(uuid4()),
        "contract_number": "FA8106-21-C-0001",
        "adjustment_date": "2025-06-30",
        "fiscal_year": 2024,
        "indirect_type": "OVERHEAD",
        "provisional_rate": Decimal("0.70"),
        "final_rate": Decimal("0.72"),
        "base_amount": Decimal("500000.00"),
        "adjustment_amount": Decimal("10000.00"),
        "currency": "USD",
        "org_unit": "ORG-US",
    }


# ============================================================================
# Schema Registration Tests
# ============================================================================


class TestContractSchemaRegistration:
    """Test that all contract schemas are properly registered."""

    def test_cost_incurred_schema_registered(self):
        """contract.cost_incurred schema should be registered."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        assert schema is not None
        assert schema.event_type == "contract.cost_incurred"
        assert schema.version == 1

    def test_billing_provisional_schema_registered(self):
        """contract.billing_provisional schema should be registered."""
        schema = EventSchemaRegistry.get("contract.billing_provisional", 1)
        assert schema is not None
        assert schema.event_type == "contract.billing_provisional"
        assert schema.version == 1

    def test_fee_accrual_schema_registered(self):
        """contract.fee_accrual schema should be registered."""
        schema = EventSchemaRegistry.get("contract.fee_accrual", 1)
        assert schema is not None
        assert schema.event_type == "contract.fee_accrual"
        assert schema.version == 1

    def test_indirect_allocation_schema_registered(self):
        """contract.indirect_allocation schema should be registered."""
        schema = EventSchemaRegistry.get("contract.indirect_allocation", 1)
        assert schema is not None
        assert schema.event_type == "contract.indirect_allocation"
        assert schema.version == 1

    def test_rate_adjustment_schema_registered(self):
        """contract.rate_adjustment schema should be registered."""
        schema = EventSchemaRegistry.get("contract.rate_adjustment", 1)
        assert schema is not None
        assert schema.event_type == "contract.rate_adjustment"
        assert schema.version == 1


# ============================================================================
# contract.cost_incurred Schema Tests
# ============================================================================


class TestCostIncurredSchema:
    """Tests for contract.cost_incurred schema validation."""

    def test_valid_direct_labor_payload(self, cost_incurred_payload):
        """Valid direct labor payload should pass validation."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        errors = validate_payload_against_schema(cost_incurred_payload, schema)
        assert errors == []

    def test_all_cost_types(self, cost_incurred_payload):
        """All cost types should be valid."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        cost_types = [
            "DIRECT_LABOR",
            "DIRECT_MATERIAL",
            "SUBCONTRACT",
            "TRAVEL",
            "ODC",
            "INDIRECT_FRINGE",
            "INDIRECT_OVERHEAD",
            "INDIRECT_GA",
        ]
        for cost_type in cost_types:
            payload = cost_incurred_payload.copy()
            payload["cost_type"] = cost_type
            errors = validate_payload_against_schema(payload, schema)
            assert errors == [], f"Cost type {cost_type} should be valid"

    def test_invalid_cost_type(self, cost_incurred_payload):
        """Invalid cost type should fail validation."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        cost_incurred_payload["cost_type"] = "INVALID_TYPE"
        errors = validate_payload_against_schema(cost_incurred_payload, schema)
        assert len(errors) > 0
        assert any("cost_type" in str(e.field or "") for e in errors)

    def test_all_source_document_types(self, cost_incurred_payload):
        """All source document types should be valid."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        doc_types = [
            "TIMESHEET",
            "AP_INVOICE",
            "MATERIAL_ISSUE",
            "EXPENSE_REPORT",
            "INDIRECT_ALLOCATION",
        ]
        for doc_type in doc_types:
            payload = cost_incurred_payload.copy()
            payload["source_document_type"] = doc_type
            errors = validate_payload_against_schema(payload, schema)
            assert errors == [], f"Source document type {doc_type} should be valid"

    def test_missing_required_contract_number(self, cost_incurred_payload):
        """Missing contract number should fail validation."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        del cost_incurred_payload["contract_number"]
        errors = validate_payload_against_schema(cost_incurred_payload, schema)
        assert len(errors) > 0
        assert any("contract_number" in str(e.field or "") for e in errors)

    def test_zero_amount_rejected(self, cost_incurred_payload):
        """Zero cost amount should fail validation."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        cost_incurred_payload["amount"] = Decimal("0")
        errors = validate_payload_against_schema(cost_incurred_payload, schema)
        assert len(errors) > 0
        assert any("amount" in str(e.field or "") for e in errors)

    def test_negative_amount_rejected(self, cost_incurred_payload):
        """Negative cost amount should fail validation."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        cost_incurred_payload["amount"] = Decimal("-100.00")
        errors = validate_payload_against_schema(cost_incurred_payload, schema)
        assert len(errors) > 0

    def test_optional_clin_number(self, cost_incurred_payload):
        """CLIN number should be optional."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        cost_incurred_payload["clin_number"] = None
        errors = validate_payload_against_schema(cost_incurred_payload, schema)
        assert errors == []

    def test_optional_labor_fields(self, cost_incurred_payload):
        """Labor-specific fields should be optional."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        payload = cost_incurred_payload.copy()
        payload["labor_category"] = None
        payload["employee_party_code"] = None
        payload["quantity"] = None
        payload["unit_rate"] = None
        errors = validate_payload_against_schema(payload, schema)
        assert errors == []

    def test_dimensions_extracted(self, cost_incurred_payload):
        """Dimensions should be correctly extracted from payload."""
        # Org unit is required, cost_center is optional
        assert "org_unit" in cost_incurred_payload
        assert cost_incurred_payload["org_unit"] == "ORG-US"
        assert cost_incurred_payload["cost_center"] == "CC-ENG"


# ============================================================================
# contract.billing_provisional Schema Tests
# ============================================================================


class TestBillingProvisionalSchema:
    """Tests for contract.billing_provisional schema validation."""

    def test_valid_cost_reimbursement_billing(self, billing_provisional_payload):
        """Valid cost reimbursement billing should pass validation."""
        schema = EventSchemaRegistry.get("contract.billing_provisional", 1)
        errors = validate_payload_against_schema(billing_provisional_payload, schema)
        assert errors == []

    def test_all_billing_types(self, billing_provisional_payload):
        """All billing types should be valid."""
        schema = EventSchemaRegistry.get("contract.billing_provisional", 1)
        billing_types = [
            "COST_REIMBURSEMENT",
            "TIME_AND_MATERIALS",
            "LABOR_HOUR",
            "FIXED_PRICE_MILESTONE",
        ]
        for billing_type in billing_types:
            payload = billing_provisional_payload.copy()
            payload["billing_type"] = billing_type
            errors = validate_payload_against_schema(payload, schema)
            assert errors == [], f"Billing type {billing_type} should be valid"

    def test_invalid_billing_type(self, billing_provisional_payload):
        """Invalid billing type should fail validation."""
        schema = EventSchemaRegistry.get("contract.billing_provisional", 1)
        billing_provisional_payload["billing_type"] = "INVALID"
        errors = validate_payload_against_schema(billing_provisional_payload, schema)
        assert len(errors) > 0

    def test_cost_breakdown_fields_required(self, billing_provisional_payload):
        """All cost breakdown fields should be required."""
        schema = EventSchemaRegistry.get("contract.billing_provisional", 1)
        required_cost_fields = [
            "direct_labor_cost",
            "fringe_cost",
            "overhead_cost",
            "ga_cost",
            "material_cost",
            "subcontract_cost",
            "travel_cost",
            "odc_cost",
            "total_cost",
        ]
        for field in required_cost_fields:
            payload = billing_provisional_payload.copy()
            del payload[field]
            errors = validate_payload_against_schema(payload, schema)
            assert len(errors) > 0, f"Field {field} should be required"

    def test_negative_costs_rejected(self, billing_provisional_payload):
        """Negative cost values should fail validation."""
        schema = EventSchemaRegistry.get("contract.billing_provisional", 1)
        billing_provisional_payload["direct_labor_cost"] = Decimal("-1000.00")
        errors = validate_payload_against_schema(billing_provisional_payload, schema)
        assert len(errors) > 0

    def test_rate_fields_optional(self, billing_provisional_payload):
        """Rate fields should be optional."""
        schema = EventSchemaRegistry.get("contract.billing_provisional", 1)
        payload = billing_provisional_payload.copy()
        payload["fringe_rate"] = None
        payload["overhead_rate"] = None
        payload["ga_rate"] = None
        payload["fee_rate"] = None
        errors = validate_payload_against_schema(payload, schema)
        assert errors == []

    def test_period_dates_required(self, billing_provisional_payload):
        """Period start and end dates should be required."""
        schema = EventSchemaRegistry.get("contract.billing_provisional", 1)

        payload1 = billing_provisional_payload.copy()
        del payload1["period_start"]
        errors1 = validate_payload_against_schema(payload1, schema)
        assert len(errors1) > 0

        payload2 = billing_provisional_payload.copy()
        del payload2["period_end"]
        errors2 = validate_payload_against_schema(payload2, schema)
        assert len(errors2) > 0


# ============================================================================
# contract.fee_accrual Schema Tests
# ============================================================================


class TestFeeAccrualSchema:
    """Tests for contract.fee_accrual schema validation."""

    def test_valid_fixed_fee_accrual(self, fee_accrual_payload):
        """Valid fixed fee accrual should pass validation."""
        schema = EventSchemaRegistry.get("contract.fee_accrual", 1)
        errors = validate_payload_against_schema(fee_accrual_payload, schema)
        assert errors == []

    def test_all_fee_types(self, fee_accrual_payload):
        """All fee types should be valid."""
        schema = EventSchemaRegistry.get("contract.fee_accrual", 1)
        fee_types = ["FIXED_FEE", "INCENTIVE_FEE", "AWARD_FEE"]
        for fee_type in fee_types:
            payload = fee_accrual_payload.copy()
            payload["fee_type"] = fee_type
            errors = validate_payload_against_schema(payload, schema)
            assert errors == [], f"Fee type {fee_type} should be valid"

    def test_invalid_fee_type(self, fee_accrual_payload):
        """Invalid fee type should fail validation."""
        schema = EventSchemaRegistry.get("contract.fee_accrual", 1)
        fee_accrual_payload["fee_type"] = "INVALID_FEE"
        errors = validate_payload_against_schema(fee_accrual_payload, schema)
        assert len(errors) > 0

    def test_ceiling_fee_optional(self, fee_accrual_payload):
        """Ceiling fee should be optional."""
        schema = EventSchemaRegistry.get("contract.fee_accrual", 1)
        fee_accrual_payload["ceiling_fee"] = None
        errors = validate_payload_against_schema(fee_accrual_payload, schema)
        assert errors == []

    def test_cumulative_fee_required(self, fee_accrual_payload):
        """Cumulative fee should be required for tracking."""
        schema = EventSchemaRegistry.get("contract.fee_accrual", 1)
        del fee_accrual_payload["cumulative_fee"]
        errors = validate_payload_against_schema(fee_accrual_payload, schema)
        assert len(errors) > 0


# ============================================================================
# contract.indirect_allocation Schema Tests
# ============================================================================


class TestIndirectAllocationSchema:
    """Tests for contract.indirect_allocation schema validation."""

    def test_valid_overhead_allocation(self, indirect_allocation_payload):
        """Valid overhead allocation should pass validation."""
        schema = EventSchemaRegistry.get("contract.indirect_allocation", 1)
        errors = validate_payload_against_schema(indirect_allocation_payload, schema)
        assert errors == []

    def test_all_indirect_types(self, indirect_allocation_payload):
        """All indirect cost types should be valid."""
        schema = EventSchemaRegistry.get("contract.indirect_allocation", 1)
        indirect_types = ["FRINGE", "OVERHEAD", "G_AND_A", "MATERIAL_HANDLING"]
        for indirect_type in indirect_types:
            payload = indirect_allocation_payload.copy()
            payload["indirect_type"] = indirect_type
            errors = validate_payload_against_schema(payload, schema)
            assert errors == [], f"Indirect type {indirect_type} should be valid"

    def test_all_rate_types(self, indirect_allocation_payload):
        """All rate types should be valid."""
        schema = EventSchemaRegistry.get("contract.indirect_allocation", 1)
        rate_types = ["PROVISIONAL", "ACTUAL", "FINAL"]
        for rate_type in rate_types:
            payload = indirect_allocation_payload.copy()
            payload["rate_type"] = rate_type
            errors = validate_payload_against_schema(payload, schema)
            assert errors == [], f"Rate type {rate_type} should be valid"

    def test_invalid_indirect_type(self, indirect_allocation_payload):
        """Invalid indirect type should fail validation."""
        schema = EventSchemaRegistry.get("contract.indirect_allocation", 1)
        indirect_allocation_payload["indirect_type"] = "INVALID"
        errors = validate_payload_against_schema(indirect_allocation_payload, schema)
        assert len(errors) > 0


# ============================================================================
# contract.rate_adjustment Schema Tests
# ============================================================================


class TestRateAdjustmentSchema:
    """Tests for contract.rate_adjustment schema validation."""

    def test_valid_rate_adjustment(self, rate_adjustment_payload):
        """Valid rate adjustment should pass validation."""
        schema = EventSchemaRegistry.get("contract.rate_adjustment", 1)
        errors = validate_payload_against_schema(rate_adjustment_payload, schema)
        assert errors == []

    def test_fiscal_year_constraints(self, rate_adjustment_payload):
        """Fiscal year should be within valid range."""
        schema = EventSchemaRegistry.get("contract.rate_adjustment", 1)

        # Valid year
        rate_adjustment_payload["fiscal_year"] = 2024
        errors = validate_payload_against_schema(rate_adjustment_payload, schema)
        assert errors == []

        # Year below minimum
        payload_low = rate_adjustment_payload.copy()
        payload_low["fiscal_year"] = 1999
        errors_low = validate_payload_against_schema(payload_low, schema)
        assert len(errors_low) > 0

        # Year above maximum
        payload_high = rate_adjustment_payload.copy()
        payload_high["fiscal_year"] = 2101
        errors_high = validate_payload_against_schema(payload_high, schema)
        assert len(errors_high) > 0

    def test_negative_adjustment_allowed(self, rate_adjustment_payload):
        """Negative adjustment amounts should be allowed (rate decreases)."""
        schema = EventSchemaRegistry.get("contract.rate_adjustment", 1)
        rate_adjustment_payload["adjustment_amount"] = Decimal("-5000.00")
        errors = validate_payload_against_schema(rate_adjustment_payload, schema)
        # Should pass - adjustments can be negative when final rate < provisional
        assert errors == []

    def test_required_rate_fields(self, rate_adjustment_payload):
        """Provisional and final rates should be required."""
        schema = EventSchemaRegistry.get("contract.rate_adjustment", 1)

        payload1 = rate_adjustment_payload.copy()
        del payload1["provisional_rate"]
        errors1 = validate_payload_against_schema(payload1, schema)
        assert len(errors1) > 0

        payload2 = rate_adjustment_payload.copy()
        del payload2["final_rate"]
        errors2 = validate_payload_against_schema(payload2, schema)
        assert len(errors2) > 0


# ============================================================================
# Profile Registration Tests
# ============================================================================


class TestContractProfileRegistration:
    """Test that all contract profiles are properly registered."""

    def test_direct_labor_profile_registered(self):
        """ContractCostDirectLabor profile should be registered."""
        profile = PolicySelector.get("ContractCostDirectLabor")
        assert profile is not None
        assert profile.trigger.event_type == "contract.cost_incurred"

    def test_direct_material_profile_registered(self):
        """ContractCostDirectMaterial profile should be registered."""
        profile = PolicySelector.get("ContractCostDirectMaterial")
        assert profile is not None

    def test_subcontract_profile_registered(self):
        """ContractCostSubcontract profile should be registered."""
        profile = PolicySelector.get("ContractCostSubcontract")
        assert profile is not None

    def test_travel_profile_registered(self):
        """ContractCostTravel profile should be registered."""
        profile = PolicySelector.get("ContractCostTravel")
        assert profile is not None

    def test_odc_profile_registered(self):
        """ContractCostODC profile should be registered."""
        profile = PolicySelector.get("ContractCostODC")
        assert profile is not None

    def test_indirect_fringe_profile_registered(self):
        """ContractCostIndirectFringe profile should be registered."""
        profile = PolicySelector.get("ContractCostIndirectFringe")
        assert profile is not None

    def test_indirect_overhead_profile_registered(self):
        """ContractCostIndirectOverhead profile should be registered."""
        profile = PolicySelector.get("ContractCostIndirectOverhead")
        assert profile is not None

    def test_indirect_ga_profile_registered(self):
        """ContractCostIndirectGA profile should be registered."""
        profile = PolicySelector.get("ContractCostIndirectGA")
        assert profile is not None

    def test_billing_cost_reimbursement_profile_registered(self):
        """ContractBillingCostReimbursement profile should be registered."""
        profile = PolicySelector.get("ContractBillingCostReimbursement")
        assert profile is not None

    def test_billing_tm_profile_registered(self):
        """ContractBillingTimeAndMaterials profile should be registered."""
        profile = PolicySelector.get("ContractBillingTimeAndMaterials")
        assert profile is not None

    def test_billing_labor_hour_profile_registered(self):
        """ContractBillingLaborHour profile should be registered."""
        profile = PolicySelector.get("ContractBillingLaborHour")
        assert profile is not None

    def test_fee_fixed_profile_registered(self):
        """ContractFeeFixedAccrual profile should be registered."""
        profile = PolicySelector.get("ContractFeeFixedAccrual")
        assert profile is not None

    def test_fee_incentive_profile_registered(self):
        """ContractFeeIncentiveAccrual profile should be registered."""
        profile = PolicySelector.get("ContractFeeIncentiveAccrual")
        assert profile is not None

    def test_fee_award_profile_registered(self):
        """ContractFeeAwardAccrual profile should be registered."""
        profile = PolicySelector.get("ContractFeeAwardAccrual")
        assert profile is not None

    def test_allocation_fringe_profile_registered(self):
        """ContractAllocationFringe profile should be registered."""
        profile = PolicySelector.get("ContractAllocationFringe")
        assert profile is not None

    def test_allocation_overhead_profile_registered(self):
        """ContractAllocationOverhead profile should be registered."""
        profile = PolicySelector.get("ContractAllocationOverhead")
        assert profile is not None

    def test_allocation_ga_profile_registered(self):
        """ContractAllocationGA profile should be registered."""
        profile = PolicySelector.get("ContractAllocationGA")
        assert profile is not None

    def test_rate_adjustment_profile_registered(self):
        """ContractRateAdjustment profile should be registered."""
        profile = PolicySelector.get("ContractRateAdjustment")
        assert profile is not None


# ============================================================================
# Profile Trigger Tests
# ============================================================================


class TestCostIncurredPolicyTriggers:
    """Test that cost incurrence profiles trigger correctly by cost_type."""

    def test_direct_labor_trigger(self):
        """Direct labor profile should trigger on DIRECT_LABOR cost type."""
        profile = PolicySelector.get("ContractCostDirectLabor")
        assert profile.trigger.event_type == "contract.cost_incurred"
        assert profile.trigger.where == (("payload.cost_type", "DIRECT_LABOR"),)

    def test_direct_material_trigger(self):
        """Direct material profile should trigger on DIRECT_MATERIAL cost type."""
        profile = PolicySelector.get("ContractCostDirectMaterial")
        assert profile.trigger.where == (("payload.cost_type", "DIRECT_MATERIAL"),)

    def test_subcontract_trigger(self):
        """Subcontract profile should trigger on SUBCONTRACT cost type."""
        profile = PolicySelector.get("ContractCostSubcontract")
        assert profile.trigger.where == (("payload.cost_type", "SUBCONTRACT"),)

    def test_travel_trigger(self):
        """Travel profile should trigger on TRAVEL cost type."""
        profile = PolicySelector.get("ContractCostTravel")
        assert profile.trigger.where == (("payload.cost_type", "TRAVEL"),)

    def test_odc_trigger(self):
        """ODC profile should trigger on ODC cost type."""
        profile = PolicySelector.get("ContractCostODC")
        assert profile.trigger.where == (("payload.cost_type", "ODC"),)

    def test_indirect_fringe_trigger(self):
        """Indirect fringe profile should trigger on INDIRECT_FRINGE cost type."""
        profile = PolicySelector.get("ContractCostIndirectFringe")
        assert profile.trigger.where == (("payload.cost_type", "INDIRECT_FRINGE"),)

    def test_indirect_overhead_trigger(self):
        """Indirect overhead profile should trigger on INDIRECT_OVERHEAD cost type."""
        profile = PolicySelector.get("ContractCostIndirectOverhead")
        assert profile.trigger.where == (("payload.cost_type", "INDIRECT_OVERHEAD"),)

    def test_indirect_ga_trigger(self):
        """Indirect G&A profile should trigger on INDIRECT_GA cost type."""
        profile = PolicySelector.get("ContractCostIndirectGA")
        assert profile.trigger.where == (("payload.cost_type", "INDIRECT_GA"),)


class TestBillingPolicyTriggers:
    """Test that billing profiles trigger correctly by billing_type."""

    def test_cost_reimbursement_trigger(self):
        """Cost reimbursement profile should trigger on COST_REIMBURSEMENT billing type."""
        profile = PolicySelector.get("ContractBillingCostReimbursement")
        assert profile.trigger.event_type == "contract.billing_provisional"
        assert profile.trigger.where == (("payload.billing_type", "COST_REIMBURSEMENT"),)

    def test_time_and_materials_trigger(self):
        """T&M profile should trigger on TIME_AND_MATERIALS billing type."""
        profile = PolicySelector.get("ContractBillingTimeAndMaterials")
        assert profile.trigger.where == (("payload.billing_type", "TIME_AND_MATERIALS"),)

    def test_labor_hour_trigger(self):
        """Labor hour profile should trigger on LABOR_HOUR billing type."""
        profile = PolicySelector.get("ContractBillingLaborHour")
        assert profile.trigger.where == (("payload.billing_type", "LABOR_HOUR"),)


class TestFeeAccrualPolicyTriggers:
    """Test that fee accrual profiles trigger correctly by fee_type."""

    def test_fixed_fee_trigger(self):
        """Fixed fee profile should trigger on FIXED_FEE fee type."""
        profile = PolicySelector.get("ContractFeeFixedAccrual")
        assert profile.trigger.event_type == "contract.fee_accrual"
        assert profile.trigger.where == (("payload.fee_type", "FIXED_FEE"),)

    def test_incentive_fee_trigger(self):
        """Incentive fee profile should trigger on INCENTIVE_FEE fee type."""
        profile = PolicySelector.get("ContractFeeIncentiveAccrual")
        assert profile.trigger.where == (("payload.fee_type", "INCENTIVE_FEE"),)

    def test_award_fee_trigger(self):
        """Award fee profile should trigger on AWARD_FEE fee type."""
        profile = PolicySelector.get("ContractFeeAwardAccrual")
        assert profile.trigger.where == (("payload.fee_type", "AWARD_FEE"),)


class TestIndirectAllocationPolicyTriggers:
    """Test that indirect allocation profiles trigger correctly by indirect_type."""

    def test_fringe_allocation_trigger(self):
        """Fringe allocation profile should trigger on FRINGE indirect type."""
        profile = PolicySelector.get("ContractAllocationFringe")
        assert profile.trigger.event_type == "contract.indirect_allocation"
        assert profile.trigger.where == (("payload.indirect_type", "FRINGE"),)

    def test_overhead_allocation_trigger(self):
        """Overhead allocation profile should trigger on OVERHEAD indirect type."""
        profile = PolicySelector.get("ContractAllocationOverhead")
        assert profile.trigger.where == (("payload.indirect_type", "OVERHEAD"),)

    def test_ga_allocation_trigger(self):
        """G&A allocation profile should trigger on G_AND_A indirect type."""
        profile = PolicySelector.get("ContractAllocationGA")
        assert profile.trigger.where == (("payload.indirect_type", "G_AND_A"),)


# ============================================================================
# Profile Ledger Effect Tests
# ============================================================================


class TestCostIncurredLedgerEffects:
    """Test ledger effects for cost incurrence profiles."""

    def test_direct_labor_ledger_effects(self):
        """Direct labor should post to GL and CONTRACT subledgers."""
        profile = PolicySelector.get("ContractCostDirectLabor")
        effects = profile.ledger_effects

        # Should have 2 effects: GL and CONTRACT
        assert len(effects) == 2

        # GL effect
        gl_effect = get_ledger_effect(profile, "GL")
        assert gl_effect is not None
        assert gl_effect.debit_role == "WIP_DIRECT_LABOR"
        assert gl_effect.credit_role == "LABOR_CLEARING"

        # CONTRACT subledger effect
        contract_effect = get_ledger_effect(profile, "CONTRACT")
        assert contract_effect is not None
        assert contract_effect.debit_role == "CONTRACT_COST_INCURRED"
        assert contract_effect.credit_role == "COST_CLEARING"

    def test_direct_material_ledger_effects(self):
        """Direct material should post to GL and CONTRACT subledgers."""
        profile = PolicySelector.get("ContractCostDirectMaterial")
        effects = profile.ledger_effects

        assert len(effects) == 2

        gl_effect = get_ledger_effect(profile, "GL")
        assert gl_effect is not None
        assert gl_effect.debit_role == "WIP_DIRECT_MATERIAL"
        assert gl_effect.credit_role == "MATERIAL_CLEARING"

    def test_subcontract_ledger_effects(self):
        """Subcontract should post to GL with AP clearing."""
        profile = PolicySelector.get("ContractCostSubcontract")
        effects = profile.ledger_effects

        gl_effect = get_ledger_effect(profile, "GL")
        assert gl_effect is not None
        assert gl_effect.debit_role == "WIP_SUBCONTRACT"
        assert gl_effect.credit_role == "AP_CLEARING"

    def test_travel_ledger_effects(self):
        """Travel should post to GL with expense clearing."""
        profile = PolicySelector.get("ContractCostTravel")
        effects = profile.ledger_effects

        gl_effect = get_ledger_effect(profile, "GL")
        assert gl_effect is not None
        assert gl_effect.debit_role == "WIP_TRAVEL"
        assert gl_effect.credit_role == "EXPENSE_CLEARING"

    def test_indirect_fringe_ledger_effects(self):
        """Indirect fringe should post to fringe pool applied."""
        profile = PolicySelector.get("ContractCostIndirectFringe")
        effects = profile.ledger_effects

        gl_effect = get_ledger_effect(profile, "GL")
        assert gl_effect is not None
        assert gl_effect.debit_role == "WIP_FRINGE"
        assert gl_effect.credit_role == "FRINGE_POOL_APPLIED"

    def test_indirect_overhead_ledger_effects(self):
        """Indirect overhead should post to overhead pool applied."""
        profile = PolicySelector.get("ContractCostIndirectOverhead")
        effects = profile.ledger_effects

        gl_effect = get_ledger_effect(profile, "GL")
        assert gl_effect is not None
        assert gl_effect.debit_role == "WIP_OVERHEAD"
        assert gl_effect.credit_role == "OVERHEAD_POOL_APPLIED"

    def test_indirect_ga_ledger_effects(self):
        """Indirect G&A should post to G&A pool applied."""
        profile = PolicySelector.get("ContractCostIndirectGA")
        effects = profile.ledger_effects

        gl_effect = get_ledger_effect(profile, "GL")
        assert gl_effect is not None
        assert gl_effect.debit_role == "WIP_GA"
        assert gl_effect.credit_role == "GA_POOL_APPLIED"


class TestBillingLedgerEffects:
    """Test ledger effects for billing profiles."""

    def test_cost_reimbursement_billing_ledger_effects(self):
        """Cost reimbursement billing should post to unbilled AR."""
        profile = PolicySelector.get("ContractBillingCostReimbursement")
        effects = profile.ledger_effects

        # Should have 3 effects: costs, fee, and contract subledger
        assert len(effects) == 3

        # Check for unbilled AR effect
        unbilled_effects = [e for e in effects if e.debit_role == "UNBILLED_AR"]
        assert len(unbilled_effects) >= 1

    def test_tm_billing_ledger_effects(self):
        """T&M billing should post to unbilled AR and contract subledger."""
        profile = PolicySelector.get("ContractBillingTimeAndMaterials")
        effects = profile.ledger_effects

        assert len(effects) == 2

        gl_effect = get_ledger_effect(profile, "GL")
        assert gl_effect is not None
        assert gl_effect.debit_role == "UNBILLED_AR"
        assert gl_effect.credit_role == "WIP_BILLED"


class TestFeeAccrualLedgerEffects:
    """Test ledger effects for fee accrual profiles."""

    def test_fixed_fee_ledger_effects(self):
        """Fixed fee should transfer from deferred to earned."""
        profile = PolicySelector.get("ContractFeeFixedAccrual")
        effects = profile.ledger_effects

        assert len(effects) == 1

        effect = effects[0]
        assert effect.ledger == "GL"
        assert effect.debit_role == "DEFERRED_FEE_REVENUE"
        assert effect.credit_role == "FEE_REVENUE_EARNED"

    def test_incentive_fee_ledger_effects(self):
        """Incentive fee should have same structure as fixed fee."""
        profile = PolicySelector.get("ContractFeeIncentiveAccrual")
        effects = profile.ledger_effects

        effect = effects[0]
        assert effect.debit_role == "DEFERRED_FEE_REVENUE"
        assert effect.credit_role == "FEE_REVENUE_EARNED"


class TestRateAdjustmentLedgerEffects:
    """Test ledger effects for rate adjustment profiles."""

    def test_rate_adjustment_ledger_effects(self):
        """Rate adjustment should post to WIP adjustment and variance."""
        profile = PolicySelector.get("ContractRateAdjustment")
        effects = profile.ledger_effects

        assert len(effects) == 1

        effect = effects[0]
        assert effect.ledger == "GL"
        assert effect.debit_role == "WIP_RATE_ADJUSTMENT"
        assert effect.credit_role == "INDIRECT_RATE_VARIANCE"


# ============================================================================
# Profile Guard Tests
# ============================================================================


class TestCostIncurredGuards:
    """Test guards for cost incurrence profiles."""

    def test_direct_labor_has_amount_guard(self):
        """Direct labor profile should have guard against zero/negative amount."""
        profile = PolicySelector.get("ContractCostDirectLabor")
        guards = profile.guards

        assert len(guards) == 1
        guard = guards[0]
        assert guard.reason_code == "INVALID_AMOUNT"
        assert "amount" in guard.expression.lower()


class TestBillingGuards:
    """Test guards for billing profiles."""

    def test_cost_reimbursement_billing_guard(self):
        """Cost reimbursement billing should have guard against zero billing."""
        profile = PolicySelector.get("ContractBillingCostReimbursement")
        guards = profile.guards

        assert len(guards) == 1
        guard = guards[0]
        assert guard.reason_code == "INVALID_BILLING"


class TestFeeAccrualGuards:
    """Test guards for fee accrual profiles."""

    def test_fixed_fee_ceiling_guard(self):
        """Fixed fee accrual should have guard against exceeding ceiling."""
        profile = PolicySelector.get("ContractFeeFixedAccrual")
        guards = profile.guards

        assert len(guards) == 1
        guard = guards[0]
        assert guard.reason_code == "FEE_CEILING_EXCEEDED"
        assert "ceiling_fee" in guard.expression.lower()

    def test_incentive_fee_no_ceiling_guard(self):
        """Incentive fee may not have ceiling guard (handled differently)."""
        profile = PolicySelector.get("ContractFeeIncentiveAccrual")
        guards = profile.guards
        # Incentive fee calculations are more complex, may not have simple ceiling guard
        assert guards == ()


# ============================================================================
# Profile Dimension Tests
# ============================================================================


class TestProfileDimensions:
    """Test that profiles extract correct dimensions."""

    def test_direct_labor_dimensions(self):
        """Direct labor should extract labor category dimension."""
        profile = PolicySelector.get("ContractCostDirectLabor")
        dims = profile.meaning.dimensions

        assert "org_unit" in dims
        assert "cost_center" in dims
        assert "contract_number" in dims
        assert "clin_number" in dims
        assert "labor_category" in dims

    def test_direct_material_dimensions(self):
        """Direct material should extract standard dimensions."""
        profile = PolicySelector.get("ContractCostDirectMaterial")
        dims = profile.meaning.dimensions

        assert "org_unit" in dims
        assert "cost_center" in dims
        assert "contract_number" in dims
        assert "clin_number" in dims
        # No labor_category for materials
        assert "labor_category" not in dims

    def test_billing_dimensions(self):
        """Billing should extract org_unit and contract_number."""
        profile = PolicySelector.get("ContractBillingCostReimbursement")
        dims = profile.meaning.dimensions

        assert "org_unit" in dims
        assert "contract_number" in dims

    def test_fee_accrual_dimensions(self):
        """Fee accrual should extract org_unit and contract_number."""
        profile = PolicySelector.get("ContractFeeFixedAccrual")
        dims = profile.meaning.dimensions

        assert "org_unit" in dims
        assert "contract_number" in dims


# ============================================================================
# Profile Economic Type Tests
# ============================================================================


class TestProfileEconomicTypes:
    """Test that profiles have correct economic types."""

    def test_cost_incurrence_economic_type(self):
        """Cost incurrence profiles should have CONTRACT_COST_INCURRENCE type."""
        profile = PolicySelector.get("ContractCostDirectLabor")
        assert profile.meaning.economic_type == "CONTRACT_COST_INCURRENCE"

    def test_indirect_cost_economic_type(self):
        """Indirect cost profiles should have CONTRACT_INDIRECT_ALLOCATION type."""
        profile = PolicySelector.get("ContractCostIndirectFringe")
        assert profile.meaning.economic_type == "CONTRACT_INDIRECT_ALLOCATION"

    def test_billing_economic_type(self):
        """Billing profiles should have CONTRACT_BILLING type."""
        profile = PolicySelector.get("ContractBillingCostReimbursement")
        assert profile.meaning.economic_type == "CONTRACT_BILLING"

    def test_fee_accrual_economic_type(self):
        """Fee accrual profiles should have FEE_ACCRUAL type."""
        profile = PolicySelector.get("ContractFeeFixedAccrual")
        assert profile.meaning.economic_type == "FEE_ACCRUAL"

    def test_indirect_allocation_economic_type(self):
        """Indirect allocation profiles should have INDIRECT_ALLOCATION type."""
        profile = PolicySelector.get("ContractAllocationFringe")
        assert profile.meaning.economic_type == "INDIRECT_ALLOCATION"

    def test_rate_adjustment_economic_type(self):
        """Rate adjustment profile should have RATE_ADJUSTMENT type."""
        profile = PolicySelector.get("ContractRateAdjustment")
        assert profile.meaning.economic_type == "RATE_ADJUSTMENT"


# ============================================================================
# Profile Effective Date Tests
# ============================================================================


class TestProfileEffectiveDates:
    """Test that profiles have correct effective dates."""

    def test_all_profiles_have_effective_date(self):
        """All contract profiles should have effective date of 2024-01-01."""
        profile_names = [
            "ContractCostDirectLabor",
            "ContractCostDirectMaterial",
            "ContractCostSubcontract",
            "ContractCostTravel",
            "ContractCostODC",
            "ContractCostIndirectFringe",
            "ContractCostIndirectOverhead",
            "ContractCostIndirectGA",
            "ContractBillingCostReimbursement",
            "ContractBillingTimeAndMaterials",
            "ContractBillingLaborHour",
            "ContractFeeFixedAccrual",
            "ContractFeeIncentiveAccrual",
            "ContractFeeAwardAccrual",
            "ContractAllocationFringe",
            "ContractAllocationOverhead",
            "ContractAllocationGA",
            "ContractRateAdjustment",
        ]

        expected_date = date(2024, 1, 1)

        for name in profile_names:
            profile = PolicySelector.get(name)
            assert profile is not None, f"Profile {name} not found"
            assert profile.effective_from == expected_date, \
                f"Profile {name} has wrong effective date"


# ============================================================================
# Integration Tests
# ============================================================================


class TestSchemaProfileIntegration:
    """Test that schemas and profiles work together."""

    def test_cost_incurred_schema_version_matches_profile(self):
        """Cost incurred profile schema version should match schema."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        profile = PolicySelector.get("ContractCostDirectLabor")

        assert profile.trigger.schema_version == schema.version

    def test_billing_schema_version_matches_profile(self):
        """Billing profile schema version should match schema."""
        schema = EventSchemaRegistry.get("contract.billing_provisional", 1)
        profile = PolicySelector.get("ContractBillingCostReimbursement")

        assert profile.trigger.schema_version == schema.version

    def test_fee_accrual_schema_version_matches_profile(self):
        """Fee accrual profile schema version should match schema."""
        schema = EventSchemaRegistry.get("contract.fee_accrual", 1)
        profile = PolicySelector.get("ContractFeeFixedAccrual")

        assert profile.trigger.schema_version == schema.version

    def test_indirect_allocation_schema_version_matches_profile(self):
        """Indirect allocation profile schema version should match schema."""
        schema = EventSchemaRegistry.get("contract.indirect_allocation", 1)
        profile = PolicySelector.get("ContractAllocationFringe")

        assert profile.trigger.schema_version == schema.version

    def test_rate_adjustment_schema_version_matches_profile(self):
        """Rate adjustment profile schema version should match schema."""
        schema = EventSchemaRegistry.get("contract.rate_adjustment", 1)
        profile = PolicySelector.get("ContractRateAdjustment")

        assert profile.trigger.schema_version == schema.version


class TestContractAccountingFlow:
    """Test complete contract accounting flows."""

    def test_cost_plus_contract_flow_profiles_exist(self):
        """All profiles needed for cost-plus contract flow should exist."""
        # 1. Cost incurrence profiles
        assert PolicySelector.get("ContractCostDirectLabor") is not None
        assert PolicySelector.get("ContractCostDirectMaterial") is not None

        # 2. Indirect allocation profiles
        assert PolicySelector.get("ContractCostIndirectFringe") is not None
        assert PolicySelector.get("ContractCostIndirectOverhead") is not None
        assert PolicySelector.get("ContractCostIndirectGA") is not None

        # 3. Billing profiles
        assert PolicySelector.get("ContractBillingCostReimbursement") is not None

        # 4. Fee accrual profiles
        assert PolicySelector.get("ContractFeeFixedAccrual") is not None

    def test_tm_contract_flow_profiles_exist(self):
        """All profiles needed for T&M contract flow should exist."""
        # 1. Cost incurrence
        assert PolicySelector.get("ContractCostDirectLabor") is not None

        # 2. Billing
        assert PolicySelector.get("ContractBillingTimeAndMaterials") is not None

    def test_rate_adjustment_flow_profiles_exist(self):
        """All profiles needed for rate adjustment flow should exist."""
        # Allocation profiles for provisional rates
        assert PolicySelector.get("ContractAllocationFringe") is not None
        assert PolicySelector.get("ContractAllocationOverhead") is not None
        assert PolicySelector.get("ContractAllocationGA") is not None

        # Rate adjustment profile
        assert PolicySelector.get("ContractRateAdjustment") is not None


# ============================================================================
# DCAA Compliance Tests
# ============================================================================


class TestDCAACompliance:
    """Test DCAA compliance requirements in schemas and profiles."""

    def test_cost_types_support_dcaa_categorization(self):
        """Cost types should support DCAA-compliant categorization."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        cost_type_field = next(f for f in schema.fields if f.name == "cost_type")

        # DCAA requires separate tracking of these cost types
        required_types = {
            "DIRECT_LABOR",
            "DIRECT_MATERIAL",
            "SUBCONTRACT",
            "TRAVEL",
            "ODC",
            "INDIRECT_FRINGE",
            "INDIRECT_OVERHEAD",
            "INDIRECT_GA",
        }

        assert cost_type_field.allowed_values == required_types

    def test_indirect_rate_types_support_dcaa(self):
        """Indirect rate types should support DCAA rate types."""
        schema = EventSchemaRegistry.get("contract.indirect_allocation", 1)
        rate_type_field = next(f for f in schema.fields if f.name == "rate_type")

        # DCAA requires distinction between provisional and final rates
        assert "PROVISIONAL" in rate_type_field.allowed_values
        assert "FINAL" in rate_type_field.allowed_values

    def test_labor_category_tracked(self):
        """Labor category should be tracked for DCAA labor billing rates."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        labor_cat_field = next(
            (f for f in schema.fields if f.name == "labor_category"), None
        )

        assert labor_cat_field is not None

    def test_clin_tracked(self):
        """CLIN should be tracked for DCAA contract line item reporting."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)
        clin_field = next(
            (f for f in schema.fields if f.name == "clin_number"), None
        )

        assert clin_field is not None

    def test_source_document_traced(self):
        """Source documents should be traceable for DCAA audit trail."""
        schema = EventSchemaRegistry.get("contract.cost_incurred", 1)

        source_type_field = next(
            (f for f in schema.fields if f.name == "source_document_type"), None
        )
        source_id_field = next(
            (f for f in schema.fields if f.name == "source_document_id"), None
        )

        assert source_type_field is not None
        assert source_id_field is not None
        assert source_type_field.required is True
        assert source_id_field.required is True

    def test_fiscal_year_tracked_for_rate_adjustments(self):
        """Fiscal year should be tracked for DCAA rate adjustments."""
        schema = EventSchemaRegistry.get("contract.rate_adjustment", 1)
        fy_field = next(f for f in schema.fields if f.name == "fiscal_year")

        assert fy_field is not None
        assert fy_field.required is True
