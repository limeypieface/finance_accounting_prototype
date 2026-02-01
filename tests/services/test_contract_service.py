"""
Comprehensive tests for Contract model and ContractService.

Tests cover:
1. Contract model properties and enums
2. CLIN management
3. Contract lifecycle operations
4. DCAA compliance validation
5. ICE reporting tracking
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.exceptions import (
    CLINInactiveError,
    CLINNotFoundError,
    ContractCeilingExceededError,
    ContractFundingExceededError,
    ContractInactiveError,
    ContractNotFoundError,
    ContractPOPExpiredError,
    UnallowableCostToContractError,
)
from finance_kernel.models.contract import (
    Contract,
    ContractLineItem,
    ContractStatus,
    ContractType,
    ICEReportingFrequency,
)
from finance_kernel.services.contract_service import (
    CLINInfo,
    ContractInfo,
    ContractService,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def contract_service(session):
    """Create a ContractService with test session."""
    return ContractService(session)


@pytest.fixture
def sample_contract(session):
    """Create a sample contract for testing."""
    actor_id = uuid4()
    contract = Contract(
        contract_number="FA8750-24-C-0001",
        contract_name="Test CPFF Contract",
        contract_type=ContractType.COST_PLUS_FIXED_FEE,
        status=ContractStatus.ACTIVE,
        currency="USD",
        funded_amount=Decimal("1000000"),
        ceiling_amount=Decimal("1500000"),
        fee_rate=Decimal("0.08"),
        ceiling_fee=Decimal("120000"),
        start_date=date(2024, 1, 1),
        end_date=date(2025, 12, 31),
        period_of_performance_end=date(2025, 12, 31),
        requires_timekeeping=True,
        ice_reporting_frequency=ICEReportingFrequency.ANNUAL,
        duns_number="123456789",
        cage_code="1AB23",
        created_by_id=actor_id,
    )
    session.add(contract)
    session.flush()
    return contract


@pytest.fixture
def ffp_contract(session):
    """Create a fixed-price contract for testing."""
    actor_id = uuid4()
    contract = Contract(
        contract_number="FA8750-24-C-0002",
        contract_name="Test FFP Contract",
        contract_type=ContractType.FIRM_FIXED_PRICE,
        status=ContractStatus.ACTIVE,
        currency="USD",
        funded_amount=Decimal("500000"),
        ceiling_amount=Decimal("500000"),
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        requires_timekeeping=False,
        ice_reporting_frequency=ICEReportingFrequency.NONE,
        created_by_id=actor_id,
    )
    session.add(contract)
    session.flush()
    return contract


# ============================================================================
# Contract Model Tests
# ============================================================================


class TestContractModel:
    """Tests for Contract model properties."""

    def test_contract_type_values(self):
        """ContractType should have all FAR contract types."""
        assert ContractType.COST_PLUS_FIXED_FEE.value == "CPFF"
        assert ContractType.COST_PLUS_INCENTIVE_FEE.value == "CPIF"
        assert ContractType.COST_PLUS_AWARD_FEE.value == "CPAF"
        assert ContractType.TIME_AND_MATERIALS.value == "T&M"
        assert ContractType.LABOR_HOUR.value == "LH"
        assert ContractType.FIRM_FIXED_PRICE.value == "FFP"
        assert ContractType.FIXED_PRICE_INCENTIVE.value == "FPI"

    def test_contract_status_values(self):
        """ContractStatus should have all lifecycle states."""
        assert ContractStatus.DRAFT.value == "draft"
        assert ContractStatus.ACTIVE.value == "active"
        assert ContractStatus.SUSPENDED.value == "suspended"
        assert ContractStatus.COMPLETED.value == "completed"
        assert ContractStatus.CLOSED.value == "closed"

    def test_ice_reporting_frequency_values(self):
        """ICEReportingFrequency should have valid frequencies."""
        assert ICEReportingFrequency.MONTHLY.value == "monthly"
        assert ICEReportingFrequency.QUARTERLY.value == "quarterly"
        assert ICEReportingFrequency.ANNUAL.value == "annual"
        assert ICEReportingFrequency.NONE.value == "none"

    def test_is_cost_reimbursement_cpff(self, sample_contract):
        """CPFF contract should be cost-reimbursement."""
        assert sample_contract.is_cost_reimbursement is True
        assert sample_contract.is_fixed_price is False

    def test_is_fixed_price_ffp(self, ffp_contract):
        """FFP contract should be fixed-price."""
        assert ffp_contract.is_fixed_price is True
        assert ffp_contract.is_cost_reimbursement is False

    def test_can_accept_charges_active(self, sample_contract):
        """Active contract should accept charges."""
        assert sample_contract.can_accept_charges is True

    def test_can_accept_charges_suspended(self, sample_contract):
        """Suspended contract should not accept charges."""
        sample_contract.status = ContractStatus.SUSPENDED
        assert sample_contract.can_accept_charges is False

    def test_can_accept_charges_inactive(self, sample_contract):
        """Inactive contract should not accept charges."""
        sample_contract.is_active = False
        assert sample_contract.can_accept_charges is False

    def test_is_within_pop_current_date(self, sample_contract):
        """Contract should be within POP for date in range."""
        # Test with known date in range
        sample_contract.start_date = date(2024, 1, 1)
        sample_contract.period_of_performance_end = date(2099, 12, 31)
        assert sample_contract.is_within_pop is True

    def test_repr(self, sample_contract):
        """Contract repr should include key info."""
        repr_str = repr(sample_contract)
        assert "FA8750-24-C-0001" in repr_str
        assert "Test CPFF Contract" in repr_str
        assert "CPFF" in repr_str


class TestContractLineItemModel:
    """Tests for ContractLineItem model."""

    def test_clin_creation(self, session, sample_contract):
        """Should create CLIN with all fields."""
        actor_id = uuid4()
        clin = ContractLineItem(
            contract_id=sample_contract.id,
            line_number="0001",
            description="Direct Labor - Engineering",
            clin_type="LABOR",
            funded_amount=Decimal("500000"),
            ceiling_amount=Decimal("750000"),
            labor_category="ENGINEER",
            hourly_rate=Decimal("150.00"),
            estimated_hours=Decimal("3333.33"),
            created_by_id=actor_id,
        )
        session.add(clin)
        session.flush()

        assert clin.id is not None
        assert clin.contract_id == sample_contract.id
        assert clin.line_number == "0001"
        assert clin.clin_type == "LABOR"
        assert clin.is_active is True

    def test_clin_repr(self, session, sample_contract):
        """CLIN repr should include key info."""
        actor_id = uuid4()
        clin = ContractLineItem(
            contract_id=sample_contract.id,
            line_number="0001",
            description="Direct Labor - Engineering Services",
            clin_type="LABOR",
            created_by_id=actor_id,
        )
        session.add(clin)
        session.flush()

        repr_str = repr(clin)
        assert "0001" in repr_str
        assert "Direct Labor" in repr_str


# ============================================================================
# ContractService Tests
# ============================================================================


class TestContractServiceCRUD:
    """Tests for ContractService CRUD operations."""

    def test_create_contract(self, contract_service, session):
        """Should create contract with all fields."""
        actor_id = uuid4()
        contract = contract_service.create_contract(
            contract_number="FA8750-24-C-0100",
            contract_name="New Test Contract",
            contract_type=ContractType.TIME_AND_MATERIALS,
            actor_id=actor_id,
            funded_amount=Decimal("200000"),
            ceiling_amount=Decimal("300000"),
            start_date=date(2024, 6, 1),
            end_date=date(2025, 5, 31),
            duns_number="987654321",
            cage_code="5XY99",
        )

        assert isinstance(contract, ContractInfo)
        assert contract.contract_number == "FA8750-24-C-0100"
        assert contract.contract_type == ContractType.TIME_AND_MATERIALS
        assert contract.status == ContractStatus.DRAFT  # Default
        assert contract.funded_amount == Decimal("200000")

    def test_get_by_id(self, contract_service, sample_contract):
        """Should retrieve contract by ID."""
        contract = contract_service.get_by_id(sample_contract.id)
        assert contract.contract_number == "FA8750-24-C-0001"

    def test_get_by_id_not_found(self, contract_service):
        """Should raise error for non-existent contract."""
        with pytest.raises(ContractNotFoundError):
            contract_service.get_by_id(uuid4())

    def test_get_by_number(self, contract_service, sample_contract):
        """Should retrieve contract by number."""
        contract = contract_service.get_by_number("FA8750-24-C-0001")
        assert contract.id == sample_contract.id

    def test_get_by_number_not_found(self, contract_service):
        """Should raise error for non-existent contract number."""
        with pytest.raises(ContractNotFoundError):
            contract_service.get_by_number("NONEXISTENT")

    def test_find_by_number_exists(self, contract_service, sample_contract):
        """Should find contract by number."""
        contract = contract_service.find_by_number("FA8750-24-C-0001")
        assert contract is not None
        assert contract.id == sample_contract.id

    def test_find_by_number_not_exists(self, contract_service):
        """Should return None for non-existent contract."""
        contract = contract_service.find_by_number("NONEXISTENT")
        assert contract is None


class TestContractServiceListing:
    """Tests for ContractService listing operations."""

    def test_list_active_all(self, contract_service, sample_contract, ffp_contract):
        """Should list all active contracts."""
        contracts = contract_service.list_active()
        contract_numbers = [c.contract_number for c in contracts]
        assert "FA8750-24-C-0001" in contract_numbers
        assert "FA8750-24-C-0002" in contract_numbers

    def test_list_active_by_type(self, contract_service, sample_contract, ffp_contract):
        """Should filter by contract type."""
        contracts = contract_service.list_active(contract_type=ContractType.FIRM_FIXED_PRICE)
        assert len(contracts) == 1
        assert contracts[0].contract_number == "FA8750-24-C-0002"

    def test_list_cost_reimbursement(self, contract_service, sample_contract, ffp_contract):
        """Should list only cost-reimbursement contracts."""
        contracts = contract_service.list_cost_reimbursement()
        contract_numbers = [c.contract_number for c in contracts]
        assert "FA8750-24-C-0001" in contract_numbers
        assert "FA8750-24-C-0002" not in contract_numbers


class TestContractServiceLifecycle:
    """Tests for ContractService lifecycle operations."""

    def test_activate_contract(self, contract_service, session):
        """Should activate a draft contract."""
        actor_id = uuid4()
        contract = contract_service.create_contract(
            contract_number="FA8750-24-C-0200",
            contract_name="Draft Contract",
            contract_type=ContractType.LABOR_HOUR,
            actor_id=actor_id,
        )
        assert contract.status == ContractStatus.DRAFT

        activated = contract_service.activate_contract(contract.id)
        assert activated.status == ContractStatus.ACTIVE
        assert activated.is_active is True

    def test_suspend_contract(self, contract_service, sample_contract):
        """Should suspend an active contract."""
        suspended = contract_service.suspend_contract(sample_contract.id)
        assert suspended.status == ContractStatus.SUSPENDED

    def test_complete_contract(self, contract_service, sample_contract):
        """Should mark contract as completed."""
        completed = contract_service.complete_contract(sample_contract.id)
        assert completed.status == ContractStatus.COMPLETED

    def test_close_contract(self, contract_service, sample_contract):
        """Should close contract permanently."""
        closed = contract_service.close_contract(sample_contract.id)
        assert closed.status == ContractStatus.CLOSED
        assert closed.is_active is False


class TestContractServiceFunding:
    """Tests for ContractService funding operations."""

    def test_add_funding(self, contract_service, sample_contract):
        """Should add funding to contract."""
        original_funding = sample_contract.funded_amount
        updated = contract_service.add_funding(
            sample_contract.id,
            Decimal("250000"),
        )
        assert updated.funded_amount == original_funding + Decimal("250000")

    def test_update_ceiling(self, contract_service, sample_contract):
        """Should update contract ceiling."""
        updated = contract_service.update_ceiling(
            sample_contract.id,
            Decimal("2000000"),
        )
        assert updated.ceiling_amount == Decimal("2000000")


# ============================================================================
# CLIN Operations Tests
# ============================================================================


class TestCLINOperations:
    """Tests for CLIN management."""

    def test_add_clin(self, contract_service, sample_contract):
        """Should add CLIN to contract."""
        actor_id = uuid4()
        clin = contract_service.add_clin(
            contract_id=sample_contract.id,
            line_number="0001",
            description="Engineering Labor",
            clin_type="LABOR",
            actor_id=actor_id,
            funded_amount=Decimal("500000"),
            labor_category="ENGINEER_SR",
            hourly_rate=Decimal("175.00"),
        )

        assert isinstance(clin, CLINInfo)
        assert clin.line_number == "0001"
        assert clin.clin_type == "LABOR"
        assert clin.funded_amount == Decimal("500000")

    def test_get_clin(self, contract_service, sample_contract):
        """Should retrieve CLIN by number."""
        actor_id = uuid4()
        contract_service.add_clin(
            contract_id=sample_contract.id,
            line_number="0002",
            description="Materials",
            clin_type="MATERIAL",
            actor_id=actor_id,
        )

        clin = contract_service.get_clin(sample_contract.id, "0002")
        assert clin.line_number == "0002"
        assert clin.clin_type == "MATERIAL"

    def test_get_clin_not_found(self, contract_service, sample_contract):
        """Should raise error for non-existent CLIN."""
        with pytest.raises(CLINNotFoundError):
            contract_service.get_clin(sample_contract.id, "9999")

    def test_list_clins(self, contract_service, sample_contract):
        """Should list all CLINs for contract."""
        actor_id = uuid4()
        contract_service.add_clin(
            sample_contract.id, "0001", "Labor", "LABOR", actor_id
        )
        contract_service.add_clin(
            sample_contract.id, "0002", "Materials", "MATERIAL", actor_id
        )
        contract_service.add_clin(
            sample_contract.id, "0003", "Travel", "TRAVEL", actor_id
        )

        clins = contract_service.list_clins(sample_contract.id)
        assert len(clins) == 3
        # Should be ordered by line number
        assert clins[0].line_number == "0001"
        assert clins[1].line_number == "0002"
        assert clins[2].line_number == "0003"


# ============================================================================
# DCAA Compliance Validation Tests
# ============================================================================


class TestDCAAValidation:
    """Tests for DCAA compliance validation."""

    def test_validate_can_charge_success(self, contract_service, sample_contract):
        """Should validate valid charge."""
        result = contract_service.validate_can_charge(
            contract_number=sample_contract.contract_number,
            charge_date=date(2024, 6, 15),
            charge_amount=Decimal("10000"),
            is_allowable=True,
        )
        assert result.contract_number == sample_contract.contract_number

    def test_validate_can_charge_contract_not_found(self, contract_service):
        """Should reject charge to non-existent contract."""
        with pytest.raises(ContractNotFoundError):
            contract_service.validate_can_charge(
                contract_number="NONEXISTENT",
                charge_date=date(2024, 6, 15),
                charge_amount=Decimal("10000"),
            )

    def test_validate_can_charge_inactive_contract(
        self, contract_service, sample_contract
    ):
        """Should reject charge to inactive contract."""
        contract_service.suspend_contract(sample_contract.id)

        with pytest.raises(ContractInactiveError):
            contract_service.validate_can_charge(
                contract_number=sample_contract.contract_number,
                charge_date=date(2024, 6, 15),
                charge_amount=Decimal("10000"),
            )

    def test_validate_can_charge_unallowable_cost(
        self, contract_service, sample_contract
    ):
        """Should reject unallowable costs."""
        with pytest.raises(UnallowableCostToContractError):
            contract_service.validate_can_charge(
                contract_number=sample_contract.contract_number,
                charge_date=date(2024, 6, 15),
                charge_amount=Decimal("10000"),
                is_allowable=False,
            )

    def test_validate_can_charge_before_pop(self, contract_service, sample_contract):
        """Should reject charge before period of performance."""
        with pytest.raises(ContractPOPExpiredError):
            contract_service.validate_can_charge(
                contract_number=sample_contract.contract_number,
                charge_date=date(2023, 12, 1),  # Before start
                charge_amount=Decimal("10000"),
            )

    def test_validate_can_charge_after_pop(self, contract_service, sample_contract):
        """Should reject charge after period of performance."""
        with pytest.raises(ContractPOPExpiredError):
            contract_service.validate_can_charge(
                contract_number=sample_contract.contract_number,
                charge_date=date(2026, 1, 15),  # After end
                charge_amount=Decimal("10000"),
            )

    def test_validate_can_charge_exceeds_funding(
        self, contract_service, sample_contract
    ):
        """Should reject charge that exceeds funding."""
        with pytest.raises(ContractFundingExceededError):
            contract_service.validate_can_charge(
                contract_number=sample_contract.contract_number,
                charge_date=date(2024, 6, 15),
                charge_amount=Decimal("1500000"),  # Exceeds 1M funding
                incurred_to_date=Decimal("0"),
            )

    def test_validate_can_charge_exceeds_ceiling(
        self, contract_service, sample_contract
    ):
        """Should reject charge that exceeds ceiling."""
        # First add more funding so we can test ceiling separately
        # Funded: 1M -> 2M, Ceiling: 1.5M
        contract_service.add_funding(sample_contract.id, Decimal("1000000"))

        with pytest.raises(ContractCeilingExceededError):
            contract_service.validate_can_charge(
                contract_number=sample_contract.contract_number,
                charge_date=date(2024, 6, 15),
                charge_amount=Decimal("600000"),
                incurred_to_date=Decimal("1000000"),  # Total 1.6M > 1.5M ceiling
            )

    def test_validate_can_charge_within_limits(
        self, contract_service, sample_contract
    ):
        """Should allow charge within all limits."""
        result = contract_service.validate_can_charge(
            contract_number=sample_contract.contract_number,
            charge_date=date(2024, 6, 15),
            charge_amount=Decimal("100000"),
            incurred_to_date=Decimal("800000"),
        )
        assert result.contract_number == sample_contract.contract_number


class TestCLINValidation:
    """Tests for CLIN charge validation."""

    def test_validate_clin_charge_success(self, contract_service, sample_contract):
        """Should validate valid CLIN charge."""
        actor_id = uuid4()
        contract_service.add_clin(
            sample_contract.id, "0001", "Labor", "LABOR", actor_id
        )

        clin = contract_service.validate_clin_charge(
            contract_id=sample_contract.id,
            clin_number="0001",
            charge_amount=Decimal("10000"),
        )
        assert clin.line_number == "0001"

    def test_validate_clin_charge_not_found(self, contract_service, sample_contract):
        """Should reject charge to non-existent CLIN."""
        with pytest.raises(CLINNotFoundError):
            contract_service.validate_clin_charge(
                contract_id=sample_contract.id,
                clin_number="9999",
                charge_amount=Decimal("10000"),
            )

    def test_validate_clin_charge_inactive(
        self, contract_service, sample_contract, session
    ):
        """Should reject charge to inactive CLIN."""
        actor_id = uuid4()
        clin = ContractLineItem(
            contract_id=sample_contract.id,
            line_number="0001",
            description="Inactive CLIN",
            clin_type="LABOR",
            is_active=False,
            created_by_id=actor_id,
        )
        session.add(clin)
        session.flush()

        with pytest.raises(CLINInactiveError):
            contract_service.validate_clin_charge(
                contract_id=sample_contract.id,
                clin_number="0001",
                charge_amount=Decimal("10000"),
            )


# ============================================================================
# ICE Reporting Tests
# ============================================================================


class TestICEReporting:
    """Tests for ICE reporting functionality."""

    def test_record_ice_submission(self, contract_service, sample_contract):
        """Should record ICE submission date."""
        submission_date = date(2024, 7, 15)
        updated = contract_service.record_ice_submission(
            sample_contract.id,
            submission_date,
        )
        # Note: ICE submission date is not in DTO, but this tests the method works
        assert updated.id == sample_contract.id

    def test_get_contracts_needing_ice_no_submission(
        self, contract_service, sample_contract
    ):
        """Should include contracts with no ICE submission."""
        contracts = contract_service.get_contracts_needing_ice()
        contract_ids = [c.id for c in contracts]
        assert sample_contract.id in contract_ids

    def test_get_contracts_needing_ice_excludes_ffp(
        self, contract_service, sample_contract, ffp_contract
    ):
        """Should exclude FFP contracts from ICE requirements."""
        contracts = contract_service.get_contracts_needing_ice()
        contract_ids = [c.id for c in contracts]
        assert sample_contract.id in contract_ids
        assert ffp_contract.id not in contract_ids

    def test_get_contracts_needing_ice_recent_submission(
        self, contract_service, sample_contract
    ):
        """Should exclude contracts with recent ICE submission."""
        # Record recent submission
        contract_service.record_ice_submission(
            sample_contract.id,
            date.today() - timedelta(days=30),  # Recent
        )

        contracts = contract_service.get_contracts_needing_ice()
        contract_ids = [c.id for c in contracts]
        # Annual frequency, so 30 days ago is still recent
        assert sample_contract.id not in contract_ids


# ============================================================================
# ContractInfo DTO Tests
# ============================================================================


class TestContractInfoDTO:
    """Tests for ContractInfo DTO properties."""

    def test_is_cost_reimbursement_property(self, contract_service, sample_contract):
        """ContractInfo should expose is_cost_reimbursement."""
        contract = contract_service.get_by_id(sample_contract.id)
        assert contract.is_cost_reimbursement is True

    def test_is_fixed_price_property(self, contract_service, ffp_contract):
        """ContractInfo should expose is_fixed_price."""
        contract = contract_service.get_by_id(ffp_contract.id)
        assert contract.is_fixed_price is True

    def test_can_accept_charges_property(self, contract_service, sample_contract):
        """ContractInfo should expose can_accept_charges."""
        contract = contract_service.get_by_id(sample_contract.id)
        assert contract.can_accept_charges is True

    def test_dto_is_frozen(self, contract_service, sample_contract):
        """ContractInfo should be frozen dataclass."""
        contract = contract_service.get_by_id(sample_contract.id)
        with pytest.raises(AttributeError):
            contract.contract_number = "CHANGED"  # Should fail


class TestCLINInfoDTO:
    """Tests for CLINInfo DTO."""

    def test_clin_dto_is_frozen(self, contract_service, sample_contract):
        """CLINInfo should be frozen dataclass."""
        actor_id = uuid4()
        contract_service.add_clin(
            sample_contract.id, "0001", "Labor", "LABOR", actor_id
        )
        clin = contract_service.get_clin(sample_contract.id, "0001")

        with pytest.raises(AttributeError):
            clin.line_number = "9999"  # Should fail
