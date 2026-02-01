# Module ORM Persistence Layer -- Implementation Plan

**Date:** 2026-01-31
**Last Audit:** 2026-02-01
**Last Update:** 2026-02-01 (Phase 2 complete, kernel guard added, starting Phase 3)
**Status:** IN-PROGRESS -- Phase 3 (ORM round-trip verification tests)
**Objective:** Wire all module domain objects to PostgreSQL via SQLAlchemy ORM models

---

## EXECUTION LOG (2026-02-01)

### Phase 0: DONE -- Architecture boundary fix

**Approach chosen:** Created `create_all_tables()` in `finance_modules/_orm_registry.py`
(not `finance_services/db_setup.py` as originally planned -- the services layer import
would have violated the DAG rule caught by architecture tests).

**Files changed:**
- `finance_modules/_orm_registry.py` -- added `create_all_tables(install_triggers=True)`
- Updated 5 production scripts to call `create_all_tables()` instead of `create_tables()`
- `finance_kernel/db/engine.py` -- removed illegal `import finance_modules` line

**Verification:** All 14 architecture tests pass.

**Learning:** `finance_services` cannot import `finance_modules` either. The only
valid location for a function that imports both kernel and modules is inside
`finance_modules/` itself (since modules are allowed to import kernel).

### Phase 1a: DONE -- Service wiring FK placeholder fixes

**Problem:** 8 service files had placeholder FK values in `session.add()` calls.
The original wiring used transaction IDs, `uuid4()`, or `actor_id` as FK values
for parent entity columns, causing FK constraint violations.

**Approach:** Add proper FK parameters to each service method signature. Make them
required (not optional with fallback) because the FK column is NOT NULL. Callers
must provide the real parent entity ID.

**Files changed (8 service files, 19 methods total):**

| Service | Methods Fixed | FK Parameter Added |
|---------|--------------|-------------------|
| `cash/service.py` | 7: record_receipt, record_disbursement, record_bank_fee, record_interest_earned, record_transfer, reconcile_bank_statement, import_bank_statement | `bank_account_id: UUID` (already param, fixed ORM usage) |
| `tax/service.py` | 4: record_tax_obligation, record_tax_payment, record_vat_settlement, record_multi_jurisdiction_tax | `jurisdiction_id: UUID` |
| `wip/service.py` | 2: record_labor_charge, record_overhead_allocation | `work_order_id: UUID`, `operation_id: UUID` |
| `budget/service.py` | 1: transfer_budget | `version_id: UUID` |
| `assets/service.py` | 1: record_asset_acquisition | `category_id: UUID` |
| `payroll/service.py` | 1: record_payroll_run | `pay_period_id: UUID | None = None` |
| `lease/service.py` | 1: record_initial_recognition | `lessee_id: UUID | None = None` |
| `ap/service.py` | 1: record_payment | `vendor_id: UUID` (moved from optional to required) |

**Learning:** Never use a transaction ID as a parent entity FK. The `or` fallback
pattern (`vendor_id=vendor_id or invoice_id`) silently substitutes a wrong entity
type -- `invoice_id` is not a valid `parties.id`. Make FK parameters required.

### Phase 1a: DONE -- Test fixture restructuring

**Approach chosen (per user direction):** Explicit opt-in fixtures. No autouse.
Every test declares which parent entities it depends on in its function signature.
Pytest enforces ordering at collection time.

**Changes to `tests/modules/conftest.py`:**
- Removed `@pytest.fixture(autouse=True)` from `_module_parent_entities`
- Split bulk Party creation into 4 individual fixtures:
  `test_vendor_party`, `test_customer_party`, `test_employee_party`, `test_lessee_party`
- Added `test_operation` fixture (depends on `test_work_order`)
- Added `TEST_OPERATION_ID` deterministic UUID
- Chained dependencies:
  - `test_payroll_employee` → `test_employee_party`
  - `test_revenue_contract` → `test_customer_party`
  - `test_lease` → `test_lessee_party`
  - `test_contract` → `test_customer_party`
  - `test_expense_report` → `test_employee_party`
  - `test_operation` → `test_work_order`
  - `test_asset` → `test_asset_category`

**18 deterministic UUIDs** available for test imports.

### Phase 1c: DONE -- Update test files with explicit fixture dependencies

**20 test files updated** across 6 parallel agents. Each test function that calls
a service posting method now explicitly declares its parent entity fixture.

**Fixture → Test file mapping (93 fixture additions total):**

| Fixture | Test Files Updated | Functions Updated |
|---------|-------------------|-------------------|
| `test_vendor_party` | AP (2), Procurement (1) | 9 + 5 + 10 = 24 |
| `test_customer_party` | AR (2), Revenue (1), Contracts (2), Credit Loss (1) | 10 + 11 + 7 + 6 + 4 + 4 = 42 |
| `test_employee_party` | Payroll (2), Expense (1) | 8 + 2 + 9 = 19 |
| `test_lessee_party` | Lease (1) | 8 |
| `test_bank_account` | Cash (2) | 8 + 5 = 13 |
| `test_asset_category` | Assets (2) | 7 + 4 = 11 |
| `test_tax_jurisdiction` | Tax (2) | 6 + 5 = 11 |
| `test_work_order` + `test_operation` | WIP (2) | 9 + 1 = 10 |
| `test_budget_version` | Budget (1) | 7 |
| `test_pay_period` | Payroll (2) | (paired with test_employee_party) |

**Rule enforced:** Pure computation tests (model instantiation, helper functions,
query methods) were intentionally left without fixtures since they don't touch ORM.

### Architecture fix: tests/conftest.py now uses production orchestration

**Problem:** `db_tables` fixture did ad-hoc `import_all_orm_models() + create_tables()`
instead of calling the production orchestration function `create_all_tables()`.

**Rule:** Tests must call the same orchestration function as production.

**Fix:** Replaced ad-hoc calls with `create_all_tables()`. Kept
`register_immutability_listeners()` (ORM defense layer, separate from DB triggers).

**File changed:** `tests/conftest.py` line 362, `finance_modules/_orm_registry.py` docstring.

### Phase 2: DONE -- Intercompany + Procurement service wiring + kernel guard

**Intercompany wiring (3 methods):**
- `post_ic_transfer()` — persists `IntercompanyTransactionModel` after successful posting
- `generate_eliminations()` — persists elimination transaction record
- `post_transfer_pricing_adjustment()` — persists with `agreement_id` FK

**Procurement wiring (3 methods with matching ORM models):**
- `receive_goods()` — persists `ReceivingReportModel`
- `create_requisition()` — persists `PurchaseRequisitionModel` + `RequisitionLineModel` children
- `match_receipt_to_po()` — persists `ReceivingReportModel` (match record)

**7 procurement methods intentionally unwired** (no matching ORM models in
`procurement/orm.py`): `create_purchase_order`, `record_commitment`,
`relieve_commitment`, `record_price_variance`, `record_quantity_variance`,
`amend_purchase_order`, `convert_requisition_to_po`.

**Kernel guard added to `create_tables()`:**
- Added `kernel_only: bool = False` keyword-only parameter
- When `kernel_only=False` (default), checks `Base.metadata.tables` count
- If fewer than 25 tables registered, raises `RuntimeError` directing caller to
  `create_all_tables()` from `finance_modules._orm_registry`
- `kernel_only=True` bypasses the guard for kernel-only unit tests
- This makes `create_all_tables()` the only safe way to get a complete schema

**Files changed:**
- `finance_modules/intercompany/service.py` — ORM persistence in 3 methods
- `finance_modules/procurement/service.py` — ORM persistence in 3 methods
- `finance_kernel/db/engine.py` — `kernel_only` guard parameter + table count check
- `finance_modules/_orm_registry.py` — docstring updated to document the guard

### Phase 3-5: PENDING

- Phase 3: ORM round-trip verification tests (17 modules)
- Phase 4: Full regression and integrity gate
- Phase 5: Commit in correct order

---

## AUDIT (2026-02-01): True Status vs. What Plan Originally Claimed

The plan was not kept in sync with actual work. An independent audit of the
codebase on 2026-02-01 found the following discrepancies and open issues.

### Corrected Progress Summary

| Step | Plan Said | Actual Status | Evidence |
|------|-----------|---------------|----------|
| Phase 0 | DONE | **FIXED** -- kernel boundary restored, production bootstrap canonical | `create_all_tables()` in `_orm_registry.py`, all 14 arch tests pass |
| Phase 1-6 ORM | DONE | **Verified correct** -- all 18 files, 106 models, 8,119 lines | File counts and `import_all_orm_models()` both verified |
| Service Wiring | PENDING | **FIXED** -- 19 methods across 8 services corrected, FK params required | All placeholder FK values removed, real parent entity IDs required |
| Test Fixtures | PENDING | **FIXED** -- 20 test files, 93 fixture additions, all explicit opt-in | All 49 module test files pass syntax validation |
| Bootstrap Path | N/A | **FIXED** -- tests/conftest.py uses `create_all_tables()` | Same orchestration as production scripts |
| Kernel Guard | N/A | **FIXED** -- `create_tables()` rejects incomplete schema | `kernel_only=True` required for kernel-only tests |
| Intercompany Wiring | PENDING | **FIXED** -- 3 methods wired with `session.add()` | IC transfer, elimination, transfer pricing |
| Procurement Wiring | PENDING | **FIXED** -- 3 methods wired (3 with matching ORM) | receive_goods, create_requisition, match_receipt_to_po |
| ORM Tests | PENDING | **IN PROGRESS** -- zero `test_*_orm.py` files exist | Phase 3 starting now |
| Final Regression | PENDING | **Not yet run** -- waiting for Phase 3 completion | Need to run full suite after ORM tests written |

### Test Results

| Scenario | Passed | Failed | Notes |
|----------|--------|--------|-------|
| Committed code (`9071c77`) | 3,627 | 2 | Architecture boundary violations only |
| With uncommitted changes | 3,566 | 63 | Architecture violations fixed, but 63 new FK failures |

---

## ISSUE 1: Architecture Boundary Violation -- RESOLVED (Phase 0)

### Problem

`finance_kernel/db/engine.py` (line 205 in committed code) imports
`finance_modules._orm_registry`. This violates the absolute architecture rule:

> **finance_kernel MUST NOT import from finance_modules**

Both `test_kernel_boundary.py` and `test_import_boundaries.py` catch this and
fail. This was shipped in commit `9071c77`.

### What the uncommitted code does (partial fix)

The uncommitted working tree removes the import from `engine.py` and adds it
to `tests/conftest.py` instead:

```python
# tests/conftest.py (uncommitted change):
# Import all module ORM models so Base.metadata discovers their tables
# before create_tables() calls create_all().  This import lives here
# (not in engine.py) to respect the kernel boundary: finance_kernel
# MUST NOT import from finance_modules.
from finance_modules._orm_registry import import_all_orm_models
import_all_orm_models()
create_tables()
```

This fixes the architecture test for the test suite, but **no production script
calls `import_all_orm_models()`**. The 6 production scripts that call
`create_tables()` would silently create only kernel tables, missing all 106
module tables.

### Options

#### Option A: Caller-is-responsible (Django pattern)

Every script/entrypoint that calls `create_tables()` must first call
`import_all_orm_models()`. The kernel function stays pure -- it creates
whatever tables are registered in `Base.metadata` at call time.

```python
# Each script does:
from finance_modules._orm_registry import import_all_orm_models
from finance_kernel.db.engine import create_tables
import_all_orm_models()
create_tables()
```

**Pro:** Cleanest architecture. Zero kernel boundary violations. This is
exactly how Django works (`manage.py` imports `INSTALLED_APPS` before anything
else).

**Con:** Fragile. Easy to forget. 6 scripts need updating. If a new script
omits the call, module tables silently don't exist. There's no compile-time
or runtime guard against the omission.

**Risk level:** Medium (silent failure mode).

#### Option B: Wrapper function in `finance_services/` (RECOMMENDED)

Create a `create_all_tables()` function in `finance_services/db_setup.py` that
calls both. The services layer is allowed to import from both kernel and modules.

```python
# finance_services/db_setup.py
from finance_modules._orm_registry import import_all_orm_models
from finance_kernel.db.engine import create_tables

def create_all_tables(install_triggers: bool = True) -> None:
    """Create kernel + module tables. Use this instead of create_tables()."""
    import_all_orm_models()
    create_tables(install_triggers=install_triggers)
```

Scripts and tests call `create_all_tables()` instead of `create_tables()`.
The kernel's `create_tables()` remains available for kernel-only scenarios
(e.g., unit tests that don't need module tables).

**Pro:** Single function. Impossible to forget module tables. Respects the
layered architecture (`finance_services` is allowed to import both). Clear
migration path -- rename calls one at a time.

**Con:** Now two functions exist that create tables. A developer could call the
wrong one. Mitigated by documentation and deprecation warnings.

**Risk level:** Low.

#### Option C: Callback parameter on `create_tables()`

```python
# finance_kernel/db/engine.py
def create_tables(
    install_triggers: bool = True,
    model_registries: list[Callable[[], None]] | None = None,
) -> None:
    if model_registries:
        for register in model_registries:
            register()
    Base.metadata.create_all(engine)
    ...
```

**Pro:** Kernel stays pure (no upward imports). The function is self-documenting
about what it expects. Callers explicitly pass `[import_all_orm_models]`.

**Con:** Unusual API. Callers still need to know about the callback. Doesn't
prevent silent omission -- passing `None` still creates kernel-only tables.

**Risk level:** Medium (same silent failure as Option A, slightly more obvious).

#### Option D: Separate `Base` for module tables

Module ORM models inherit from a `ModuleBase` with separate `MetaData`.
A `create_module_tables()` function lives in `finance_modules/`.

**Pro:** Total layer separation. Each layer owns its own DDL.

**Con:** All 18 orm.py files need updating to use `ModuleBase`. Cross-layer
FK relationships (module tables referencing kernel `parties.id`, etc.) become
complex -- SQLAlchemy needs shared metadata or naming conventions for
cross-metadata FKs. This is a significant refactor with high risk of
breaking existing model definitions.

**Risk level:** High (major refactor, FK complexity).

#### Option E: Move `create_tables()` out of the kernel

Relocate `create_tables()` to `finance_services/db_setup.py` where it can
import from both layers freely.

**Pro:** The function naturally belongs at the service layer since it
orchestrates DDL across layers.

**Con:** Major refactor. 7+ call sites need updating. The kernel's
`db/engine.py` API shrinks. `get_engine()` and `get_session()` stay in
kernel while `create_tables()` moves out -- the split feels unnatural.
Every test and script importing from `finance_kernel.db.engine` breaks.

**Risk level:** High (wide blast radius).

### Recommendation

**Option B** is the best balance of correctness, safety, and effort. It adds
one file (`finance_services/db_setup.py`), requires no changes to existing
kernel code, and provides a single function that can't silently omit module
tables. The `conftest.py` change (already in the working tree) is the
test-specific version of this pattern and can stay as-is or migrate to call
`create_all_tables()`.

---

## ISSUE 2: Service Wiring Causes 63 FK Violations -- PARTIALLY RESOLVED (Phase 1a)

### Problem

The uncommitted service wiring adds `session.add()` calls that create child
ORM entities (invoices, payments, labor entries, etc.) without ensuring the
parent entities they reference via FK exist in the database.

Example: `APService.record_invoice()` creates an `APInvoiceModel` with
`vendor_id` pointing to `parties.id`. But the test never created that Party
row. PostgreSQL rejects the insert with `ForeignKeyViolation`.

### Affected modules (all 63 failures are FK violations)

| Module | Failing Tests | Missing Parent Entity |
|--------|--------------|----------------------|
| AP | 5 | Party (vendor) |
| AR | 4 | Party (customer) |
| Assets | 7 | AssetCategory, Asset (for schedules/disposals/transfers) |
| Budget | 2 | BudgetVersion |
| Cash | 6 | BankAccount |
| Contracts | 2 | Contract (kernel model) |
| Expense | 2 | Employee (Party), ExpenseReport (for reimbursements) |
| Lease | 4 | Party (lessee), Lease (for payments/modifications) |
| Payroll | 2 | PayPeriod, Employee (Party) |
| Project | 1 | Project |
| Revenue | 7 | Party (customer), RevenueContract |
| Tax | 8 | TaxJurisdiction |
| WIP | 3 | WorkOrder |
| Cross-module | 8 | Various (parametrized test hitting all modules) |

### Root cause

The service wiring creates child ORM entities inside methods like
`record_invoice()`, but these methods were designed when no persistence
existed. The test fixtures set up kernel infrastructure (accounts, periods,
policies) but not module-level parent entities (vendors, bank accounts,
categories). The FK constraints are correct -- the tests are incomplete.

### Options

#### Option A: Fix test fixtures to pre-populate parent entities

Update the test conftest and/or individual test files to create the parent
ORM rows before running service methods. For example, before testing
`record_invoice()`, insert a Party row for the vendor.

```python
# Example fixture addition:
@pytest.fixture
def vendor(db_session):
    from finance_kernel.models.party import PartyModel
    party = PartyModel(id=VENDOR_ID, name="Test Vendor", party_type="vendor", ...)
    db_session.add(party)
    db_session.flush()
    return party
```

**Pro:** FK constraints stay enforced. Tests become more realistic, mirroring
actual business workflows. Data integrity is validated. This is the correct
long-term approach.

**Con:** Significant test changes. Need to identify every parent entity for
every failing test. Some parent entities have their own parents (chain FKs),
requiring multi-level fixture setup. Estimated ~20-30 fixture additions.

**Risk level:** Low (correct fix), effort: Medium.

#### Option B: Create parent entities inside service methods (upsert)

Each service method checks if the referenced parent exists and creates it
if missing.

```python
def record_invoice(self, vendor_id, ...):
    # Ensure vendor exists:
    if not self._session.get(PartyModel, vendor_id):
        self._session.add(PartyModel(id=vendor_id, name="Unknown", ...))
    orm_invoice = APInvoiceModel(vendor_id=vendor_id, ...)
    self._session.add(orm_invoice)
```

**Pro:** Service methods are self-contained. Tests don't need changes.

**Con:** Fundamentally wrong. Auto-creating a Party with `name="Unknown"`
just because an invoice references it violates the business domain. Parties
should be created through `PartyService.create_party()` as part of a real
business flow, not as a side effect of invoice recording. This hides data
quality problems.

**Risk level:** High (masks real data issues, wrong abstraction).

#### Option C: Remove within-module FK constraints

Drop FK constraints from child tables. Keep the UUID columns but don't
enforce referential integrity in the database.

```python
# Instead of:
vendor_id: Mapped[UUID] = mapped_column(ForeignKey("parties.id"))
# Use:
vendor_id: Mapped[UUID] = mapped_column(UUIDString())  # no FK
```

**Pro:** Simple. All tests pass immediately. Service wiring works as-is.

**Con:** Loses referential integrity. Orphaned records become possible.
Contradicts the plan's explicit design decision ("within-module: explicit
ForeignKey"). The database becomes weaker. Future data cleanup problems.
FKs exist for a reason in a financial system -- they prevent dangling
references to vendors/customers/accounts that don't exist.

**Risk level:** High (weakens data integrity in a financial system).

#### Option D: Guard `session.add()` with try/except, skip on failure

```python
try:
    self._session.add(orm_invoice)
    self._session.flush()
except IntegrityError:
    self._session.rollback()
    logger.warning("Could not persist ORM model, FK missing")
```

**Pro:** Tests don't break. Existing behavior preserved when FKs are missing.

**Con:** Worst of all worlds. You don't know what's persisted and what isn't.
Silent data loss in a financial system. The ORM layer becomes unreliable.
Debugging is a nightmare. Absolutely not acceptable.

**Risk level:** Critical (silent data loss in financial system).

#### Option E: Fix services to create parents first AND fix test fixtures (RECOMMENDED)

Two-part approach:
1. Services that create top-level entities (vendors, bank accounts, work orders)
   should have explicit "register/create" methods that persist the parent ORM
   entity.
2. Service methods that create child entities should require the parent to
   already exist (FK enforced).
3. Tests call the parent-creation methods first, mirroring real business flow.

```python
# Test mirrors real business workflow:
def test_record_invoice_posts(self, ap_service, vendor):
    # vendor fixture creates PartyModel via PartyService
    result = ap_service.record_invoice(vendor_id=vendor.id, ...)
    assert result.is_success
```

**Pro:** Most correct approach. Tests mirror real usage. FK constraints
validate correctness. Services have complete lifecycle methods. Business
objects are created through proper channels.

**Con:** Most work. Need to audit which services need "setup" methods and
which already have them. Some parent entities (Party, Account) are kernel
models that should be created via kernel services, not module services.

**Risk level:** Low (correct fix), effort: High.

### Recommendation

**Option A** for immediate fix (update test fixtures). **Option E** as the
full solution when services are complete. The FK constraints are correct and
should not be weakened. A financial system without referential integrity is
a financial system waiting to produce wrong numbers.

---

## ISSUE 3: Incomplete Service Wiring

### Problem

The plan says service wiring is "PENDING" for all modules. In reality, 15 of
17 module services have uncommitted `session.add()` calls, but 2 are
incomplete and the work is not committed.

### Per-module wiring status (uncommitted changes)

| Module | ORM Imports | session.add() Calls | Lines Added | Status |
|--------|------------|---------------------|-------------|--------|
| AP | 1 | 5 | +91 | Wired |
| AR | 1 | 7 | +121 | Wired |
| Assets | 1 | 6 | +94 | Wired |
| Budget | 1 | 2 | +19 | Wired |
| Cash | 1 | 9 | +132 | Wired |
| Contracts | 2 | 2 | +35 | Wired |
| Expense | 1 | 2 | +28 | Wired |
| GL | 1 | 2 | +15 | Wired |
| Intercompany | 1 | **0** | +2 | **Import only -- no wiring** |
| Inventory | 1 | 4 | +63 | Wired |
| Lease | 1 | 5 | +45 | Wired |
| Payroll | 1 | 2 | +26 | Wired |
| Procurement | 0 | **0** | +0 | **Not started** |
| Project | 1 | 2 | +22 | Wired |
| Revenue | 1 | 5 | +32 | Wired |
| Tax | 2 | 6 | +60 | Wired |
| WIP | 1 | 4 | +70 | Wired |
| Credit Loss | 0 | **0** | +0 | **Not started** (no orm.py either) |
| Reporting | 0 | 0 | +0 | N/A (read-only, excluded by design) |

**Gap:** Intercompany has the import but zero `session.add()` calls.
Procurement and Credit Loss have no changes at all (Credit Loss also has no
orm.py file since it was not in the original 18-module scope).

---

## ISSUE 4: No ORM Round-Trip Tests

### Problem

The plan calls for `tests/modules/test_{module}_orm.py` files to verify:
- `from_dto()` -> persist -> query -> `to_dto()` round-trip
- FK constraints work
- Indexes exist
- Status transitions work

Zero of these files exist.

---

## Summary: What Needs To Happen (Ordered)

| Priority | Task | Effort | Status |
|----------|------|--------|--------|
| **P0** | Fix architecture boundary (Issue 1) | Small | **DONE** -- `create_all_tables()` in `_orm_registry.py` |
| **P1a** | Fix FK placeholder values in services (Issue 2) | Medium | **DONE** -- 8 services, 19 methods fixed |
| **P1b** | Restructure test fixtures for explicit deps | Medium | **DONE** -- conftest rewritten, autouse removed |
| **P1c** | Update ~20 test files for new fixture pattern | Medium | **DONE** -- 20 files, 93 fixture additions |
| **P2** | Complete intercompany + procurement wiring + kernel guard | Small | **DONE** -- 6 methods wired, `create_tables()` guarded |
| **P3** | Write ORM round-trip tests (Issue 4) | Medium (17 test files) | **IN PROGRESS** |
| **P4** | Full regression gate | Small | Pending |
| **P5** | Commit all uncommitted work | Small | Pending |

---

### Actual ORM Model Counts (created)

| Module | File | Models | Lines |
|--------|------|--------|-------|
| AP | `finance_modules/ap/orm.py` | 8 | 707 |
| AR | `finance_modules/ar/orm.py` | 9 | 756 |
| Assets | `finance_modules/assets/orm.py` | 7 | 564 |
| Budget | `finance_modules/budget/orm.py` | 5 | 391 |
| Cash | `finance_modules/cash/orm.py` | 6 | 462 |
| Contracts | `finance_modules/contracts/orm.py` | 4 | 346 |
| Expense | `finance_modules/expense/orm.py` | 4 | 345 |
| GL | `finance_modules/gl/orm.py` | 7 | 546 |
| Intercompany | `finance_modules/intercompany/orm.py` | 3 | 272 |
| Inventory | `finance_modules/inventory/orm.py` | 7 | 586 |
| Lease | `finance_modules/lease/orm.py` | 5 | 477 |
| Payroll | `finance_modules/payroll/orm.py` | 9 | 736 |
| Procurement | `finance_modules/procurement/orm.py` | 3 | 310 |
| Project | `finance_modules/project/orm.py` | 3 | 283 |
| Revenue | `finance_modules/revenue/orm.py` | 6 | 606 |
| Tax | `finance_modules/tax/orm.py` | 10 | 733 |
| WIP | `finance_modules/wip/orm.py` | 8 | 755 |
| Period Close | `finance_services/orm.py` | 2 | 245 |
| **TOTAL** | **18 files** | **106** | **8,119** |

---

## Problem

All 19 `finance_modules/*/models.py` files define frozen dataclasses (147 total) that are NEVER persisted to the database. Only kernel journal entries survive process restart. Business objects (invoices, payments, assets, leases, employees, etc.) are ephemeral -- created during service calls and then lost.

## Solution

Create SQLAlchemy ORM models (`orm.py`) in each module directory, update service methods to persist them alongside journal entries in the same transaction.

## Sindri Integration Constraint

Sindri already has ORM models for: Company (vendors/customers), Item, Order (PO/SO/MO), Inventory (locations, transfers, transactions), BOM, MRP. We do NOT duplicate these. Finance modules reference Sindri entity IDs via `String(100)` columns (no FK constraint, since they may be in separate databases).

---

## Scope: 102 ORM Models Across 7 Phases

### What gets ORM models (97 + 2 profiles + 3 cross-cutting = 102)

Finance-only entities with no Sindri equivalent: invoices, payments, receipts, assets, depreciation, budgets, bank accounts, employees, timecards, leases, tax returns, work orders, etc.

### What does NOT get ORM models (~30 classes)

- **Reporting module** (15 DTOs) -- derived read-only
- **Computed results:** BudgetVariance, CashForecast, EVMSnapshot, ProductionCostSummary, TaxProvision, etc.
- **Entities already in kernel:** Account, FiscalPeriod
- **Entities owned by Sindri:** Item, Location, StockLevel, PurchaseOrder, PurchaseOrderLine

---

## Phase 0: Foundation Infrastructure -- DONE

**Created:**
- `finance_modules/_orm_registry.py` -- `import_all_orm_models()` function (imports all 18 orm.py files)

**Modified:**
- `finance_kernel/db/engine.py` -- calls `import_all_orm_models()` in `create_tables()` before `Base.metadata.create_all()`

**Pattern:** Every `orm.py` inherits from `TrackedBase`, uses `{module}_{entity}` table names, includes `to_dto()`/`from_dto()` conversion functions.

---

## Phase 1: Core Financial Cycle (AP, AR, Cash) -- 23 models -- ORM DONE, SERVICE WIRING PENDING

### Phase 1A: AP (`finance_modules/ap/orm.py`) -- 8 models

| Model | Table | Key Fields |
|-------|-------|------------|
| VendorProfileModel | `ap_vendor_profiles` | party_id FK, payment_terms, default_payment_method, is_1099_eligible |
| APInvoiceModel | `ap_invoices` | vendor_id, invoice_number (UQ), dates, amounts, status |
| APInvoiceLineModel | `ap_invoice_lines` | invoice_id FK, qty, price, GL code |
| APPaymentModel | `ap_payments` | vendor_id, method, amount, reference, status |
| APPaymentBatchModel | `ap_payment_batches` | date, method, total, status |
| APPaymentRunModel | `ap_payment_runs` | date, currency, status, totals |
| APPaymentRunLineModel | `ap_payment_run_lines` | run_id FK, invoice_id, amount |
| APVendorHoldModel | `ap_vendor_holds` | vendor_id, reason, status |

**Service changes:** `ap/service.py` -- add `session.add()` in `record_invoice`, `record_payment`, `create_payment_run`, `hold_vendor`, `release_vendor_hold`

### Phase 1B: AR (`finance_modules/ar/orm.py`) -- 9 models

CustomerProfileModel, ARInvoiceModel, ARInvoiceLineModel, ARReceiptModel, ARReceiptAllocationModel, ARCreditMemoModel, ARDunningHistoryModel, ARCreditDecisionModel, ARAutoApplyRuleModel

**Service changes:** `ar/service.py` -- add `session.add()` in all posting + domain object creation methods

### Phase 1C: Cash (`finance_modules/cash/orm.py`) -- 6 models

BankAccountModel, BankTransactionModel, ReconciliationModel, BankStatementModel, BankStatementLineModel, ReconciliationMatchModel

**Service changes:** `cash/service.py` -- add `session.add()` in all posting + domain object creation methods

---

## Phase 2: Asset-Intensive (Assets, Inventory, WIP) -- 19 models -- ORM DONE, SERVICE WIRING PENDING

### Phase 2A: Assets (`finance_modules/assets/orm.py`) -- 7 models

AssetCategoryModel, AssetModel, DepreciationScheduleModel, AssetDisposalModel, AssetTransferModel, AssetRevaluationModel, DepreciationComponentModel

### Phase 2B: Inventory (`finance_modules/inventory/orm.py`) -- 6 models

InventoryReceiptModel, InventoryIssueModel, InventoryAdjustmentModel, StockTransferModel, CycleCountModel, **StandardCostModel** (backs `ValuationService._standard_costs`)

*No Item/Location/StockLevel -- Sindri owns these*

### Phase 2C: WIP (`finance_modules/wip/orm.py`) -- 6 models

WorkOrderModel, WorkOrderLineModel, OperationModel, LaborEntryModel, OverheadApplicationModel, ByproductRecordModel

---

## Phase 3: Compliance-Heavy (Tax, Payroll, GL) -- 23 models -- ORM DONE, SERVICE WIRING PENDING

### Phase 3A: Tax (`finance_modules/tax/orm.py`) -- 8 models

TaxJurisdictionModel, TaxRateModel, TaxExemptionModel, TaxTransactionModel, TaxReturnModel, TemporaryDifferenceModel, DeferredTaxAssetModel, DeferredTaxLiabilityModel

### Phase 3B: Payroll (`finance_modules/payroll/orm.py`) -- 9 models

EmployeeModel, PayPeriodModel, TimecardModel, TimecardLineModel, PaycheckModel, PayrollRunModel, WithholdingResultModel, BenefitsDeductionModel, EmployerContributionModel

### Phase 3C: GL (`finance_modules/gl/orm.py`) -- 6 models

JournalBatchModel, RecurringEntryModel, AccountReconciliationModel, PeriodCloseTaskModel, TranslationResultModel, RevaluationResultModel

---

## Phase 4: Specialized (Revenue, Lease, Budget) -- 16 models -- ORM DONE, SERVICE WIRING PENDING

### Phase 4A: Revenue (`finance_modules/revenue/orm.py`) -- 6 models

RevenueContractModel, PerformanceObligationModel, TransactionPriceModel, SSPAllocationModel, RecognitionScheduleModel, ContractModificationModel

### Phase 4B: Lease (`finance_modules/lease/orm.py`) -- 5 models

LeaseModel, LeasePaymentModel, ROUAssetModel, LeaseLiabilityModel, LeaseModificationModel

### Phase 4C: Budget (`finance_modules/budget/orm.py`) -- 5 models

BudgetVersionModel, BudgetEntryModel, BudgetLockModel, EncumbranceModel, ForecastEntryModel

---

## Phase 5: Supporting (Expense, Project, Contracts, Intercompany, Procurement) -- 17 models -- ORM DONE, SERVICE WIRING PENDING

### Phase 5A: Expense (`finance_modules/expense/orm.py`) -- 4 models

ExpenseReportModel, ExpenseLineModel, CorporateCardModel, CardTransactionModel

### Phase 5B: Project (`finance_modules/project/orm.py`) -- 3 models

ProjectModel, WBSElementModel, MilestoneModel

### Phase 5C: Contracts (`finance_modules/contracts/orm.py`) -- 4 models

ContractModificationModel, SubcontractModel, AuditFindingModel, CostDisallowanceModel

### Phase 5D: Intercompany (`finance_modules/intercompany/orm.py`) -- 3 models

IntercompanyAgreementModel, ICTransactionModel, EliminationRuleModel

### Phase 5E: Procurement (`finance_modules/procurement/orm.py`) -- 3 models

RequisitionModel, RequisitionLineModel, ReceiptMatchModel

*No PurchaseOrder/PurchaseOrderLine/Receipt -- Sindri owns these*

---

## Phase 6: Cross-Cutting -- 2 models -- ORM DONE, SERVICE WIRING PENDING

### Phase 6A: Period Close (`finance_services/orm.py`)

PeriodCloseRunModel (`period_close_runs`), CloseCertificateModel (`close_certificates`)

---

## Service Update Pattern (all modules follow this)

```python
# BEFORE (current -- business object not persisted):
def record_invoice(self, invoice_id, vendor_id, amount, ...):
    payload = { ... }
    result = self._poster.post_event(...)
    if result.is_success:
        self._session.commit()
    return result

# AFTER (business object persisted atomically with journal entry):
def record_invoice(self, invoice_id, vendor_id, amount, ...):
    invoice_model = APInvoiceModel(id=invoice_id, vendor_id=vendor_id, ...)
    self._session.add(invoice_model)

    result = self._poster.post_event(...)
    if result.is_success:
        self._session.commit()  # both invoice AND journal entry committed
    else:
        self._session.rollback()  # rolls back both
    return result
```

Same SQLAlchemy session, same transaction, same commit/rollback. Zero risk of partial persistence.

---

## FK Strategy

- **Within-module:** Explicit ForeignKey (e.g., `ap_invoice_lines.invoice_id` -> `ap_invoices.id`)
- **To kernel:** ForeignKey to kernel tables (`parties.id`, `journal_entries.id`, `fiscal_periods.period_code`)
- **To Sindri:** `String(100)` columns, NO FK constraint (separate databases)

## Conventions (matching existing kernel patterns)

- Inherit from `TrackedBase` (UUID pk + audit columns)
- Table names: `{module}_{plural_entity}` (e.g., `ap_invoices`)
- Enums: `String(N)`, not native DB enums
- Money: `Numeric(38, 9)` via type_annotation_map
- UUIDs: `UUIDString()`
- Indexes: `idx_{table}_{column}`, Unique: `uq_{table}_{column}`
- Relationships: `lazy="selectin"`, `back_populates` for bidirectional
- String columns: always specify max length

## Verification (after each phase)

1. `python3 -m pytest tests/ -v --tb=short` -- zero regressions
2. New `tests/modules/test_{module}_orm.py` passes
3. `Base.metadata.create_all(engine)` discovers all new tables
4. Architecture boundary test still passes (kernel imports no module code)

---

## Decisions Made

- Keep frozen dataclasses as DTOs; ORM models are the persistence layer
- Each `orm.py` includes `to_dto()` / `from_dto()` conversion functions
- No Sindri entity duplication -- reference by ID only
- VendorProfile and CustomerProfile extend Party (not duplicate it)
- StandardCostModel backs the previously in-memory `_standard_costs` dict
- PeriodCloseRun gets its own ORM model for crash recovery
- Reporting module excluded entirely (derived, read-only)

---

## Detailed Model Specifications

### AP Models (Phase 1A)

#### VendorProfileModel (`ap_vendor_profiles`)
- `id` UUID PK (TrackedBase)
- `party_id` UUIDString FK -> parties.id, UNIQUE
- `payment_terms_days` Integer, default 30
- `default_payment_method` String(20), default "ach"
- `default_gl_account_code` String(50), nullable
- `is_1099_eligible` Boolean, default False
- Index: `idx_ap_vp_party` on party_id

#### APInvoiceModel (`ap_invoices`)
- `id` UUID PK (TrackedBase)
- `vendor_id` UUIDString FK -> parties.id
- `invoice_number` String(100), UNIQUE
- `invoice_date` Date
- `due_date` Date
- `currency` String(3)
- `subtotal` Numeric(38,9)
- `tax_amount` Numeric(38,9)
- `total_amount` Numeric(38,9)
- `status` String(30), default "draft"
- `po_id` UUIDString, nullable (reference to Sindri order)
- `match_variance` Numeric(38,9), nullable
- `approved_by_id` UUIDString, nullable
- `approved_at` Date, nullable
- Relationship: `lines` -> APInvoiceLineModel (cascade all, delete-orphan)
- Indexes: `idx_ap_inv_vendor`, `idx_ap_inv_status`, `idx_ap_inv_due_date`
- Unique: `uq_ap_inv_number`

#### APInvoiceLineModel (`ap_invoice_lines`)
- `id` UUID PK (TrackedBase)
- `invoice_id` UUIDString FK -> ap_invoices.id
- `line_number` Integer
- `description` String(500)
- `quantity` Numeric(38,9)
- `unit_price` Numeric(38,9)
- `amount` Numeric(38,9)
- `gl_account_code` String(50)
- `po_line_id` UUIDString, nullable (reference to Sindri order line)
- `receipt_line_id` UUIDString, nullable
- Unique: `uq_ap_invline_number` on (invoice_id, line_number)

#### APPaymentModel (`ap_payments`)
- `id` UUID PK (TrackedBase)
- `vendor_id` UUIDString FK -> parties.id
- `payment_date` Date
- `payment_method` String(20)
- `amount` Numeric(38,9)
- `currency` String(3)
- `reference` String(200)
- `status` String(20), default "draft"
- `discount_taken` Numeric(38,9), default 0
- `bank_account_id` UUIDString, nullable
- Indexes: `idx_ap_pmt_vendor`, `idx_ap_pmt_status`, `idx_ap_pmt_date`

#### APPaymentBatchModel (`ap_payment_batches`)
- `id` UUID PK (TrackedBase)
- `batch_date` Date
- `payment_method` String(20)
- `total_amount` Numeric(38,9)
- `status` String(20), default "draft"
- Many-to-many with payments via junction (or JSON payment_ids)

#### APPaymentRunModel (`ap_payment_runs`)
- `id` UUID PK (TrackedBase)
- `payment_date` Date
- `currency` String(3)
- `status` String(20), default "draft"
- `total_amount` Numeric(38,9), default 0
- `line_count` Integer, default 0
- `created_by` UUIDString, nullable
- `executed_by` UUIDString, nullable
- Relationship: `lines` -> APPaymentRunLineModel

#### APPaymentRunLineModel (`ap_payment_run_lines`)
- `id` UUID PK (TrackedBase)
- `run_id` UUIDString FK -> ap_payment_runs.id
- `invoice_id` UUIDString FK -> ap_invoices.id
- `vendor_id` UUIDString FK -> parties.id
- `amount` Numeric(38,9)
- `discount_amount` Numeric(38,9), default 0
- `payment_id` UUIDString, nullable

#### APVendorHoldModel (`ap_vendor_holds`)
- `id` UUID PK (TrackedBase)
- `vendor_id` UUIDString FK -> parties.id
- `reason` String(500)
- `hold_date` Date
- `held_by` UUIDString
- `status` String(20), default "active"
- `released_date` Date, nullable
- `released_by` UUIDString, nullable
- Indexes: `idx_ap_hold_vendor`, `idx_ap_hold_status`

### AR Models (Phase 1B)

#### CustomerProfileModel (`ar_customer_profiles`)
- `party_id` UUIDString FK -> parties.id, UNIQUE
- `credit_limit` Numeric(38,9), nullable
- `payment_terms_days` Integer, default 30
- `default_gl_account_code` String(50), nullable
- `tax_exempt` Boolean, default False
- `tax_id` String(50), nullable
- `dunning_level` Integer, default 0

#### ARInvoiceModel (`ar_invoices`)
- Same pattern as AP but with `customer_id`, `balance_due`, `sales_order_id`

#### ARInvoiceLineModel (`ar_invoice_lines`)
- Same pattern as AP but with `tax_code`, `tax_amount`

#### ARReceiptModel (`ar_receipts`)
- `customer_id`, `receipt_date`, `amount`, `currency`, `payment_method`, `reference`, `status`, `bank_account_id`, `unallocated_amount`

#### ARReceiptAllocationModel (`ar_receipt_allocations`)
- `receipt_id` FK, `invoice_id` FK, `amount`, `discount_taken`

#### ARCreditMemoModel (`ar_credit_memos`)
- `customer_id`, `credit_memo_number` (UQ), `issue_date`, `amount`, `currency`, `reason`, `status`, `original_invoice_id`, `applied_to_invoice_id`

#### ARDunningHistoryModel (`ar_dunning_history`)
- `customer_id`, `level`, `sent_date`, `as_of_date`, `total_overdue`, `invoice_count`, `currency`, `notes`

#### ARCreditDecisionModel (`ar_credit_decisions`)
- `customer_id`, `decision_date`, `previous_limit`, `new_limit`, `order_amount`, `approved`, `reason`, `decided_by`

#### ARAutoApplyRuleModel (`ar_auto_apply_rules`)
- `name` (UQ), `priority`, `match_field`, `tolerance`, `is_active`

### Cash Models (Phase 1C)

#### BankAccountModel (`cash_bank_accounts`)
- `code` (UQ), `name`, `institution`, `account_number_masked`, `currency`, `gl_account_code`, `is_active`

#### BankTransactionModel (`cash_bank_transactions`)
- `bank_account_id` FK, `transaction_date`, `amount`, `transaction_type`, `reference`, `description`, `external_id`, `reconciled`, `matched_journal_line_id`

#### ReconciliationModel (`cash_reconciliations`)
- `bank_account_id` FK, `statement_date`, `statement_balance`, `book_balance`, `adjusted_book_balance`, `variance`, `status`, `completed_by_id`, `completed_at`

#### BankStatementModel (`cash_bank_statements`)
- `bank_account_id` FK, `statement_date`, `opening_balance`, `closing_balance`, `line_count`, `format`, `currency`

#### BankStatementLineModel (`cash_bank_statement_lines`)
- `statement_id` FK, `transaction_date`, `amount`, `reference`, `description`, `transaction_type`

#### ReconciliationMatchModel (`cash_reconciliation_matches`)
- `statement_line_id` FK, `journal_line_id` FK (nullable), `match_confidence`, `match_method`

### Remaining Phases

Phases 2-6 follow the same model specification pattern. Each ORM model maps 1:1 to its corresponding frozen dataclass, with:
- UUID PK from TrackedBase
- All dataclass fields as mapped columns
- Appropriate indexes and constraints
- Parent-child relationships via FK + `relationship()`
- Enums stored as String(N) values
- Sindri references as String(100) without FK
