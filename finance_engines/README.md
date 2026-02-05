# Finance Engines

Pure calculation engines for the finance stack. **No I/O, no database, no session.** All engines live in this package; the catalog below is the single source of truth when adding or changing engines.

## Contract

- **Pure**: No `datetime.now()` or `date.today()`; callers pass timestamps/dates. No DB, no network.
- **Kernel-only imports**: May import `finance_kernel.domain` value types (e.g. `Money`, `Quantity`); must not import `finance_services` or `finance_modules`.
- **Decimal only**: All monetary amounts and quantities use `Decimal`; floats are forbidden.
- **Deterministic**: Same inputs → same outputs. Enables replay and tests.

Stateful orchestration (e.g. CorrectionService, ReconciliationManager) lives in **finance_services**; they call these engines.

## Engine catalog

| Module | Description |
|--------|-------------|
| **variance** | PPV, quantity variance, FX variance, sales price variance. |
| **allocation** | FIFO, LIFO, pro-rata, weighted, equal, specific allocation. |
| **allocation_cascade** | DCAA multi-step indirect cost allocation (Fringe → Overhead → G&A). |
| **matching** | 3-way match (PO↔Receipt↔Invoice), 2-way match, bank reconciliation. |
| **aging** | AP/AR aging buckets, configurable periods, slow-moving inventory. |
| **subledger** | Base subledger types and pure helpers (AP, AR, Bank, etc.). |
| **tax** | VAT, GST, withholding, compound (tax-on-tax), inclusive/exclusive. |
| **billing** | Government contract billing (CPFF, T&M, FFP, cost-plus). |
| **ice** | DCAA Incurred Cost Electronically (Schedules A–J). |
| **contracts** | Contract engine utilities (pure). |
| **approval** | Approval engine (pure). |
| **expense_compliance** | Expense/DCAA compliance. |
| **rate_compliance** | Rate compliance. |
| **timesheet_compliance** | Timesheet compliance. |
| **correction** | Unwind logic, compensating entries (pure); orchestration in finance_services. |
| **reconciliation** | Reconciliation domain and checker (bank, match); orchestration in finance_services. |
| **valuation** | Cost lot, cost method, consumption (FIFO/LIFO/weighted average/standard). |
| **tracer** | Engine tracing/diagnostics (`@traced_engine`). |

When you add or rename an engine, update this table and the exports in `__init__.py`.

## Usage

Canonical import surface is `finance_engines` (re-exports from submodules):

```python
from finance_engines.variance import VarianceCalculator, VarianceType
from finance_engines.allocation import AllocationEngine, AllocationMethod
from finance_engines.matching import MatchingEngine, MatchResult
from finance_engines.valuation import CostLot, CostMethod
```

Full list of public symbols: see `__init__.py` `__all__`.

## See also

- **finance_kernel/README.md** — Architecture, invariants, interpretation layer, primitives (R25).
- **finance_services/README.md** — Orchestration that calls these engines; no engines → services import.
- **finance_config/README.md** — Configuration entrypoint; engines do not read config.
- **CLAUDE.md** — Invariant table, layer rules, R25 (kernel primitives only).
