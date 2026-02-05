

General ledger

Core ledger structure

* Chart of accounts (multi-segment)
* Account hierarchies
* Account types (asset, liability, equity, revenue, expense)
* Natural balance enforcement
* Posting periods and fiscal calendars
* Multi-book accounting

Journal management

* Manual journal entry
* Recurring journals
* Reversing journals
* Statistical journals
* Accrual journals
* Adjusting entries

Posting controls

* Balanced journal enforcement
* Account posting restrictions
* Period locks (soft / hard close)
* Future period posting controls
* Approval workflows

Ledger operations

* Trial balance
* General ledger inquiry
* Journal drill-down
* Source transaction traceability
* Ledger export

Multi-entity and consolidation

Entity structure

* Legal entities
* Business units
* Divisions / segments
* Intercompany relationships

Consolidation

* Multi-entity rollups
* Minority interest
* Equity eliminations
* Investment eliminations
* Consolidation adjustments

Intercompany

* Intercompany AR/AP
* Due-to / due-from tracking
* Automated eliminations
* Transfer pricing rules

Currency

* Multi-currency ledger
* FX rate tables
* Historical vs average rates
* Translation adjustments
* CTA (cumulative translation adjustment)

Accounts receivable

Customer management

* Customer master
* Credit limits
* Payment terms
* Dunning rules

Billing

* Invoice generation
* Credit memos
* Debit memos
* Pro-forma invoices
* Milestone billing
* Progress billing

Cash application

* Payment receipts
* Lockbox import
* Auto-cash matching
* Short pay handling
* Write-offs

Collections

* Aging reports
* Dunning letters
* Promise-to-pay tracking
* Dispute management

Revenue postings

* AR → revenue journals
* Deferred revenue integration

Accounts payable

Vendor management

* Vendor master
* 1099 tracking
* Payment terms
* Banking details

Invoice processing

* Invoice entry
* 2-way / 3-way match
* Non-PO invoices
* Recurring invoices

Payments

* Check runs
* ACH / wire payments
* Payment batching
* Positive pay files

Expense allocation

* Department coding
* Project coding
* Program attribution

AP reporting

* AP aging
* Cash requirements forecast

Cash and treasury

Bank management

* Bank accounts
* Signatories
* Bank fees

Cash transactions

* Deposits
* Withdrawals
* Transfers

Reconciliation

* Bank rec matching
* Statement import
* Exception handling

Treasury

* Cash positioning
* Liquidity forecasting
* Debt tracking

Revenue recognition

Contract management

* Contract master
* Performance obligations
* SSP allocation

Recognition models

* Point-in-time
* Over-time
* Milestone
* Usage-based

Deferrals

* Deferred revenue schedules
* Amortization waterfalls

Modifications

* Contract amendments
* Reallocation logic

Compliance

* ASC 606 / IFRS 15 support

Cost accounting

Cost methods

* Standard costing
* Actual costing
* FIFO / LIFO / weighted average

Inventory valuation

* Material cost layers
* Labor absorption
* Overhead absorption

Variance analysis

* Purchase price variance
* Labor variance
* Overhead variance
* Yield variance

Burdening

* Overhead rates
* Machine rates
* Department rates

WIP accounting

* WIP capitalization
* Stage valuation
* Completion recognition

Manufacturing finance

Production costing

* Work orders / manufacturing orders
* Routing cost capture
* Labor capture
* Machine time capture

BOM costing

* Multi-level BOM rollups
* Revision costing
* What-if cost simulations

Program costing

* Contract cost tracking
* CLIN / SLIN tracking
* Government program attribution

Inventory finance

Inventory structure

* Locations
* Lots / serials
* Bins

Transactions

* Receipts
* Issues
* Transfers
* Adjustments

Financial impacts

* Inventory → COGS
* Scrap accounting
* Obsolescence reserves

Reconciliation

* Inventory subledger → GL tie-out

Fixed assets

Asset lifecycle

* Asset creation
* Capitalization rules
* Asset categories

Depreciation

* Straight-line
* Declining balance
* Units of production

Books

* GAAP book
* Tax book
* IFRS book

Events

* Transfers
* Disposals
* Impairments

Projects and job costing

Project structure

* WBS hierarchies
* Phases / tasks

Cost capture

* Labor
* Materials
* Expenses

Revenue linkage

* Project billing
* T&M billing

Profitability

* Project P&L
* Estimate vs actual

Procurement finance

Purchasing

* Purchase requisitions
* Purchase orders
* Blanket POs

Matching

* 2-way match
* 3-way match

Commitments

* Encumbrance accounting
* Budget pre-commitment

Financial planning and analysis

Budgeting

* Annual budgets
* Rolling forecasts

Planning

* Headcount planning
* Expense planning

Reporting

* Budget vs actual
* Variance analysis

Close management

Close orchestration

* Close calendars
* Task assignments

Automation

* Auto-accruals
* Recurring entries

Reconciliation

* Account recs
* Subledger tie-outs

Intercompany close

* Eliminations
* Balancing

Reporting and analytics

Financial statements

* Balance sheet
* Income statement
* Cash flow

Management reporting

* Segment reporting
* Program reporting

Ad hoc reporting

* Query builder
* Saved reports

Dashboards

* KPI dashboards
* Cash dashboards

Compliance and regulatory

Tax

* Sales tax
* VAT
* Use tax

Statutory reporting

* Local GAAP adjustments

Government compliance

* DCAA cost tracking
* FAR allowability
* CAS allocation

Audit and controls

Audit trail

* Journal lineage
* Field change logs

Approvals

* Segregation of duties
* Approval matrices

Controls

* Posting controls
* Access logs

Evidence

* Document attachments
* Policy references

Integration and data

Banking

* Bank feeds
* Payment files

Payroll

* Payroll journals
* Benefits allocation

Expense systems

* Expense imports

Data management

* Data imports
* Data exports
* API access

System infrastructure

Security

* Role-based access
* Field-level permissions

Governance

* Policy versioning
* Posting rule management

Performance

* Batch posting
* Real-time posting

Extensibility

* Custom fields
* Custom dimensions

Checklist usage guidance

Use this list across three tracking dimensions:

1. Feature existence
2. Behavioral completeness
3. Compliance readiness

Parity is achieved only when all three are satisfied.

If useful, this can be expanded into:

* MVP vs parity scoping
* Build vs buy analysis
* Sequenced roadmap by implementation difficulty

---

## Gap analysis vs current implementation (2026-02)

This section maps the checklist above to the codebase and calls out **remaining gaps** (missing, partial, or behavioral). Use it to prioritize roadmap and scope MVP vs full parity.

### General ledger

| Checklist item | Status | Notes |
|----------------|--------|-------|
| Chart of accounts (multi-segment) | **Partial** | COA exists with hierarchy (`parent_id`), account types, natural balance. **Gap:** No composite/multi-segment account codes (e.g. company.segment.department.account); dimensions on lines provide segmentation, not COA structure. |
| Account hierarchies | Done | Account model has `parent_id`; GL has `AccountHierarchy`; role bindings in config. |
| Account types / natural balance | Done | AccountType enum; NormalBalance; R4 enforced. |
| Posting periods / fiscal calendars | Done | FiscalPeriod, PeriodService, R12/R13. |
| Multi-book accounting | Done | Ledger_id, dual-ledger profiles, R21. |
| Manual journal entry | Done | ModulePostingService, GL profiles. |
| Recurring journals | Done | RecurringEntryModel, `generate_recurring_entry`, batch task `gl.recurring_entries`. |
| Reversing journals | Done | ReversalService, correction via reversal (R10). |
| Statistical journals | **Gap** | No first-class statistical (non-monetary) journal type or reporting. |
| Accrual journals / adjusting entries | **Partial** | Adjusting entries supported via effective date and period; no dedicated “adjusting period” workflow or accrual template beyond recurring. GL config has `adjusting_entries_period`. |
| Balanced enforcement / period locks / approval | Done | R4, R12/R13, approval engine, workflow guards. |
| Future period posting controls | **Partial** | Period status governs; explicit “allow future period” policy not centralized. |
| Trial balance / GL inquiry / drill-down / traceability | Done | Selectors, trial balance, trace bundle, R21 snapshot. |
| Ledger export | **Partial** | Scripts and demos; no canonical “export GL” API or report. |
| Legal entities / business units / divisions | **Partial** | Legal entity in config; dimensions (e.g. cost_center, department) on lines. **Gap:** No first-class BU/division entity model or hierarchy. |
| Multi-entity rollups | **Partial** | IntercompanyService.consolidate() sums entity balances and elimination amount; no persisted rollup ledger or automated posting of consolidation entries. |
| Minority interest / investment eliminations | **Gap** | ConsolidationResult has elimination_amount; no minority interest or equity/investment elimination logic. |
| Intercompany AR/AP / due-to due-from / eliminations | Done | IC module, reconcile_ic_balances, IC profiles; consolidation config (eliminate_intercompany). |
| Transfer pricing rules | **Partial** | Intercompany has markup/transfer; no full transfer-pricing rule engine or documentation. |
| Multi-currency / FX / translation / CTA | Done | Money, multi-currency trial balance, translate_balances, record_cta (ASC 830), revaluation. |

### Accounts receivable

| Checklist item | Status | Notes |
|----------------|--------|-------|
| Customer master / credit limits / payment terms | Done | Party, AR config, credit limits (blocked enforcement). |
| Dunning rules | Done | DunningLevel config, generate_dunning_letters, ARDunningHistoryModel. |
| Invoice / credit memo / debit memo | Done | AR invoicing, credit memo workflows. |
| Pro-forma invoices | **Gap** | No pro-forma (non-posting) invoice type. |
| Milestone / progress billing | **Partial** | Contracts/billing engine (CPFF, T&M, etc.); AR integration for milestone billing may be partial. |
| Payment receipts / auto-cash matching | Done | apply_payment, MatchingEngine, observability hooks. |
| Lockbox import | **Gap** | No lockbox file format or lockbox-specific import. |
| Short pay / write-offs | **Partial** | Write-offs (AR small-balance, credit_loss); short pay handling not explicit. |
| Aging reports | Done | AgingCalculator, calculate_aging (AR/AP). |
| Dunning letters / promise-to-pay / dispute | **Partial** | Dunning letters generated; promise-to-pay and dispute management are minimal (e.g. dispute flag on contracts; no dedicated AR dispute workflow). |
| AR → revenue / deferred revenue | Done | Profiles, contracts/revenue. |

### Accounts payable

| Checklist item | Status | Notes |
|----------------|--------|-------|
| Vendor master / 1099 / payment terms / banking | Done | Party, AP config (threshold_1099), VendorProfileModel (is_1099_eligible). |
| Invoice entry / 2-way / 3-way match / non-PO / recurring | Done | ReconciliationManager.create_three_way_match, MatchingEngine; non-PO and recurring via workflows. |
| Check runs / ACH-wire / payment batching / positive pay | **Partial** | Cash module: payment file generation (NACHA); **Gap:** No positive pay file output or check-printing integration. |
| Department / project / program attribution | Done | Dimensions on lines (project_id, wbs_code, etc.). |

### Cash and treasury

| Checklist item | Status | Notes |
|----------------|--------|-------|
| Bank accounts / signatories / fees | Done | Cash module, bank fee workflows. |
| Deposits / withdrawals / transfers | Done | record_receipt, record_disbursement, record_transfer. |
| Bank rec matching / statement import / exception | Done | ReconciliationManager, find_bank_match_suggestions, match_bank_transaction, auto_reconcile; MT940/BAI2/CAMT053. |
| Cash positioning / liquidity forecasting / debt tracking | **Gap** | No treasury cash positioning, liquidity forecast, or debt-tracking module. |

### Revenue recognition

| Checklist item | Status | Notes |
|----------------|--------|-------|
| Contract master / performance obligations / SSP | Done | Contracts module, billing engine. |
| Point-in-time / over-time / milestone / usage-based | **Partial** | Contract billing types (CPFF, T&M, FFP); over-time and usage-based recognition logic may be partial. |
| Deferred revenue / amortization | Done | Contracts, deferral profiles. |
| Contract amendments / reallocation | **Partial** | Contract amendments; reallocation logic may be limited. |
| ASC 606 / IFRS 15 | **Partial** | Policy-driven; no explicit ASC 606 disclosure or checklist. |

### Cost accounting / manufacturing / inventory

| Checklist item | Status | Notes |
|----------------|--------|-------|
| Standard / actual / FIFO / LIFO / weighted average | Done | ValuationLayer (FIFO, LIFO, standard); standard cost variance. **Note:** Weighted average referenced in tax/credit_loss; not as a primary inventory cost method in valuation. |
| Cost layers / labor and overhead absorption | Done | Cost lots, WIP, variance engine. |
| PPV / labor / overhead / yield variance | Done | VarianceCalculator, allocation cascade. |
| WIP capitalization / stage / completion | Done | WIP module, manufacturing orders. |
| Work orders / manufacturing orders / routing / BOM | Done | WIP (manufacturing orders), project WBS; BOM costing via inventory/WIP. |
| CLIN/SLIN / government attribution | Done | Contracts (CLINs), DCAA. |
| Inventory locations / lots / serials / bins | **Partial** | Locations, lots; serials/bins may be minimal. |
| Receipts / issues / transfers / adjustments | Done | Inventory module. |
| COGS / scrap / obsolescence reserves | **Partial** | COGS and scrap (issue as scrap); obsolescence reserve logic not fully built out. |
| Subledger → GL tie-out | Done | Lifecycle reconciliation, subledger services. |

### Fixed assets / projects / procurement

| Checklist item | Status | Notes |
|----------------|--------|-------|
| Asset lifecycle / depreciation / books / events | Done | Assets module (SL, declining, UOP), multi-book, transfers/disposals/impairments. |
| WBS / phases-tasks / cost capture / project billing | Done | Project module (WBSElement, phases), project_id/wbs_code dimensions, T&M. |
| Project P&L / estimate vs actual | **Partial** | Cost capture and reporting; formal estimate-vs-actual (EVM) may be partial (EVM in project models). |
| Purchase requisitions / PO / blanket PO / encumbrance | Done | Procurement (requisition, PO, encumbrance, commitment, relief). |

### Close / reporting / compliance / audit

| Checklist item | Status | Notes |
|----------------|--------|-------|
| Close calendars / task assignments / auto-accruals | Done | PeriodCloseOrchestrator, close_order, batch recurring. |
| Account recs / subledger tie-outs / IC close | Done | reconcile_account, lifecycle recon, bank recon, IC reconcile_ic_balances. |
| Balance sheet / income statement / cash flow | Done | Reporting module, demo_reports, scripts. |
| Segment reporting | Done | build_segment_report, dimension-based segment_report. |
| Query builder / saved reports / dashboards | **Gap** | No generic query builder, saved report definitions, or KPI/cash dashboards. |
| Sales tax / VAT / use tax | **Partial** | Tax engine; statutory VAT/use-tax reporting may be partial. |
| Statutory / local GAAP | **Partial** | Config sets (e.g. US-GAAP-2026-*); local GAAP adjustments not a dedicated feature. |
| DCAA / FAR / CAS | Done | DCAA streams (timesheet, expense, rate compliance). |
| Audit trail / approvals / controls / evidence | Done | Audit chain, ApprovalService, workflow guards; **Gap:** No document attachments or policy-reference storage. |

### Integration / infrastructure

| Checklist item | Status | Notes |
|----------------|--------|-------|
| Bank feeds / payment files | **Partial** | Statement import (MT940, etc.); payment file generation (NACHA). **Gap:** No live bank feed connector. |
| Payroll journals / benefits | Done | Payroll module, journals via posting. |
| Expense imports | Done | Ingestion, expense module. |
| Data imports / exports / API | **Partial** | Ingestion pipeline, scripts; no formal REST API or export API. |
| Role-based access / field-level permissions | **Gap** | Actor_id and approval roles; no full RBAC or field-level security. |
| Policy versioning / posting rules | Done | Config, compiled policies, R21 snapshot. |
| Batch / real-time posting | Done | Batch framework, single-event posting. |
| Custom fields / dimensions | Done | Dimensions on lines, config-driven. |

### Summary: highest-impact gaps

1. **RBAC / security** — No role-based access control or field-level permissions (actor_id only).
2. **Treasury** — No cash positioning, liquidity forecasting, or debt tracking.
3. **Statistical journals** — No non-monetary journal type.
4. **Consolidation** — No minority interest or investment eliminations; rollups are computational only (no consolidated ledger posting).
5. **Lockbox / positive pay** — No lockbox import or positive pay file generation.
6. **Pro-forma invoices** — No non-posting invoice type.
7. **Query builder / saved reports / dashboards** — No ad hoc reporting or dashboard layer.
8. **Document attachments / policy references** — No evidence store for attachments or policy refs.
9. **Multi-segment COA** — Segmentation via dimensions, not composite account codes.
10. **Bank feed connector** — No live bank feed integration (file-based import only).
