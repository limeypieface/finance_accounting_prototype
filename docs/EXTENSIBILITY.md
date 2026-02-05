# Extensibility: Custom features without changing core logic

Users and implementers can add custom behavior **without modifying** the finance kernel, config compiler, or core services. The core remains closed; extension is done via **configuration**, **new modules**, **registered strategies**, and **registered engines** at the edges.

## Principle: core is closed

- **Kernel** (`finance_kernel`): Invariants (R1–R29, L*, P*), posting pipeline, primitives, and selectors do not change for custom features. No user code runs inside the kernel except by **registration** of strategies that implement the public strategy interface.
- **Config** (`finance_config`): Loader, compiler, schema, and single entrypoint stay as-is. Custom behavior is expressed as **new or overridden YAML** (policies, workflows, COA), not as changes to the config package.
- **Services** (`finance_services`): Orchestration and DI stay as-is. Custom behavior is added by **registering** strategies/engines and by **new modules** that call the same posting entrypoint.

What **must never** be overridden or bypassed:

- Posting outcome authority (R29): only the kernel may set POSTED/REJECTED.
- Balance (R4), immutability, period lock (R12), idempotency (R3).
- Primitive types (R25): all amounts, quantities, rates, artifact refs use kernel types.

Custom code may **not** change the kernel’s decision to post or reject, alter journal lines after validation, or introduce parallel Money/Currency types.

---

## 1. Configuration-only extension (preferred)

Most custom behavior should be **declarative** in YAML. No code changes.

| Extension | Where | What you add |
|-----------|--------|----------------|
| **New event types** | Config set `policies/*.yaml` | New profile with `trigger.event_type`, meaning, ledger_effects, guards. |
| **New workflows** | `approval_policies.yaml` / workflow definitions | New workflow and transitions; bind in policies. |
| **New accounts / structure** | `chart_of_accounts.yaml`, `ledgers.yaml` | New accounts, role bindings. |
| **Guards and controls** | `controls.yaml`, profile `guards` | REJECT/BLOCK conditions, control rules. |
| **Engine parameters** | `engine_params.yaml` | Parameters for allocation, matching, tax, etc. |

The kernel and services do not change. You add or override a **configuration set** (or a new set under `finance_config/sets/`) and call `get_active_config(legal_entity, as_of_date)` as usual. PostingOrchestrator and ModulePostingService use the pack from config; new profiles and workflows apply automatically.

**Constraint:** Event types and workflows must still follow the workflow directive (no generic/catch-all workflows). Each custom event type should have a dedicated profile and, if used in a module, a dedicated workflow.

---

## 2. New module (custom domain logic)

For custom **domain** behavior (new processes, new entities), add a **new module** alongside `finance_modules/ap`, `ar`, etc.

- The module owns its ORM (if any), service API, and workflow bindings.
- It **posts** via the same entrypoint as every other module: **ModulePostingService.post_event()** (or PostingOrchestrator in services).
- It emits **event types** that are covered by **profiles in config** (see §1). The kernel interprets those events using the configured policies; the module does not implement posting logic itself.
- It may call **EngineDispatcher** for calculations (e.g. custom allocation) if it registers a custom engine (see §4).

The kernel and config package are unchanged. You add `finance_modules/custom_feature/` (or a customer-specific package) and a matching config set (or extend an existing set) with policies and workflows for your event types. See **docs/MODULE_DEVELOPMENT_GUIDE.md**.

---

## 3. Custom posting strategies (event → proposed entry)

When declarative **profiles** are not enough and you need **procedural** event-to-lines logic (e.g. custom allocation of lines by rule), use a **custom posting strategy**.

- Implement **BasePostingStrategy** (or **PostingStrategy**) in **your** package (not in the kernel). The strategy must be **pure**: no I/O, no `datetime.now()`; receive **EventEnvelope** and **ReferenceData**, return **StrategyResult** with **ProposedLine** specs using kernel types (Money, etc.).
- **Register** the strategy at application startup (e.g. before any posting):

  ```python
  from finance_kernel.domain.strategy_registry import register_strategy
  from my_package.my_strategy import MyCustomStrategy

  register_strategy(MyCustomStrategy())
  ```

- Use an **event_type** that only your strategy handles. Ensure a **profile** exists for that event type so the interpretation path can resolve roles and pass validation; the Bookkeeper path (if used) will dispatch to your strategy via **StrategyRegistry.get(event_type)**.

The kernel does not import your package. The kernel only requires that the strategy conforms to the public interface (R14, R15, R22, R23). You are responsible for lifecycle version and replay policy if replay is required.

**When to use:** Prefer config (profiles and ledger_effects) first. Use a custom strategy only when the transformation from event to lines cannot be expressed declaratively.

---

## 4. Custom engines (pure calculations)

For custom **calculations** (e.g. a custom allocation method, custom tax formula) that are invoked during posting or reconciliation:

- Implement a **pure function** (or small engine) in your package. Same contract as `finance_engines`: no I/O, kernel types (Money, Decimal, etc.), deterministic.
- Create an **invoker** that adapts the EngineDispatcher contract `(payload, FrozenEngineParams)` to your function’s signature (same pattern as **finance_services/invokers.py**).
- **Register** the engine with the same **EngineDispatcher** instance used by the orchestrator:

  ```python
  dispatcher.register("my_custom_engine", my_invoker)
  ```

- In **config**, reference the engine in the profile’s **required_engines** (or equivalent) so that the workflow executor or posting path calls it. Engine parameters can be supplied via **engine_params** in config.

The kernel never calls engines directly; **finance_services** (EngineDispatcher, invokers) do. So custom engines are added at the **services** layer. Do not modify `finance_engines` or kernel; keep your engine in a separate package and register it at startup after `register_standard_engines(dispatcher)`.

---

## 5. Optional: observe-only hooks (future)

A common ask is “run my code after every post” (e.g. notify, sync to another system). That should **not** change the posting outcome or journal content.

If you introduce hooks, they should:

- Be **observe-only**: receive (e.g.) event_id, outcome, entry_ids after the kernel has committed. They must not return a modified outcome or lines.
- Be **registered** at the services layer (e.g. on PostingOrchestrator or ModulePostingService), not inside the kernel.
- Be **best-effort**: failures in hooks must not cause the posting transaction to roll back. Run hooks after commit or in a separate side path.

The current codebase does not yet define a formal hook API; this is a placeholder for a small, explicit callback registry at the orchestration layer if you add it later.

---

## Summary

| Goal | How | Where custom code lives |
|------|-----|---------------------------|
| New event types, rules, workflows, COA | Config (YAML) | New or extended config set |
| New domain process / entity | New module | `finance_modules/` or customer package |
| Custom event → lines logic | Custom strategy + registration | Your package; register at startup |
| Custom calculation used by posting | Custom engine + invoker + registration | Your package; register with EngineDispatcher |
| Notify / sync after post | Observe-only hooks (future) | Your package; register on orchestrator |

The **core** (kernel, config compiler, invariants, posting authority) stays unchanged. Custom features are added at the **edges**: config content, new modules, and registered strategies/engines/hooks, with clear boundaries so that core logic is never bypassed or overridden.

---

## See also

- **CLAUDE.md** — Invariants and layer rules; R14/R15 (strategy/engine extension), R25 (primitives), R29 (posting authority).
- **docs/MODULE_DEVELOPMENT_GUIDE.md** — How to build a module and register strategies.
- **finance_config/README.md** — Single config entrypoint; all behavior from config.
- **finance_services/invokers.py** — How engines are registered and invoked.
