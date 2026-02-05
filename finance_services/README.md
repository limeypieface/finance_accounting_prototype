# Finance Services

**Stateful orchestration** over the kernel and engines. This is the only layer that holds database sessions, calls kernel services (e.g. ModulePostingService, LinkGraphService), and invokes pure engines. **finance_kernel** and **finance_engines** must never import from this package.

## What this package does

- **Wires** the kernel (posting, period, audit, link graph) and **config** (policy pack, role bindings) into a single entrypoint for posting and workflows.
- **Orchestrates** stateful workflows: correction, reconciliation, valuation, subledger balance/open-item handling, period close.
- **Dispatches** to **finance_engines** for pure calculations (variance, allocation, matching, tax, etc.); services supply session, clock, and config where needed.
- **Exposes** the canonical import surface for external callers (e.g. modules, scripts): `PostingOrchestrator`, `SubledgerService`, `CorrectionEngine`, `post_event_from_external`, etc.

## Key entry points

| Entry point | Role |
|-------------|------|
| **PostingOrchestrator** | Builds kernel services (ModulePostingService, policy source, role resolver, clock) from **config**; canonical DI for posting. Use `post_event()` for module-driven posting. |
| **post_event_from_external** | Contract validation + post via a supplied ModulePostingService (integration boundary). |
| **SubledgerService** | Subledger aggregation and open-item logic; delegates to AP/AR/Bank/Contract/Inventory subledger services. |
| **CorrectionEngine** | Cascade unwind and compensating entries; uses EconomicLink graph and kernel reversal. |
| **ReconciliationManager** | Payment application, document matching; uses matching engine and kernel. |
| **ValuationLayer** | Cost lot and valuation; uses valuation engine and kernel. |
| **EngineDispatcher** | Registers and invokes pure engines (variance, allocation, etc.); used by services that need calculations. |
| **PeriodCloseOrchestrator** | Period close workflow; uses kernel PeriodService and posting. |

Config is obtained via **finance_config.get_active_config(legal_entity, as_of_date)**; the pack is passed into the orchestrator or used to build policy source and role resolver. Services do not read YAML or env vars themselves.

## Boundaries

- **finance_kernel**: Services call kernel APIs (ModulePostingService, InterpretationCoordinator, JournalWriter, LinkGraphService, etc.). Kernel never imports from services.
- **finance_engines**: Services call pure engines for calculations; engines never import from services.
- **finance_config**: Services (and modules) call `get_active_config()`; config never imports from services. See **finance_config/README.md** for the config contract.

## See also

- **finance_config/README.md** — How configuration is loaded and the single entrypoint; services consume config from there.
- **finance_kernel/README.md** — Posting pipeline, invariants, ModulePostingService.
- **finance_engines/README.md** — Pure engine catalog; services dispatch to these.
- **CLAUDE.md** — Layer rules and forbidden imports.
- **docs/EXTENSIBILITY.md** — Custom features via config, modules, strategy/engine registration; core stays closed.
