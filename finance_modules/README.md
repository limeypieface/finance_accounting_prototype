# Finance Modules

**Thin ERP modules** (AP, AR, Cash, Inventory, Payroll, etc.) that provide **declarative** profiles, workflows, and config schemas, and a **service facade** that delegates all journal posting to the kernel and all calculations to engines/services. The kernel and engines do **not** import from this package.

## Role in the stack

```
finance_modules/   ← You are here. Declarative: profiles, workflows, config. Service = orchestration + tx.
    │
    ▼
finance_services/  PostingOrchestrator, WorkflowExecutor, SubledgerService, reconciliation, correction…
    │
    ▼
finance_engines/   Pure engines (variance, matching, allocation, tax). Kernel domain values only.
    │
    ▼
finance_config/    YAML config sets. get_active_config(legal_entity, as_of_date).
    │
    ▼
finance_kernel/    Journal, audit, ModulePostingService, LinkGraphService. Financial truth.
```

Modules **call down** into services (which call kernel and engines). They **do not** hold financial truth: journal and link graph are the system of record (R26). Module ORM is an operational projection for workflows and UX.

## What each module provides

Every module follows the same layout (some add optional `helpers`, `calculations`, `dcaa_orm`, etc.):

| Area | Role |
|------|------|
| **config.py** | Module-specific policy and settings (e.g. approval thresholds, defaults). |
| **models.py** | Domain models (dataclasses / value types) used in the module. |
| **orm.py** | SQLAlchemy ORM models and table definitions. Registered via `_orm_registry`. |
| **profiles.py** | Accounting profiles: event_type → accounting policy (roles, guards). Registered with kernel. |
| **service.py** | Public API: creates events, runs workflows, calls PostingOrchestrator / ModulePostingService. |
| **workflows.py** | Lifecycle workflows (states, transitions, guards). One workflow per business process (R28). |

Posting is **always** via the services layer: modules get a `ModulePostingService` (or `PostingOrchestrator`) and call `post_event()`. Only the kernel may assert `POSTED` (R29). Services use **account ROLES** in profiles; COA resolution happens in the kernel at posting time (L1).

## Module catalog

| Module | Directory | Purpose |
|--------|-----------|---------|
| **AP** | `ap/` | Vendor invoices, PO/receipt/invoice matching, payments, aging, accruals, prepayments. |
| **AR** | `ar/` | Customer invoices, receipts, collections, aging, credit holds. |
| **Assets** | `assets/` | Fixed assets, depreciation, disposal. |
| **Budget** | `budget/` | Budget definitions, variance tracking. |
| **Cash** | `cash/` | Bank accounts, reconciliation, internal transfers, auto-reconcile. |
| **Contracts** | `contracts/` | Government contracts, CLINs, billing types (CPFF, T&M, FFP), DCAA, rate compliance. |
| **Credit Loss** | `credit_loss/` | CECL / ASC 326 allowance, reserve. |
| **Expense** | `expense/` | Travel & expense, corporate cards, DCAA expense compliance. |
| **GL** | `gl/` | Chart of accounts, manual entries, period close, consolidation. |
| **Intercompany** | `intercompany/` | Intercompany balances, eliminations. |
| **Inventory** | `inventory/` | Stock, receipts, issues, valuation (FIFO/LIFO/WA), landed cost. |
| **Lease** | `lease/` | ASC 842 leases, ROU assets, lease liability. |
| **Payroll** | `payroll/` | Timecards, paychecks, labor distribution, DCAA timesheet compliance. |
| **Procurement** | `procurement/` | Requisitions, purchase orders, receiving. |
| **Project** | `project/` | Project accounting, EVM, cost accumulation. |
| **Reporting** | `reporting/` | Read-only statements (P&L, balance sheet, cash flow). |
| **Revenue** | `revenue/` | ASC 606 revenue recognition, performance obligations. |
| **Tax** | `tax/` | Sales/use tax, VAT, reporting. |
| **WIP** | `wip/` | Work in process, manufacturing orders, labor, overhead, variances. |

## Shared utilities

| File | Role |
|------|------|
| **_orm_registry.py** | Imports all module ORM modules so `Base.metadata` has every table. Provides `create_all_tables()` (production-safe: register module ORM then create all tables). Call from scripts/tests; kernel `create_tables()` is guarded unless module models are loaded. |
| **_posting_helpers.py** | Helpers for posting flows: `guard_failure_result()`, `commit_or_rollback()`, protocol for `WorkflowExecutorLike`. Used by module `service.py` to handle guard results and session lifecycle after `post_event()`. |

## Startup and registration

- **Profiles** are **not** auto-registered at import. Call **`register_all_modules()`** at startup (or in test fixtures) so the kernel’s policy selector and role bindings see all module profiles. See `finance_modules/__init__.py`.
- **ORM**: Use **`create_all_tables()`** from `finance_modules._orm_registry` so all kernel and module tables exist. Do not rely on kernel-only `create_tables()` unless you explicitly want kernel-only schema.

## Key invariants (modules)

- **R25** — Use kernel value types only (Money, ArtifactRef, etc.). No parallel Money/Amount types in modules.
- **R26** — Journal (+ link graph) is system of record. Module ORM is derivable projection.
- **R27** — Variance and ledger impact come from kernel policy (profiles, guards), not from module branching.
- **R28** — Each action uses a **specific** lifecycle workflow. No generic/catch-all workflows (see docs/WORKFLOW_DIRECTIVE.md).
- **R29** — Only the kernel may assert `POSTED`. Module services return transition/guard outcomes; use `ModulePostingResult.is_ledger_fact` vs `is_transition` to distinguish posting from governance.

## Boundaries

- **finance_services**: Modules call PostingOrchestrator, WorkflowExecutor, SubledgerService, reconciliation, correction, etc. Services never import from individual modules (they depend on the **module layer** only via protocols or explicit wiring).
- **finance_kernel**: Modules do not import kernel services directly for posting from application code; they use the **PostingOrchestrator** (or a supplied ModulePostingService) from finance_services. They may use kernel **domain** types (Money, ArtifactRef, etc.) and kernel **models** where ORM references exist. Kernel must **not** import from finance_modules.
- **finance_config**: Modules (and their profiles) are driven by config. Config sets define COA, policies, workflows, role bindings. Config is loaded via `finance_config.get_active_config()`; modules do not read YAML or env directly.
- **finance_engines**: Modules do not call engines directly; finance_services (e.g. reconciliation, valuation) do. Engines stay pure and kernel-domain-only.

## See also

- **docs/MODULE_DEVELOPMENT_GUIDE.md** — How to add or extend a module; required imports; profile and workflow patterns.
- **docs/WORKFLOW_DIRECTIVE.md** — No generic workflows; each action has a dedicated lifecycle.
- **finance_services/README.md** — PostingOrchestrator, WorkflowExecutor, and how modules get posting and workflows.
- **finance_kernel/README.md** — Posting pipeline, ModulePostingService, invariants.
- **CLAUDE.md** — Layer rules, forbidden imports, R25–R29.
