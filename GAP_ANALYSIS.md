# Comprehensive ERP Gap Analysis

**Date:** 2026-01-29
**Scope:** Full system assessment against world-class ERP requirements
**Benchmark:** SAP S/4HANA, Oracle Cloud Financials, Workday Financials, Deltek Costpoint (GovCon)

---

## Executive Summary

This system is a **genuinely exceptional accounting kernel** with architecture that surpasses the technical foundations of most commercial ERPs. The event-sourced, append-only, pure-functional-core design with defense-in-depth immutability is world-class. The DCAA/government contracting support (ICE schedules, CPFF/T&M/FFP billing, indirect rate cascades) is competitive with Deltek Costpoint.

**The kernel and engine layers are essentially complete.** The 12 existing engines (variance, allocation, matching, aging, tax, valuation, reconciliation, correction, billing, ICE, allocation cascade, subledger) provide the computational foundations needed by all planned modules. No new engines are required.

The remaining work falls into two categories:

1. **Modules (~85% of remaining work):** New and deepened `finance_modules/` using existing kernel primitives, existing engines, and new YAML policies. Each new capability is expressed as new event types + new policies + module-level service orchestration.
2. **Infrastructure (~15% of remaining work):** Three platform concerns that cannot be modules: API layer, workflow runtime, and job scheduling.

**Current State:** ~37,000 LOC across 5 layers, 12 modules, 12 engines, 24 invariants, 1000+ tests.

**Maturity Assessment:**

| Layer | Maturity | Remaining Work |
|-------|----------|----------------|
| finance_kernel | **Production-grade** | Done. Minor DI refactor in backlog. |
| finance_engines | **Production-grade** | Done. 12 engines cover all needed computation. |
| finance_config | **Production-grade** | Add IFRS policy set when needed. |
| finance_modules | **Scaffold-grade** | Primary work area. Deepen 12 existing + add 7 new. |
| finance_services | **Foundation-grade** | Add workflow_service.py. Existing 4 are sufficient. |
| **Infrastructure** | **Not started** | API layer, auth, job scheduler. |

### Why Modules Are Sufficient

The architecture was designed so that new business capabilities are expressed as:

- **New event types** → new YAML policies in `finance_config/`
- **New module orchestration** → `finance_modules/*/service.py` calling existing kernel services
- **Existing engines** → allocation, matching, aging, tax, variance, valuation already cover the computation
- **Economic links** → document relationships already modeled and extensible
- **Multi-ledger posting** → GL + subledger posting already works

Any computation that seems to require a "new engine" (depreciation formulas, PV calculations, currency translation math, budget variance, EVM metrics) is actually a handful of pure helper functions that belong inside the module itself. They don't warrant standalone engine packages.

---

## Part 1: What You Have (Strengths)

These are genuine competitive advantages over SAP/Oracle:

### 1.1 Architecture (Better Than SAP)
- **Event-sourced, append-only** -- SAP still uses mutable GL tables
- **Pure functional core** -- Domain logic has zero side effects; SAP has I/O scattered everywhere
- **Defense-in-depth immutability** -- ORM listeners + 26 PostgreSQL triggers; SAP relies on application-level only
- **Deterministic replay** -- Can reconstruct any ledger state from events alone (R6); SAP stores balances
- **Typed exceptions with machine-readable codes** (R18) -- SAP uses string matching

### 1.2 Concurrency Model (Better Than Most)
- Row-level locking for idempotency (R8)
- Locked counter row for sequences (R9) -- no MAX(seq)+1 race conditions
- 200-thread posting stress tests
- Period close serialization via SELECT...FOR UPDATE

### 1.3 Government Contracting (Competitive With Deltek)
- DCAA ICE submission engine (Schedules A-J)
- Indirect rate cascade (Fringe -> Overhead -> G&A)
- Contract billing: CPFF, CPIF, CPAF, T&M, LH, FFP, FPI
- Rate adjustment/true-up calculations
- Unallowable cost segregation
- Funding/ceiling enforcement

### 1.4 Document Relationship Model (Novel)
- EconomicLink primitives with typed relationships (FULFILLED_BY, PAID_BY, REVERSED_BY, etc.)
- Acyclic graph enforcement per link type
- Unconsumed value tracking through link graph
- Correction cascade via graph traversal

### 1.5 Configuration System (Better Than SAP IMG)
- YAML-driven accounting policies compiled to frozen runtime artifact
- Guard expression validation via restricted AST
- Configuration fingerprint pinning for integrity
- Lifecycle management (DRAFT -> REVIEWED -> APPROVED -> PUBLISHED)
- Engine parameter contracts with JSON schema validation

### 1.6 Test Infrastructure (Exceptional)
- 1000+ tests across 17 categories
- Adversarial tests (attack vector simulation)
- Concurrency tests (real PostgreSQL, 200 threads)
- Metamorphic tests (post-reverse equivalence)
- Property-based fuzzing (Hypothesis)
- Architecture enforcement tests (import boundary verification)

### 1.7 Engine Coverage (Complete)
The 12 existing engines provide all needed computational primitives:

| Engine | Capabilities | Used By |
|--------|-------------|---------|
| **AllocationEngine** | Pro-rata, FIFO, LIFO, weighted, equal, specific | AP, AR, revenue, budget |
| **MatchingEngine** | 2-way, 3-way, bank, custom scoring | AP, procurement, cash |
| **AgingCalculator** | Configurable buckets, counterparty aggregation | AP, AR, inventory |
| **TaxCalculator** | Sales, VAT, GST, withholding, compound, inclusive/exclusive | Tax, AP, AR, expense |
| **VarianceCalculator** | Price, quantity, FX, standard cost; allocation by weight | Inventory, WIP, budget |
| **ValuationLayer** | FIFO, LIFO, standard, specific, weighted avg; cost lots | Inventory, WIP |
| **ReconciliationManager** | Payment application, 3-way match, bank reconciliation | AP, AR, cash |
| **CorrectionEngine** | Cascade unwind, void, adjust, reclass, period correct | All modules |
| **BillingEngine** | CPFF, CPIF, T&M, LH, FFP, FPI; withholding, funding caps | Contracts, project |
| **ICEEngine** | Schedules A-J; cross-schedule validation | Contracts (DCAA) |
| **AllocationCascade** | Multi-step indirect rate cascade (Fringe→OH→G&A) | Contracts (DCAA) |
| **SubledgerEngine** | Generic subledger entries with GL linkage | All modules |

---

## Part 2: Gaps

### Classification

Each gap is classified as:
- **MODULE** -- Filled by new or deepened `finance_modules/` using existing kernel + engines
- **INFRASTRUCTURE** -- Requires new platform-level capability (API, auth, scheduler)
- **CONFIG** -- Filled by new YAML policy sets in `finance_config/`

### Priority Tiers
- **P0 -- Blocking:** Cannot go to production without these
- **P1 -- Core ERP:** Expected by any customer evaluating an ERP
- **P2 -- Competitive:** Needed to compete with SAP/Oracle

---

### INFRASTRUCTURE GAPS (3 items -- the only non-module work)

#### GAP-01: REST/GraphQL API Layer [P0] [INFRASTRUCTURE]

**Current State:** No external API. System is only accessible via Python imports.

**Why this can't be a module:** It's an external interface layer that sits above all modules.

| Component | Description | Effort |
|-----------|-------------|--------|
| REST API Framework | FastAPI with OpenAPI spec | Medium |
| Authentication | OAuth 2.0 / JWT token-based auth | Medium |
| Authorization (RBAC) | Role-based access control with fine-grained permissions | Large |
| Segregation of Duties | SoD conflict matrix and enforcement | Medium |
| Rate Limiting | Per-tenant, per-endpoint throttling | Small |
| API Versioning | v1/v2 backwards compatibility | Small |
| Webhook Publisher | Event-driven notifications for external systems | Medium |
| Bulk Operations | Batch posting, batch import | Medium |

**Architecture:**
```
finance_api/
    app.py                 # FastAPI application
    auth/                  # Authentication & authorization
        models.py          # User, Role, Permission
        rbac.py            # Permission evaluation
        sod.py             # Segregation of duties matrix
    routes/                # One file per module
    middleware/             # Logging, correlation IDs, error handling
    serializers/           # Pydantic request/response schemas
```

---

#### GAP-02: Workflow Execution Runtime [P0] [INFRASTRUCTURE]

**Current State:** Declarative workflow definitions exist in every module (`workflows.py`) but there is NO runtime engine to execute them. Guard, Transition, and Workflow dataclasses define state machines, but nothing evaluates transitions, enforces guards, or persists workflow state.

**Why this can't be a module:** It's a cross-cutting runtime that all modules depend on. Every module's approval process, status transitions, and escalations route through it.

| Component | Description | Effort |
|-----------|-------------|--------|
| Workflow Runtime | Evaluate guards, execute transitions, persist state | Large |
| Approval Engine | Multi-level approval with delegation and escalation | Large |
| Workflow History | Complete audit trail of all transitions | Medium |
| Parallel Approval | AND/OR routing for multi-approver scenarios | Medium |
| Timeout/Escalation | Auto-escalate stalled approvals | Medium |

**Architecture:**
```
finance_services/workflow_service.py   # Workflow runtime engine
finance_kernel/models/workflow.py      # WorkflowInstance, WorkflowStep persistence
finance_modules/*/workflows.py         # Already exists (definitions only)
```

---

#### GAP-03: Batch Processing & Job Scheduling [P1] [INFRASTRUCTURE]

**Current State:** All operations are synchronous, single-transaction. No way to run period-end batch jobs.

**Why this can't be a module:** It's platform infrastructure (Celery/APScheduler) that all modules use for their batch operations (mass depreciation, mass accrual, payment runs, revaluation runs, recurring entries).

| Component | Description | Effort |
|-----------|-------------|--------|
| Job Scheduler | Cron-like scheduling for recurring tasks | Medium |
| Batch Runner | Process thousands of events with progress tracking | Medium |
| Recurring Entry Scheduler | Auto-generate entries on schedule | Medium |
| Batch Status Tracking | Monitor batch job progress and errors | Small |

**Architecture:** Celery or APScheduler integrated into `finance_api/`. Each module registers its batch tasks. The scheduler invokes them.

---

### NEW MODULE GAPS (7 new modules)

#### GAP-04: Financial Reporting Module [P0] [MODULE]

**Current State:** `ledger_selector.py` computes trial balance. That's the only reporting capability.

**Why it's a module:** Financial statements are transformations of trial balance data grouped by account type. The BS/IS are queries + formatting. The cash flow statement (indirect method) is the most complex piece -- it derives cash flows from accrual-basis entries -- but this is still a query transformation, not new computation.

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Trial Balance Report | Formatted output with comparative periods | `ledger_selector.py` |
| Balance Sheet | ASC 210 / IAS 1 classified balance sheet | Trial balance + account type grouping |
| Income Statement | Single-step and multi-step formats | Trial balance + account type grouping |
| Cash Flow Statement | Direct and indirect methods (ASC 230 / IAS 7) | Journal line queries + classification |
| Statement of Changes in Equity | Period-over-period equity movements | Equity account queries |
| Segment Reporting | ASC 280 / IFRS 8 operating segments | Dimension-based filtering |
| Report Renderer | PDF, Excel, CSV, XBRL output | Standard formatting library |
| Consolidation | Multi-entity roll-up with eliminations | Queries per entity + elimination entries via kernel |

**Architecture:**
```
finance_modules/reporting/
    service.py          # Report generation orchestration
    models.py           # ReportDefinition, ReportOutput
    formatters.py       # PDF, Excel, CSV, XBRL renderers
    statements.py       # Pure functions: trial_balance → BS/IS/CF
    config.py           # Report layouts, classification rules
```

---

#### GAP-05: Revenue Recognition Module (ASC 606 / IFRS 15) [P1] [MODULE]

**Current State:** AR module has basic invoice/payment/deferred revenue recording. No 5-step model.

**Why it's a module:** The 5-step model is orchestration logic. Price allocation uses the existing `AllocationEngine`. Variable consideration estimation is module-level arithmetic. Recognition timing produces events that post through the existing kernel.

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Contract Identification | Identify contracts with customers | Module model + workflow |
| Performance Obligation ID | Identify distinct POs | Module logic |
| Transaction Price | Variable consideration, constraints | Module-level helper functions |
| Price Allocation | Relative standalone selling price | `AllocationEngine` (pro-rata) |
| Revenue Recognition | Point-in-time vs. over-time | New event types + YAML policies |
| Contract Modification | Change order impact on existing POs | New event types + economic links |

**Architecture:**
```
finance_modules/revenue/
    service.py          # 5-step model orchestration
    models.py           # PerformanceObligation, ContractRevenue
    profiles.py         # Rev rec YAML policies
    workflows.py        # Contract lifecycle state machine
    config.py           # Recognition rules, SSP methods
    helpers.py          # Variable consideration, SSP calc (pure functions)
```

---

#### GAP-06: Lease Accounting Module (ASC 842 / IFRS 16) [P1] [MODULE]

**Current State:** Not implemented.

**Why it's a module:** PV calculation is one function. Amortization schedule is a loop. Classification is a decision tree. All posting goes through existing kernel. No new engine warranted.

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Lease Classification | Finance vs. operating determination | Module-level decision logic |
| ROU Asset Calculation | Present value of lease payments | Pure function (PV formula) |
| Lease Liability Amortization | Effective interest method | Pure function (amort schedule) |
| Lease Modification | Remeasurement on modification | New event types |
| Lease Posting | Initial recognition, periodic amortization | Existing kernel posting |
| Disclosure Support | ASC 842 required disclosures | Queries from module data |

**Architecture:**
```
finance_modules/lease/
    service.py          # Lease lifecycle orchestration
    models.py           # Lease, LeasePayment, ROUAsset, LeaseLiability
    profiles.py         # Lease accounting YAML policies
    workflows.py        # Lease approval state machine
    config.py           # Discount rate defaults, classification thresholds
    calculations.py     # PV, amortization, modification (pure functions)
```

---

#### GAP-07: Budgeting & Planning Module [P1] [MODULE]

**Current State:** No budget module.

**Why it's a module:** Budget entries are just another posting type. Budget vs actual is query both and subtract. Encumbrance is a new event type + policy. No new computation beyond what the kernel already provides.

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Budget Entry | Budget by account, period, dimension | Module model + kernel posting |
| Budget vs Actual | Variance analysis | Query + subtraction |
| Encumbrance Accounting | Commit on PO, relieve on invoice | New event types + YAML policies |
| Rolling Forecast | Re-forecast based on actuals | Module-level calculation |
| Budget Transfer | Reallocate between cost centers | New event type |
| Budget Lock | Prevent changes to approved budgets | Workflow + status flag |

**Architecture:**
```
finance_modules/budget/
    service.py          # Budget CRUD and comparison
    models.py           # BudgetEntry, BudgetVersion, BudgetLock
    profiles.py         # Encumbrance posting policies
    workflows.py        # Budget approval state machine
    config.py           # Budget periods, approval thresholds
```

---

#### GAP-08: Intercompany Module [P1] [MODULE]

**Current State:** Config supports `legal_entity` scope. No intercompany infrastructure.

**Why it's a module:** IC transaction = post to both entities using existing kernel. Elimination = reversal entry via existing correction engine. Consolidation = query all entities and sum. The kernel already handles multi-entity via config scope.

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| IC Transaction Posting | Auto-generate offsetting entries | Kernel posting (two calls) |
| IC Elimination | Eliminate IC balances at consolidation | `CorrectionEngine` (reversal entries) |
| IC Reconciliation | Match IC balances across entities | `ReconciliationManager` + `MatchingEngine` |
| Consolidation Roll-Up | Sum across entities | Queries per entity |
| Transfer Pricing | Arm's-length pricing | Module-level rules |

**Architecture:**
```
finance_modules/intercompany/
    service.py          # IC posting, elimination, reconciliation
    models.py           # IntercompanyAgreement, ICTransaction
    profiles.py         # IC posting policies
    config.py           # Entity hierarchy, elimination rules
```

---

#### GAP-09: Project Accounting Module [P2] [MODULE]

**Current State:** WIP module handles production orders. Contract module handles government contracts. No unified project accounting.

**Why it's a module:** EVM metrics (BCWS, BCWP, ACWP, CPI, SPI) are queries + arithmetic. Project billing uses existing `BillingEngine`. Cost tracking uses existing kernel posting. WBS hierarchy is a model.

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| WBS Structure | Work Breakdown Structure hierarchy | Module model |
| Project Budget | Budget at WBS element level | Budget module (GAP-07) |
| EVM Calculations | CPI, SPI, EAC, ETC | Module-level pure functions |
| Project Revenue Recognition | Percentage of completion | Module calculation + kernel posting |
| Project Billing | Milestone, T&M, cost-plus | `BillingEngine` (already exists) |
| Project Cost Tracking | Costs by WBS element | Dimension-based posting |

**Architecture:**
```
finance_modules/project/
    service.py          # Project lifecycle, EVM, billing
    models.py           # Project, WBSElement, ProjectBudget
    profiles.py         # Project accounting policies
    workflows.py        # Project lifecycle state machine
    config.py           # EVM thresholds, billing rules
    evm.py              # EVM calculations (pure functions)
```

---

#### GAP-10: Credit Loss Module (ASC 326 / CECL) [P2] [MODULE]

**Current State:** Bad debt provision exists as simple recording. No expected credit loss model.

**Why it's a module:** ECL calculation is statistical math (loss rates, PD/LGD models) that produces a provision amount. That amount is posted as a journal entry through the existing kernel. The model itself is module-level computation.

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Historical Loss Rates | Statistical analysis of historical losses | Queries + module math |
| ECL Calculation | Forward-looking expected credit loss | Module-level pure functions |
| Vintage Analysis | Loss rates by origination cohort | Queries + grouping |
| Provision Posting | Book allowance for credit losses | Existing kernel posting |
| Disclosure Support | Required ASC 326 disclosures | Queries from module data |

---

### EXISTING MODULE DEEPENING GAPS (12 existing modules)

These modules have the right structure (models, profiles, config, workflows) but need their `service.py` implementations deepened.

#### GAP-11: Multi-Currency Completion [P1] [MODULE: existing]

**Current State:** `multicurrency` capability is disabled. Exchange rate model, FX gain/loss tests exist. Multi-currency journal lines supported by kernel.

**Why it's module work:** Currency translation = balances × rate. Revaluation = new event type + policy. The kernel already supports multi-currency journal lines and exchange rates. The module just needs to orchestrate: "for each foreign account, get balance, apply rate, post translation entry."

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Currency Translation | Current rate, temporal methods | Queries + multiplication |
| CTA Posting | Cumulative Translation Adjustment | New event type + policy |
| Period-End Revaluation | Unrealized gain/loss calculation | Queries + posting |
| Multi-Currency Trial Balance | Balances in local/group/hard currencies | `ledger_selector` + rate lookup |
| Enable multicurrency config | Turn on the capability flag | Config YAML change |

---

#### GAP-12: GL Module Deepening [P1] [MODULE: existing]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Period Close Automation | Ordered close tasks with dependencies | Workflow runtime (GAP-02) |
| Recurring Entries | Template-based auto-generation | Module logic + kernel posting |
| Retained Earnings Roll | Year-end close entry | New event type + policy |
| Journal Entry Approval | Multi-level approval | Workflow runtime (GAP-02) |
| Account Reconciliation | Sign-off per account per period | Module model + queries |

---

#### GAP-13: AP Module Deepening [P1] [MODULE: existing]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Payment Run | Batch payment selection + processing | Module logic + kernel posting |
| Auto 3-Way Match | Automated PO-receipt-invoice matching | `MatchingEngine` (exists) |
| Vendor Management | Vendor scoring, terms, holds | Module models |
| Early Payment Discount | Discount calculation + posting | Module logic + new policy |

---

#### GAP-14: AR Module Deepening [P1] [MODULE: existing]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Dunning Workflow | Collection letter generation | `AgingCalculator` (exists) + workflow |
| Cash Application Automation | Auto-match payments to invoices | `MatchingEngine` (exists) |
| Credit Management | Credit limit enforcement on orders | Party model (exists) |
| Write-Off Automation | Rules-based write-off of small balances | Module logic + posting |

---

#### GAP-15: Cash Module Deepening [P2] [MODULE: existing]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Bank Statement Import | MT940, BAI2, CAMT.053 parsing | File parsing utilities |
| Auto-Reconciliation | Rules-based GL matching | `MatchingEngine` (exists) |
| Payment Processing | ACH/Wire file generation | Module output formatting |
| Cash Forecasting | Projected cash flows | Module queries + arithmetic |

---

#### GAP-16: Inventory Module Deepening [P2] [MODULE: existing]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Cycle Count | Periodic physical inventory count | Module model + adjustment events |
| ABC Classification | Value-based inventory stratification | Module queries + sorting |
| Reorder Point | Min/max/reorder calculations | Module-level config + alerts |
| Inter-Warehouse Transfer | Stock movement between locations | New event type + economic links |

---

#### GAP-17: Assets Module Deepening [P2] [MODULE: existing]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Depreciation Methods | SL, DDB, SYD, units-of-production | Pure functions inside module |
| Mass Depreciation Run | Batch depreciation for all assets | Loop + kernel posting (uses scheduler GAP-03) |
| Impairment Testing | Fair value vs carrying amount | Module-level comparison logic |
| CIP Management | Capitalize costs over time | New event type + policy |
| Asset Transfer | Between cost centers/entities/locations | New event type |

---

#### GAP-18: Payroll Module Deepening [P2] [MODULE: existing]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Gross-to-Net | Pay calculations with deductions | Module-level pure functions |
| Tax Withholding | Federal, state, local tax | `TaxCalculator` (exists) + tax table data |
| Labor Distribution | Cost allocation to projects/cost centers | `AllocationEngine` (exists) |
| Benefits Deductions | Health, dental, 401k | Module-level config + calculation |
| NACHA File Generation | Direct deposit ACH files | Module output formatting |

---

#### GAP-19: WIP Module Deepening [P2] [MODULE: existing]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Production Order Costing | Material + labor + overhead | `ValuationLayer` (exists) + kernel posting |
| Overhead Application | Apply indirect rates to production | `AllocationCascade` (exists) |
| Variance Analysis | Material, labor, overhead variances | `VarianceCalculator` (exists) |
| Production Completion | Transfer WIP to finished goods | New event type + policy |

---

#### GAP-20: Tax Module Deepening [P2] [MODULE: existing]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Deferred Tax (ASC 740) | Book vs tax basis → DTA/DTL | Module-level calculation + posting |
| Multi-Jurisdiction | State + local tax compliance | `TaxCalculator` (exists) + jurisdiction data |
| Tax Provision | Current + deferred = total provision | Module-level aggregation |
| Tax Return Data Export | Data extract for tax return software | Module queries + formatting |

---

#### GAP-21: Procurement Module Deepening [P2] [MODULE: existing]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Requisition-to-PO | Conversion workflow | Workflow runtime (GAP-02) |
| PO Amendment | Versioned modifications | Module model + economic links |
| Goods Receipt Integration | Link receipt to PO lines | `MatchingEngine` + economic links |
| Supplier Evaluation | Scoring by delivery/quality/price | Module-level queries + calculation |

---

#### GAP-22: Expense Module Deepening [P2] [MODULE: existing]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| Policy Enforcement | Per-diem limits, category caps | Guard expressions in YAML policies |
| Corporate Card Import | Parse card transaction files | File parsing utilities |
| Approval Workflow | Multi-level expense approval | Workflow runtime (GAP-02) |
| Mileage Calculator | IRS rate-based reimbursement | Module-level config + calculation |

---

#### GAP-23: Contracts Module Deepening [P2] [MODULE: existing]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| DCAA Compliance Workflow | Full incurred cost audit workflow | Workflow runtime (GAP-02) + `ICEEngine` |
| Contract Modification | Fund changes, scope changes | Module model + new event types |
| Subcontract Management | Sub flow-down, cost tracking | Module model + economic links |

---

### CONFIG-ONLY GAPS

#### GAP-24: IFRS Support [P2] [CONFIG]

**Current State:** IFRS capability is disabled. System is US GAAP only.

**Why it's just config:** The entire point of the YAML policy system is that different accounting standards are different policy sets applied to the same kernel. IFRS support means writing a second `finance_config/sets/IFRS-2026-v1/` directory with IFRS-specific policies.

| Component | Description | Effort |
|-----------|-------------|--------|
| IFRS Policy Set | Complete IFRS policies in YAML | Large (volume of policies) |
| IAS 21 (Foreign Currency) | Translation policies | Policies + module work (GAP-11) |
| IAS 36 (Impairment) | Impairment policies | Policies + module work (GAP-17) |
| IFRS 15 (Revenue) | Revenue policies | Policies + module work (GAP-05) |
| IFRS 16 (Leases) | Lease policies | Policies + module work (GAP-06) |
| Dual-GAAP Reporting | Parallel US GAAP + IFRS | Two config sets, same kernel |

---

### ANCILLARY GAPS (important but secondary)

#### GAP-25: Data Import/Export [P1] [UTILITY]

| Component | Description | Effort |
|-----------|-------------|--------|
| CSV/Excel Import | Batch journal entry, COA, opening balance import | Medium |
| Data Migration Tool | Initial load from legacy systems | Large |
| Bank Statement Parsing | MT940, BAI2, CAMT.053 formats | Medium |
| Export Framework | Configurable data extracts for analytics | Medium |
| Audit Export | Structured export for external auditors | Medium |

This is a utility layer, not a module or engine. File parsing functions called by modules.

---

#### GAP-26: Audit & Compliance Reporting [P1] [MODULE]

| Component | Description | Existing Primitive Used |
|-----------|-------------|------------------------|
| SoD Matrix Report | Segregation of duties conflicts | RBAC data (GAP-01) |
| Access Review Report | Periodic access certification | RBAC data (GAP-01) |
| Change Log Report | Config and master data changes | Audit chain (exists) |
| Regulatory Filing | SAF-T, FEC, SII output formats | Queries + formatting |
| Audit Trail Export | Structured export for auditors | Audit events (exists) |

---

#### GAP-27: Document Management [P2] [UTILITY]

| Component | Description | Effort |
|-----------|-------------|--------|
| Document Store | S3/blob storage integration | Medium |
| Attachment Model | Link documents to any entity | Medium |
| Document Retention | Policy-based retention | Medium |

---

#### GAP-28: Notification System [P2] [UTILITY]

| Component | Description | Effort |
|-----------|-------------|--------|
| Email Notifications | Templated emails for approvals/alerts | Medium |
| Webhook Publisher | Event-driven external notifications | Medium |
| Alert Rules | Configurable threshold-based alerts | Medium |

---

#### GAP-29: User Interface [P2] [SEPARATE APPLICATION]

| Component | Description | Effort |
|-----------|-------------|--------|
| Web Application | React/Vue SPA consuming the API | Very Large |
| GL Workbench | Journal entry, trial balance, inquiries | Large |
| AP/AR Workbenches | Invoice, payment, aging views | Large |
| Period Close Cockpit | Close task management dashboard | Large |
| Admin Console | Configuration, user management | Large |

The UI is a separate application that consumes the API (GAP-01). It has no impact on the kernel, engines, modules, or services.

---

## Part 3: Module Completeness Assessment

### Existing Module Depth

| Module | Models | Profiles | Service LOC | Engine Integration | Completeness | Key Remaining Work |
|--------|--------|----------|------------|-------------------|-------------|-------------------|
| **GL** | 5 types | Defined | Thin | N/A | 40% | Period close, recurring entries, ret. earnings |
| **AP** | 5 types | 10 policies | 784 | Allocation, Matching, Aging | 55% | Payment run, auto-match, vendor mgmt |
| **AR** | 6 types | 14 policies | 805 | Allocation, Aging | 50% | Dunning, cash application, credit mgmt |
| **Inventory** | 7 types | Defined | 662 | Valuation, Variance | 50% | Cycle count, ABC, reorder |
| **Cash** | Defined | Defined | 578 | Matching, Reconciliation | 35% | Statement import, auto-recon, payments |
| **Payroll** | 5 types | Defined | Thin | Tax | 15% | Gross-to-net, withholding, labor dist |
| **Tax** | 5 types | Defined | Thin | Tax Calculator | 20% | Deferred tax, multi-jurisdiction, provision |
| **WIP** | 5 types | Defined | Thin | Variance | 20% | Production costing, overhead, completion |
| **Assets** | 4 types | Defined | Thin | -- | 20% | Depreciation methods, mass run, impairment |
| **Expense** | 4 types | 7 policies | 477 | Allocation, Tax | 35% | Policy enforcement, card import, approval |
| **Procurement** | Defined | Defined | Thin | Matching | 15% | Req-to-PO, amendments, receipt integration |
| **Contracts** | Defined | Defined | Thin | Billing, ICE | 25% | DCAA workflow, modifications, subcontracts |

### New Modules Needed

| Module | Purpose | Primary Engine Dependencies |
|--------|---------|---------------------------|
| **reporting** | Financial statements (BS, IS, CF, equity) | `ledger_selector` queries |
| **revenue** | ASC 606 / IFRS 15 five-step model | `AllocationEngine` |
| **lease** | ASC 842 / IFRS 16 lease accounting | None (pure functions inside module) |
| **budget** | Budgeting, encumbrance, forecasting | None (queries + arithmetic) |
| **intercompany** | IC transactions, elimination, consolidation | `ReconciliationManager`, `MatchingEngine` |
| **project** | Project accounting, WBS, EVM | `BillingEngine` |
| **credit_loss** | ASC 326 / CECL expected credit loss | None (statistical functions inside module) |

---

## Part 4: Prioritized Build Roadmap

### Phase 1: Production Foundation [P0]

**Goal:** Make the system deployable and usable.

| # | Item | Type | Depends On |
|---|------|------|-----------|
| 1 | REST API Layer + Auth/RBAC | INFRASTRUCTURE | -- |
| 2 | Workflow Execution Runtime | INFRASTRUCTURE | -- |
| 3 | Financial Reporting Module | MODULE (new) | -- |
| 4 | Data Import/Export Utilities | UTILITY | -- |

### Phase 2: Core Financial Completeness [P1]

**Goal:** Feature parity with mid-market ERP (NetSuite/Sage Intacct level).

| # | Item | Type | Depends On |
|---|------|------|-----------|
| 5 | GL Module Deepening | MODULE (deepen) | Workflow runtime |
| 6 | AP Module Deepening | MODULE (deepen) | Workflow runtime |
| 7 | AR Module Deepening | MODULE (deepen) | Workflow runtime |
| 8 | Multi-Currency Completion | MODULE (deepen) | -- |
| 9 | Revenue Recognition Module | MODULE (new) | -- |
| 10 | Lease Accounting Module | MODULE (new) | -- |
| 11 | Budgeting Module | MODULE (new) | -- |
| 12 | Intercompany Module | MODULE (new) | -- |
| 13 | Batch Processing & Scheduling | INFRASTRUCTURE | -- |
| 14 | Audit & Compliance Reporting | MODULE (new) | Auth/RBAC |

### Phase 3: Competitive Differentiation [P2]

**Goal:** Feature parity with SAP/Oracle for target markets.

| # | Item | Type | Depends On |
|---|------|------|-----------|
| 15 | Cash Module Deepening | MODULE (deepen) | -- |
| 16 | Inventory Module Deepening | MODULE (deepen) | -- |
| 17 | Assets Module Deepening | MODULE (deepen) | Batch scheduler |
| 18 | Payroll Module Deepening | MODULE (deepen) | -- |
| 19 | WIP Module Deepening | MODULE (deepen) | -- |
| 20 | Tax Module Deepening | MODULE (deepen) | -- |
| 21 | Procurement Module Deepening | MODULE (deepen) | Workflow runtime |
| 22 | Expense Module Deepening | MODULE (deepen) | Workflow runtime |
| 23 | Contracts Module Deepening | MODULE (deepen) | Workflow runtime |
| 24 | Project Accounting Module | MODULE (new) | Budget module |
| 25 | Credit Loss Module | MODULE (new) | -- |
| 26 | IFRS Policy Set | CONFIG | Modules from Phase 2 |
| 27 | Document Management | UTILITY | -- |
| 28 | Notification System | UTILITY | -- |
| 29 | User Interface | SEPARATE APP | API layer |

---

## Part 5: Architecture -- What Changes, What Doesn't

### Unchanged (Done)

```
finance_kernel/       # DONE. No changes needed.
finance_engines/      # DONE. 12 engines cover all computation.
finance_config/       # DONE. Add new policy sets as needed.
```

### Primary Work Area

```
finance_modules/
    # DEEPEN existing 12 modules (service.py implementations)
    ap/  ar/  inventory/  cash/  gl/  payroll/
    tax/  wip/  assets/  expense/  procurement/  contracts/

    # ADD 7 new modules (same 6-file pattern)
    reporting/      # NEW: Financial statements
    revenue/        # NEW: ASC 606
    lease/          # NEW: ASC 842
    budget/         # NEW: Budgeting & encumbrance
    intercompany/   # NEW: IC transactions & consolidation
    project/        # NEW: Project accounting & EVM
    credit_loss/    # NEW: ASC 326 / CECL
```

### New Infrastructure (Small)

```
finance_api/                        # NEW: REST API + Auth
    app.py
    auth/
    routes/
    middleware/
    serializers/

finance_services/
    workflow_service.py             # NEW: Workflow runtime
    # Existing 4 services unchanged:
    # correction_service.py, reconciliation_service.py,
    # subledger_service.py, valuation_service.py
```

### Summary: What Gets Built

| Category | Count | % of Remaining Work |
|----------|-------|-------------------|
| New modules | 7 | ~35% |
| Deepened existing modules | 12 | ~35% |
| API layer + auth | 1 | ~15% |
| Workflow runtime | 1 | ~5% |
| Job scheduler | 1 | ~3% |
| Utilities (import/export, docs, notifications) | 3 | ~5% |
| IFRS config set | 1 | ~2% |
| **No new engines** | **0** | **0%** |

---

## Part 6: Comparison Matrix vs SAP S/4HANA

| Feature Area | This System | SAP S/4HANA | Gap | Fix Type |
|-------------|-------------|-------------|-----|----------|
| **General Ledger** | Strong kernel, thin GL module | Complete | Medium | Deepen module |
| **Accounts Payable** | Good (55%) | Complete | Medium | Deepen module |
| **Accounts Receivable** | Good (50%) | Complete | Medium | Deepen module |
| **Asset Accounting** | Basic (20%) | Complete | Large | Deepen module |
| **Cost Accounting** | WIP + overhead allocation | CO module | Large | Deepen WIP + new project module |
| **Inventory** | Good (50%) with valuation | Complete (MM) | Medium | Deepen module |
| **Revenue Recognition** | None | RAR (ASC 606) | Critical | New module |
| **Lease Accounting** | None | RE-FX | Critical | New module |
| **Treasury** | Basic cash mgmt | Full TRM | Large | Deepen module |
| **Financial Reporting** | Trial balance only | BW/Fiori | Critical | New module |
| **Consolidation** | None | Group Reporting | Critical | New intercompany module |
| **Tax** | Transaction tax only | Vertex | Large | Deepen module |
| **Budget** | None | BPC / SAC | Critical | New module |
| **Multi-Currency** | Foundation only | Complete | Large | Deepen module + enable config |
| **Multi-Entity** | Config scope only | Complete | Large | New intercompany module |
| **Workflow** | Declarative only | SAP WF | Critical | New service (1 file) |
| **API** | None | OData, BAPIs | Critical | New layer |
| **Security** | Actor ID only | Full RBAC | Critical | Part of API layer |
| **Batch Processing** | None | Background jobs | Large | Job scheduler |
| **Reporting** | None | Fiori Analytics | Critical | New module |
| | | | | |
| **Architecture Quality** | **Superior** | Legacy monolith | **Advantage** | -- |
| **Audit Trail** | **Superior** | Change documents | **Advantage** | -- |
| **Immutability** | **Superior** | App-level only | **Advantage** | -- |
| **Gov Contracting** | **Competitive** | Requires Deltek | **Advantage** | -- |
| **Event Sourcing** | **Yes** | No | **Advantage** | -- |
| **Replay Determinism** | **Yes** | No | **Advantage** | -- |
| **Test Quality** | **Exceptional** | Proprietary | **Advantage** | -- |

---

## Part 7: Risk Assessment

### Technical Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| API layer introduces security surface | High | Security-first design, penetration testing |
| Module deepening breaks existing tests | Medium | Run full test suite before/after each module |
| Batch processing may stress single-transaction model | Medium | Each batch item is its own transaction |
| IFRS dual-reporting doubles policy maintenance | Medium | Shared modules, separate YAML policy sets |

### Organizational Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Scope creep -- building all gaps at once | Critical | Phase strictly; ship Phase 1 before Phase 2 |
| Architecture degradation under pressure | High | Architecture tests enforce boundaries automatically |
| Test quality regression as modules grow | Medium | Maintain test-per-invariant discipline (R20) |
| Module work feels repetitive (same 6-file pattern) | Low | Consistency is a feature, not a bug |

---

## Appendix A: Lines of Code by Layer

| Layer | Files | LOC | % of Total | Status |
|-------|-------|-----|-----------|--------|
| finance_kernel | ~50 | ~12,000 | 32% | **Done** |
| finance_engines | ~18 | ~12,500 | 34% | **Done** |
| finance_modules | ~84 | ~8,500 | 23% | Primary work area |
| finance_services | ~5 | ~2,550 | 7% | +1 file (workflow) |
| finance_config | ~10 | ~2,100 | 6% | +IFRS set when needed |
| **Total** | **~167** | **~37,650** | **100%** | |
| tests | ~103 | ~15,000+ | (excluded) | |

## Appendix B: Invariant Coverage Matrix

| Invariant | Kernel Code | DB Trigger | ORM Listener | Unit Test | Concurrency Test | Adversarial Test |
|-----------|-------------|------------|-------------|-----------|-----------------|-----------------|
| R1 | Yes | Yes | Yes | Yes | N/A | Yes |
| R2 | Yes | N/A | N/A | Yes | N/A | Yes |
| R3 | Yes | Yes (UNIQUE) | N/A | Yes | Yes | Yes |
| R4 | Yes | Yes | N/A | Yes | Yes | N/A |
| R5 | Yes | Yes | N/A | Yes | N/A | Yes |
| R6 | Yes | N/A | N/A | Yes | N/A | N/A |
| R7 | Yes | N/A | N/A | Yes | Yes | Yes |
| R8 | Yes | Yes | N/A | Yes | Yes | Yes |
| R9 | Yes | N/A | N/A | Yes | Yes | N/A |
| R10 | Yes | Yes | Yes | Yes | N/A | Yes |
| R11 | Yes | N/A | N/A | Yes | Yes | N/A |
| R12 | Yes | N/A | Yes | Yes | Yes | Yes |
| R13 | Yes | N/A | N/A | Yes | N/A | N/A |
| R14 | Yes | N/A | N/A | Yes | N/A | N/A |
| R15 | Yes | N/A | N/A | Yes | N/A | N/A |
| R16 | Yes | N/A | N/A | Yes | N/A | N/A |
| R17 | Yes | N/A | N/A | Yes | N/A | N/A |
| R18 | Yes | N/A | N/A | Yes | N/A | N/A |
| R19 | Yes | N/A | N/A | Yes | N/A | N/A |
| R20 | Yes | N/A | N/A | Yes | N/A | N/A |
| R21 | Yes | N/A | N/A | Yes | N/A | N/A |
| R22 | Yes | N/A | N/A | Yes | N/A | Yes |
| R23 | Yes | N/A | N/A | Yes | N/A | N/A |
| R24 | Yes | N/A | N/A | Yes | N/A | N/A |

---

*This analysis was generated from a complete codebase review of all 167+ source files, 103 test files, and all configuration artifacts. Revised to reflect that the kernel and engine layers are complete, and remaining work is primarily module-level.*
