# Plan: Three Tiered Configuration Sets

## Objective

Create three standalone on-disk configuration sets under `finance_config/sets/`:
- **US-GAAP-2026-STARTUP** — 5 modules (inventory, payroll, cash, gl, expense)
- **US-GAAP-2026-MIDMARKET** — 10 modules (+ap, ar, assets, tax, procurement)
- **US-GAAP-2026-ENTERPRISE** — 19 modules (all)

Each set is a complete, self-contained directory with full YAML: `root.yaml`, `chart_of_accounts.yaml`, `ledgers.yaml`, `controls.yaml`, `engine_params.yaml`, `subledger_contracts.yaml`, and `policies/`. Rewire B9 benchmarks to load each tier directly from disk instead of runtime filtering.

---

## Config Selection Strategy

The existing `_find_matching_config()` in `finance_config/__init__.py:161-242` already supports multiple config sets via scope matching + version tiebreaking:

| Config Set | `legal_entity` | `version` | Who matches it |
|---|---|---|---|
| US-GAAP-2026-v1 (unchanged) | `*` (wildcard) | 1 | All existing tests via `get_active_config("*", ...)` |
| US-GAAP-2026-STARTUP | `STARTUP` | 2 | `get_active_config("STARTUP", ...)` |
| US-GAAP-2026-MIDMARKET | `MIDMARKET` | 2 | `get_active_config("MIDMARKET", ...)` |
| US-GAAP-2026-ENTERPRISE | `ENTERPRISE` | 2 | `get_active_config("ENTERPRISE", ...)` |

When `legal_entity="STARTUP"` is queried, both STARTUP (exact) and v1 (wildcard) match. STARTUP wins via version 2 > 1. When `legal_entity="*"` is queried (existing tests), only v1 matches — new sets have specific entity names. **No code changes to `finance_config/__init__.py`.**

---

## Phase 1: Build Generation Script

**File:** `scripts/generate_tier_configs.py`

A Python script that programmatically derives each tier's config from the v1 source of truth. This ensures:
- No missing role bindings (programmatic trace is exhaustive)
- Easy regeneration when v1 changes
- Consistency across sets

### Algorithm per tier:
1. Load v1 config via `assemble_from_directory()`
2. Filter `policies` by module membership
3. Trace ALL roles from filtered policies' `ledger_effects[].debit_role/credit_role` + `line_mappings[].role`
4. Add `ROUNDING` role (always required by Bookkeeper)
5. Filter `subledger_contracts` by `owner_module`
6. Add `control_account_role` from filtered subledger contracts
7. Filter `role_bindings` to only traced roles (preserving ledger discrimination)
8. Determine required ledgers from policies' `ledger_effects[].ledger` + subledger contracts
9. Filter `engine_configs` to only engines in filtered policies' `required_engines`
10. Write `root.yaml` with tier-specific `config_id`, `legal_entity`, `version: 2`, strict `capabilities`
11. Write `chart_of_accounts.yaml` with filtered role_bindings
12. Write `ledgers.yaml` with required ledger definitions
13. Copy `controls.yaml` verbatim (universal)
14. Write `engine_params.yaml` with filtered engines
15. Write `subledger_contracts.yaml` with filtered contracts
16. Copy only the tier's policy YAML files to `policies/`
17. Validate: call `assemble_from_directory()` + `compile_policy_pack()` on the output

### Tier module definitions:
```
STARTUP:    inventory, payroll, cash, gl, expense
MIDMARKET:  + ap, ar, assets, tax, procurement
ENTERPRISE: + wip, contracts, reporting, revenue, lease, budget, intercompany, credit_loss, project
```

### Expected outputs per tier:

| Tier | Policies | Engines | Subledger Contracts | Ledgers |
|---|---|---|---|---|
| STARTUP | ~61 | variance, allocation | INVENTORY, BANK | GL, INVENTORY, BANK |
| MIDMARKET | ~117 | +matching, tax, aging | +AP, AR | +AP, AR |
| ENTERPRISE | ~198 | +billing, allocation_cascade, ice | +WIP | +CONTRACT |

---

## Phase 2: Run Generation Script

Execute the script to create all three config sets:

```
finance_config/sets/US-GAAP-2026-STARTUP/
  root.yaml
  chart_of_accounts.yaml
  ledgers.yaml
  controls.yaml
  engine_params.yaml
  subledger_contracts.yaml
  policies/  (5 files: inventory, payroll, cash, gl, expense)

finance_config/sets/US-GAAP-2026-MIDMARKET/
  root.yaml
  chart_of_accounts.yaml
  ledgers.yaml
  controls.yaml
  engine_params.yaml
  subledger_contracts.yaml
  policies/  (10 files: + ap, ar, assets, tax, procurement)

finance_config/sets/US-GAAP-2026-ENTERPRISE/
  root.yaml
  chart_of_accounts.yaml
  ledgers.yaml
  controls.yaml
  engine_params.yaml
  subledger_contracts.yaml
  policies/  (18 files: all modules)
```

### Validation (inline in script):
- Each set assembles without error
- Each set compiles without `CompilationFailedError`
- Policy counts match expectations
- Zero compiler warnings for missing role bindings

---

## Phase 3: Rewire Benchmark Infrastructure

### 3a. `tests/benchmarks/tier_config.py`

Add `load_tier_config()`:

```python
_TIER_ENTITY_MAP = {
    "SIMPLE": "STARTUP",
    "MEDIUM": "MIDMARKET",
    "FULL": "ENTERPRISE",
}

def load_tier_config(tier: ComplexityTier) -> CompiledPolicyPack:
    """Load a tier's config directly from its on-disk config set."""
    from finance_config import get_active_config
    legal_entity = _TIER_ENTITY_MAP[tier.name]
    return get_active_config(legal_entity=legal_entity, as_of_date=date(2026, 6, 15))
```

Keep `filter_compiled_pack()` (backward compat). Keep `register_tier_modules()`.

### 3b. `tests/benchmarks/test_bench_full_event_coverage.py`

Replace:
```python
full_config = get_active_config(legal_entity="*", as_of_date=EFFECTIVE)
tier_config = filter_compiled_pack(full_config, tier)
```

With:
```python
from tests.benchmarks.tier_config import load_tier_config
tier_config = load_tier_config(tier)
```

### 3c. Check other benchmark files

Grep for `filter_compiled_pack` usage and update similarly.

---

## Phase 4: Verify

### 4a. B9 benchmarks
```bash
python3 -m pytest tests/benchmarks/test_bench_full_event_coverage.py -v --tb=short
```
Expected: Same pass counts as before (SIMPLE 58/61, MEDIUM 93/117, FULL 169/198)

### 4b. Full regression
```bash
python3 -m pytest tests/ -v --tb=short -x
```
Expected: All existing tests pass (they load v1 via `legal_entity="*"`)

### 4c. Direct loading verification
Quick smoke test that each new config_id loads correctly:
```python
from finance_config import get_active_config
from datetime import date
for entity in ("STARTUP", "MIDMARKET", "ENTERPRISE"):
    pack = get_active_config(entity, date(2026, 6, 15))
    print(f"{pack.config_id}: {len(pack.policies)} policies")
```

---

## Files Created

| File | Purpose |
|---|---|
| `scripts/generate_tier_configs.py` | Generation script (source of truth for tier derivation) |
| `finance_config/sets/US-GAAP-2026-STARTUP/` (7 files + 5 policy files) | STARTUP config set |
| `finance_config/sets/US-GAAP-2026-MIDMARKET/` (7 files + 10 policy files) | MIDMARKET config set |
| `finance_config/sets/US-GAAP-2026-ENTERPRISE/` (7 files + 18 policy files) | ENTERPRISE config set |

## Files Modified

| File | Change |
|---|---|
| `tests/benchmarks/tier_config.py` | Add `load_tier_config()` and `_TIER_ENTITY_MAP` |
| `tests/benchmarks/test_bench_full_event_coverage.py` | Replace `filter_compiled_pack` with `load_tier_config` |

## Files NOT Modified

| File | Reason |
|---|---|
| `finance_config/__init__.py` | Version tiebreaking handles selection natively |
| `finance_config/assembler.py` | No changes needed |
| `finance_config/compiler.py` | No changes needed |
| `tests/conftest.py` | `test_config` uses `legal_entity="*"` → still matches only v1 |
| `finance_config/sets/US-GAAP-2026-v1/` | Left completely untouched |

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Missing role binding → L1 violation at posting time | Generation script traces roles programmatically from every policy |
| Wrong config loaded by existing tests | v1 wildcard scope only matches `legal_entity="*"` queries; new sets use specific entities |
| Policy files drift between v1 and tier copies | Re-run generation script when v1 changes |
| Assembler slowdown scanning 5 directories | Config loading is build/test-time only; negligible impact |
