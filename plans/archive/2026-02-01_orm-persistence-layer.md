# Module ORM Persistence Layer Implementation

**Date:** 2026-01-31
**Last Update:** 2026-02-01
**Status:** COMPLETE -- ready to commit
**Reference plan:** `plans/MODULE_ORM_PERSISTENCE_PLAN.md`
**Baseline:** 3,614+ tests passed before ORM work began
**Current:** 4,052 pass / 0 fail / 0 errors

---

## Objective

Persist all module-level business objects (invoices, payments, assets, leases,
employees, etc.) to the database via SQLAlchemy ORM models. Currently, only
kernel journal entries survive process restart -- the 147 frozen dataclasses
across 19 modules are ephemeral in-memory objects.

---

## Completed Phases

### Phase 0: Architecture Boundary Fix -- DONE

- Created `create_all_tables()` in `finance_modules/_orm_registry.py`
- Removed illegal `import finance_modules` from `finance_kernel/db/engine.py`
- Added `kernel_only` guard parameter to `create_tables()`
- Updated `tests/conftest.py` to use production orchestration path
- Removed unused `create_tables` import from `tests/conftest.py`
- All 14 architecture tests pass

### Phase 1: ORM Model Files -- DONE (106 models, 8,119 lines)

All 18 `orm.py` files created:

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

### Phase 2: Service Wiring -- DONE (17 modules, 75+ session.add() calls)

All 17 module services wired with `session.add()` calls to persist ORM
models atomically with journal entries.

| Module | Service File | session.add() Calls | Status |
|--------|-------------|---------------------|--------|
| AP | `ap/service.py` | 5 | WIRED |
| AR | `ar/service.py` | 7 | WIRED |
| Cash | `cash/service.py` | 9 | WIRED |
| Assets | `assets/service.py` | 6 | WIRED |
| Inventory | `inventory/service.py` | 4 | WIRED |
| WIP | `wip/service.py` | 4 | WIRED |
| Tax | `tax/service.py` | 6 | WIRED |
| Payroll | `payroll/service.py` | 2 | WIRED |
| GL | `gl/service.py` | 2 | WIRED |
| Revenue | `revenue/service.py` | 5 | WIRED |
| Lease | `lease/service.py` | 5 | WIRED |
| Budget | `budget/service.py` | 2 | WIRED |
| Expense | `expense/service.py` | 2 | WIRED |
| Project | `project/service.py` | 2 | WIRED |
| Contracts | `contracts/service.py` | 2 | WIRED |
| Intercompany | `intercompany/service.py` | 3 | WIRED |
| Procurement | `procurement/service.py` | 3 | WIRED |
| Reporting | `reporting/service.py` | 0 | N/A (read-only) |

### Phase 2a: FK Parameter Fixes -- DONE (8 services, 19 methods)

Service methods updated to require real parent entity IDs instead of
placeholder UUIDs.

### Phase 2b: Test Fixture Restructuring -- DONE

- Removed autouse from `_module_parent_entities`
- Split into individual fixtures: `test_vendor_party`, `test_customer_party`,
  `test_employee_party`, `test_lessee_party`, `test_bank_account`,
  `test_asset_category`, `test_asset`, `test_tax_jurisdiction`,
  `test_work_order`, `test_operation`, `test_budget_version`,
  `test_pay_period`, `test_payroll_employee`, `test_revenue_contract`,
  `test_lease`, `test_contract`, `test_expense_report`, `test_project`,
  `test_ic_agreement`, `test_gov_contract`
- 18 deterministic UUIDs for test imports
- 30+ test files updated with explicit fixture dependencies

### Phase 2c: ORM Model / Fixture Alignment -- DONE

Fixed 5 field name mismatches between ORM models and test fixtures:
- `AssetCategoryModel`: `useful_life_months` -> `useful_life_years`, GL account names
- `BudgetVersionModel` -> `BudgetModel`: fixture was creating wrong entity type
- `PayPeriodModel`: `period_code`/`status` -> `period_number`/`year`/`pay_frequency`
- `TaxJurisdictionModel`: removed non-existent `country` field
- `WorkOrderModel`: removed non-existent `priority` field
- `AssetModel`: `asset_tag` -> `asset_number`, `residual_value` -> `salvage_value`
- `EmployeeModel`: fixed `party_id`/`status`/`pay_rate`/`department` column names

### Phase 3: ORM Round-Trip Tests -- DONE (18 files, 423 tests)

All 18 `test_{module}_orm.py` files created and passing:
- `from_dto()` -> persist -> query -> `to_dto()` round-trip verification
- FK constraint enforcement tests
- Index existence tests
- Unique constraint tests
- Status transition tests

### Phase 4: Full Regression Gate -- DONE

**4,052 tests pass, 0 failures, 0 errors** (up from 3,614 baseline).

---

## Decisions Made

1. **TrackedBase inheritance**: All module ORM models inherit from `TrackedBase` (UUID PK + audit timestamps)
2. **FK strategy**: Within-module explicit FK, to kernel FK, to Sindri `String(100)` with NO FK
3. **Enum storage**: `String(50)` columns storing `.value`, not native DB enums
4. **Money precision**: `Numeric(38, 9)` via `type_annotation_map` (inherited from `Base`)
5. **Conversion methods**: `to_dto()` instance method + `from_dto()` classmethod on every model
6. **Table naming**: `{module}_{plural_entity}` (e.g., `ap_invoices`, `cash_bank_accounts`)
7. **Sindri deconfliction**: No ORM models for Item, Location, StockLevel, PurchaseOrder, Company (Sindri owns these)
8. **Reporting module**: Excluded from ORM (read-only derived data, no persistence needed)
9. **Registry pattern**: `_orm_registry.py` with `create_all_tables()` as production entrypoint
10. **Explicit test fixtures**: No autouse; every test declares parent entities it depends on
11. **Architecture boundary**: `create_all_tables()` lives in `finance_modules/_orm_registry.py`, not kernel
12. **Kernel guard**: `create_tables(kernel_only=False)` rejects incomplete schema unless `kernel_only=True`

## Key Files Created/Modified

**New files (37):**
- `finance_modules/*/orm.py` (17 files) -- ORM models for each module
- `finance_services/orm.py` -- Period close ORM models
- `finance_modules/_orm_registry.py` -- Registry + `create_all_tables()`
- `tests/modules/test_*_orm.py` (18 files) -- ORM round-trip tests

**Modified files (30+):**
- `finance_kernel/db/engine.py` -- kernel guard, removed illegal import
- `finance_modules/*/service.py` (17 files) -- session.add() wiring
- `tests/conftest.py` -- production orchestration path
- `tests/modules/conftest.py` -- parent entity fixtures
- `tests/modules/test_*_service.py` (20+ files) -- fixture dependencies

## Next Steps

- **Phase 5:** Commit all uncommitted work in correct order
- Archive this plan to `plans/archive/`
