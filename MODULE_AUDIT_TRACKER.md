# Module Audit Tracker

Systematic audit of all finance modules for correctness, completeness, and test coverage.

## AUDIT COMPLETE — All 12 Modules Audited

| Module | Status | Issues Found | Issues Fixed | Tests Added |
|--------|--------|-------------|--------------|-------------|
| AR | DONE | from_context bugs, missing methods | All | — |
| AP | DONE | UNBALANCED_INTENT, missing role | All | — |
| Assets | DONE | 3 unbalanced profiles, 1 orphaned, duplicate LedgerEffects | All | +1 |
| Cash | DONE | 8 from_context bugs, 5 orphaned profiles | All | +5 |
| Procurement | DONE | 2 orphaned profiles, chart role mismatch | All | +2 |
| Inventory | DONE | None (clean) | N/A | — |
| WIP | DONE | 2 orphaned, 1 unbalanced, wrong ArtifactType | All | +2 |
| Payroll | DONE | Accrual unbalanced, scope unreachable, 5 orphaned | All | +5 |
| Expense | DONE | 3 orphaned, 4 missing chart roles, payload bug | All | +3 |
| Tax | DONE | VAT settlement unbalanced, missing method, 4 missing chart roles | All | +4 |
| Contracts | DONE | Billing duplicate LedgerEffect, missing profile, 3 orphaned | 5 of 6 | +1 |
| GL | DONE | 6 missing chart roles, 7 orphaned profiles | 6 of 7 (2 accepted) | +6 |

**Final regression: 1143 passed, 0 failures** (up from 1119 pre-audit baseline, +24 tests added)

## Audit Checklist (per module)

Each module is audited against these 7 checks:

| # | Check | What we look for |
|---|-------|-----------------|
| 1 | **Profile review** | Read `profiles.py` — verify roles, mappings, `from_context` usage, LedgerEffect count per ledger |
| 2 | **YAML parity** | Read `policies/<module>.yaml` — must match profiles.py exactly |
| 3 | **Service coverage** | Read `service.py` — every profile must have a service method that emits its event type |
| 4 | **Chart roles** | Every role referenced in profiles must exist in `chart_of_accounts.yaml` |
| 5 | **Conftest roles** | Every role must be bound in `tests/conftest.py` module_role_resolver |
| 6 | **Test coverage** | Every service method must have an integration test with strict assertions |
| 7 | **Log verification** | New/fixed tests must show full pipeline trace: `status: "posted"`, `entry_count: N` |

## Known Bug Patterns

- **`from_context` with non-amount fields** — UUIDs, strings, booleans cause UNBALANCED_INTENT
- **Duplicate same-ledger LedgerEffects** — causes `concurrent_insert_conflict`
- **Multi-leg entries missing per-line `from_context`** — all lines get primary amount, unbalanced
- **Missing chart/conftest roles** — profile matches but journal writer can't resolve account

---

## Completed Audits

### AR (Accounts Receivable)
- **Status:** DONE
- **Issues found:** `from_context` bugs, missing service methods, missing roles
- **All tests pass:** Yes

### AP (Accounts Payable)
- **Status:** DONE
- **Issues found:** `APPaymentWithDiscount` UNBALANCED_INTENT (CASH line missing `from_context`, posting amount wrong), missing `PURCHASE_DISCOUNT` role
- **Fixes:** Added `from_context="payment_amount"` to CASH line, posting amount = payment + discount, added role to chart + conftest
- **Tests:** 11 AP tests, all pass
- **Log verified:** Yes — `entry_count: 2` (GL + AP subledger)

### Assets (Fixed Assets)
- **Status:** DONE
- **Issues found:** 3 profiles with unbalanced multi-leg mappings, 1 orphaned profile (CIP), duplicate GL LedgerEffects
- **Fixes:** Added proper `from_context` per leg, consolidated LedgerEffects to 1 per ledger, added `record_cip_capitalized()` method
- **Tests:** 10 asset tests, all pass
- **Log verified:** Yes

### Cash (Cash/Bank)
- **Status:** DONE
- **Issues found:** 8 `from_context` bugs (non-amount fields), 5 orphaned profiles, missing roles
- **Fixes:** Removed bad `from_context`, added 5 service methods, added 15 chart roles, added conftest accounts
- **Tests:** 11 cash tests, all pass
- **Log verified:** Yes

### Procurement
- **Status:** DONE
- **Issues found:** 2 orphaned profiles (POCommitment, POCommitmentRelief), chart role mismatch (`RESERVE_ENCUMBRANCE` vs `RESERVE_FOR_ENCUMBRANCE`), missing chart roles
- **Fixes:** Added `record_commitment()` + `relieve_commitment()`, renamed chart role, added `PURCHASE_COMMITMENT` + `COMMITMENT_OFFSET`
- **Tests:** 8 procurement tests, all pass
- **Log verified:** Yes

### Inventory
- **Status:** DONE
- **Issues found:** None — module is clean
- **Details:**
  - 11 profiles, all correct (no `from_context` bugs, no duplicate LedgerEffects, correct multi-ledger GL + INVENTORY subledger)
  - YAML matches profiles exactly
  - All 11 profiles covered by 10 service methods (no orphans): `receive_inventory`, `receive_with_variance`, `issue_for_sale`, `issue_for_production`, `issue_for_scrap`, `issue_for_transfer`, `receive_transfer`, `receive_from_production`, `adjust_positive`, `adjust_negative`, `revalue_inventory`
  - All chart roles present, all conftest roles bound
- **Tests:** 14 integration tests at `tests/integration/test_inventory_service.py`, all pass
- **Log verified:** Yes — all posting traces confirmed

### WIP (Work in Progress)
- **Status:** DONE
- **Issues found:** 4 issues
  1. 2 orphaned profiles: WipScrap (`wip.scrap`) and WipRework (`wip.rework`) — no service methods
  2. WipOverheadVariance UNBALANCED_INTENT — 3 mapping lines (1 debit, 2 credits: OVERHEAD_APPLIED, OVERHEAD_VARIANCE, OVERHEAD_CONTROL). Extra OVERHEAD_CONTROL credit caused imbalance
  3. `record_material_issue()` used wrong `ArtifactType.SHIPMENT` for CONSUMED_BY link — EconomicLink requires `COST_LOT`
  4. Tests asserted wrong expectations (ValueError for material issue, allowed POSTING_FAILED for overhead variance)
- **Fixes:**
  - Added `record_scrap()` and `record_rework()` service methods
  - Removed OVERHEAD_CONTROL from WipOverheadVariance mappings (profiles.py + wip.yaml) — now balanced 2-line entry
  - Changed `ArtifactType.SHIPMENT` → `ArtifactType.COST_LOT` in `record_material_issue()`
  - Updated tests: material_issue asserts POSTED, overhead_variance asserts strict POSTED, added 2 new tests (scrap + rework), updated structural test
- **Tests:** 12 WIP tests, all pass
- **Log verified:** Yes — all 4 fixed/new tests show `status: "posted"`, `entry_count: 1`

---

## Remaining Audits

### Payroll
- **Status:** DONE
- **Issues found:** 7 issues
  1. PayrollAccrual UNBALANCED_INTENT — ACCRUED_PAYROLL credit line had no `from_context`, got full gross_pay while tax/benefit credits added additional amounts. Credits >> Debits.
  2. TimesheetPTO unreachable — `scope="PTO:*"` but `post_event()` has no scope parameter, so profile never matched. Scope restriction was redundant (unique event type).
  3. 5 orphaned profiles: PayrollPayment, PayrollBenefitsPayment, TimesheetRegular, TimesheetOvertime, TimesheetPTO
- **Fixes:**
  - Added `from_context="net_pay_amount"` to ACCRUED_PAYROLL credit line (profiles.py + payroll.yaml)
  - Service computes `net_pay = gross_pay - withholdings` and passes `net_pay_amount` in payload
  - Removed `scope="PTO:*"` from TimesheetPTO (profiles.py + payroll.yaml)
  - Added 5 service methods: `record_payroll_payment()`, `record_benefits_payment()`, `record_regular_hours()`, `record_overtime()`, `record_pto()`
  - Added 5 integration tests, tightened payroll_run to assert POSTED strictly, updated structural test
- **Tests:** 13 payroll tests, all pass
- **Log verified:** Yes — all 6 new/fixed tests show `status: "posted"`, `entry_count: 1`

---

## Remaining Audits

### Expense (Travel & Expense)
- **Status:** DONE
- **Issues found:** 5 issues
  1. 3 orphaned profiles: ExpenseCardPayment (`expense.card_payment`), ExpenseAdvanceIssued (`expense.advance_issued`), ExpenseAdvanceCleared (`expense.advance_cleared`) — no service methods
  2. 4 missing chart_of_accounts.yaml roles: EMPLOYEE_PAYABLE, CORPORATE_CARD_LIABILITY, ADVANCE_CLEARING, PROJECT_WIP
  3. `allocate_expense()` payload bug: `expense_lines` items used `"allocated_amount"` key but `foreach` expects `"amount"` — caused UNBALANCED_INTENT
  4. `test_allocate_expense_posts` allowed POSTING_FAILED — masked bug #3
- **Fixes:**
  - Added 4 roles to chart_of_accounts.yaml (EMPLOYEE_PAYABLE → 2410, CORPORATE_CARD_LIABILITY → 2420, ADVANCE_CLEARING → 1190, PROJECT_WIP → 1311)
  - Added 3 service methods: `record_card_payment()`, `issue_advance()`, `clear_advance()`
  - Fixed `allocate_expense()` payload: added `"amount"` key to `expense_lines` items for foreach
  - Added 3 integration tests, tightened allocate_expense to assert strict POSTED, updated structural test
- **Tests:** 11 expense tests, all pass
- **Log verified:** Yes — all 4 new/fixed tests show `status: "posted"`, `entry_count: 1`

### Tax
- **Status:** DONE
- **Issues found:** 5 issues
  1. VatSettlement UNBALANCED_INTENT — TAX_RECEIVABLE credit line had no `from_context`, got posting amount (output VAT) instead of input VAT. Credits = output_vat + net_payment >> debits = output_vat.
  2. Missing `record_vat_settlement()` service method — `record_tax_obligation()` dispatch works but doesn't pass required context fields (`input_vat_amount`, `net_payment`)
  3. 4 missing chart_of_accounts.yaml roles: TAX_RECEIVABLE (1160), TAX_EXPENSE (5920), USE_TAX_ACCRUAL (2700), TAX_CLEARING (2850)
  4. 4 profiles with no integration tests: UseTaxAccrued, VatInput, VatSettlement, TaxRefundReceived
- **Fixes:**
  - Added `from_context="input_vat_amount"` to TAX_RECEIVABLE credit line in VatSettlement (profiles.py + tax.yaml)
  - Added `record_vat_settlement()` method with proper payload: `output_vat`, `input_vat_amount`, `net_payment`
  - Added 4 roles to chart_of_accounts.yaml
  - Added 4 integration tests, updated structural test
- **Tests:** 10 tax tests, all pass
- **Log verified:** Yes — all 4 new tests show `status: "posted"`, `entry_count: 1`

### 2. Contracts (Government Contracts + DCAA Compliance)
- **Status:** DONE
- **Scope:** 29 profiles (18 contract + 11 DCAA override), 8 service methods (7 original + 1 new), 11 tests (3 structural + 8 integration)
- **Files modified:**
  - `finance_modules/contracts/profiles.py` — Fixed BillingCostReimb, added FundingObligation profile + AccountRole entries
  - `finance_modules/contracts/service.py` — Added cost_billing to billing payload, added record_fee_accrual()
  - `finance_config/sets/US-GAAP-2026-v1/policies/contracts.yaml` — Fixed billing policy, added funding policy
  - `finance_config/sets/US-GAAP-2026-v1/chart_of_accounts.yaml` — Added 33 contract-specific roles
  - `tests/modules/test_contracts_service.py` — Tightened assertions, added fee accrual test, updated structural test
  - `tests/conftest.py` — Already correct (all 36 roles bound, lines 1596-1628)

#### Issues Found and Fixed (6)

**Issue 1: 33 missing chart_of_accounts.yaml roles — FIXED**
- Added 29 GL roles + 4 CONTRACT subledger roles to chart_of_accounts.yaml

**Issue 2: ContractBillingCostReimbursement — duplicate GL LedgerEffect + missing from_context — FIXED**
- Merged 2 GL LedgerEffects into 1, added `from_context="cost_billing"` to WIP_BILLED credit, `from_context="fee_amount"` to DEFERRED_FEE_REVENUE credit
- Service computes `cost_billing = net_billing - fee_amount` in payload
- Balance: Dr UNBILLED_AR(net_billing) = Cr WIP_BILLED(cost_billing) + Cr DEFERRED_FEE_REVENUE(fee_amount) ✓

**Issue 3: test_generate_billing_posts allowed POSTING_FAILED — FIXED**
- Tightened to assert strict `POSTED` + `is_success` + `journal_entry_ids > 0`

**Issue 4: No FundingAction profile — FIXED**
- Added ContractFundingObligation profile: Dr OBLIGATION_CONTROL / Cr RESERVE_FOR_ENCUMBRANCE
- Added to profiles.py, contracts.yaml, and _ALL_PROFILES
- Updated test from expecting PROFILE_NOT_FOUND to strict POSTED

**Issue 5: No record_fee_accrual() service method — FIXED**
- Added `record_fee_accrual()` method emitting `contract.fee_accrual`
- Added integration test for FIXED_FEE accrual
- Updated structural test expected methods list

**Issue 6: 11 DCAA override profiles — ACCEPTABLE (no fix needed)**
- Override profiles intercept events from AP/payroll/bank modules when allowability context is present
- Tested via `tests/domain/test_dcaa_compliance.py`

#### Audit Checklist
- [x] Read profiles.py (29 profiles analyzed)
- [x] Read YAML (29 policies, matches profiles)
- [x] Read service.py (8 methods — 7 original + 1 new)
- [x] Read test file (11 tests — 10 original + 1 new)
- [x] Check chart roles (33 added)
- [x] Check conftest roles (all 36 roles bound)
- [x] Fix Issue 1: Chart roles — DONE
- [x] Fix Issue 2: BillingCostReimb — DONE (profiles.py + YAML + service.py)
- [x] Fix Issue 3: Tighten billing test — DONE
- [x] Fix Issue 4: FundingAction profile — DONE (profiles.py + YAML + test)
- [x] Fix Issue 5: record_fee_accrual() — DONE (service.py + test + structural)
- [x] Run tests: 11/11 passed
- [x] Log verify: 3 fixed/new tests show `status: "posted"`
- [x] Regression run: 1137 passed, 0 failures
- [x] Update this tracker

### 3. GL (General Ledger)
- **Status:** DONE
- **Scope:** 10 profiles (4 core GL + 2 deferred + 4 FX), 12 service methods (6 original + 6 new), 16 tests (3 structural + 13 integration)
- **Files modified:**
  - `finance_modules/gl/service.py` — Added 6 service methods (deferred revenue/expense recognition, 4 FX gain/loss)
  - `finance_config/sets/US-GAAP-2026-v1/chart_of_accounts.yaml` — Added 6 missing roles
  - `tests/modules/test_gl_service.py` — Added 6 integration tests, updated structural test

#### Issues Found and Fixed (5)

**Issue 1: 6 missing chart_of_accounts.yaml roles — FIXED**
- Added: DIVIDENDS (3300), DEFERRED_REVENUE (2380), INTERCOMPANY_DUE_FROM (1800), INTERCOMPANY_DUE_TO (2550), FOREIGN_EXCHANGE_GAIN_LOSS (6970), ROUNDING (6998)

**Issue 2: 7 orphaned profiles with no service methods — FIXED (6 of 7)**
- Added 6 service methods: `recognize_deferred_revenue()`, `recognize_deferred_expense()`, `record_fx_unrealized_gain()`, `record_fx_unrealized_loss()`, `record_fx_realized_gain()`, `record_fx_realized_loss()`
- FXRevaluation profile (`gl.fx_revaluation`) left orphaned — **redundant** (same role on debit/credit sides, superseded by the 4 specific FX gain/loss profiles)

**Issue 3: FXRevaluation profile structurally questionable — ACCEPTED**
- Same role (FOREIGN_EXCHANGE_GAIN_LOSS) on both debit and credit sides → self-canceling entry
- Only 1 mapping line (debit only) — credit mapping missing
- Redundant: all real FX posting handled by FXUnrealizedGain/Loss, FXRealizedGain/Loss profiles
- No service method added (would produce a no-op journal entry)

**Issue 4: 2 service methods with no profile (known gaps) — ACCEPTED**
- `record_journal_entry()` → `gl.journal_entry` — returns PROFILE_NOT_FOUND
- `record_adjustment()` → `gl.adjustment` — returns PROFILE_NOT_FOUND
- Tests document this as known TODO. Manual journal entries don't fit the fixed debit/credit profile pattern — would need a "pass-through" or "foreach" profile design.

**Issue 5: YAML parity — CLEAN**
- All 10 YAML policies match profiles.py exactly (roles, mappings, guards)
- Conftest bindings: all 16 GL roles bound correctly

#### Audit Checklist
- [x] Read profiles.py (10 profiles analyzed)
- [x] Read YAML (10 policies, matches profiles)
- [x] Read service.py (12 methods — 6 original + 6 new)
- [x] Read test file (16 tests — 10 original + 6 new)
- [x] Check chart roles (6 missing → added)
- [x] Check conftest roles (all 16 roles bound)
- [x] Fix Issue 1: Chart roles — DONE
- [x] Fix Issue 2: Add 6 service methods — DONE
- [x] Add 6 integration tests + update structural test — DONE
- [x] Run tests: 16/16 passed
- [x] Log verify: 4 sample new tests show `status: "posted"`
- [x] Regression run: 1143 passed, 0 failures
- [x] Update this tracker

---

## Regression Baseline

After each module audit, run the full module suite to confirm no regressions:

```
pytest tests/modules/ -v
```

| Checkpoint | Tests | Failures | Date |
|-----------|-------|----------|------|
| Pre-audit baseline | 1119 | 0 | 2026-01-29 |
| After Cash audit | 1119 | 0 | 2026-01-29 |
| After Assets audit | 1119 | 0 | 2026-01-29 |
| After Procurement + AP audit | 1122 | 0 | 2026-01-29 |
| After Inventory audit | 1122 | 0 | 2026-01-29 |
| After WIP audit | 1124 | 0 | 2026-01-29 |
| After Payroll audit | 1129 | 0 | 2026-01-29 |
| After Expense audit | 1132 | 0 | 2026-01-29 |
| After Tax audit | 1136 | 0 | 2026-01-29 |
| After Contracts audit | 1137 | 0 | 2026-01-29 |
| After GL audit | 1143 | 0 | 2026-01-29 |
