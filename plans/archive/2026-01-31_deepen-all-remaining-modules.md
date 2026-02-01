# Plan: Deepen All Remaining Modules (16 Items)

**Date:** 2026-01-31
**Status:** COMPLETE
**Baseline:** 3,252 passed, 0 failed
**After Phase 1:** 3,308 passed, 0 failed
**After Phase 2:** 3,406 passed, 0 failed
**After Phase 3:** 3,522 passed, 0 failed
**After Phase 4:** 3,569 passed, 0 failed
**Source:** GAP_ANALYSIS.md + MODULE_BUILD_PLAN.md
**Full plan:** `.claude/plans/temporal-snuggling-parasol.md`

---

## Phase 1: P1 Existing Module Deepening — DONE

### 1A. GL Module — DONE
- 3 new methods: generate_recurring_entry, record_retained_earnings_roll, reconcile_account
- 2 new models: AccountReconciliation, PeriodCloseTask
- 2 new profiles: GLRecurringEntry, GLRetainedEarningsRoll
- Tests: 8 tests in test_gl_deepening.py

### 1B. AP Module — DONE
- 5 new methods: create_payment_run, execute_payment_run, auto_match_invoices, hold_vendor, release_vendor_hold
- 3 new models: PaymentRun, PaymentRunLine, VendorHold
- 0 new profiles (reuses existing APPayment, APInvoiceReceived)
- Tests: 11 tests in test_ap_deepening.py

### 1C. AR Module — DONE
- 6 new methods: generate_dunning_letters, auto_apply_payment, check_credit_limit, update_credit_limit, auto_write_off_small_balances, record_finance_charge
- 4 new models: DunningLevel, DunningHistory, CreditDecision, AutoApplyRule
- 1 new profile: ARFinanceCharge
- Tests: 15 tests in test_ar_deepening.py

### 1D. Multi-Currency Completion — DONE
- 4 new methods: translate_balances, record_cta, run_period_end_revaluation, multi_currency_trial_balance
- 3 new models: TranslationResult, RevaluationResult, MultiCurrencyTrialBalance
- 1 new profile: FXTranslationAdjustment
- CUMULATIVE_TRANSLATION_ADJ role added to chart_of_accounts.yaml
- Tests: 17 tests in test_multicurrency_deepening.py

---

## Phase 2: P1 New Modules — DONE

### 2A. Revenue Recognition (ASC 606) — DONE
- 10 methods, 6 models, 8 profiles, 5 helpers
- Tests: 28 tests in test_revenue_service.py
- New module: finance_modules/revenue/ (7 files + helpers.py)

### 2B. Lease Accounting (ASC 842) — DONE
- 10 methods, 7 models, 9 profiles, 5 calculations
- Tests: 28 tests in test_lease_service.py
- New module: finance_modules/lease/ (7 files + calculations.py)
- New COA roles: ROU_ASSET, LEASE_LIABILITY, ROU_AMORTIZATION, LEASE_INTEREST

### 2C. Budgeting — DONE
- 10 methods, 6 models, 6 profiles
- Tests: 17 tests in test_budget_service.py
- New module: finance_modules/budget/ (7 files)
- New COA roles: BUDGET_CONTROL, BUDGET_OFFSET

### 2D. Intercompany — DONE
- 7 methods, 5 models, 4 profiles
- Tests: 18 tests in test_intercompany_service.py
- New module: finance_modules/intercompany/ (7 files)
- Phase 2 regression: 3,406 passed, 0 failed

---

## Phase 3: P2 Existing Module Deepening — DONE

### 3A. Cash Module — DONE
- 5 new methods: import_bank_statement, auto_reconcile, generate_payment_file, forecast_cash, record_nsf_return
- 5 new models: BankStatement, BankStatementLine, ReconciliationMatch, CashForecast, PaymentFile
- 2 new profiles: CashAutoReconciled, CashNSFReturn
- New helpers.py: parse_mt940, parse_bai2, parse_camt053, format_nacha
- Tests: 23 tests in test_cash_deepening.py

### 3B. Assets Module — DONE
- 5 new methods: run_mass_depreciation, record_asset_transfer, test_impairment, record_revaluation, record_component_depreciation
- 3 new models: AssetTransfer, AssetRevaluation, DepreciationComponent
- 4 new profiles: AssetMassDepreciation, AssetTransferred, AssetRevalued, AssetComponentDepreciation
- New helpers.py: straight_line, double_declining_balance, sum_of_years_digits, units_of_production, calculate_impairment_loss
- Tests: 23 tests in test_assets_deepening.py

### 3C. Payroll Module — DONE
- 4 new methods: calculate_gross_to_net, record_benefits_deduction, generate_nacha_file, record_employer_contribution
- 3 new models: WithholdingResult, BenefitsDeduction, EmployerContribution
- 2 new profiles: PayrollBenefitsDeducted, PayrollEmployerContribution
- New helpers.py: calculate_federal_withholding, calculate_state_withholding, calculate_fica, generate_nacha_batch
- Tests: 16 tests in test_payroll_deepening.py

### 3D. WIP Module — DONE
- 3 new methods: calculate_production_cost, record_byproduct, calculate_unit_cost
- 3 new models: ProductionCostSummary, ByproductRecord, UnitCostBreakdown
- 1 new profile: WIPByproductRecorded
- Tests: 7 tests in test_wip_deepening.py

### 3E. Tax Module — DONE
- 7 new methods: calculate_deferred_tax, record_deferred_tax_asset, record_deferred_tax_liability, calculate_provision, record_multi_jurisdiction_tax, export_tax_return_data, record_tax_adjustment
- 5 new models: TemporaryDifference, DeferredTaxAsset, DeferredTaxLiability, TaxProvision, Jurisdiction
- 4 new profiles: TaxDTARecorded, TaxDTLRecorded, TaxMultiJurisdiction, TaxAdjustment
- New helpers.py: calculate_temporary_differences, calculate_dta_valuation_allowance, calculate_effective_tax_rate, aggregate_multi_jurisdiction
- Tests: 23 tests in test_tax_deepening.py

### 3F. Contracts Module — DONE
- 6 new methods: record_contract_modification, record_subcontract_cost, record_equitable_adjustment, run_dcaa_audit_prep, generate_sf1034, record_cost_disallowance
- 4 new models: ContractModification, Subcontract, AuditFinding, CostDisallowance
- 4 new profiles: ContractModified, ContractSubcost, ContractEquitableAdj, ContractCostDisallowed
- Tests: 11 tests in test_contracts_deepening.py
- Phase 3 regression: 3,522 passed, 0 failed

---

## Phase 4: P2 New Modules — DONE

### 4A. Project Accounting (GAP-09, M-18) — DONE
- 10 methods: create_project, record_cost, bill_milestone, bill_time_materials, recognize_revenue, revise_budget, complete_phase, calculate_evm, get_project_status, get_wbs_cost_report
- 5 models: Project, WBSElement, ProjectBudget, Milestone, EVMSnapshot
- 6 profiles: ProjectCostRecorded, ProjectBillingMilestone, ProjectBillingTM, ProjectRevenueRecognized, ProjectBudgetRevised, ProjectPhaseCompleted
- 8 EVM calculations: calculate_bcws, calculate_bcwp, calculate_acwp, calculate_cpi, calculate_spi, calculate_eac, calculate_etc, calculate_vac
- Tests: 25 tests in test_project_service.py
- New module: finance_modules/project/ (7 files: __init__.py, models.py, evm.py, profiles.py, service.py, config.py, workflows.py)
- New COA roles added to test conftest: CONTRACT_REVENUE, DIRECT_COST
- YAML policy: finance_config/sets/US-GAAP-2026-v1/policies/project.yaml

### 4B. Credit Loss / CECL (GAP-10, M-19) — DONE
- 8 methods: calculate_ecl, record_provision, adjust_provision, record_write_off, record_recovery, run_vintage_analysis, apply_forward_looking, get_disclosure_data
- 5 models: ECLEstimate, VintageAnalysis, LossRate, CreditPortfolio, ForwardLookingAdjustment
- 4 profiles: CreditLossProvision, CreditLossAdjustment, CreditLossWriteOff, CreditLossRecovery
- 5 calculations: calculate_ecl_loss_rate, calculate_ecl_pd_lgd, calculate_vintage_loss_curve, apply_forward_looking_adjustment, calculate_provision_change
- Tests: 22 tests in test_credit_loss_service.py
- New module: finance_modules/credit_loss/ (7 files: __init__.py, models.py, calculations.py, profiles.py, service.py, config.py, workflows.py)
- YAML policy: finance_config/sets/US-GAAP-2026-v1/policies/credit_loss.yaml
- Phase 4 regression: 3,569 passed, 0 failed

---

## Decisions Made
- No workflow service needed (GAP-02 deferred)
- Maximize engine reuse (VarianceCalculator, AllocationEngine, MatchingEngine, AgingCalculator, TaxCalculator, AllocationCascade, BillingEngine, ICEEngine)
- Module helpers.py ONLY for domain math engines don't cover
- Zero-amount postings rejected by kernel — all modules must pass non-zero amounts
- DeterministicClock only has now() — use self._clock.now().date() for dates

## Completed Work Summary
- **Phase 1:** 18 methods, 12 models, 4 profiles, 51 tests
- **Phase 2:** 37 methods, 24 models, 27 profiles, 91 tests
- **Phase 3:** 30 methods, 23 models, 17 profiles, 103 tests
- **Phase 4:** 18 methods, 10 models, 10 profiles, 47 tests
- **Grand total:** 103 methods, 69 models, 58 profiles, 292 tests added
- **Final regression:** 3,569 passed, 0 failed (up from 3,252 baseline)

## Status: ALL PHASES COMPLETE
All 16 module items have been built and tested. Plan ready for archival.
