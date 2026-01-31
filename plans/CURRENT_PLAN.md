# Plan: Deepen Expense, Inventory, Procurement Modules

**Date:** 2026-01-30
**Status:** COMPLETE
**Test baseline:** 3252 passed, 0 failed, 11 skipped, 13 xfailed, 10 xpassed

---

## Objective

Deepen 3 existing modules to production-grade per MODULE_BUILD_PLAN.md (M-16, M-10, M-15).
Execution order: Expense -> Inventory -> Procurement

---

## Phase 1: Expense Module — COMPLETE

- Created `finance_modules/expense/helpers.py` (3 pure functions: calculate_mileage, calculate_per_diem, validate_expense_against_policy)
- Added 4 models to `models.py`: ExpensePolicy, PolicyViolation, MileageRate, PerDiemRate
- Added 6 methods to `service.py`: validate_against_policy, import_card_transactions, calculate_mileage, calculate_per_diem, record_policy_violation, record_receipt_match
- Added 1 profile: ExpenseReceiptMatched (Dr EXPENSE / Cr CORPORATE_CARD_LIABILITY)
- Added YAML policy, updated exports
- 37 tests pass (19 helper + 18 service)

## Phase 2: Inventory Module — COMPLETE

- Created `finance_modules/inventory/helpers.py` (3 pure functions: classify_abc, calculate_reorder_point, calculate_eoq)
- Added 4 models: CycleCount, ABCClassification, ReorderPoint, ItemValue
- Added 5 methods: record_cycle_count, classify_abc, calculate_reorder_point, record_inter_warehouse_transfer, record_shelf_life_write_off
- Added 5 profiles with GL + INVENTORY subledger effects: CycleCountPositive, CycleCountNegative, WarehouseTransferOut, WarehouseTransferIn, ExpiredWriteOff
- Added 5 YAML policies, updated exports, added conftest role bindings
- 37 tests pass (16 helper + 21 integration)

**Key fix:** ABC classification boundary bug — changed to `prev_cumulative < threshold` to correctly classify the first item.

## Phase 3: Procurement Module — COMPLETE

- Added 3 models: PurchaseOrderVersion, ReceiptMatch, SupplierScore
- Added 6 methods: create_requisition, convert_requisition_to_po, amend_purchase_order, match_receipt_to_po, evaluate_supplier, record_quantity_variance
- Added 4 registered profiles: RequisitionCreated, POAmended, ReceiptMatched (GL + AP subledger), QuantityVariance
- Added YAML policies, QUANTITY_VARIANCE account role, updated exports
- Added GL role bindings for INVENTORY_IN_TRANSIT and QUANTITY_VARIANCE to chart_of_accounts.yaml
- 14 tests pass

**Key decisions:**
- RequisitionConverted uses two-step posting (commitment_relieved + po_encumbered) to avoid dual-GL-effect issue
- ReceiptMatched uses AP subledger (INVOICE/SUPPLIER_BALANCE) for 3-way match
- DERIVED_FROM link type for requisition-to-PO tracking (SOURCED_FROM too restrictive)

## Phase 4: Full Regression — COMPLETE

3252 passed, 0 failed (up from 3214 baseline = +38 net new tests)

---

## Summary

| Module | New Methods | New Models | New Profiles | New Tests |
|--------|-----------|-----------|-------------|----------|
| Expense | 6 | 4 | 1 | +26 |
| Inventory | 5 | 4 | 5 | +21 |
| Procurement | 6 | 3 | 4 | +7 |
| **Total** | **17** | **11** | **10** | **+54** |

New files: `expense/helpers.py`, `inventory/helpers.py`, `test_expense_helpers.py`, `test_inventory_helpers.py`
