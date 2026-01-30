# Finance Modules Testing Strategy

## Overview

Comprehensive testing for all 11 ERP modules ensuring correctness, consistency, and integration with the Finance Kernel.

## Quick Start - Test Commands

```bash
# Run ALL module tests (31 test files, 501 test functions)
pytest tests/modules/ -v

# Run specific test categories:
pytest tests/modules/test_config_schemas.py -v        # Config validation (20 tests)
pytest tests/modules/test_config_fuzzing.py -v        # Hypothesis fuzzing (17 tests)
pytest tests/modules/test_config_validation.py -v     # Config validation rules (43 tests)
pytest tests/modules/test_model_immutability.py -v    # Immutability (14 tests)
pytest tests/modules/test_model_invariants.py -v      # Model invariants (27 tests)
pytest tests/modules/test_profile_balance.py -v       # Profile balance (12 tests)
pytest tests/modules/test_workflow_transitions.py -v  # Workflow validity (12 tests)
pytest tests/modules/test_boundary_conditions.py -v   # Edge cases (35 tests)
pytest tests/modules/test_workflow_adversarial.py -v  # Adversarial (19 tests)
pytest tests/modules/test_guard_execution.py -v       # Guard execution (18 tests)

# Gap coverage tests (from TEST_GAP_ANALYSIS.md):
pytest tests/modules/test_blocked_party.py -v         # Blocked party (25 tests)
pytest tests/modules/test_asset_depreciation.py -v    # Asset depreciation (23 tests)
pytest tests/modules/test_returns.py -v               # Returns/credit notes (28 tests)
pytest tests/modules/test_payment_terms.py -v         # Payment terms (26 tests)
pytest tests/modules/test_invoice_status.py -v        # Invoice status (27 tests)
pytest tests/modules/test_cost_center.py -v           # Cost center propagation (23 tests)
pytest tests/modules/test_landed_cost.py -v           # Landed cost allocation (15 tests)
pytest tests/modules/test_bank_reconciliation.py -v   # Bank reconciliation (14 tests)
pytest tests/modules/test_intercompany.py -v          # Intercompany journals (14 tests)

# Module service integration tests:
pytest tests/modules/test_ap_service.py -v             # AP service (6 tests)
pytest tests/modules/test_ar_service.py -v             # AR service (7 tests)
pytest tests/modules/test_assets_service.py -v         # Assets service (7 tests)
pytest tests/modules/test_cash_service.py -v           # Cash service (6 tests)
pytest tests/modules/test_contracts_service.py -v      # Contracts service (10 tests)
pytest tests/modules/test_expense_service.py -v        # Expense service (8 tests)
pytest tests/modules/test_gl_service.py -v             # GL service (10 tests)
pytest tests/modules/test_payroll_service.py -v        # Payroll service (8 tests)
pytest tests/modules/test_procurement_service.py -v    # Procurement service (6 tests)
pytest tests/modules/test_tax_service.py -v            # Tax service (6 tests)
pytest tests/modules/test_wip_service.py -v            # WIP service (10 tests)
pytest tests/modules/test_cross_module_flow.py -v      # Cross-module flow (5 tests)

# Run with coverage report
pytest tests/modules/ --cov=finance_modules --cov-report=html

# Run only fast tests (exclude Hypothesis)
pytest tests/modules/ -v --ignore=tests/modules/test_config_fuzzing.py

# Run Hypothesis tests with more examples
pytest tests/modules/test_config_fuzzing.py -v --hypothesis-seed=random

# Run tests matching a pattern
pytest tests/modules/ -v -k "decimal"           # All decimal-related tests
pytest tests/modules/ -v -k "workflow"          # All workflow tests
pytest tests/modules/ -v -k "AP"                # All AP module tests

# Run tests with short output
pytest tests/modules/ -q

# Run tests and stop on first failure
pytest tests/modules/ -x

# Run tests in parallel (requires pytest-xdist)
pytest tests/modules/ -n auto
```

## Test Categories

### 1. Configuration Tests

#### 1.1 Config Schema Tests (`test_config_schemas.py`) - 20 tests
- **Default values**: All configs instantiate with sensible defaults
- **Override behavior**: Defaults can be overridden at instantiation
- **Dictionary loading**: `from_dict()` correctly handles nested objects
- **Isolation**: Modifying one config doesn't affect others

#### 1.2 Config Fuzzing Tests (`test_config_fuzzing.py`) - 17 tests
- **Property-based testing**: Hypothesis generates random valid inputs
- **Decimal precision**: Decimal values are preserved exactly
- **Collection handling**: Lists and tuples work correctly
- **Edge cases**: Empty strings, None values, high-precision decimals

#### 1.3 Config Validation Tests (`test_config_validation.py`) - 43 tests
- **Validation rules**: All config fields enforce valid ranges and types
- **Cross-field validation**: Dependent field constraints enforced
- **Error messages**: Validation errors provide clear descriptions

### 2. Domain Model Tests

#### 2.1 Model Immutability Tests (`test_model_immutability.py`) - 14 tests
- **Frozen dataclasses**: All domain models raise `FrozenInstanceError` on mutation
- **Hash stability**: Immutable objects have stable hashes
- **Equality**: Same data produces equal objects

#### 2.2 Model Invariants Tests (`test_model_invariants.py`) - 27 tests
- **Required fields**: All required fields enforced at construction
- **Value constraints**: Domain-specific value constraints validated
- **Relationship integrity**: Cross-model references are consistent

#### 2.3 Boundary Condition Tests (`test_boundary_conditions.py`) - 35 tests
- **Decimal precision**: 10-digit, zero, very small/large values
- **Date boundaries**: Leap years, month ends, fiscal periods
- **Collection boundaries**: Empty, single, large collections
- **Enum exhaustive**: All enum values tested
- **UUID handling**: Nil UUID, max UUID, uniqueness
- **String handling**: Empty, long, unicode, special characters

### 3. Economic Profile Tests

#### 3.1 Profile Balance Tests (`test_profile_balance.py`) - 12 tests
- **Structural balance**: Every profile has at least one debit and one credit
- **Event type uniqueness**: No duplicate event types across modules
- **Role coverage**: All account roles are used

#### 3.2 Guard Execution Tests (`test_guard_execution.py`) - 18 tests
- **Guard evaluation**: Guards fire correctly for matching conditions
- **Guard precedence**: REJECT takes priority over BLOCK
- **Bypass roles**: Authorized roles can bypass BLOCK guards

### 4. Workflow Tests

#### 4.1 Workflow Transition Tests (`test_workflow_transitions.py`) - 12 tests
- **Valid initial state**: Initial state exists in state list
- **Valid transitions**: All from/to states exist
- **Reachability**: No orphan states
- **Terminal states**: At least one terminal state exists
- **Naming conventions**: States and actions are lowercase

#### 4.2 Workflow Adversarial Tests (`test_workflow_adversarial.py`) - 19 tests
- **Invalid transitions**: Self-transitions, transitions from terminals
- **Guard validation**: Guarded transitions have metadata
- **Exhaustive paths**: All states reachable, happy path exists
- **State machine completeness**: No dead ends, meaningful names
- **Concurrency patterns**: Race condition detection
- **Business rules**: Approval before execution, posting after validation
- **Fuzzing**: Random action sequences

### 5. Gap Coverage Tests

Tests addressing all gaps identified in `docs/TEST_GAP_ANALYSIS.md`:

#### 5.1 Blocked Party Tests (`test_blocked_party.py`) - 25 tests
- **Supplier blocks**: Invoice/payment blocked for held suppliers
- **Customer blocks**: Invoice blocked for credit-held customers
- **Date-based holds**: Release date enforcement
- **Hold release**: Operations allowed after hold expires

#### 5.2 Asset Depreciation Tests (`test_asset_depreciation.py`) - 23 tests
- **Straight-line**: Annual and monthly depreciation, pro-rata first year
- **Double declining**: Accelerated depreciation with SL switch
- **Written-down value**: Constant rate on declining balance
- **Disposal**: Gain/loss on sale, scrap entries
- **Revaluation**: Upward revaluation, impairment

#### 5.3 Returns/Credit Note Tests (`test_returns.py`) - 28 tests
- **Purchase returns**: GL reversal, price difference variance, partial returns
- **Sales returns**: Credit note reversal, COGS reversal, restock

#### 5.4 Payment Terms Tests (`test_payment_terms.py`) - 26 tests
- **Term allocation**: FIFO allocation to due dates, partial payments
- **Early payment discount**: 2/10 Net 30, discount expiry, tax impact
- **Over-allocation**: Rejection of excess allocation

#### 5.5 Invoice Status Tests (`test_invoice_status.py`) - 27 tests
- **Payment status**: Full/partial payment status updates
- **Cancellation**: Status reversion on payment cancel
- **Credit notes**: Outstanding reduction

#### 5.6 Cost Center Tests (`test_cost_center.py`) - 23 tests
- **Propagation**: Invoice/payment CC flows to GL
- **Defaults**: Account default CC applied when unspecified
- **Querying**: Balance filtered by cost center

#### 5.7 Landed Cost Tests (`test_landed_cost.py`) - 15 tests
- **Allocation**: By value, quantity, weight
- **GL entries**: Correct landed cost journalization
- **Valuation**: Inventory rate includes landed costs

#### 5.8 Bank Reconciliation Tests (`test_bank_reconciliation.py`) - 14 tests
- **Matching**: Exact amount, reference, date tolerance
- **Status**: Reconciled/unreconciled tracking

#### 5.9 Intercompany Tests (`test_intercompany.py`) - 14 tests
- **Mirror entries**: Auto-create pair in target company
- **Cancellation**: Cancel both entries together
- **Currency**: Handle different functional currencies

### 6. Module Service Integration Tests

End-to-end tests for each module's service layer, verifying that module services correctly orchestrate posting through `ModulePostingService` and the kernel pipeline.

#### 6.1 AP Service Tests (`test_ap_service.py`) - 6 tests
- **Invoice posting**: AP invoice creates correct journal entries
- **Payment posting**: AP payment settles outstanding invoices

#### 6.2 AR Service Tests (`test_ar_service.py`) - 7 tests
- **Invoice posting**: AR invoice creates receivable entries
- **Receipt posting**: AR receipt applies against open invoices

#### 6.3 Assets Service Tests (`test_assets_service.py`) - 7 tests
- **Acquisition**: Asset purchase creates capitalization entries
- **Depreciation**: Periodic depreciation posts correctly

#### 6.4 Cash Service Tests (`test_cash_service.py`) - 6 tests
- **Deposits**: Bank deposit creates correct entries
- **Disbursements**: Cash disbursement posts correctly

#### 6.5 Contracts Service Tests (`test_contracts_service.py`) - 10 tests
- **Billing**: Contract billing creates revenue entries
- **DCAA compliance**: Cost-plus contract posting with DCAA rules

#### 6.6 Expense Service Tests (`test_expense_service.py`) - 8 tests
- **Expense reports**: Expense submission creates GL entries
- **Reimbursement**: Employee reimbursement posting

#### 6.7 GL Service Tests (`test_gl_service.py`) - 10 tests
- **Manual journals**: Direct GL entry posting
- **Adjustments**: Period-end adjustment entries

#### 6.8 Payroll Service Tests (`test_payroll_service.py`) - 8 tests
- **Payroll runs**: Payroll posting creates salary/tax entries
- **Accruals**: Payroll accrual entries

#### 6.9 Procurement Service Tests (`test_procurement_service.py`) - 6 tests
- **PO receipt**: Goods receipt creates inventory entries
- **Three-way match**: Invoice/PO/receipt matching

#### 6.10 Tax Service Tests (`test_tax_service.py`) - 6 tests
- **Tax collection**: Sales tax posting
- **Tax remittance**: Tax payment entries

#### 6.11 WIP Service Tests (`test_wip_service.py`) - 10 tests
- **Labor capture**: WIP labor posting
- **Overhead allocation**: WIP overhead distribution

#### 6.12 Cross-Module Flow Tests (`test_cross_module_flow.py`) - 5 tests
- **End-to-end flows**: Multi-module transaction chains (e.g., procurement -> AP -> cash)
- **Inter-module consistency**: Balances reconcile across module boundaries

## Test File Structure

```
tests/modules/
├── __init__.py
├── conftest.py                      # Shared fixtures
├── TESTING_STRATEGY.md              # This document
│
│ # Core infrastructure tests
├── test_config_schemas.py           # Config defaults & loading (20 tests)
├── test_config_fuzzing.py           # Hypothesis property tests (17 tests)
├── test_config_validation.py        # Config validation rules (43 tests)
├── test_model_immutability.py       # Frozen dataclass tests (14 tests)
├── test_model_invariants.py         # Model invariant tests (27 tests)
├── test_profile_balance.py          # Economic profile balance (12 tests)
├── test_workflow_transitions.py     # State machine validity (12 tests)
├── test_boundary_conditions.py      # Edge cases (35 tests)
├── test_workflow_adversarial.py     # Adversarial workflow tests (19 tests)
├── test_guard_execution.py          # Guard execution tests (18 tests)
│
│ # Gap coverage tests
├── test_blocked_party.py            # Blocked supplier/customer (25 tests)
├── test_asset_depreciation.py       # Asset depreciation methods (23 tests)
├── test_returns.py                  # Returns/credit notes GL (28 tests)
├── test_payment_terms.py            # Payment term allocation (26 tests)
├── test_invoice_status.py           # Invoice status on payment (27 tests)
├── test_cost_center.py              # Cost center propagation (23 tests)
├── test_landed_cost.py              # Landed cost allocation (15 tests)
├── test_bank_reconciliation.py      # Bank reconciliation matching (14 tests)
├── test_intercompany.py             # Intercompany journals (14 tests)
│
│ # Module service integration tests
├── test_ap_service.py               # AP module service tests (6 tests)
├── test_ar_service.py               # AR module service tests (7 tests)
├── test_assets_service.py           # Assets module service tests (7 tests)
├── test_cash_service.py             # Cash module service tests (6 tests)
├── test_contracts_service.py        # Contracts module service tests (10 tests)
├── test_expense_service.py          # Expense module service tests (8 tests)
├── test_gl_service.py               # GL module service tests (10 tests)
├── test_payroll_service.py          # Payroll module service tests (8 tests)
├── test_procurement_service.py      # Procurement module service tests (6 tests)
├── test_tax_service.py              # Tax module service tests (6 tests)
├── test_wip_service.py              # WIP module service tests (10 tests)
└── test_cross_module_flow.py        # Cross-module flow tests (5 tests)
```

## Test Results Summary

| Test File | Tests | Classes |
|-----------|-------|---------|
| test_config_schemas.py | 20 | 4 |
| test_config_fuzzing.py | 17 | 7 |
| test_config_validation.py | 43 | 10 |
| test_model_immutability.py | 14 | 11 |
| test_model_invariants.py | 27 | 9 |
| test_profile_balance.py | 12 | 12 |
| test_workflow_transitions.py | 12 | 8 |
| test_boundary_conditions.py | 35 | 8 |
| test_workflow_adversarial.py | 19 | 7 |
| test_guard_execution.py | 18 | 10 |
| test_blocked_party.py | 25 | 7 |
| test_asset_depreciation.py | 23 | 7 |
| test_returns.py | 28 | 8 |
| test_payment_terms.py | 26 | 8 |
| test_invoice_status.py | 27 | 9 |
| test_cost_center.py | 23 | 8 |
| test_landed_cost.py | 15 | 7 |
| test_bank_reconciliation.py | 14 | 5 |
| test_intercompany.py | 14 | 7 |
| test_ap_service.py | 6 | 2 |
| test_ar_service.py | 7 | 2 |
| test_assets_service.py | 7 | 2 |
| test_cash_service.py | 6 | 2 |
| test_contracts_service.py | 10 | 2 |
| test_expense_service.py | 8 | 2 |
| test_gl_service.py | 10 | 2 |
| test_payroll_service.py | 8 | 2 |
| test_procurement_service.py | 6 | 2 |
| test_tax_service.py | 6 | 2 |
| test_wip_service.py | 10 | 2 |
| test_cross_module_flow.py | 5 | 3 |
| **Total** | **501** | **181** |

Skipped tests are for known special cases:
- AR Receipt circular workflow
- Period Close pre-initial state
- Dynamic profiles (labor_distribution, recon_adjustment, fx_revaluation)

## Dependencies

```bash
# Required
pip install pytest

# For Hypothesis fuzzing tests
pip install hypothesis

# For coverage reports
pip install pytest-cov

# For parallel execution
pip install pytest-xdist
```

## Coverage Goals

| Category | Target | Current |
|----------|--------|---------|
| Config schemas | 100% | 100% |
| Model definitions | 100% | 100% |
| Economic profiles | 100% | 100% |
| Workflows | 100% | 100% |
| Blocked party | 100% | 100% |
| Asset depreciation | 100% | 100% |
| Returns/credit notes | 100% | 100% |
| Payment terms | 100% | 100% |

## Invariants Under Test

These invariants are verified across all modules:

1. **I1: Immutability** - All domain models are immutable
2. **I2: Balance** - All journal entries balance (debits = credits)
3. **I3: State validity** - All state transitions are valid
4. **I4: Config isolation** - Config changes don't leak between instances
5. **I5: Event uniqueness** - All event types are unique across system
6. **I6: No orphan states** - All workflow states are reachable
7. **I7: Decimal precision** - Financial amounts preserve 10-digit precision
8. **I8: Guard completeness** - Multiple paths from same state have guards

## Adding Tests for New Modules

When adding a new module:

1. Add config tests to `test_config_schemas.py`
2. Add config fuzzing to `test_config_fuzzing.py`
3. Add config validation to `test_config_validation.py`
4. Add model immutability tests to `test_model_immutability.py`
5. Add model invariant tests to `test_model_invariants.py`
6. Add profile balance tests to `test_profile_balance.py`
7. Add workflow tests to `test_workflow_transitions.py`
8. Add workflow to `test_workflow_adversarial.py` ALL_WORKFLOWS list
9. Add boundary tests to `test_boundary_conditions.py` as needed
10. Add guard execution tests to `test_guard_execution.py`

## CI/CD Integration

Tests run on every PR:
- Unit tests: Always run (~1 second)
- Hypothesis tests: Run with reduced examples for speed
- Full Hypothesis: Run on nightly builds with max_examples=1000
