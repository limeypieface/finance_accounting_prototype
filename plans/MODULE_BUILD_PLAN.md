# Module Build Plan: Deepen 12 Existing + Create 6 New Modules

**Date:** 2026-01-30 (revised 2026-01-30 — subledger integration added)
**Source:** GAP_ANALYSIS.md (GAP-05 through GAP-23)
**Scope:** All MODULE-type gaps — no infrastructure (API, workflow runtime, scheduler)
**Prerequisite:** Fix 128 test failures first (CURRENT_PLAN.md Item 4, Category A)
**Dependency:** Subledger work (CURRENT_PLAN.md SL-Phase 1–10) — this plan assumes
subledger infrastructure is operational. See "Subledger Integration" section below.

---

## Objective

Bring all `finance_modules/` to production-grade completeness by:
1. Deepening 12 existing modules (service.py implementations, new event types, engine integration)
2. Creating 6 new modules (revenue, lease, budget, intercompany, project, credit_loss)

Each module follows the standard 6-file pattern:
```
finance_modules/MODULE_NAME/
    __init__.py      # Public API exports
    config.py        # Configuration schema (frozen dataclasses)
    models.py        # Domain models (frozen dataclasses, enums)
    profiles.py      # Accounting policies + registration
    service.py       # Orchestration via engines + kernel
    workflows.py     # State machine definitions
```

Plus a corresponding YAML policy file:
```
finance_config/sets/US-GAAP-2026-v1/policies/MODULE_NAME.yaml
```

---

## Architecture Rules (Apply to Every Module)

These rules are non-negotiable. Every module must comply:

1. **Services call `ModulePostingService.post_event()`** — no direct journal writes
2. **Frozen dataclasses** for all DTOs and value objects
3. **`Decimal` for money** — never `float`
4. **Clock injection** — never `datetime.now()` or `date.today()`
5. **Account ROLES in policies** — resolved to COA codes at posting time (L1)
6. **Guards in YAML** — business rule validation via guard expressions
7. **Where-clause dispatch** — one profile per event type variant (P1)
8. **Economic links** — document relationships tracked via `LinkGraphService`
9. **Engine invocation via EngineDispatcher** — never call engines directly
10. **Tests per invariant (R20)** — unit + integration for each new capability
11. **Subledger `entity_id_field` on profiles** — every profile with a subledger `LedgerEffect`
    must declare which payload field provides the entity ID (SL-Phase 6 requirement)
12. **Subledger entry verification in tests** — every posting test must verify subledger
    entries are created alongside journal entries when the profile has subledger ledger effects
13. **Reconciliation updates subledger status** — any method that matches/reconciles documents
    (payment application, 3-way match, bank reconciliation) must update
    `SubledgerEntryModel.reconciliation_status` and create `SubledgerReconciliationModel` records

---

## Subledger Integration (Cross-Cutting)

After subledger work (CURRENT_PLAN.md SL-Phase 1–10) is complete, every module that posts
events with subledger `LedgerEffect` entries will **automatically** create
`SubledgerEntryModel` records alongside journal entries. This section defines the
subledger contract for each module.

### SubledgerType Mapping

Existing types (defined in `finance_kernel/domain/subledger_control.py`):

| SubledgerType | Module(s) | Entity Concept | `entity_id_field` |
|---------------|-----------|----------------|-------------------|
| `AP` | AP | Vendor | `vendor_id` |
| `AR` | AR | Customer | `customer_id` |
| `INVENTORY` | Inventory | Item/SKU | `item_id` |
| `BANK` | Cash | Bank Account | `bank_account_id` |
| `FIXED_ASSETS` | Assets | Asset | `asset_id` |
| `PAYROLL` | Payroll | Employee | `employee_id` |
| `WIP` | WIP | Job/Production Order | `job_id` |
| `INTERCOMPANY` | Intercompany | Counterparty Entity | `counterparty_entity_id` |

**New types required** (must be added to `SubledgerType` enum, with corresponding
control contracts in `subledger_contracts.yaml` and concrete `SubledgerService`
implementations in `finance_services/`):

| SubledgerType | Module | Entity Concept | `entity_id_field` | Control Account Role |
|---------------|--------|----------------|-------------------|---------------------|
| `REVENUE` | Revenue (M-5) | Revenue Contract | `contract_id` | `REVENUE_CONTRACT_CONTROL` |
| `LEASE` | Lease (M-6) | Lease Agreement | `lease_id` | `LEASE_LIABILITY_CONTROL` |
| `PROJECT` | Project (M-18) | WBS Element | `wbs_element_id` | `PROJECT_WIP_CONTROL` |

Budget (M-7) and Credit Loss (M-19) do **not** need their own SubledgerTypes:
- Budget entries are memo postings (not GL balances that need entity-level reconciliation)
- Credit Loss provisions post to the AR subledger (adjusting the customer-level allowance)

### Subledger Contracts for New Types

Each new SubledgerType requires an entry in `subledger_contracts.yaml`:

```yaml
# Added to finance_config/sets/US-GAAP-2026-v1/subledger_contracts.yaml
- subledger_id: REVENUE
  owner_module: revenue
  control_account_role: REVENUE_CONTRACT_CONTROL
  entry_types: [RECOGNITION, DEFERRAL, MODIFICATION, ALLOCATION]
  timing: real_time
  tolerance_type: absolute
  tolerance_amount: "0.01"
  enforce_on_post: true
  enforce_on_close: true
  reconciliation_currency_mode: per_currency

- subledger_id: LEASE
  owner_module: lease
  control_account_role: LEASE_LIABILITY_CONTROL
  entry_types: [INITIAL_RECOGNITION, PAYMENT, INTEREST, AMORTIZATION, MODIFICATION, TERMINATION]
  timing: real_time
  tolerance_type: absolute
  tolerance_amount: "0.01"
  enforce_on_post: true
  enforce_on_close: true
  reconciliation_currency_mode: per_currency

- subledger_id: PROJECT
  owner_module: project
  control_account_role: PROJECT_WIP_CONTROL
  entry_types: [COST, BILLING, REVENUE_RECOGNITION]
  timing: period_end
  tolerance_type: percentage
  tolerance_percentage: "1.0"
  enforce_on_post: false
  enforce_on_close: true
  reconciliation_currency_mode: per_currency
```

### Concrete SubledgerService Implementations Required

Three new concrete services (same pattern as `APSubledgerService` etc. from SL-Phase 4):

| Service | File | Entity Validation |
|---------|------|-------------------|
| `RevenueSubledgerService` | `finance_services/subledger_revenue.py` | Entity must be a revenue contract |
| `LeaseSubledgerService` | `finance_services/subledger_lease.py` | Entity must be a lease agreement |
| `ProjectSubledgerService` | `finance_services/subledger_project.py` | Entity must be a WBS element |

### Subledger Impact on Entity-Level Queries

With subledger selectors operational, modules can query entity-level balances directly
instead of deriving them from journal lines. This simplifies several existing methods:

| Module | Current Approach | After Subledger |
|--------|-----------------|-----------------|
| AP aging | Derive from journal lines | Query AP subledger open items via `SubledgerSelector` |
| AR aging | Derive from journal lines | Query AR subledger open items via `SubledgerSelector` |
| Inventory valuation | Derive from cost lots | Query inventory subledger by item |
| Bank reconciliation | Match GL entries | Query bank subledger vs statement lines |
| Contract cost tracking | Derive from journal lines | Query contract subledger by contract |

### Period Close Ordering with Subledgers

After SL-Phase 8, the `ALL_SUBLEDGERS_CLOSED` guard is enforced. GL cannot close until
all subledger periods are closed in order:

```
INVENTORY → WIP → AR → AP → FIXED_ASSETS → PAYROLL → REVENUE → LEASE → PROJECT → GL
```

New subledger types (REVENUE, LEASE, PROJECT) must be added to the close ordering in
`finance_modules/gl/config.py` and the period close workflow.

---

## Current State Summary

| Module | Current LOC | Methods | Completeness | Priority |
|--------|------------|---------|-------------|----------|
| **GL** | 682 | 13 | 40% | P1 |
| **AP** | 784 | 11 | 55% | P1 |
| **AR** | 804 | 11 | 50% | P1 |
| **Inventory** | 661 | 12 | 50% | P2 |
| **Cash** | 577 | 9 | 35% | P2 |
| **Payroll** | 679 | 11 | 15% | P2 |
| **Tax** | 308 | 4 | 20% | P2 |
| **WIP** | 639 | 10 | 20% | P2 |
| **Assets** | 434 | 7 | 20% | P2 |
| **Expense** | 629 | 9 | 35% | P2 |
| **Procurement** | 463 | 6 | 15% | P2 |
| **Contracts** | 518 | 9 | 25% | P2 |
| **Reporting** | 546 | 11 | 95% | Done |
| **Revenue** | — | — | 0% | P1 (new) |
| **Lease** | — | — | 0% | P1 (new) |
| **Budget** | — | — | 0% | P1 (new) |
| **Intercompany** | — | — | 0% | P1 (new) |
| **Project** | — | — | 0% | P2 (new) |
| **Credit Loss** | — | — | 0% | P2 (new) |

---

## Phase 1: P1 Existing Module Deepening

### M-1: GL Module Deepening (GAP-12)

**Current:** 682 LOC, 13 methods. Has FX gain/loss, intercompany, deferred rev/exp, dividends.
**Gap:** No period close automation, no recurring entries, no retained earnings roll.

#### New Methods for `service.py`

| Method | Event Type | Description | Engine |
|--------|-----------|-------------|--------|
| `generate_recurring_entry` | `gl.recurring_entry` | Generate entry from template | None |
| `record_retained_earnings_roll` | `gl.retained_earnings_roll` | Year-end P&L close to RE | None |
| `record_reclassification` | `gl.reclassification` | Account reclassification | CorrectionEngine |
| `record_accrual` | `gl.accrual` | Period-end accrual entry | None |
| `reverse_accrual` | `gl.accrual_reversal` | Auto-reverse opening accrual | None |
| `reconcile_account` | N/A (no posting) | Sign-off on account balance | None |

#### New Models (`models.py`)

- `RecurringEntryTemplate` — template with schedule, amount, accounts
- `AccountReconciliation` — period sign-off record per account
- `PeriodCloseTask` — ordered close task with dependency tracking

#### New Profiles (`profiles.py` + `gl.yaml`)

- `GLRecurringEntry` — recurring entry posting
- `GLRetainedEarningsRoll` — close revenue/expense to retained earnings
- `GLReclassification` — reclassify between accounts
- `GLAccrual` — record accrual
- `GLAccrualReversal` — auto-reverse accrual

#### Workflow Updates (`workflows.py`)

- `PERIOD_CLOSE_WORKFLOW` — ordered close tasks (already has guard defs, needs task sequence)
- `JOURNAL_ENTRY_APPROVAL` — multi-level approval (depends on workflow runtime GAP-02)

#### Subledger Integration

- **Period close must respect subledger close ordering.** After SL-Phase 8, the
  `ALL_SUBLEDGERS_CLOSED` guard is real. The `PERIOD_CLOSE_WORKFLOW` must enforce:
  `INVENTORY → WIP → AR → AP → FIXED_ASSETS → PAYROLL → REVENUE → LEASE → PROJECT → GL`
- **Retained earnings roll** must only execute after all subledger periods are closed.
  The roll itself does not post to any subledger (it's a GL-only P&L→RE transfer).
- **`reconcile_account` should query subledger balances** when the account is a control
  account. Use `SubledgerSelector.get_aggregate_balance()` to compare GL control balance
  against subledger aggregate. This enables G9-aware account sign-off.
- **Update `close_order`** in `finance_modules/gl/config.py` to include new subledger
  types: REVENUE, LEASE, PROJECT (after subledger work delivers them).

#### Dependencies
- Period close automation depends on workflow runtime (GAP-02) for approval steps
- Period close depends on subledger infrastructure (SL-Phase 8) for close ordering
- Recurring entries depend on job scheduler (GAP-03) for scheduling
- Retained earnings roll has no external dependencies — can build now

---

### M-2: AP Module Deepening (GAP-13)

**Current:** 784 LOC, 11 methods. Has invoice, payment, 3-way match, accrual, prepayment.
**Gap:** No payment run, no automated matching, no vendor management.

#### New Methods for `service.py`

| Method | Event Type | Description | Engine |
|--------|-----------|-------------|--------|
| `create_payment_run` | `ap.payment_run_created` | Select invoices for batch payment | AgingCalculator |
| `execute_payment_run` | `ap.payment_run_executed` | Process batch, post all payments | AllocationEngine |
| `auto_match_invoices` | `ap.auto_matched` | Automated PO-receipt-invoice match | MatchingEngine |
| `record_early_payment_discount` | `ap.discount_taken` | Discount for early payment | None |
| `hold_vendor` | N/A (no posting) | Place vendor on payment hold | None |
| `release_vendor_hold` | N/A (no posting) | Release hold | None |
| `score_vendor` | N/A (no posting) | Compute vendor performance score | None |

#### New Models (`models.py`)

- `PaymentRun` — batch payment header with selection criteria
- `PaymentRunLine` — individual payment within a run
- `VendorScore` — delivery, quality, price scoring
- `VendorHold` — hold record with reason and effective dates

#### New Profiles (`profiles.py` + `ap.yaml`)

- `APPaymentRun` — batch payment posting
- `APAutoMatched` — auto 3-way match posting
- `APEarlyPaymentDiscount` — discount taken posting

#### Subledger Integration

- **SubledgerType:** `AP` (existing). Entity: Vendor. `entity_id_field: vendor_id`
- **All existing and new posting methods** automatically create AP subledger entries
  (vendor-level) via SL-Phase 6 wiring. No additional code needed for entry creation.
- **`auto_match_invoices`** must update `SubledgerEntryModel.reconciliation_status` for
  matched invoice and receipt entries (open → reconciled) and create
  `SubledgerReconciliationModel` records pairing the debit/credit entries.
- **`execute_payment_run`** must update reconciliation status for each paid invoice's
  subledger entry (open → reconciled).
- **`calculate_aging`** should be refactored to query AP subledger open items via
  `SubledgerSelector.get_open_items(entity_id, SubledgerType.AP)` instead of deriving
  from journal lines. This is more efficient and entity-aware.
- **All new profiles** must declare `entity_id_field: vendor_id` on their AP `LedgerEffect`.

#### Dependencies
- Payment run needs batch processing (GAP-03) for large volumes
- Auto-match uses existing `MatchingEngine` — no new engine needed
- Subledger reconciliation requires SL-Phase 4 (concrete APSubledgerService)

---

### M-3: AR Module Deepening (GAP-14)

**Current:** 804 LOC, 11 methods. Has invoice, payment, credit memo, write-off, deferred revenue.
**Gap:** No dunning, no automated cash application, no credit management.

#### New Methods for `service.py`

| Method | Event Type | Description | Engine |
|--------|-----------|-------------|--------|
| `generate_dunning_letters` | N/A (no posting) | Generate collection letters by aging | AgingCalculator |
| `auto_apply_payment` | `ar.auto_applied` | Rules-based payment matching | MatchingEngine |
| `check_credit_limit` | N/A (no posting) | Evaluate customer credit | None |
| `update_credit_limit` | N/A (no posting) | Adjust customer credit limit | None |
| `auto_write_off_small_balances` | `ar.small_balance_write_off` | Rules-based write-off | None |
| `record_finance_charge` | `ar.finance_charge` | Late payment interest | None |

#### New Models (`models.py`)

- `DunningLevel` — dunning letter level (1-4) with template and escalation
- `DunningHistory` — record of letters sent per customer
- `CreditDecision` — credit limit decision with reasoning
- `AutoApplyRule` — payment matching rules (reference number, amount, customer)

#### New Profiles (`profiles.py` + `ar.yaml`)

- `ARAutoApplied` — auto-matched payment application
- `ARSmallBalanceWriteOff` — automated write-off
- `ARFinanceCharge` — late payment interest posting

#### Subledger Integration

- **SubledgerType:** `AR` (existing). Entity: Customer. `entity_id_field: customer_id`
- **All existing and new posting methods** automatically create AR subledger entries
  (customer-level) via SL-Phase 6 wiring.
- **`auto_apply_payment`** must update `SubledgerEntryModel.reconciliation_status` for
  matched invoice and payment entries (open → reconciled) and create
  `SubledgerReconciliationModel` records.
- **`auto_write_off_small_balances`** must update reconciliation status for written-off
  subledger entries.
- **`generate_dunning_letters`** should query AR subledger open items via
  `SubledgerSelector.get_open_items(entity_id, SubledgerType.AR)` to identify overdue
  items per customer, instead of deriving from journal lines.
- **`check_credit_limit`** can use `SubledgerSelector.get_aggregate_balance()` per
  customer to get outstanding AR balance for credit evaluation.
- **Credit Loss module (M-19)** provisions post adjustments to the AR subledger.

#### Dependencies
- Dunning workflow depends on notification system (GAP-28) for letter delivery
- Cash application uses existing `MatchingEngine`
- Subledger queries require SL-Phase 3 (SubledgerSelector)

---

### M-4: Multi-Currency Completion (GAP-11)

**Current:** Multicurrency capability disabled in config. Exchange rate model exists. FX gain/loss events in GL module.
**Gap:** No currency translation, no CTA, no period-end revaluation, no multi-currency trial balance.

#### Changes Required

This is primarily GL module + config work, not a separate module:

| Change | Location | Description |
|--------|----------|-------------|
| Enable multicurrency | `root.yaml` | Set `multicurrency: enabled` |
| `translate_balances` method | `gl/service.py` | Current/temporal rate translation |
| `record_cta` method | `gl/service.py` | CTA posting (event: `fx.cta_posted`) |
| `run_period_end_revaluation` method | `gl/service.py` | Mass FX revaluation |
| Multi-currency trial balance | `reporting/service.py` | TB in local/group/hard currencies |
| Exchange rate service | `finance_kernel/services/` | Rate lookup with date/type |

#### New Profiles

- `FXTranslationAdjustment` — CTA posting
- `FXPeriodEndRevaluation` — mass revaluation posting

#### New Models

- `TranslationResult` — translation output per account/currency
- `RevaluationResult` — revaluation output with gain/loss amounts

#### Subledger Integration (SL-G3)

- **Per-currency reconciliation is enforced** by SL-G3 invariant. G9 enforcement
  (GL control = SL aggregate) is checked per currency. This means:
- **`run_period_end_revaluation`** must post FX adjustments to **both** the GL control
  account and the corresponding subledger. A revaluation of a foreign AP invoice must
  create a GL journal entry (FX gain/loss vs AP control) **and** an AP subledger entry
  for the same vendor, same currency, so G9 stays balanced per currency.
- **`translate_balances`** (CTA) operates at the entity/currency level. CTA postings
  affect the GL equity section but do not create subledger entries (CTA is GL-only).
- **Multi-currency trial balance** should use `SubledgerSelector` for entity-level
  foreign currency balances (vendor/customer/item balances by currency).
- **All subledger contracts** set `reconciliation_currency_mode: per_currency` by default.
  This means revaluation adjustments must maintain per-currency G9 alignment.

---

## Phase 2: P1 New Modules

### M-5: Revenue Recognition Module — GAP-05 (ASC 606 / IFRS 15)

**Current:** Nothing. AR has basic deferred revenue but no 5-step model.

#### Files to Create

```
finance_modules/revenue/
    __init__.py
    config.py        # Recognition rules, SSP methods, variable consideration constraints
    models.py        # RevenueContract, PerformanceObligation, TransactionPrice, SSPAllocation
    profiles.py      # Rev rec policies (point-in-time, over-time, contract modification)
    service.py       # 5-step model orchestration
    workflows.py     # Contract lifecycle state machine
    helpers.py       # Variable consideration, SSP calc, progress measurement (pure functions)
```

#### Event Types

| Event Type | Description | Engine Used |
|------------|-------------|-------------|
| `revenue.contract_identified` | Step 1: Identify contract | None |
| `revenue.po_identified` | Step 2: Identify performance obligations | None |
| `revenue.price_determined` | Step 3: Determine transaction price | None |
| `revenue.price_allocated` | Step 4: Allocate price to POs | AllocationEngine |
| `revenue.recognized_point_in_time` | Step 5a: Recognize at point in time | None |
| `revenue.recognized_over_time` | Step 5b: Recognize over time (input/output) | None |
| `revenue.contract_modified` | Contract modification (cumulative/prospective) | None |
| `revenue.variable_consideration_updated` | Re-estimate variable consideration | None |

#### Key Models (`models.py`)

- `RevenueContract` — customer contract with combinability rules
- `PerformanceObligation` — distinct good/service with standalone selling price
- `TransactionPrice` — price with variable consideration and constraints
- `SSPAllocation` — allocated price per PO (relative SSP method)
- `RecognitionSchedule` — over-time recognition schedule (input/output method)
- `ContractModification` — modification with impact assessment

#### Key Methods (`service.py`)

- `identify_contract(customer_id, contract_terms, ...)` — Step 1
- `identify_performance_obligations(contract_id, deliverables, ...)` — Step 2
- `determine_transaction_price(contract_id, base_price, variable_components, ...)` — Step 3
- `allocate_transaction_price(contract_id, ...)` — Step 4 (uses AllocationEngine)
- `recognize_revenue(contract_id, po_id, method, ...)` — Step 5
- `modify_contract(contract_id, modification_type, ...)` — Modification handling
- `update_variable_consideration(contract_id, ...)` — Re-estimation
- `get_contract_status(contract_id)` — Current state of all POs
- `get_unbilled_revenue(as_of_date)` — Unbilled revenue report
- `get_deferred_revenue(as_of_date)` — Deferred revenue report

#### Profiles (8 policies)

- `RevenuePointInTime` — recognition at point of transfer
- `RevenueOverTimeInput` — over-time (input method, e.g., cost-to-cost)
- `RevenueOverTimeOutput` — over-time (output method, e.g., units delivered)
- `RevenuePriceAllocation` — SSP allocation posting
- `RevenueContractModificationCumulative` — cumulative catch-up
- `RevenueContractModificationProspective` — prospective adjustment
- `RevenueVariableConsiderationUpdate` — re-estimation posting
- `RevenueLicenseWithRenewal` — license + renewal separation

#### Helpers (`helpers.py` — pure functions)

- `estimate_variable_consideration(scenarios, constraint_threshold)` — expected value or most likely
- `calculate_ssp(method, observable_price, adjusted_market, ...)` — standalone selling price
- `measure_progress_input(costs_incurred, total_estimated_costs)` — cost-to-cost
- `measure_progress_output(units_delivered, total_units)` — output method
- `assess_modification_type(modification, original_contract)` — cumulative vs prospective

#### Subledger Integration

- **New SubledgerType:** `REVENUE` (must be added to enum). Entity: Revenue Contract.
  `entity_id_field: contract_id`
- **Requires:** New entry in `subledger_contracts.yaml` (see Subledger Integration section),
  new `RevenueSubledgerService` in `finance_services/subledger_revenue.py`, new
  `REVENUE_CONTRACT_CONTROL` account role in COA.
- **All posting profiles** must include a `REVENUE` subledger `LedgerEffect` with
  `entity_id_field: contract_id` so that contract-level subledger entries are created.
- **Contract asset/liability tracking:** The REVENUE subledger provides contract-level
  balance tracking (unbilled revenue = contract asset, deferred revenue = contract liability).
  `get_unbilled_revenue()` and `get_deferred_revenue()` should query subledger balances
  via `SubledgerSelector` rather than deriving from journal lines.
- **G9 enforcement:** `enforce_on_post: true` means every revenue recognition posting is
  validated against the REVENUE control account in real time.
- **Period close:** REVENUE subledger must close before GL (added to close ordering).

---

### M-6: Lease Accounting Module — GAP-06 (ASC 842 / IFRS 16)

**Current:** Nothing.

#### Files to Create

```
finance_modules/lease/
    __init__.py
    config.py        # Discount rate defaults, classification thresholds, exemptions
    models.py        # Lease, LeasePayment, ROUAsset, LeaseLiability, AmortizationSchedule
    profiles.py      # Lease accounting policies (finance, operating, short-term)
    service.py       # Lease lifecycle orchestration
    workflows.py     # Lease approval state machine
    calculations.py  # PV, amortization, modification remeasurement (pure functions)
```

#### Event Types

| Event Type | Description | Engine Used |
|------------|-------------|-------------|
| `lease.classified` | Classify as finance vs operating | None |
| `lease.initial_recognition` | Record ROU asset + liability | None |
| `lease.payment_made` | Record lease payment | None |
| `lease.interest_accrued` | Accrue interest on liability | None |
| `lease.amortization_recorded` | ROU asset amortization | None |
| `lease.modified` | Lease modification/remeasurement | None |
| `lease.terminated_early` | Early termination | CorrectionEngine |
| `lease.renewed` | Lease renewal assessment | None |
| `lease.impairment` | ROU asset impairment | None |

#### Key Models (`models.py`)

- `Lease` — lease terms (start, end, payments, options, escalations)
- `LeaseClassification` — enum: FINANCE, OPERATING, SHORT_TERM, LOW_VALUE
- `LeasePayment` — scheduled payment (fixed + variable)
- `ROUAsset` — right-of-use asset with carrying amount
- `LeaseLiability` — liability with effective interest rate
- `AmortizationScheduleLine` — single line in amortization schedule
- `LeaseModification` — modification terms and remeasurement

#### Key Methods (`service.py`)

- `classify_lease(lease_terms, ...)` — finance vs operating determination
- `record_initial_recognition(lease_id, ...)` — ROU asset + liability
- `generate_amortization_schedule(lease_id)` — full schedule
- `record_periodic_payment(lease_id, payment_date)` — payment posting
- `accrue_interest(lease_id, period_end)` — interest accrual
- `record_amortization(lease_id, period_end)` — ROU amortization
- `modify_lease(lease_id, new_terms, ...)` — remeasurement
- `terminate_early(lease_id, termination_date)` — early termination
- `get_lease_portfolio(as_of_date)` — all active leases with balances
- `get_disclosure_data(as_of_date)` — ASC 842 required disclosures

#### Calculations (`calculations.py` — pure functions)

- `present_value(payments, discount_rate, start_date)` — PV of lease payments
- `build_amortization_schedule(pv, rate, payments)` — effective interest schedule
- `classify_lease_type(lease_terms, fair_value, economic_life, ...)` — classification logic
- `remeasure_liability(remaining_payments, new_rate, modification_date)` — remeasurement
- `calculate_rou_adjustment(original_rou, liability_change, ...)` — ROU adjustment

#### Subledger Integration

- **New SubledgerType:** `LEASE` (must be added to enum). Entity: Lease Agreement.
  `entity_id_field: lease_id`
- **Requires:** New entry in `subledger_contracts.yaml` (see Subledger Integration section),
  new `LeaseSubledgerService` in `finance_services/subledger_lease.py`, new
  `LEASE_LIABILITY_CONTROL` account role in COA.
- **All posting profiles** must include a `LEASE` subledger `LedgerEffect` with
  `entity_id_field: lease_id` so that lease-level subledger entries are created.
- **Lease-level balance tracking:** The LEASE subledger tracks ROU asset and lease liability
  balances per lease agreement. `get_lease_portfolio()` and `get_disclosure_data()` should
  query subledger balances via `SubledgerSelector` for per-lease liability/asset amounts.
- **G9 enforcement:** `enforce_on_post: true` means every lease posting is validated
  against the LEASE_LIABILITY_CONTROL account in real time.
- **Modification remeasurement:** When a lease is modified, the LEASE subledger entries
  for the original terms remain (append-only), and the remeasurement creates new subledger
  entries reflecting the adjustment. The subledger balance automatically reflects the
  current liability after remeasurement.
- **Period close:** LEASE subledger must close before GL (added to close ordering).

---

### M-7: Budgeting & Planning Module — GAP-07

**Current:** Nothing. GL has `compute_budget_variance()` stub.

#### Files to Create

```
finance_modules/budget/
    __init__.py
    config.py        # Budget periods, approval thresholds, encumbrance rules
    models.py        # BudgetEntry, BudgetVersion, BudgetLock, Encumbrance, Forecast
    profiles.py      # Encumbrance posting policies
    service.py       # Budget CRUD, comparison, encumbrance, forecasting
    workflows.py     # Budget approval state machine
```

#### Event Types

| Event Type | Description | Engine Used |
|------------|-------------|-------------|
| `budget.entry_posted` | Record budget amount | None |
| `budget.transfer_posted` | Transfer between cost centers | None |
| `budget.encumbrance_committed` | Commit on PO creation | None |
| `budget.encumbrance_relieved` | Relieve on invoice receipt | None |
| `budget.encumbrance_cancelled` | Cancel on PO cancellation | None |
| `budget.forecast_updated` | Update rolling forecast | None |

#### Key Models (`models.py`)

- `BudgetVersion` — versioned budget (original, revised, forecast)
- `BudgetEntry` — amount by account, period, dimension, version
- `BudgetLock` — prevent changes to approved budgets
- `Encumbrance` — committed amount linked to PO via economic link
- `BudgetVariance` — computed variance (favorable/unfavorable)
- `ForecastEntry` — rolling forecast by period

#### Key Methods (`service.py`)

- `post_budget_entry(account_id, period, amount, version, dimensions, ...)` — record budget
- `transfer_budget(from_account, to_account, amount, period, ...)` — reallocation
- `lock_budget(version, period_range)` — prevent changes
- `record_encumbrance(po_id, amount, account, ...)` — commit on PO
- `relieve_encumbrance(invoice_id, po_id, amount, ...)` — relieve on invoice
- `cancel_encumbrance(po_id, ...)` — cancel on PO cancellation
- `get_budget_vs_actual(account, period, version)` — variance report
- `get_encumbrance_balance(account, period)` — outstanding commitments
- `update_forecast(period, entries, ...)` — rolling forecast
- `get_available_budget(account, period)` — budget - actual - encumbrances

#### Profiles (6 policies)

- `BudgetEntry` — budget amount posting
- `BudgetTransfer` — transfer between accounts/dimensions
- `EncumbranceCommit` — PO commitment
- `EncumbranceRelieve` — invoice receipt
- `EncumbranceCancel` — PO cancellation
- `ForecastUpdate` — forecast revision

#### Subledger Integration

- **No dedicated SubledgerType.** Budget entries are memo postings to a budget ledger,
  not GL control account postings requiring entity-level subledger reconciliation.
- Encumbrance postings may post to the GL (if the organization uses encumbrance
  accounting on the GL), but these do not require subledger entries — they are
  account-level commitments, not entity-level balances.

---

### M-8: Intercompany Module — GAP-08

**Current:** GL has `record_intercompany_transfer()`. Config supports `legal_entity` scope. No dedicated IC infrastructure.

#### Files to Create

```
finance_modules/intercompany/
    __init__.py
    config.py        # Entity hierarchy, elimination rules, transfer pricing
    models.py        # IntercompanyAgreement, ICTransaction, EliminationEntry
    profiles.py      # IC posting policies (transfer, elimination)
    service.py       # IC posting, elimination, reconciliation, consolidation
    workflows.py     # IC approval state machine
```

#### Event Types

| Event Type | Description | Engine Used |
|------------|-------------|-------------|
| `ic.transfer_posted` | IC transaction (posts to both entities) | None |
| `ic.elimination_posted` | Elimination entry at consolidation | CorrectionEngine |
| `ic.reconciliation_completed` | IC balance reconciliation | ReconciliationManager |
| `ic.markup_posted` | Transfer pricing markup/adjustment | None |

#### Key Models (`models.py`)

- `IntercompanyAgreement` — agreement between two entities with terms
- `ICTransaction` — individual IC transaction with offsetting entries
- `EliminationRule` — auto-elimination rule (account pairs, entity pairs)
- `ConsolidationResult` — rolled-up entity financials
- `ICReconciliationResult` — reconciliation status per entity pair

#### Key Methods (`service.py`)

- `post_ic_transfer(from_entity, to_entity, amount, account, ...)` — dual posting
- `generate_eliminations(period, entity_scope, ...)` — auto-eliminate IC balances
- `reconcile_ic_balances(entity_a, entity_b, period)` — match IC balances
- `consolidate(entities, period, ...)` — sum across entities
- `get_ic_balance(entity_a, entity_b, as_of_date)` — outstanding IC balance
- `get_elimination_report(period)` — elimination entries for review
- `post_transfer_pricing_adjustment(agreement_id, ...)` — arm's-length adjustment

#### Subledger Integration

- **SubledgerType:** `INTERCOMPANY` (existing). Entity: Counterparty Entity.
  `entity_id_field: counterparty_entity_id`
- **IC transaction posting** creates INTERCOMPANY subledger entries on both sides
  (each entity gets a subledger entry for the counterparty). This gives per-entity-pair
  IC balance tracking.
- **`reconcile_ic_balances`** should query INTERCOMPANY subledger open items via
  `SubledgerSelector` for each entity pair, then update reconciliation status on
  matched entries. This replaces journal-line-level derivation.
- **`get_ic_balance`** uses `SubledgerSelector.get_aggregate_balance()` filtered by
  counterparty entity to get outstanding IC balance per pair.
- **Elimination entries** do not create subledger entries (they are consolidation-level
  GL adjustments, not entity-level balances).
- **G9 enforcement:** The INTERCOMPANY control account must reconcile against the
  aggregate of all IC subledger entries across counterparty entities.

---

## Phase 3: P2 Existing Module Deepening

### M-9: Cash Module Deepening (GAP-15)

**Current:** 577 LOC, 9 methods. Has receipt, disbursement, transfer, reconciliation.
**Gap:** No bank statement import, no auto-reconciliation, no payment processing, no cash forecasting.

#### New Methods

| Method | Event Type | Description | Engine |
|--------|-----------|-------------|--------|
| `import_bank_statement` | N/A (ingestion) | Parse MT940/BAI2/CAMT.053 | None |
| `auto_reconcile` | `cash.auto_reconciled` | Rules-based GL matching | MatchingEngine |
| `generate_payment_file` | N/A (output) | ACH/Wire file generation | None |
| `forecast_cash` | N/A (no posting) | Projected cash flows | None |
| `record_nsf_return` | `cash.nsf_return` | NSF/returned check | None |

#### New Models

- `BankStatement` — imported statement with header/lines
- `BankStatementLine` — individual bank transaction
- `ReconciliationMatch` — bank line matched to GL entry
- `CashForecast` — projected inflows/outflows by period
- `PaymentFile` — generated payment file record

#### Subledger Integration

- **SubledgerType:** `BANK` (existing). Entity: Bank Account. `entity_id_field: bank_account_id`
- **`auto_reconcile`** must update `SubledgerEntryModel.reconciliation_status` for matched
  bank subledger entries (open → reconciled) and create `SubledgerReconciliationModel` records
  pairing bank statement lines to subledger entries.
- **`import_bank_statement`** should compare imported statement lines against BANK subledger
  open items via `SubledgerSelector.get_open_items(bank_account_id, SubledgerType.BANK)`.
  This is more direct than matching against GL journal entries.
- **`reconcile_bank_statement`** (existing) should be updated to use subledger queries
  for the bank-side matching.
- **`forecast_cash`** can use `SubledgerSelector.get_aggregate_balance()` per bank account
  as the starting cash position.

---

### M-10: Inventory Module Deepening (GAP-16)

**Current:** 661 LOC, 12 methods. Has receive, issue (FIFO), adjust, revalue, transfer.
**Gap:** No cycle count, no ABC classification, no reorder point, no inter-warehouse.

#### New Methods

| Method | Event Type | Description | Engine |
|--------|-----------|-------------|--------|
| `record_cycle_count` | `inventory.cycle_count` | Physical count + variance | VarianceCalculator |
| `classify_abc` | N/A (no posting) | Value-based stratification | None |
| `calculate_reorder_point` | N/A (no posting) | Min/max/reorder calculation | None |
| `record_inter_warehouse_transfer` | `inventory.warehouse_transfer` | Location-to-location | None |
| `record_shelf_life_write_off` | `inventory.expired` | Expired inventory write-off | None |

#### New Models

- `CycleCount` — count event with expected vs actual
- `ABCClassification` — item classification (A/B/C) with thresholds
- `ReorderPoint` — min/max/reorder per item per location
- `WarehouseLocation` — location within a warehouse

#### Subledger Integration

- **SubledgerType:** `INVENTORY` (existing). Entity: Item/SKU. `entity_id_field: item_id`
- **`record_cycle_count`** should compare physical count against INVENTORY subledger balance
  per item via `SubledgerSelector.get_aggregate_balance(item_id, SubledgerType.INVENTORY)`.
  Variance = physical count - subledger balance.
- **`classify_abc`** should query INVENTORY subledger balances per item to stratify by value.
- **`record_inter_warehouse_transfer`** creates subledger entries for both source and
  destination (debit destination item, credit source item — same item, different location
  dimension).
- **Inventory valuation reports** can use subledger balance queries per item instead of
  aggregating journal lines.

---

### M-11: Assets Module Deepening (GAP-17)

**Current:** 434 LOC, 7 methods. Has acquisition, CIP, depreciation, disposal, impairment, scrap.
**Gap:** No depreciation method library, no mass depreciation, no impairment testing, no asset transfer.

#### New Methods

| Method | Event Type | Description | Engine |
|--------|-----------|-------------|--------|
| `run_mass_depreciation` | `asset.mass_depreciation` | Batch for all assets | None (loop) |
| `record_asset_transfer` | `asset.transferred` | Between cost centers/entities | None |
| `test_impairment` | N/A (no posting) | Fair value vs carrying amount | None |
| `record_revaluation` | `asset.revalued` | Asset revaluation (IFRS) | None |
| `record_component_depreciation` | `asset.component_depreciation` | Component-level depreciation | None |

#### New Helpers (`helpers.py` — pure functions)

- `straight_line(cost, salvage, useful_life, periods_elapsed)` — SL depreciation
- `double_declining(cost, salvage, useful_life, periods_elapsed, accumulated)` — DDB
- `sum_of_years_digits(cost, salvage, useful_life, period)` — SYD
- `units_of_production(cost, salvage, total_units, units_this_period)` — UOP
- `calculate_impairment_loss(carrying_amount, fair_value)` — impairment test

#### Subledger Integration

- **SubledgerType:** `FIXED_ASSETS` (existing). Entity: Asset. `entity_id_field: asset_id`
- **`run_mass_depreciation`** creates FIXED_ASSETS subledger entries for each asset's
  depreciation posting. The subledger balance per asset reflects net book value
  (acquisition - accumulated depreciation - impairment).
- **`test_impairment`** should query FIXED_ASSETS subledger balance per asset via
  `SubledgerSelector.get_aggregate_balance(asset_id)` to get current carrying amount.
- **`record_asset_transfer`** creates subledger entries reflecting the transfer
  (credit source asset, debit destination — or update dimensions on same asset).
- **Asset register reports** can query FIXED_ASSETS subledger for per-asset balances
  instead of aggregating journal lines.

---

### M-12: Payroll Module Deepening (GAP-18)

**Current:** 679 LOC, 11 methods. Has payroll run, tax, payment, benefits, hours, PTO, labor allocation.
**Gap:** No gross-to-net, no tax withholding tables, no labor distribution detail, no benefits deduction detail.

#### New Methods

| Method | Event Type | Description | Engine |
|--------|-----------|-------------|--------|
| `calculate_gross_to_net` | N/A (pure calc) | Full pay calculation | TaxCalculator |
| `calculate_withholding` | N/A (pure calc) | Federal/state/local tax | TaxCalculator |
| `record_benefits_deduction` | `payroll.benefits_deducted` | Health/dental/401k | None |
| `distribute_labor` | `payroll.labor_distributed` | Cost allocation to projects | AllocationEngine |
| `generate_nacha_file` | N/A (output) | Direct deposit ACH file | None |
| `record_employer_contribution` | `payroll.employer_contribution` | Employer match posting | None |

#### New Helpers (`helpers.py` — pure functions)

- `calculate_federal_withholding(gross, filing_status, allowances, tax_table)` — federal tax
- `calculate_state_withholding(gross, state, filing_status, tax_table)` — state tax
- `calculate_fica(gross, ytd_gross, ss_limit, ss_rate, medicare_rate)` — FICA
- `calculate_benefits_deduction(plan_type, coverage_level, rates)` — benefits
- `generate_nacha_batch(payments, company_info)` — ACH file formatting

#### Subledger Integration

- **SubledgerType:** `PAYROLL` (existing). Entity: Employee. `entity_id_field: employee_id`
- **`distribute_labor`** creates PAYROLL subledger entries per employee reflecting the
  cost allocation to projects/cost centers.
- **Employee-level balance tracking:** The PAYROLL subledger tracks accrued wages,
  withholdings, and net pay per employee. Useful for year-end W-2 reconciliation.
- **`record_benefits_deduction`** and **`record_employer_contribution`** create PAYROLL
  subledger entries per employee.

---

### M-13: WIP Module Deepening (GAP-19)

**Current:** 639 LOC, 10 methods. Has material issue, labor charge, overhead, job completion, scrap, rework, variances.
**Gap:** No production order costing, no overhead application rates, no production completion transfer.

#### New Methods

| Method | Event Type | Description | Engine |
|--------|-----------|-------------|--------|
| `calculate_production_cost` | N/A (pure calc) | Full production order cost | ValuationLayer |
| `apply_overhead_rate` | `wip.overhead_rate_applied` | Apply indirect rates | AllocationCascade |
| `transfer_to_finished_goods` | `wip.transferred_to_fg` | WIP to FG transfer | None |
| `record_byproduct` | `wip.byproduct_recorded` | Byproduct value recognition | None |
| `calculate_unit_cost` | N/A (pure calc) | Per-unit cost by component | None |

#### Subledger Integration

- **SubledgerType:** `WIP` (existing). Entity: Job/Production Order. `entity_id_field: job_id`
- **`transfer_to_finished_goods`** closes out the WIP subledger entries for the completed
  job (credit WIP subledger) and creates INVENTORY subledger entries for the finished goods
  (debit INVENTORY subledger). Both subledger types are updated atomically.
- **`calculate_production_cost`** should query WIP subledger balance per job via
  `SubledgerSelector.get_aggregate_balance(job_id, SubledgerType.WIP)` for accumulated
  costs to date.
- **`apply_overhead_rate`** creates WIP subledger entries for the overhead allocated to
  each job.

---

### M-14: Tax Module Deepening (GAP-20)

**Current:** 308 LOC, 4 methods. Has tax obligation, payment, VAT settlement, tax calculation.
**Gap:** No deferred tax (ASC 740), no multi-jurisdiction, no tax provision, no return data export.

#### New Methods

| Method | Event Type | Description | Engine |
|--------|-----------|-------------|--------|
| `calculate_deferred_tax` | N/A (pure calc) | Book vs tax temporary differences | None |
| `record_deferred_tax_asset` | `tax.dta_recorded` | DTA recognition | None |
| `record_deferred_tax_liability` | `tax.dtl_recorded` | DTL recognition | None |
| `calculate_provision` | N/A (pure calc) | Current + deferred = total | None |
| `record_multi_jurisdiction_tax` | `tax.jurisdiction_obligation` | State/local tax | TaxCalculator |
| `export_tax_return_data` | N/A (output) | Data extract for tax software | None |
| `record_tax_adjustment` | `tax.adjustment` | Prior period adjustment | CorrectionEngine |

#### New Models

- `TemporaryDifference` — book vs tax basis per account
- `DeferredTaxAsset` — DTA with valuation allowance
- `DeferredTaxLiability` — DTL by source
- `TaxProvision` — current + deferred components
- `Jurisdiction` — tax jurisdiction with rates and rules

#### Subledger Integration

- **No dedicated SubledgerType.** Tax postings affect the GL directly (tax payable/receivable
  accounts). Tax obligations are tracked at the account level, not at an entity level
  requiring subledger reconciliation.
- **Deferred tax** (DTA/DTL) postings are GL-only. No subledger entries needed.
- Tax amounts that are part of AP/AR transactions are captured in those modules' subledger
  entries (the tax portion flows through AP/AR subledger as part of the invoice total).

---

### M-15: Procurement Module Deepening (GAP-21)

**Current:** 463 LOC, 6 methods. Has PO creation, commitment, goods receipt, price variance.
**Gap:** No requisition-to-PO, no PO amendment, no goods receipt integration, no supplier evaluation.

#### New Methods

| Method | Event Type | Description | Engine |
|--------|-----------|-------------|--------|
| `create_requisition` | `procurement.requisition_created` | Purchase requisition | None |
| `convert_requisition_to_po` | `procurement.requisition_converted` | Req-to-PO conversion | None |
| `amend_purchase_order` | `procurement.po_amended` | Versioned PO modification | None |
| `match_receipt_to_po` | `procurement.receipt_matched` | Link receipt to PO lines | MatchingEngine |
| `evaluate_supplier` | N/A (no posting) | Performance scoring | None |
| `record_quantity_variance` | `procurement.quantity_variance` | Qty received vs ordered | VarianceCalculator |

#### New Models

- `Requisition` — purchase request with justification
- `PurchaseOrderVersion` — versioned PO with amendment history
- `ReceiptMatch` — receipt-to-PO line matching result
- `SupplierScore` — delivery, quality, price performance metrics

#### Subledger Integration

- **SubledgerType:** `AP` (shared with AP module). Entity: Vendor. `entity_id_field: vendor_id`
- Procurement events that create AP obligations (PO commitment, goods receipt) will
  create AP subledger entries via the shared AP subledger infrastructure.
- **`match_receipt_to_po`** should update reconciliation status on matched AP subledger
  entries (PO commitment → receipt matched).

---

### M-16: Expense Module Deepening (GAP-22)

**Current:** 629 LOC, 9 methods. Has expense, report, allocation, reimbursement, card, advance.
**Gap:** No policy enforcement, no card import, no mileage, no per-diem.

#### New Methods

| Method | Event Type | Description | Engine |
|--------|-----------|-------------|--------|
| `validate_against_policy` | N/A (validation) | Per-diem/category cap enforcement | None |
| `import_card_transactions` | N/A (ingestion) | Parse card transaction file | None |
| `calculate_mileage` | N/A (pure calc) | IRS rate-based reimbursement | None |
| `calculate_per_diem` | N/A (pure calc) | GSA/custom per-diem rates | None |
| `record_policy_violation` | N/A (no posting) | Flag and route for review | None |
| `record_receipt_match` | `expense.receipt_matched` | Match receipt to transaction | MatchingEngine |

#### New Models

- `ExpensePolicy` — category limits, per-diem rates, mileage rates
- `PolicyViolation` — violation record with exception request
- `MileageRate` — IRS rate by year/effective date
- `PerDiemRate` — rate by location/date

#### Subledger Integration

- **No dedicated SubledgerType.** Expense postings create AP subledger entries (employee
  reimbursement is an AP obligation) or direct GL postings (corporate card payments).
- Employee reimbursement obligations use `entity_id_field: employee_id` on the AP
  subledger (employees are treated as a special vendor class for reimbursement).

---

### M-17: Contracts Module Deepening (GAP-23)

**Current:** 518 LOC, 9 methods. Has cost incurrence, billing, funding, indirect allocation, rate adjustment, fee accrual, ICE, cascade.
**Gap:** No DCAA compliance workflow, no contract modification, no subcontract management.

#### New Methods

| Method | Event Type | Description | Engine |
|--------|-----------|-------------|--------|
| `record_contract_modification` | `contract.modified` | Scope/funding change | None |
| `record_subcontract_cost` | `contract.subcontract_cost` | Sub flow-down cost | None |
| `record_equitable_adjustment` | `contract.equitable_adjustment` | REA processing | None |
| `run_dcaa_audit_prep` | N/A (no posting) | Pre-audit compliance check | ICEEngine |
| `generate_sf1034` | N/A (output) | Public voucher generation | None |
| `record_cost_disallowance` | `contract.cost_disallowed` | DCAA disallowance | CorrectionEngine |

#### New Models

- `ContractModification` — modification with effective date and scope
- `Subcontract` — subcontract agreement with flow-down terms
- `AuditFinding` — DCAA finding with response tracking
- `CostDisallowance` — disallowed cost with reason and remediation

#### Subledger Integration

- **SubledgerType:** Contracts use multiple subledgers depending on the transaction:
  - Cost incurrence → `WIP` subledger (entity: contract/job, `entity_id_field: contract_id`)
  - Billing → `AR` subledger (entity: customer/government agency, `entity_id_field: customer_id`)
  - Subcontract costs → `AP` subledger (entity: subcontractor, `entity_id_field: vendor_id`)
- **`run_dcaa_audit_prep`** can query subledger balances per contract to verify cost
  segregation (allowable vs unallowable) at the contract entity level.
- **`record_cost_disallowance`** creates reversal subledger entries on the WIP subledger
  for the disallowed amount on the affected contract.

---

## Phase 4: P2 New Modules

### M-18: Project Accounting Module — GAP-09

**Current:** Nothing. WIP handles production, Contracts handles government.
**Depends on:** Budget module (M-7)

#### Files to Create

```
finance_modules/project/
    __init__.py
    config.py        # EVM thresholds, billing rules, rev rec methods
    models.py        # Project, WBSElement, ProjectBudget, Milestone
    profiles.py      # Project accounting policies
    service.py       # Project lifecycle, EVM, billing, cost tracking
    workflows.py     # Project lifecycle state machine
    evm.py           # EVM calculations (pure functions)
```

#### Event Types

| Event Type | Description | Engine Used |
|------------|-------------|-------------|
| `project.cost_recorded` | Record cost to WBS element | None |
| `project.billing_milestone` | Bill at milestone | BillingEngine |
| `project.billing_time_materials` | T&M billing | BillingEngine |
| `project.revenue_recognized` | % completion rev rec | None |
| `project.budget_revised` | Project budget revision | None |
| `project.phase_completed` | Phase completion | None |

#### Key Models (`models.py`)

- `Project` — project header with type, dates, status
- `WBSElement` — work breakdown structure node (hierarchical)
- `ProjectBudget` — budget by WBS element and cost type
- `Milestone` — project milestone with billing trigger
- `EVMSnapshot` — point-in-time EVM metrics

#### EVM Calculations (`evm.py` — pure functions)

- `calculate_bcws(planned_value, schedule)` — Budgeted Cost of Work Scheduled
- `calculate_bcwp(earned_value, progress)` — Budgeted Cost of Work Performed
- `calculate_acwp(actual_costs)` — Actual Cost of Work Performed
- `calculate_cpi(bcwp, acwp)` — Cost Performance Index
- `calculate_spi(bcwp, bcws)` — Schedule Performance Index
- `calculate_eac(budget, cpi)` — Estimate at Completion
- `calculate_etc(eac, acwp)` — Estimate to Complete
- `calculate_vac(budget, eac)` — Variance at Completion

#### Subledger Integration

- **New SubledgerType:** `PROJECT` (must be added to enum). Entity: WBS Element.
  `entity_id_field: wbs_element_id`
- **Requires:** New entry in `subledger_contracts.yaml` (see Subledger Integration section),
  new `ProjectSubledgerService` in `finance_services/subledger_project.py`, new
  `PROJECT_WIP_CONTROL` account role in COA.
- **`enforce_on_post: false`, `enforce_on_close: true`** — project subledger uses
  period-end enforcement (not real-time) because project cost accruals may temporarily
  diverge from GL during the period.
- **`project.cost_recorded`** creates PROJECT subledger entries per WBS element, enabling
  cost tracking at the work breakdown structure level.
- **EVM calculations** should query PROJECT subledger balances per WBS element via
  `SubledgerSelector.get_aggregate_balance(wbs_element_id, SubledgerType.PROJECT)` for
  ACWP (Actual Cost of Work Performed).
- **`project.revenue_recognized`** creates both PROJECT subledger entries (revenue side)
  and potentially REVENUE subledger entries (if ASC 606 contract tracking is also needed).
- **Period close:** PROJECT subledger must close before GL (added to close ordering).
- **Relationship to WIP:** Project accounting uses its own `PROJECT` subledger, distinct
  from the `WIP` subledger used by production/manufacturing. A project may have
  manufacturing sub-tasks that use the WIP subledger.

---

### M-19: Credit Loss Module — GAP-10 (ASC 326 / CECL)

**Current:** Nothing. AR has `record_bad_debt_provision()` for simple provisioning.

#### Files to Create

```
finance_modules/credit_loss/
    __init__.py
    config.py        # Loss rate methods, PD/LGD models, vintage parameters
    models.py        # ECLEstimate, VintageAnalysis, LossRate, Provision
    profiles.py      # ECL posting policies
    service.py       # ECL orchestration
    workflows.py     # ECL review/approval state machine
    calculations.py  # Statistical calculations (pure functions)
```

#### Event Types

| Event Type | Description | Engine Used |
|------------|-------------|-------------|
| `credit_loss.provision_recorded` | Book allowance for credit losses | None |
| `credit_loss.provision_adjusted` | Adjust allowance | None |
| `credit_loss.write_off_recorded` | Charge off against allowance | None |
| `credit_loss.recovery_recorded` | Recovery of previously written off | None |

#### Key Models (`models.py`)

- `ECLEstimate` — expected credit loss estimate per portfolio segment
- `VintageAnalysis` — loss rates by origination cohort
- `LossRate` — historical loss rate by segment/vintage
- `CreditPortfolio` — grouping of receivables by risk characteristics
- `ForwardLookingAdjustment` — macro-economic adjustment factors

#### Calculations (`calculations.py` — pure functions)

- `calculate_ecl_loss_rate(historical_losses, pool_balance, forward_adjustment)` — weighted loss rate
- `calculate_ecl_pd_lgd(probability_of_default, loss_given_default, exposure)` — PD/LGD model
- `calculate_vintage_loss_curve(cohort_data, aging_periods)` — vintage analysis
- `apply_forward_looking_adjustment(base_rate, macro_factors)` — CECL adjustment
- `calculate_provision_change(current_estimate, prior_estimate)` — provision delta

#### Subledger Integration

- **No dedicated SubledgerType.** Credit loss provisions adjust the AR subledger.
- **`credit_loss.provision_recorded`** creates AR subledger entries (credit: allowance
  against customer receivable) so that the AR subledger balance per customer reflects
  the net realizable value (gross receivable - allowance).
- **`credit_loss.write_off_recorded`** updates AR subledger reconciliation status for the
  written-off customer entries (open → written_off).
- **`credit_loss.recovery_recorded`** creates AR subledger entries reversing part of the
  prior write-off.
- **ECL estimation** should use `SubledgerSelector.get_open_items()` per customer/portfolio
  segment to get the exposure amounts for loss rate calculation.

---

## Phase Summary

| Phase | Items | Module Count | Scope |
|-------|-------|-------------|-------|
| **Phase 1** | M-1 through M-4 | 4 (deepen existing) | P1 existing: GL, AP, AR, Multi-Currency |
| **Phase 2** | M-5 through M-8 | 4 (create new) | P1 new: Revenue, Lease, Budget, Intercompany |
| **Phase 3** | M-9 through M-17 | 9 (deepen existing) | P2 existing: Cash, Inventory, Assets, Payroll, WIP, Tax, Procurement, Expense, Contracts |
| **Phase 4** | M-18 through M-19 | 2 (create new) | P2 new: Project, Credit Loss |
| **Total** | **19 module items** | **6 new + 13 deepened** | |

---

## Dependencies Between Modules

```
M-18 (Project) ───depends on──→ M-7 (Budget)
M-4 (Multi-Currency) ──────────→ GL module (M-1)
M-8 (Intercompany) ────────────→ Multi-Currency (M-4) for IC FX
```

Modules with workflow runtime (GAP-02) dependency:
- M-1 GL (journal approval, period close automation)
- M-2 AP (payment run approval)
- M-3 AR (dunning workflow)
- M-15 Procurement (requisition approval)
- M-16 Expense (expense approval)
- M-17 Contracts (DCAA audit workflow)

These modules can be built now with the methods/posting logic, but approval workflow
features should be stubbed until GAP-02 (workflow runtime) is delivered.

### Subledger Dependencies

All module work depends on subledger infrastructure from CURRENT_PLAN.md:

| SL Phase | Delivers | Required By |
|----------|----------|-------------|
| SL-Phase 1 | Unified `SubledgerType` enum, domain types | All modules (for type references) |
| SL-Phase 2 | `SubledgerEntryModel` ORM | All modules (subledger entries) |
| SL-Phase 3 | `SubledgerSelector` | M-1, M-2, M-3, M-9, M-10, M-11, M-5, M-6, M-18 (queries) |
| SL-Phase 4 | Concrete `SubledgerService` implementations | All posting modules |
| SL-Phase 5 | G9 enforcement in JournalWriter | All modules with `enforce_on_post: true` |
| SL-Phase 6 | Subledger entry creation in posting pipeline | All posting modules (automatic) |
| SL-Phase 7 | `subledger_contracts.yaml` config | New SubledgerTypes (REVENUE, LEASE, PROJECT) |
| SL-Phase 8 | Period close enforcement | M-1 GL (close ordering) |

**New SubledgerType registration** (REVENUE, LEASE, PROJECT) can happen during SL-Phase 7
by adding entries to `subledger_contracts.yaml`. The concrete service implementations
(SL-Phase 4 pattern) should be created alongside each new module.

**Recommended execution order:**
1. Complete SL-Phase 1–10 (subledger infrastructure)
2. Fix 128 test failures (CURRENT_PLAN Item 4)
3. Phase 1 of this plan (M-1 through M-4) — deepen existing P1 modules
4. Phase 2 of this plan (M-5 through M-8) — create new P1 modules (adds new SubledgerTypes)
5. Phase 3 of this plan (M-9 through M-17) — deepen existing P2 modules
6. Phase 4 of this plan (M-18 through M-19) — create new P2 modules

---

## Test Strategy

Each module deepening or creation requires:

1. **Unit tests** for all pure helper functions (calculations, EVM, PV, etc.)
2. **Service integration tests** for each new method (post event, verify journal + links)
3. **Profile dispatch tests** — verify where-clause selects correct profile
4. **Guard tests** — verify business rule guards reject invalid events
5. **Engine integration tests** — verify engine results flow through posting pipeline
6. **Economic link tests** — verify document relationships are tracked
7. **Architecture tests** — verify no import boundary violations
8. **Subledger entry tests** — for every posting method, verify that the correct
   `SubledgerEntryModel` records are created alongside journal entries:
   - Correct `subledger_type` (AP, AR, INVENTORY, etc.)
   - Correct `entity_id` (vendor, customer, item, etc.)
   - Correct `amount` and `currency`
   - Correct `journal_entry_id` and `journal_line_id` linkage
   - Correct `reconciliation_status` (initially OPEN)
9. **Subledger reconciliation tests** — for methods that match/reconcile documents,
   verify that `SubledgerEntryModel.reconciliation_status` is updated and
   `SubledgerReconciliationModel` records are created
10. **G9 enforcement tests** — for modules with `enforce_on_post: true`, verify that
    posting fails if subledger aggregate would diverge from GL control account beyond
    tolerance
11. **Subledger period close tests** — verify that subledger periods can be closed
    independently and that GL close is blocked until all subledger periods are closed

Test file naming: `tests/modules/test_MODULE_NAME.py` (extend existing or create new)

---

## File Count Estimate

| Category | New Files | Modified Files |
|----------|-----------|----------------|
| Phase 1 (deepen 4) | ~4 helpers | ~12 service/profile/model/yaml |
| Phase 2 (create 4) | ~28 (7 per module) + 3 SL services + 1 yaml | ~4 yaml configs + enum + COA |
| Phase 3 (deepen 9) | ~4 helpers | ~27 service/profile/model/yaml |
| Phase 4 (create 2) | ~14 (7 per module) + 1 SL service + 1 yaml | ~2 yaml configs + enum |
| Tests | ~19 test files | ~6 existing test files |
| Subledger infra | 4 new SL services, 1 yaml update | SubledgerType enum, subledger_contracts.yaml, COA |
| **Total** | **~77 new** | **~57 modified** |

---

## Decisions Log

*(To be updated as work progresses)*

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-01-30 | Plan created | Per GAP_ANALYSIS.md feedback |
| | Workflow-dependent features stubbed | GAP-02 not yet delivered |
| | No new engines needed | All computation covered by existing 12 engines |
| | `helpers.py` / `calculations.py` for pure math | Module-level pure functions, not engine-worthy |
| 2026-01-30 | Subledger integration added | CURRENT_PLAN SL-Phase 1–10 delivers subledger infrastructure that all modules must use |
| | 3 new SubledgerTypes: REVENUE, LEASE, PROJECT | Revenue contracts, lease agreements, and WBS elements need entity-level balance tracking and G9 enforcement |
| | Budget and Credit Loss: no dedicated SubledgerType | Budget is memo (not GL balance); Credit Loss adjusts AR subledger |
| | Tax and Expense: no dedicated SubledgerType | Tax is GL-only; Expense uses AP subledger for reimbursement |
| | Procurement shares AP SubledgerType | PO/receipt obligations are vendor-level AP balances |
| | Contracts use multiple SubledgerTypes | Cost → WIP, Billing → AR, Subcontracts → AP |
| | Period close ordering extended | INVENTORY → WIP → AR → AP → ASSETS → PAYROLL → REVENUE → LEASE → PROJECT → GL |
| | Reconciliation methods must update SL status | Every match/apply/reconcile must update SubledgerEntryModel.reconciliation_status |
| | Entity-level queries via SubledgerSelector | Replaces journal-line derivation for aging, valuation, balance reports |
