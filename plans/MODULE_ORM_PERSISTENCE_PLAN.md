# Module ORM Persistence Layer -- Implementation Plan

**Date:** 2026-01-31
**Status:** IN-PROGRESS
**Objective:** Wire all module domain objects to PostgreSQL via SQLAlchemy ORM models

### Progress Summary

| Step | Description | Status |
|------|-------------|--------|
| Phase 0 | Foundation infrastructure (`_orm_registry.py`, `engine.py` hook) | DONE |
| Phase 1-6 ORM | Create all 18 `orm.py` files (106 models, 8,119 lines) | DONE |
| Validation | Import verification + test suite regression check | PENDING (next session) |
| Service Wiring | Add `session.add()` calls in all 18 module services | PENDING |
| ORM Tests | Round-trip persistence tests per module | PENDING |
| Final Regression | Full test suite pass | PENDING |

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
