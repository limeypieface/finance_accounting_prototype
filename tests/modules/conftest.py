"""
Shared fixtures for module tests.

Provides parent entity fixtures needed by module ORM FK constraints.
All IDs are deterministic so tests can import and use them directly.

DESIGN RULE: Every fixture is opt-in.  No autouse.  Each test explicitly
declares which parent entities it depends on in its function signature.
Pytest enforces ordering -- dependency order is visible, reviewable, and
caught at test collection time instead of runtime FK crashes.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

# ---------------------------------------------------------------------------
# Deterministic parent entity IDs
# These well-known UUIDs are used by tests and fixtures to satisfy FK
# constraints when module services persist ORM models.
# ---------------------------------------------------------------------------

TEST_VENDOR_ID = UUID("00000000-0000-4000-a000-000000000001")
TEST_CUSTOMER_ID = UUID("00000000-0000-4000-a000-000000000002")
TEST_EMPLOYEE_ID = UUID("00000000-0000-4000-a000-000000000003")
TEST_LESSEE_ID = UUID("00000000-0000-4000-a000-000000000004")
TEST_BANK_ACCOUNT_ID = UUID("00000000-0000-4000-a000-000000000010")
TEST_ASSET_CATEGORY_ID = UUID("00000000-0000-4000-a000-000000000020")
TEST_ASSET_ID = UUID("00000000-0000-4000-a000-000000000021")
TEST_TAX_JURISDICTION_ID = UUID("00000000-0000-4000-a000-000000000030")
TEST_WORK_ORDER_ID = UUID("00000000-0000-4000-a000-000000000040")
TEST_OPERATION_ID = UUID("00000000-0000-4000-a000-000000000041")
TEST_BUDGET_VERSION_ID = UUID("00000000-0000-4000-a000-000000000050")
TEST_PAY_PERIOD_ID = UUID("00000000-0000-4000-a000-000000000060")
TEST_PROJECT_ID = UUID("00000000-0000-4000-a000-000000000070")
TEST_REVENUE_CONTRACT_ID = UUID("00000000-0000-4000-a000-000000000080")
TEST_LEASE_ID = UUID("00000000-0000-4000-a000-000000000090")
TEST_CONTRACT_ID = UUID("00000000-0000-4000-a000-0000000000a0")
TEST_EXPENSE_REPORT_ID = UUID("00000000-0000-4000-a000-0000000000b0")
TEST_PAYROLL_EMPLOYEE_ID = UUID("00000000-0000-4000-a000-0000000000c0")
TEST_IC_AGREEMENT_ID = UUID("00000000-0000-4000-a000-0000000000d0")


# ---------------------------------------------------------------------------
# Original utility fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_account_mappings():
    """Sample account mappings for testing."""
    return {
        "ar_asset": "1200-000",
        "revenue": "4000-000",
        "cash": "1000-000",
        "tax_liability": "2100-000",
    }


@pytest.fixture
def sample_approval_levels():
    """Sample approval hierarchy for testing."""
    return [
        {"max_amount": Decimal("1000.00"), "approver_role": "supervisor"},
        {"max_amount": Decimal("10000.00"), "approver_role": "manager"},
        {"max_amount": Decimal("100000.00"), "approver_role": "director"},
    ]


@pytest.fixture
def random_uuid():
    """Generate a random UUID."""
    return uuid4()


# ---------------------------------------------------------------------------
# Kernel Party fixtures (opt-in, individual)
#
# Tests that need a vendor Party explicitly request `test_vendor_party`.
# Tests that need a customer Party explicitly request `test_customer_party`.
# This makes FK dependency chains visible in the test signature.
# ---------------------------------------------------------------------------

@pytest.fixture
def test_vendor_party(session, test_actor_id):
    """Create a SUPPLIER Party (vendor) for AP/Procurement FK constraints."""
    from finance_kernel.models.party import Party, PartyStatus, PartyType
    existing = session.get(Party, TEST_VENDOR_ID)
    if existing is not None:
        return existing
    party = Party(
        id=TEST_VENDOR_ID,
        party_type=PartyType.SUPPLIER,
        party_code="TEST-VENDOR",
        name="Test Vendor",
        status=PartyStatus.ACTIVE,
        is_active=True,
        created_by_id=test_actor_id,
    )
    session.add(party)
    session.flush()
    return party


@pytest.fixture
def test_customer_party(session, test_actor_id):
    """Create a CUSTOMER Party for AR/Revenue/Contracts FK constraints."""
    from finance_kernel.models.party import Party, PartyStatus, PartyType
    existing = session.get(Party, TEST_CUSTOMER_ID)
    if existing is not None:
        return existing
    party = Party(
        id=TEST_CUSTOMER_ID,
        party_type=PartyType.CUSTOMER,
        party_code="TEST-CUSTOMER",
        name="Test Customer",
        status=PartyStatus.ACTIVE,
        is_active=True,
        created_by_id=test_actor_id,
    )
    session.add(party)
    session.flush()
    return party


@pytest.fixture
def test_employee_party(session, test_actor_id):
    """Create an EMPLOYEE Party for Payroll/Expense FK constraints."""
    from finance_kernel.models.party import Party, PartyStatus, PartyType
    existing = session.get(Party, TEST_EMPLOYEE_ID)
    if existing is not None:
        return existing
    party = Party(
        id=TEST_EMPLOYEE_ID,
        party_type=PartyType.EMPLOYEE,
        party_code="TEST-EMPLOYEE",
        name="Test Employee",
        status=PartyStatus.ACTIVE,
        is_active=True,
        created_by_id=test_actor_id,
    )
    session.add(party)
    session.flush()
    return party


@pytest.fixture
def test_lessee_party(session, test_actor_id):
    """Create a CUSTOMER Party (lessee) for Lease FK constraints."""
    from finance_kernel.models.party import Party, PartyStatus, PartyType
    existing = session.get(Party, TEST_LESSEE_ID)
    if existing is not None:
        return existing
    party = Party(
        id=TEST_LESSEE_ID,
        party_type=PartyType.CUSTOMER,
        party_code="TEST-LESSEE",
        name="Test Lessee",
        status=PartyStatus.ACTIVE,
        is_active=True,
        created_by_id=test_actor_id,
    )
    session.add(party)
    session.flush()
    return party


# ---------------------------------------------------------------------------
# Module parent entity fixtures (opt-in, with explicit dependency chains)
# ---------------------------------------------------------------------------

@pytest.fixture
def test_bank_account(session, test_actor_id):
    """Create a BankAccount for Cash module FK constraints."""
    from finance_modules.cash.orm import BankAccountModel
    existing = session.get(BankAccountModel, TEST_BANK_ACCOUNT_ID)
    if existing is not None:
        return existing
    acct = BankAccountModel(
        id=TEST_BANK_ACCOUNT_ID,
        code="TEST-BANK-001",
        name="Test Operating Account",
        institution="Test Bank",
        account_number_masked="****1234",
        currency="USD",
        gl_account_code="1010-000",
        is_active=True,
        created_by_id=test_actor_id,
    )
    session.add(acct)
    session.flush()
    return acct


@pytest.fixture
def test_asset_category(session, test_actor_id):
    """Create an AssetCategory for Assets module FK constraints."""
    from finance_modules.assets.orm import AssetCategoryModel
    existing = session.get(AssetCategoryModel, TEST_ASSET_CATEGORY_ID)
    if existing is not None:
        return existing
    cat = AssetCategoryModel(
        id=TEST_ASSET_CATEGORY_ID,
        code="TEST-EQUIP",
        name="Test Equipment",
        useful_life_years=5,
        depreciation_method="straight_line",
        gl_asset_account="1500-000",
        gl_depreciation_account="1510-000",
        gl_accumulated_depreciation_account="6000-000",
        created_by_id=test_actor_id,
    )
    session.add(cat)
    session.flush()
    return cat


@pytest.fixture
def test_asset(session, test_actor_id, test_asset_category):
    """Create an Asset for Assets module FK constraints (child entities).

    Depends on: test_asset_category (FK to asset categories).
    """
    from finance_modules.assets.orm import AssetModel
    existing = session.get(AssetModel, TEST_ASSET_ID)
    if existing is not None:
        return existing
    asset = AssetModel(
        id=TEST_ASSET_ID,
        asset_number="TEST-ASSET-001",
        description="Test Equipment Asset",
        category_id=TEST_ASSET_CATEGORY_ID,
        acquisition_date=date(2024, 1, 1),
        acquisition_cost=Decimal("10000.00"),
        salvage_value=Decimal("1000.00"),
        useful_life_months=60,
        status="in_service",
        created_by_id=test_actor_id,
    )
    session.add(asset)
    session.flush()
    return asset


@pytest.fixture
def test_tax_jurisdiction(session, test_actor_id):
    """Create a TaxJurisdiction for Tax module FK constraints."""
    from finance_modules.tax.orm import TaxJurisdictionModel
    existing = session.get(TaxJurisdictionModel, TEST_TAX_JURISDICTION_ID)
    if existing is not None:
        return existing
    jur = TaxJurisdictionModel(
        id=TEST_TAX_JURISDICTION_ID,
        code="CA",
        name="California",
        jurisdiction_type="state",
        created_by_id=test_actor_id,
    )
    session.add(jur)
    session.flush()
    return jur


@pytest.fixture
def test_work_order(session, test_actor_id):
    """Create a WorkOrder for WIP module FK constraints."""
    from finance_modules.wip.orm import WorkOrderModel
    existing = session.get(WorkOrderModel, TEST_WORK_ORDER_ID)
    if existing is not None:
        return existing
    wo = WorkOrderModel(
        id=TEST_WORK_ORDER_ID,
        order_number="WO-TEST-001",
        item_id=uuid4(),
        quantity_ordered=Decimal("100"),
        quantity_completed=Decimal("0"),
        quantity_scrapped=Decimal("0"),
        status="released",
        created_by_id=test_actor_id,
    )
    session.add(wo)
    session.flush()
    return wo


@pytest.fixture
def test_operation(session, test_actor_id, test_work_order):
    """Create an Operation for WIP LaborEntry FK constraints.

    Depends on: test_work_order (FK to wip_work_orders).
    """
    from finance_modules.wip.orm import OperationModel
    existing = session.get(OperationModel, TEST_OPERATION_ID)
    if existing is not None:
        return existing
    op = OperationModel(
        id=TEST_OPERATION_ID,
        work_order_id=TEST_WORK_ORDER_ID,
        sequence=10,
        work_center_id=uuid4(),
        description="Assembly Operation",
        setup_time_hours=Decimal("0.5"),
        run_time_hours=Decimal("2.0"),
        labor_rate=Decimal("50.00"),
        overhead_rate=Decimal("25.00"),
        status="not_started",
        created_by_id=test_actor_id,
    )
    session.add(op)
    session.flush()
    return op


@pytest.fixture
def test_budget_version(session, test_actor_id):
    """Create a Budget (header) for Budget module FK constraints.

    The budget service's ``version_id`` parameter and ``BudgetTransferModel.version_id``
    FK both reference ``budget_budgets.id`` (the BudgetModel), not
    ``budget_versions`` (the amendment snapshot table).
    """
    from finance_modules.budget.orm import BudgetModel
    existing = session.get(BudgetModel, TEST_BUDGET_VERSION_ID)
    if existing is not None:
        return existing
    bv = BudgetModel(
        id=TEST_BUDGET_VERSION_ID,
        name="FY2024 Operating Budget",
        fiscal_year=2024,
        status="approved",
        created_by_id=test_actor_id,
    )
    session.add(bv)
    session.flush()
    return bv


@pytest.fixture
def test_pay_period(session, test_actor_id):
    """Create a PayPeriod for Payroll module FK constraints."""
    from finance_modules.payroll.orm import PayPeriodModel
    existing = session.get(PayPeriodModel, TEST_PAY_PERIOD_ID)
    if existing is not None:
        return existing
    pp = PayPeriodModel(
        id=TEST_PAY_PERIOD_ID,
        period_number=1,
        year=2024,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 15),
        pay_date=date(2024, 1, 20),
        pay_frequency="biweekly",
        created_by_id=test_actor_id,
    )
    session.add(pp)
    session.flush()
    return pp


@pytest.fixture
def test_payroll_employee(session, test_actor_id, test_employee_party):
    """Create a payroll Employee for Payroll module FK constraints.

    Depends on: test_employee_party (FK to parties via party_id).
    """
    from finance_modules.payroll.orm import EmployeeModel
    existing = session.get(EmployeeModel, TEST_PAYROLL_EMPLOYEE_ID)
    if existing is not None:
        return existing
    emp = EmployeeModel(
        id=TEST_PAYROLL_EMPLOYEE_ID,
        employee_number="EMP-TEST-001",
        first_name="Test",
        last_name="Employee",
        hire_date=date(2023, 1, 1),
        pay_type="salary",
        base_pay=Decimal("75000.00"),
        pay_frequency="biweekly",
        is_active=True,
        created_by_id=test_actor_id,
    )
    session.add(emp)
    session.flush()
    return emp


@pytest.fixture
def test_project(session, test_actor_id):
    """Create a Project for Project module FK constraints."""
    from finance_modules.project.orm import ProjectModel
    existing = session.get(ProjectModel, TEST_PROJECT_ID)
    if existing is not None:
        return existing
    proj = ProjectModel(
        id=TEST_PROJECT_ID,
        name="Test Project",
        project_type="fixed_price",
        status="active",
        start_date=date(2024, 1, 1),
        total_budget=Decimal("500000.00"),
        currency="USD",
        created_by_id=test_actor_id,
    )
    session.add(proj)
    session.flush()
    return proj


@pytest.fixture
def test_revenue_contract(session, test_actor_id, test_customer_party):
    """Create a RevenueContract for Revenue module FK constraints.

    Depends on: test_customer_party (FK to parties via customer_id).
    """
    from finance_modules.revenue.orm import RevenueContractModel
    existing = session.get(RevenueContractModel, TEST_REVENUE_CONTRACT_ID)
    if existing is not None:
        return existing
    rc = RevenueContractModel(
        id=TEST_REVENUE_CONTRACT_ID,
        contract_number="REV-TEST-001",
        customer_id=TEST_CUSTOMER_ID,
        start_date=date(2024, 1, 1),
        total_consideration=Decimal("100000.00"),
        currency="USD",
        status="active",
        created_by_id=test_actor_id,
    )
    session.add(rc)
    session.flush()
    return rc


@pytest.fixture
def test_lease(session, test_actor_id, test_lessee_party):
    """Create a Lease for Lease module FK constraints.

    Depends on: test_lessee_party (FK to parties via lessee_id).
    """
    from finance_modules.lease.orm import LeaseModel
    existing = session.get(LeaseModel, TEST_LEASE_ID)
    if existing is not None:
        return existing
    lease = LeaseModel(
        id=TEST_LEASE_ID,
        lease_number="LEASE-TEST-001",
        lessee_id=TEST_LESSEE_ID,
        lessor_name="Test Lessor LLC",
        classification="finance",
        commencement_date=date(2024, 1, 1),
        end_date=date(2028, 12, 31),
        monthly_payment=Decimal("5000.00"),
        discount_rate=Decimal("0.05"),
        currency="USD",
        status="active",
        created_by_id=test_actor_id,
    )
    session.add(lease)
    session.flush()
    return lease


@pytest.fixture
def test_contract(session, test_actor_id, test_customer_party):
    """Create a kernel Contract for Contracts module FK constraints.

    Depends on: test_customer_party (FK to parties via customer_id).
    """
    from finance_kernel.models.contract import Contract, ContractStatus, ContractType
    existing = session.get(Contract, TEST_CONTRACT_ID)
    if existing is not None:
        return existing
    contract = Contract(
        id=TEST_CONTRACT_ID,
        contract_number="CONTRACT-TEST-001",
        contract_name="Test Contract",
        contract_type=ContractType.FIRM_FIXED_PRICE,
        status=ContractStatus.ACTIVE,
        customer_party_id=TEST_CUSTOMER_ID,
        start_date=date(2024, 1, 1),
        end_date=date(2025, 12, 31),
        ceiling_amount=Decimal("1000000.00"),
        funded_amount=Decimal("500000.00"),
        currency="USD",
        created_by_id=test_actor_id,
    )
    session.add(contract)
    session.flush()
    return contract


@pytest.fixture
def test_expense_report(session, test_actor_id, test_employee_party):
    """Create an ExpenseReport for Expense module FK constraints.

    Depends on: test_employee_party (FK to parties via employee_id).
    """
    from finance_modules.expense.orm import ExpenseReportModel
    existing = session.get(ExpenseReportModel, TEST_EXPENSE_REPORT_ID)
    if existing is not None:
        return existing
    report = ExpenseReportModel(
        id=TEST_EXPENSE_REPORT_ID,
        employee_id=TEST_EMPLOYEE_ID,
        report_number="EXP-TEST-001",
        report_date=date(2024, 1, 15),
        purpose="Test expense report",
        submitted_date=date(2024, 1, 15),
        total_amount=Decimal("500.00"),
        currency="USD",
        status="submitted",
        created_by_id=test_actor_id,
    )
    session.add(report)
    session.flush()
    return report


@pytest.fixture
def test_ic_agreement(session, test_actor_id):
    """Create an IntercompanyAgreement for Intercompany module FK constraints."""
    from finance_modules.intercompany.orm import IntercompanyAgreementModel
    existing = session.get(IntercompanyAgreementModel, TEST_IC_AGREEMENT_ID)
    if existing is not None:
        return existing
    agreement = IntercompanyAgreementModel(
        id=TEST_IC_AGREEMENT_ID,
        entity_a="ENTITY_A",
        entity_b="ENTITY_B",
        agreement_type="transfer",
        markup_rate=Decimal("0"),
        currency="USD",
        effective_from=date(2024, 1, 1),
        created_by_id=test_actor_id,
    )
    session.add(agreement)
    session.flush()
    return agreement
