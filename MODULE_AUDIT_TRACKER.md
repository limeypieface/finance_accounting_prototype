# Module Audit Tracker

Systematic audit of all finance modules for correctness, completeness, and test coverage.

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
- **Status:** IN PROGRESS — analysis complete, fixes not yet applied
- **Scope:** 29 profiles (18 contract + 11 DCAA override), 7 service methods, 10 tests (3 structural + 7 integration)
- **Files:**
  - `finance_modules/contracts/profiles.py` — READ, analyzed
  - `finance_modules/contracts/service.py` — READ, analyzed
  - `finance_config/sets/US-GAAP-2026-v1/policies/contracts.yaml` — READ, analyzed
  - `tests/modules/test_contracts_service.py` — READ, analyzed
  - `finance_config/sets/US-GAAP-2026-v1/chart_of_accounts.yaml` — checked
  - `tests/conftest.py` — checked (all 36 contract roles ARE bound, lines 1596-1628)

#### Issues Found (6)

**Issue 1: 33 missing chart_of_accounts.yaml roles**
- Nearly all contract-specific roles are missing from chart (but ARE bound in conftest)
- GL roles needed (29): WIP_DIRECT_LABOR (1411), WIP_DIRECT_MATERIAL (1412), WIP_SUBCONTRACT (1413), WIP_TRAVEL (1414), WIP_ODC (1415), WIP_FRINGE (1416), WIP_OVERHEAD (1417), WIP_GA (1418), WIP_RATE_ADJUSTMENT (1419), WIP_BILLED (1420), UNBILLED_AR (1150), MATERIAL_CLEARING (2820), AP_CLEARING (2860), EXPENSE_CLEARING (2830), FRINGE_POOL_APPLIED (2872), OVERHEAD_POOL_APPLIED (2871), GA_POOL_APPLIED (2873), DEFERRED_FEE_REVENUE (2810), FEE_REVENUE_EARNED (4800), INDIRECT_RATE_VARIANCE (6880), EXPENSE_ALLOWABLE (6110), EXPENSE_UNALLOWABLE (6120), EXPENSE_CONDITIONAL (6130), LABOR_ALLOWABLE (6140), LABOR_UNALLOWABLE (6150), OVERHEAD_POOL_ALLOWABLE (6890), OVERHEAD_UNALLOWABLE (6160), OBLIGATION_CONTROL (1620)
- CONTRACT subledger roles needed (4): CONTRACT_COST_INCURRED (SL-4001), COST_CLEARING (SL-4002), BILLED (SL-4003), COST_BILLED (SL-4004)
- **Status: DONE** — all 33 roles added to `chart_of_accounts.yaml` (lines 372-469). Verified the edit landed correctly.

**Issue 2: ContractBillingCostReimbursement — duplicate GL LedgerEffect + missing from_context**
- Profile has 2 GL LedgerEffects (both debit UNBILLED_AR): one for cost portion (Cr WIP_BILLED), one for fee portion (Cr DEFERRED_FEE_REVENUE)
- Duplicate same-ledger LedgerEffects cause `concurrent_insert_conflict` (known bug pattern)
- No `from_context` on any mapping lines — ALL 6 lines get posting amount (net_billing), so both GL entries book the full amount. Total UNBILLED_AR = 2x net_billing, total credits = WIP_BILLED(net) + DEFERRED_FEE_REVENUE(net) = 2x net_billing. Entry appears balanced per-journal but semantically wrong (fee portion should be fee_amount, cost portion should be cost_billing)
- **Fix:** Merge 2 GL LedgerEffects into 1. Add `from_context="cost_billing"` to WIP_BILLED credit, `from_context="fee_amount"` to DEFERRED_FEE_REVENUE credit. Add `cost_billing = net_billing - fee_amount` to service payload. UNBILLED_AR debit (no from_context) = net_billing. Credits = cost_billing + fee_amount = net_billing. Balanced.
- Files to edit: `profiles.py` (lines 469-517), `contracts.yaml` (lines 279-323), `service.py` (generate_billing payload ~line 213)
- **Status: NOT DONE**

**Issue 3: test_generate_billing_posts allows POSTING_FAILED**
- Comment says "CONCURRENT_INSERT" but real cause is Issue 2 (duplicate GL LedgerEffects)
- After fixing Issue 2, tighten to assert strict `POSTED`
- File: `test_contracts_service.py` line 137
- **Status: NOT DONE**

**Issue 4: No FundingAction profile — record_funding_action() returns PROFILE_NOT_FOUND**
- Service emits `contract.funding_action` but no profile exists in profiles.py or contracts.yaml
- Test at line 158 explicitly expects `PROFILE_NOT_FOUND`
- **Fix:** Add `ContractFundingObligation` profile: Dr OBLIGATION_CONTROL / Cr RESERVE_FOR_ENCUMBRANCE. Add `OBLIGATION_CONTROL` to AccountRole enum. Add matching YAML policy. Update test to assert POSTED.
- Conftest already has `OBLIGATION_CONTROL` bound (line 1629 → `module_accounts["encumbrance"]`)
- Chart needs `OBLIGATION_CONTROL` at 1620 (included in Issue 1 chart additions)
- **Status: NOT DONE**

**Issue 5: No record_fee_accrual() service method — 3 fee profiles orphaned**
- Profiles ContractFeeFixedAccrual, ContractFeeIncentiveAccrual, ContractFeeAwardAccrual dispatch on `contract.fee_accrual` with where-clause on `payload.fee_type` (FIXED_FEE, INCENTIVE_FEE, AWARD_FEE)
- No service method emits `contract.fee_accrual` — these profiles are unreachable
- **Fix:** Add `record_fee_accrual()` to service.py with params: `contract_id, fee_type, amount, effective_date, actor_id, cumulative_fee, ceiling_fee`. Payload: `contract_number, fee_type, amount, cumulative_fee, ceiling_fee`. Event type: `contract.fee_accrual`.
- Add integration test for fixed fee accrual.
- Update structural test expected methods list.
- **Status: NOT DONE**

**Issue 6: 11 DCAA override profiles — no dedicated service methods needed**
- These profiles use `PrecedenceMode.OVERRIDE` with `schema_version=2` to intercept events from AP/payroll/bank modules when `payload.allowability` field is present
- They don't need their own service methods — they fire when the AP/payroll/bank services emit events with allowability context
- No integration tests exist for DCAA profiles in the contracts test file
- **Status: ACCEPTABLE** — override profiles are tested via `tests/domain/test_dcaa_compliance.py`

#### Audit Checklist Progress
- [x] Read profiles.py (29 profiles analyzed)
- [x] Read YAML (29 policies, matches profiles)
- [x] Read service.py (7 methods analyzed)
- [x] Read test file (10 tests analyzed)
- [x] Check chart roles (33 missing identified)
- [x] Check conftest roles (all 36 roles bound)
- [x] Fix Issue 1: Chart roles (DONE — 33 roles added, lines 372-469)
- [ ] Fix Issue 2: BillingCostReimb (profiles.py + YAML + service.py)
- [ ] Fix Issue 3: Tighten billing test assertion
- [ ] Fix Issue 4: Add FundingAction profile (profiles.py + YAML + test)
- [ ] Fix Issue 5: Add record_fee_accrual() (service.py + test + structural)
- [ ] Run tests (`pytest tests/modules/test_contracts_service.py -v`)
- [ ] Log verify (new/fixed tests show `status: "posted"`)
- [ ] Regression run (`pytest tests/modules/ -v`)
- [ ] Update this tracker with final results

### 3. GL (General Ledger)
- **Status:** NOT STARTED
- **Priority:** Low — likely thin (period close, year-end), but foundational
- **Files to audit:**
  - `finance_modules/gl/profiles.py`
  - `finance_modules/gl/service.py`
  - `finance_config/sets/US-GAAP-2026-v1/policies/gl.yaml`
  - `tests/modules/test_gl_service.py`
- **Audit steps:**
  - [ ] Read profiles.py
  - [ ] Read YAML
  - [ ] Read service.py
  - [ ] Read test file
  - [ ] Check chart roles
  - [ ] Check conftest roles
  - [ ] Fix all issues
  - [ ] Run tests
  - [ ] Log verify
  - [ ] Update this tracker

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
| After Contracts audit | — | — | — |
| After GL audit | — | — | — |
