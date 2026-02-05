# Finance Config

**Single entrypoint for all accounting configuration.** No component may read config files or env vars directly; everything flows through `get_active_config()`.

## What this package does

- **Loads** YAML configuration sets from `sets/<config-set-id>/` (root, COA, ledgers, policies, controls, etc.).
- **Validates** against schema, guard AST, dispatch ambiguity, and role coverage.
- **Compiles** to a **CompiledPolicyPack** — the only artifact returned to callers. No raw YAML or file handles are exposed.
- **Optional fingerprint pinning**: when `APPROVED_FINGERPRINT` exists in a set, the compiled canonical fingerprint must match (integrity).

Callers (e.g. **finance_services**, **finance_modules**) obtain config once per legal entity and as-of date, then pass the pack into the kernel or use it to build role resolvers, policy sources, and guards.

## Public API

```python
from finance_config import get_active_config

pack = get_active_config(legal_entity="ACME", as_of_date=date.today())
# pack: CompiledPolicyPack (frozen). Use for policies, role_bindings, scope, etc.
```

- **`get_active_config(legal_entity, as_of_date, config_dir=None)`** — The only public entrypoint. Returns `CompiledPolicyPack`. Emits `FINANCE_CONFIG_TRACE` on success.
- **`validate_configuration(config_set)`** — Build-time validation (schema, guards, etc.); used internally and by tooling.

## Layout

| Area | Role |
|------|------|
| **loader.py** | Load and parse YAML fragments. |
| **schema.py** | Structured types for configuration sets (scope, policies, role bindings). |
| **assembler.py** | Assemble a set from a directory → `AccountingConfigurationSet`. |
| **compiler.py** | Compile to `CompiledPolicyPack` (guard AST, dispatch, checksum). |
| **integrity.py** | Fingerprint pin verification. |
| **validator.py** | Schema and structural validation. |
| **sets/** | One directory per configuration set (e.g. US-GAAP-2026-SINDRI); each has `root.yaml`, `chart_of_accounts.yaml`, `policies/*.yaml`, and optionally `import_mappings/` for finance_ingestion. |

## Boundaries

- **Kernel**: Must **never** import from `finance_config`. Bridges in this package (or in services) translate compiled packs into kernel-compatible inputs (e.g. policy source, role resolver).
- **Consumers**: **finance_services** (e.g. PostingOrchestrator) and **finance_modules** call `get_active_config()` and pass the pack into the kernel or use it to build policy sources and role bindings.

## See also

- **finance_services/README.md** — Orchestration layer that consumes config and wires the kernel.
- **finance_kernel/README.md** — Invariants and interpretation layer; no config dependency.
- **CLAUDE.md** — Single entrypoint rule and layer diagram.
- **finance_ingestion/README.md** — Staging and promotion use import_mappings from config.
- **docs/EXTENSIBILITY.md** — How to extend behavior via config and registration without changing core.
