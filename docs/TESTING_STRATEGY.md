# Finance Modules Testing Strategy

## Overview

This document describes testing for **`tests/modules/`** — ERP module tests covering configuration, domain models, economic profiles, workflows, gap coverage, and module service integration. The **full test suite** (domain, engines, posting, audit, fuzzing, integration, services, etc.) is documented in **`docs/TEST_COMMANDS.md`**.

**Scope:** `tests/modules/` — **~73 test files**, **~1,972 tests** (as of last update; run `pytest tests/modules/ --collect-only -q` for current count).

**Database:** Pytest uses a separate DB (`finance_kernel_pytest` by default) so tests never drop or truncate interactive/scripts data. See **docs/TEST_COMMANDS.md** for setup (`createdb -U finance finance_kernel_pytest`).

---

## Quick Start - Test Commands

```bash
# Run ALL module tests (~73 files, ~1,972 tests)
pytest tests/modules/ -v

# Run full project test suite (all categories)
pytest tests/ -v --tb=short

# Core infrastructure tests
pytest tests/modules/test_config_schemas.py tests/modules/test_config_validation.py tests/modules/test_config_fuzzing.py -v
pytest tests/modules/test_model_immutability.py tests/modules/test_model_invariants.py -v
pytest tests/modules/test_profile_balance.py tests/modules/test_guard_execution.py -v
pytest tests/modules/test_workflow_transitions.py tests/modules/test_workflow_adversarial.py -v
pytest tests/modules/test_boundary_conditions.py -v

# Gap coverage tests (from GAP_ANALYSIS.md)
pytest tests/modules/test_blocked_party.py tests/modules/test_asset_depreciation.py tests/modules/test_returns.py -v
pytest tests/modules/test_payment_terms.py tests/modules/test_invoice_status.py tests/modules/test_cost_center.py -v
pytest tests/modules/test_landed_cost.py tests/modules/test_bank_reconciliation.py tests/modules/test_intercompany.py -v

# Module service integration tests
pytest tests/modules/test_ap_service.py 
pytest tests/modules/test_ar_service.py 
pytest tests/modules/test_assets_service.py -v
pytest tests/modules/test_cash_service.py tests/modules/test_contracts_service.py tests/modules/test_expense_service.py -v
pytest tests/modules/test_gl_service.py tests/modules/test_payroll_service.py tests/modules/test_procurement_service.py -v
pytest tests/modules/test_tax_service.py tests/modules/test_wip_service.py -v
pytest tests/modules/test_credit_loss_service.py tests/modules/test_revenue_service.py tests/modules/test_lease_service.py -v
pytest tests/modules/test_budget_service.py tests/modules/test_project_service.py tests/modules/test_intercompany_service.py -v
pytest tests/modules/test_cross_module_flow.py -v

# ORM tests (per-module persistence)
pytest tests/modules/test_ap_orm.py tests/modules/test_ar_orm.py tests/modules/test_assets_orm.py -v

# Run with coverage
pytest tests/modules/ --cov=finance_modules --cov-report=html

# Run only fast tests (exclude Hypothesis)
pytest tests/modules/ -v --ignore=tests/modules/test_config_fuzzing.py

# Run tests matching a pattern
pytest tests/modules/ -v -k "decimal"
pytest tests/modules/ -v -k "workflow"
pytest tests/modules/ -v -k "AP"

# Stop on first failure
pytest tests/modules/ -x

# Parallel (requires pytest-xdist)
pytest tests/modules/ -n auto
```

---

## Full Test Suite (Beyond Modules)

The project has **21+ test directories**. For commands by category (unit, posting, audit, fuzzing, integration, services, architecture, etc.), see **`docs/TEST_COMMANDS.md`**. Key areas:

| Area | Directory | Focus |
|------|-----------|--------|
| Fuzzing | `tests/fuzzing/` | Hypothesis (amounts, idempotency, workflow executor, workflow posting E2E) |
| Integration | `tests/integration/` | Approval + posting E2E, reversal E2E, module posting |
| Services | `tests/services/` | Approval, workflow executor, reversal, lifecycle recon, etc. |
| Domain | `tests/domain/` | Pure logic, schemas, profiles, economic link |
| Engines | `tests/engines/` | Valuation, allocation, matching, approval engine |
| Posting | `tests/posting/` | Balance, idempotency, period lock |
| Audit | `tests/audit/` | Immutability, hash chain |
| Architecture | `tests/architecture/` | Import boundaries, R20 mapping |

---

## Test Categories (tests/modules/)

### 1. Configuration Tests

#### 1.1 Config Schema Tests (`test_config_schemas.py`)
- Default values, override behavior, dictionary loading, isolation

#### 1.2 Config Fuzzing Tests (`test_config_fuzzing.py`)
- Hypothesis property-based testing; decimal precision, collections, edge cases

#### 1.3 Config Validation Tests (`test_config_validation.py`)
- Validation rules, cross-field validation, error messages

### 2. Domain Model Tests

#### 2.1 Model Immutability (`test_model_immutability.py`)
- Frozen dataclasses, hash stability, equality

#### 2.2 Model Invariants (`test_model_invariants.py`)
- Required fields, value constraints, relationship integrity

#### 2.3 Boundary Conditions (`test_boundary_conditions.py`)
- Decimal precision, date boundaries, collections, enums, UUIDs, strings

### 3. Economic Profile Tests

#### 3.1 Profile Balance (`test_profile_balance.py`)
- Structural balance, event type uniqueness, role coverage

#### 3.2 Guard Execution (`test_guard_execution.py`)
- Guard evaluation, precedence (REJECT vs BLOCK), bypass roles

### 4. Workflow Tests

#### 4.1 Workflow Transitions (`test_workflow_transitions.py`)
- Initial state, from/to states, reachability, terminal states, naming

#### 4.2 Workflow Adversarial (`test_workflow_adversarial.py`)
- Invalid transitions, guard validation, exhaustive paths, concurrency patterns

### 5. Gap Coverage Tests

From `docs/GAP_ANALYSIS.md`: blocked party, asset depreciation, returns/credit notes, payment terms, invoice status, cost center, landed cost, bank reconciliation, intercompany (see doc for details).

### 6. Module Service Integration Tests

End-to-end service layer tests: AP, AR, Assets, Cash, Contracts, Expense, GL, Payroll, Procurement, Tax, WIP, Credit Loss, Revenue, Lease, Budget, Project, Intercompany service, and cross-module flow. Each `test_*_service.py` file includes both core posting integration and per-module logic (e.g. payment runs, dunning, credit management, multicurrency translation, batch operations) — one service test file per module.

**Actor required (G14):** Any test that posts MUST have a valid actor. Use the module’s service fixture (e.g. `ap_service`), which depends on `party_service` and `test_actor_party`, and pass `actor_id=test_actor_id` in posting calls. Without a Party for the actor, posting returns `REJECTED` or `INVALID_ACTOR`. See **Actor required for posting tests** in `docs/TEST_COMMANDS.md`.

### 7. ORM Tests

Per-module persistence: `test_*_orm.py` for AP, AR, Assets, Budget, Cash, Contracts, Expense, GL, Inventory, Intercompany, Lease, Payroll, Procurement, Revenue, Tax, WIP, Period Close, Project.

### 8. Helpers Tests

`test_expense_helpers.py`, `test_inventory_helpers.py`.

---

## Test File Structure

```
tests/modules/
├── __init__.py
├── conftest.py
│
├── # Core infrastructure
├── test_config_schemas.py
├── test_config_fuzzing.py
├── test_config_validation.py
├── test_model_immutability.py
├── test_model_invariants.py
├── test_profile_balance.py
├── test_guard_execution.py
├── test_workflow_transitions.py
├── test_workflow_adversarial.py
├── test_boundary_conditions.py
│
├── # Gap coverage
├── test_blocked_party.py
├── test_asset_depreciation.py
├── test_returns.py
├── test_payment_terms.py
├── test_invoice_status.py
├── test_cost_center.py
├── test_landed_cost.py
├── test_bank_reconciliation.py
├── test_intercompany.py
│
├── # Module service integration
├── test_ap_service.py
├── test_ar_service.py
├── test_assets_service.py
├── test_cash_service.py
├── test_contracts_service.py
├── test_expense_service.py
├── test_gl_service.py
├── test_payroll_service.py
├── test_procurement_service.py
├── test_tax_service.py
├── test_wip_service.py
├── test_credit_loss_service.py
├── test_revenue_service.py
├── test_lease_service.py
├── test_budget_service.py
├── test_project_service.py
├── test_intercompany_service.py
├── test_cross_module_flow.py
│
├── # ORM (per-module persistence)
├── test_ap_orm.py
├── test_ar_orm.py
├── test_assets_orm.py
├── test_budget_orm.py
├── test_cash_orm.py
├── test_contracts_orm.py
├── test_expense_orm.py
├── test_gl_orm.py
├── test_inventory_orm.py
├── test_intercompany_orm.py
├── test_lease_orm.py
├── test_payroll_orm.py
├── test_procurement_orm.py
├── test_revenue_orm.py
├── test_tax_orm.py
├── test_wip_orm.py
├── test_period_close_orm.py
├── test_project_orm.py
│
├── # Helpers
├── test_expense_helpers.py
├── test_inventory_helpers.py
```

*(This document lives in `docs/TESTING_STRATEGY.md`.)*

---

## Test Results Summary

| Metric | Value |
|--------|--------|
| Test files | ~73 |
| Tests collected | ~1,972 |
| Categories | Config, models, profiles, workflows, gap, services, ORM, helpers |

For exact counts: `pytest tests/modules/ --collect-only -q`.

Skipped tests (known special cases): AR Receipt circular workflow; Period Close pre-initial state; dynamic profiles (e.g. labor_distribution, recon_adjustment, fx_revaluation).

---

## Dependencies

```bash
pip install pytest
pip install hypothesis    # For config fuzzing and property-based tests
pip install pytest-cov   # For coverage
pip install pytest-xdist # For parallel execution
```

---

## Coverage Goals

| Category | Target |
|----------|--------|
| Config schemas | 100% |
| Model definitions | 100% |
| Economic profiles | 100% |
| Workflows | 100% |
| Gap coverage (blocked party, depreciation, returns, etc.) | 100% |

---

## Invariants Under Test

1. **I1: Immutability** — Domain models immutable
2. **I2: Balance** — Journal entries balance (debits = credits)
3. **I3: State validity** — State transitions valid
4. **I4: Config isolation** — No config leakage between instances
5. **I5: Event uniqueness** — Event types unique across system
6. **I6: No orphan states** — All workflow states reachable
7. **I7: Decimal precision** — Financial amounts preserve precision
8. **I8: Guard completeness** — Multiple paths from same state have guards

---

## Adding Tests for New Modules

1. Add config tests to `test_config_schemas.py`, `test_config_validation.py`, `test_config_fuzzing.py`
2. Add model tests to `test_model_immutability.py`, `test_model_invariants.py`
3. Add profile tests to `test_profile_balance.py`
4. Add workflow tests to `test_workflow_transitions.py`, `test_workflow_adversarial.py` (ALL_WORKFLOWS)
5. Add boundary tests to `test_boundary_conditions.py` as needed
6. Add guard tests to `test_guard_execution.py`
7. Add gap tests if covered by GAP_ANALYSIS (e.g. new `test_<topic>.py`)
8. Add service test `test_<module>_service.py` (include any per-module logic here)
9. Add ORM test `test_<module>_orm.py` as needed

---

## CI/CD Integration

- Unit/infrastructure tests: run on every PR
- Hypothesis tests: reduced examples for speed; full runs (e.g. max_examples=1000) on nightly if configured
