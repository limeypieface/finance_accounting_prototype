# Module ORM Persistence Layer Implementation

**Date:** 2026-01-31
**Status:** IN-PROGRESS (Phase 1 ORM creation DONE, Phase 2 service wiring PENDING)
**Reference plan:** `plans/MODULE_ORM_PERSISTENCE_PLAN.md`
**Baseline:** 3,614+ tests passed before changes

---

## Objective

Persist all module-level business objects (invoices, payments, assets, leases,
employees, etc.) to the database via SQLAlchemy ORM models. Currently, only
kernel journal entries survive process restart -- the 147 frozen dataclasses
across 19 modules are ephemeral in-memory objects.

---

## What Was Done This Session

### Phase 0: Foundation Infrastructure -- DONE

| File | Action | Status |
|------|--------|--------|
| `finance_modules/_orm_registry.py` | Created `import_all_orm_models()` function | DONE |
| `finance_kernel/db/engine.py` | Added `import_all_orm_models()` call in `create_tables()` | DONE |

### Phase 1: ORM Model Files -- DONE (106 models, 8,119 lines)

All 18 `orm.py` files created by parallel agents:

| Module | File | Models | Lines | Status |
|--------|------|--------|-------|--------|
| AP | `finance_modules/ap/orm.py` | 8 | 707 | DONE |
| AR | `finance_modules/ar/orm.py` | 9 | 756 | DONE |
| Assets | `finance_modules/assets/orm.py` | 7 | 564 | DONE |
| Budget | `finance_modules/budget/orm.py` | 5 | 391 | DONE |
| Cash | `finance_modules/cash/orm.py` | 6 | 462 | DONE |
| Contracts | `finance_modules/contracts/orm.py` | 4 | 346 | DONE |
| Expense | `finance_modules/expense/orm.py` | 4 | 345 | DONE |
| GL | `finance_modules/gl/orm.py` | 7 | 546 | DONE |
| Intercompany | `finance_modules/intercompany/orm.py` | 3 | 272 | DONE |
| Inventory | `finance_modules/inventory/orm.py` | 7 | 586 | DONE |
| Lease | `finance_modules/lease/orm.py` | 5 | 477 | DONE |
| Payroll | `finance_modules/payroll/orm.py` | 9 | 736 | DONE |
| Procurement | `finance_modules/procurement/orm.py` | 3 | 310 | DONE |
| Project | `finance_modules/project/orm.py` | 3 | 283 | DONE |
| Revenue | `finance_modules/revenue/orm.py` | 6 | 606 | DONE |
| Tax | `finance_modules/tax/orm.py` | 10 | 733 | DONE |
| WIP | `finance_modules/wip/orm.py` | 8 | 755 | DONE |
| Period Close | `finance_services/orm.py` | 2 | 245 | DONE |
| **TOTAL** | **18 files** | **106** | **8,119** | **DONE** |

---

## What Still Needs To Be Done

### Phase 2: Validation & Fixes -- PENDING (do this FIRST next session)

1. **Run test suite** to see if ORM creation caused any regressions:
   ```bash
   python3 -m pytest tests/ -v --tb=short
   ```
2. **Fix any import errors** in orm.py files (agents may have guessed field names)
3. **Verify all orm.py files import cleanly**:
   ```bash
   python3 -c "from finance_modules._orm_registry import import_all_orm_models; import_all_orm_models(); print('OK')"
   ```
4. **Verify table discovery**:
   ```python
   from finance_kernel.db.base import Base
   from finance_modules._orm_registry import import_all_orm_models
   import_all_orm_models()
   print(f"Tables: {len(Base.metadata.tables)}")
   for t in sorted(Base.metadata.tables): print(f"  {t}")
   ```

### Phase 3: Service Wiring -- PENDING (bulk of remaining work)

For each module service, add `session.add(model)` calls so business objects
are persisted atomically alongside journal entries. The pattern:

```python
# In each service method that creates a business object:
orm_model = APInvoiceModel.from_dto(invoice_dto, created_by_id=actor_id)
self._session.add(orm_model)
# ... existing post_event() call ...
# Commit happens at end of transaction (both ORM + journal entry)
```

Services to update (19 total):

| Module | Service File | Methods to Update | Status |
|--------|-------------|-------------------|--------|
| AP | `finance_modules/ap/service.py` | record_invoice, record_payment, create_payment_run, hold_vendor, release_hold | PENDING |
| AR | `finance_modules/ar/service.py` | create_invoice, record_receipt, apply_credit_memo, create_dunning | PENDING |
| Cash | `finance_modules/cash/service.py` | create_bank_account, record_transaction, start_reconciliation | PENDING |
| Assets | `finance_modules/assets/service.py` | acquire_asset, record_depreciation, dispose_asset, transfer_asset | PENDING |
| Inventory | `finance_modules/inventory/service.py` | receive_inventory, issue_inventory, adjust_inventory, transfer_stock | PENDING |
| WIP | `finance_modules/wip/service.py` | create_work_order, record_labor, apply_overhead | PENDING |
| Tax | `finance_modules/tax/service.py` | record_tax_payment, file_return, create_exemption | PENDING |
| Payroll | `finance_modules/payroll/service.py` | create_employee, submit_timecard, run_payroll | PENDING |
| GL | `finance_modules/gl/service.py` | create_recurring_entry, post_adjustment | PENDING |
| Revenue | `finance_modules/revenue/service.py` | create_contract, recognize_revenue | PENDING |
| Lease | `finance_modules/lease/service.py` | create_lease, modify_lease | PENDING |
| Budget | `finance_modules/budget/service.py` | create_budget, approve_budget, transfer_budget | PENDING |
| Expense | `finance_modules/expense/service.py` | submit_report, approve_report, reimburse | PENDING |
| Project | `finance_modules/project/service.py` | create_project, record_cost | PENDING |
| Contracts | `finance_modules/contracts/service.py` | create_deliverable, record_billing | PENDING |
| Intercompany | `finance_modules/intercompany/service.py` | create_transaction, settle | PENDING |
| Procurement | `finance_modules/procurement/service.py` | create_requisition, receive | PENDING |
| Reporting | `finance_modules/reporting/service.py` | (read-only, no persistence needed) | N/A |
| Period Close | `finance_services/period_close_orchestrator.py` | execute_close | PENDING |

### Phase 4: ORM Tests -- PENDING

Create `tests/modules/test_{module}_orm.py` for each module to verify:
- Round-trip: `from_dto()` -> persist -> query -> `to_dto()` == original
- FK constraints work
- Indexes exist
- Status transitions work

### Phase 5: Final Regression -- PENDING

Run full test suite and confirm zero regressions.

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
9. **Registry pattern**: `_orm_registry.py` with `import_all_orm_models()` called by `create_tables()`

## Key Files Created/Modified

- `finance_modules/_orm_registry.py` -- NEW: imports all orm.py modules
- `finance_kernel/db/engine.py` -- MODIFIED: calls `import_all_orm_models()` in `create_tables()`
- `finance_modules/*/orm.py` (17 files) -- NEW: ORM models for each module
- `finance_services/orm.py` -- NEW: Period close ORM models

## Resume Instructions

1. Read this file first
2. Run: `python3 -c "from finance_modules._orm_registry import import_all_orm_models; import_all_orm_models(); print('OK')"` to verify imports
3. Run: `python3 -m pytest tests/ -v --tb=short` to check for regressions
4. Fix any import errors in orm.py files (field name mismatches with models.py)
5. Begin Phase 3 (service wiring) starting with AP as the reference implementation
6. Reference `plans/MODULE_ORM_PERSISTENCE_PLAN.md` for detailed model specs
