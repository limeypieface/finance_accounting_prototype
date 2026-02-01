# Plan: Convert ~375 Fake/Mock Tests to Real Integration Tests

**Created:** 2026-01-29
**Branch:** feature/economic-link-primitive
**Status:** IN PROGRESS

## Summary

Convert 21 test files (~375 tests) from fake/mock patterns to real integration tests that exercise the actual architecture end-to-end. **No new production code needed** — all 12 module services (56 public methods), all engines, and all fixtures exist. This is purely test-side wiring.

---

## Categories of Fake Tests

### Category 1: Skipped Spec Tests (8 files, ~180 tests)
Files with `pytestmark = pytest.mark.skip()` that define inline mock domain models. Honest about being fake (they're skipped), but need to be made real.

| File | Tests | Inline Mock Classes |
|------|-------|---------------------|
| `tests/modules/test_asset_depreciation.py` | 23 | DepreciationCalculator, Asset, DisposalResult |
| `tests/modules/test_payment_terms.py` | 26 | PaymentTermCalculator, PaymentAllocator, DiscountCalculator |
| `tests/modules/test_invoice_status.py` | 27 | InvoiceStatusManager, StatusTracker |
| `tests/modules/test_cost_center.py` | 23 | CostCenterService, GLEntryGenerator |
| `tests/modules/test_landed_cost.py` | 15 | LandedCostAllocator, LandedCostGLGenerator |
| `tests/modules/test_returns.py` | 28 | ReturnGLGenerator, ReturnTracker |
| `tests/modules/test_intercompany.py` | 14 | IntercompanyService |
| `tests/multicurrency/test_fx_gain_loss.py` | 24 | FXGainLossCalculator |

### Category 2: Source-Grep Tests (12 files, ~190 tests) — HIGHEST PRIORITY
Tests that use `inspect.getsource()` to check that class name strings exist in source code. They **actively pass** and give false confidence. Never instantiate or call anything.

| File | Tests | What It Checks (string grep) |
|------|-------|------------------------------|
| `tests/modules/test_ap_service.py` | 12 | "AllocationEngine" in source |
| `tests/modules/test_ar_service.py` | 10 | "ReconciliationManager" in source |
| `tests/modules/test_assets_service.py` | 10 | "VarianceCalculator" in source |
| `tests/modules/test_cash_service.py` | 9 | "MatchingEngine" in source |
| `tests/modules/test_contracts_service.py` | 10 | "BillingEngine" in source |
| `tests/modules/test_expense_service.py` | 9 | "AllocationEngine" in source |
| `tests/modules/test_gl_service.py` | 9 | "ModulePostingService" in source |
| `tests/modules/test_payroll_service.py` | 9 | "AllocationCascade" in source |
| `tests/modules/test_procurement_service.py` | 9 | "MatchingEngine" in source |
| `tests/modules/test_tax_service.py` | 8 | "TaxCalculator" in source |
| `tests/modules/test_wip_service.py` | 11 | "ValuationLayer" in source |
| `tests/modules/test_cross_module_flow.py` | 84 | Cross-module string checks |

### Category 3: unittest.mock (1 file, 5 tests) — NO CHANGE NEEDED
`tests/crash/test_fault_injection.py` — Uses `@patch` to inject faults at specific code paths. This is a legitimate pattern for crash testing. No conversion needed.

---

## Step 1: Extend conftest.py Role Bindings

**File:** `tests/conftest.py`

The `module_accounts` and `module_role_resolver` fixtures must be extended to include all roles required by all 12 module profiles.

**New roles needed (by module):**

| Module | Roles to Add |
|--------|-------------|
| Assets | FIXED_ASSET, DEPRECIATION_EXPENSE, ACCUMULATED_DEPRECIATION, GAIN_ON_DISPOSAL, LOSS_ON_DISPOSAL, IMPAIRMENT_LOSS, CIP |
| Cash | BANK, BANK_FEE_EXPENSE, INTEREST_INCOME, RECON_VARIANCE, CASH_IN_TRANSIT |
| Payroll | SALARY_EXPENSE, WAGE_EXPENSE, PAYROLL_TAX_EXPENSE, FEDERAL_TAX_PAYABLE, STATE_TAX_PAYABLE, FICA_PAYABLE, BENEFITS_PAYABLE, ACCRUED_PAYROLL, LABOR_CLEARING, OVERHEAD_POOL |
| Contracts | WIP_DIRECT_LABOR, CONTRACT_COST_INCURRED, COST_CLEARING, UNBILLED_AR, DEFERRED_FEE_REVENUE, FEE_REVENUE_EARNED, OBLIGATION_CONTROL, etc. |
| Expense | EXPENSE (if not present) |
| Tax | TAX_EXPENSE, TAX_PAYABLE |
| GL | RETAINED_EARNINGS, INCOME_SUMMARY, INTERCOMPANY_RECEIVABLE, INTERCOMPANY_PAYABLE, DIVIDENDS_PAYABLE |
| Procurement | (uses existing INVENTORY, GRNI, PPV, ACCOUNTS_PAYABLE) |

---

## Step 2: Convert Category 2 — Source-Grep Tests (12 files)

**Conversion pattern for every file:**
1. Delete all `inspect.getsource()` calls and `assert "ClassName" in source` assertions
2. Keep structural tests (constructor signature checks) as lightweight sanity checks
3. Add a service fixture using the gold-standard pattern
4. Add a `TestXServiceIntegration` class with one real test per public method
5. Assert on real `ModulePostingResult` objects

**Execution order:**

| # | File | Service | Methods to Test |
|---|------|---------|-----------------|
| 1 | `test_gl_service.py` | GeneralLedgerService | record_journal_entry, record_adjustment, record_closing_entry, record_intercompany_transfer, record_dividend_declared, compute_budget_variance |
| 2 | `test_ap_service.py` | APService | record_invoice, record_payment, match_invoice_to_po, calculate_aging |
| 3 | `test_ar_service.py` | ARService | record_invoice, record_payment, apply_payment, calculate_aging |
| 4 | `test_cash_service.py` | CashService | record_receipt, record_disbursement, reconcile_bank_statement |
| 5 | `test_assets_service.py` | FixedAssetService | record_asset_acquisition, record_depreciation, record_disposal, record_impairment, record_scrap |
| 6 | `test_expense_service.py` | ExpenseService | record_expense, record_expense_report, allocate_expense, record_reimbursement, record_card_statement |
| 7 | `test_procurement_service.py` | ProcurementService | create_purchase_order, receive_goods, record_price_variance |
| 8 | `test_payroll_service.py` | PayrollService | record_payroll_run, record_payroll_tax, allocate_labor_costs, run_dcaa_cascade, compute_payroll_variance |
| 9 | `test_wip_service.py` | WipService | record_material_issue, record_labor_charge, record_overhead_allocation, complete_job, variance methods |
| 10 | `test_tax_service.py` | TaxService | record_tax_obligation, record_tax_payment, calculate_tax |
| 11 | `test_contracts_service.py` | GovernmentContractsService | record_cost_incurrence, generate_billing, record_funding_action, etc. |
| 12 | `test_cross_module_flow.py` | All 12 | Replace 36 source-grep tests with 12 parameterized real-posting tests |

**Gold-standard fixture pattern:**
```python
@pytest.fixture
def ap_service(session, module_role_resolver, deterministic_clock, register_modules):
    from finance_modules.ap.service import APService
    return APService(session=session, role_resolver=module_role_resolver, clock=deterministic_clock)
```

**Gold-standard test pattern:**
```python
class TestAPServiceIntegration:
    def test_record_invoice_posts_journal(self, ap_service, current_period, test_actor_id, deterministic_clock):
        result = ap_service.record_invoice(
            invoice_id=uuid4(), vendor_id=uuid4(), amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(), actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0
```

**For tuple-returning methods (engine_result, posting_result):**
```python
def test_record_labor_variance(self, wip_service, current_period, test_actor_id, deterministic_clock):
    variance_result, posting_result = wip_service.record_labor_variance(
        job_id="JOB-100", standard_hours=Decimal("40"), actual_hours=Decimal("45"),
        standard_rate=Decimal("50.00"), effective_date=deterministic_clock.now().date(),
        actor_id=test_actor_id,
    )
    assert posting_result.is_success
```

**For `test_cross_module_flow.py`:**
- Keep `TestAllServicesExist` and `TestUniformConstructorSignature`
- Delete `TestR7TransactionBoundary`, `TestNoDirectORMJournalWrites`, `TestEngineImportPatterns`
- Add `TestAllServicesPostSuccessfully` — 12 parameterized tests, one per module

---

## Step 3: Convert Category 1 — Skipped Spec Tests (8 files)

Remove skip markers, delete inline mock classes, wire to real services.

| # | File | Real Target | Approach |
|---|------|-------------|----------|
| 13 | `test_intercompany.py` | `GeneralLedgerService.record_intercompany_transfer()` | Direct mapping |
| 14 | `test_cost_center.py` | GL/expense posting with cost_center dimension | Post with cost_center in payload |
| 15 | `test_landed_cost.py` | `InventoryService.receive_inventory()` + `adjust_inventory()` | Receive then adjust |
| 16 | `test_asset_depreciation.py` | `FixedAssetService` methods | Service posting + engine math |
| 17 | `test_returns.py` | AP/AR services for debit/credit notes | Invoice then return |
| 18 | `test_invoice_status.py` | AP/AR invoice + payment lifecycle | Multi-step lifecycle |
| 19 | `test_payment_terms.py` | AP/AR payment with discount; AllocationEngine | Service + engine |
| 20 | `test_fx_gain_loss.py` | AP/AR with currency parameter | Foreign currency posting |

---

## Step 4: Verify Category 3 — Fault Injection (1 file)

**File:** `tests/crash/test_fault_injection.py`

No conversion needed — `@patch` for crash simulation is legitimate. Just verify tests pass.

---

## Verification Checklist

After each file conversion:
- [ ] No `inspect.getsource()` calls remain
- [ ] No `pytestmark = pytest.mark.skip()` remains
- [ ] No inline mock domain classes remain
- [ ] Every test calls a real service method or engine
- [ ] Every posting test asserts `result.is_success`
- [ ] `pytest tests/modules/test_X.py -v` passes

Final:
- [ ] `python3 -m pytest tests/ -x --timeout=60` — full suite passes

---

## Reference: All 12 Module Services (Fully Implemented)

| Module | Service Class | Constructor | Public Methods |
|--------|--------------|-------------|----------------|
| AP | APService | session, role_resolver, clock | record_invoice, record_payment, match_invoice_to_po, calculate_aging |
| AR | ARService | session, role_resolver, clock | record_invoice, record_payment, apply_payment, calculate_aging |
| Cash | CashService | session, role_resolver, clock | record_receipt, record_disbursement, reconcile_bank_statement |
| Procurement | ProcurementService | session, role_resolver, clock | create_purchase_order, receive_goods, record_price_variance |
| WIP | WipService | session, role_resolver, clock | record_material_issue, record_labor_charge, record_overhead_allocation, complete_job, 3 variance methods |
| Payroll | PayrollService | session, role_resolver, clock | record_payroll_run, record_payroll_tax, allocate_labor_costs, run_dcaa_cascade, compute_payroll_variance |
| Contracts | GovernmentContractsService | session, role_resolver, clock | record_cost_incurrence, generate_billing, record_funding_action, record_indirect_allocation, record_rate_adjustment, run_allocation_cascade, compile_ice |
| Tax | TaxService | session, role_resolver, clock | record_tax_obligation, record_tax_payment, calculate_tax |
| Assets | FixedAssetService | session, role_resolver, clock | record_asset_acquisition, record_depreciation, record_disposal, record_impairment, record_scrap |
| GL | GeneralLedgerService | session, role_resolver, clock | record_journal_entry, record_adjustment, record_closing_entry, compute_budget_variance, record_intercompany_transfer, record_dividend_declared |
| Expense | ExpenseService | session, role_resolver, clock | record_expense, record_expense_report, allocate_expense, record_reimbursement, record_card_statement |
| Inventory | InventoryService | session, role_resolver, clock | receive_inventory, receive_with_variance, issue_sale, issue_production, issue_scrap, adjust_inventory, revalue_inventory, etc. |
