# Architecture Refactoring & Centralized Configuration — Implementation Plan

**Objective:** Refactor the finance kernel architecture to make authority boundaries and purity guarantees explicit through naming, packaging, configuration, and documentation. Centralize regulatory and company behavior into a single, versioned, immutable, replayable configuration artifact. Add enforcement, tests, and tracing so "defined but unused" components cannot persist unnoticed.

**Orthogonality principle:** Regulatory and company policy changes must be expressed as configuration changes. Algorithmic and performance changes must be expressed as code changes. These must remain orthogonal.

---

## Progress Tracker

| Part | Description | Status | Notes |
|------|-------------|--------|-------|
| 0 | Kernel Boundary & Invariants Contract | DONE | 8 tests, invariants.py |
| 1 | AccountingConfigurationSet — Schema, Loader, Compiler | DONE | 9 files, 129 policies in YAML, round-trip verified |
| 2 | Rename Conceptual Layers | DONE | 5 files renamed, 39 files updated, 13 rename patterns |
| 3 | Purity Split — engines vs services | NOT STARTED | |
| 4 | Engine Contracts | NOT STARTED | |
| 5 | Wire CompiledPolicyPack into Runtime | NOT STARTED | |
| 6 | Architecture Enforcement | NOT STARTED | |
| 7 | Runtime Tracing | NOT STARTED | |
| 8 | Policy-to-Engine Binding | NOT STARTED | |
| 9 | Tests — Wiring, Bypass, Dead Components | NOT STARTED | |
| 10 | Wire Remaining 11 Module Services | NOT STARTED | Inventory done prior |
| 11 | Documentation Updates | NOT STARTED | |

---

## Part 0: Kernel Boundary & Invariants Contract

The kernel is structural law. Its invariants are non-configurable — no configuration set, capability toggle, or policy may override balance enforcement, immutability, period rules, or link legality. This part makes that explicit and enforced.

### 0A: Kernel invariants declaration

Kernel invariants are hardcoded in the posting boundary. They are not exposed to or influenced by AccountingConfigurationSet. Specifically:

**Non-configurable invariants (kernel law):**
- Double-entry balance enforcement (debits = credits per journal entry)
- Immutability of posted journal entries (append-only ledger)
- Period lock enforcement (no posting to closed periods)
- Link legality (economic links between valid entries only)
- Sequence monotonicity (event sequence numbers never go backwards)
- Idempotency (same event cannot be posted twice)

**Configurable through AccountingConfigurationSet:**
- Which policies exist and when they apply
- Which accounts map to which roles
- Engine parameters and thresholds
- Guard expressions and control rules
- Capability toggles

### 0B: Architecture dependency rules for `finance_kernel/**`

**`finance_kernel/**` (entire package, not just `domain/`) may NOT import:**
- `finance_services/**`
- `finance_config/**`
- `finance_modules/**`

The only allowed direction: services and config load into orchestrators; orchestrators call kernel; kernel never depends upward.

### 0C: Posting boundary exclusivity

Nothing writes journal rows except the kernel posting boundary (`JournalWriter` / `InterpretationCoordinator`). This is enforced by:

- DB triggers that reject direct inserts without the kernel's session marker
- Architecture gate: forbid imports of `finance_kernel.models.journal` outside the kernel services layer and a narrow allowlist (test fixtures)
- Negative bypass tests (Part 9.5)

### 0D: Tests

- Assert that kernel invariant code has no dependency on `finance_config` or `finance_services`
- Assert that `finance_kernel.models.journal` is not imported by module services or engine code
- Assert that kernel posting boundary is the sole writer of journal rows

---

## Part 1: AccountingConfigurationSet — Schema, Loader, Compiler

### 1A: Two distinct artifacts

**AccountingConfigurationSet** = human-authored, reviewable source artifact. Composed from fragments. Stored as YAML.

**CompiledPolicyPack** = machine-validated, frozen runtime artifact. Produced by compiler from ConfigurationSet. The only object that runtime posting entrypoints accept.

Rule: YAML loading is build/test tooling only. No service or orchestrator ever touches YAML or raw ConfigurationSet at runtime.

### 1B: Create `finance_config/` package

```
finance_config/
├── __init__.py              # Exports get_active_config() — the ONLY public entrypoint
├── schema.py                # Pydantic/dataclass models for ConfigurationSet
├── loader.py                # Load YAML fragments → assembled ConfigurationSet (private)
├── validator.py             # Build-time and runtime validation
├── compiler.py              # ConfigurationSet → CompiledPolicyPack
├── lifecycle.py             # DRAFT → PUBLISHED lifecycle + migration semantics
├── guard_ast.py             # Restricted AST for guard expressions
├── assembler.py             # Compose fragments into one ConfigurationSet
└── sets/                    # Versioned configuration fragments
    └── US-GAAP-2026-v1/
        ├── root.yaml                # Scope, identity, predecessor, capabilities
        ├── chart_of_accounts.yaml   # Role bindings
        ├── ledgers.yaml             # Ledger definitions
        ├── policies/
        │   ├── inventory.yaml       # Inventory domain policies
        │   ├── ap.yaml              # AP domain policies
        │   ├── ar.yaml              # AR domain policies
        │   └── ...                  # One per domain
        ├── engine_params.yaml       # Engine configuration parameters
        ├── controls.yaml            # Governance and control rules
        └── dcaa_overlay.yaml        # DCAA/CAS overlay (conditional)
```

**Fragment composition model:** Humans edit small, well-owned fragments. The assembler composes them into one `AccountingConfigurationSet` at build time. Runtime only sees the single `CompiledPolicyPack`. This preserves "single source of truth at runtime" while avoiding a monolithic 4,000-line YAML.

### 1C: Define `AccountingConfigurationSet` schema

**File:** `finance_config/schema.py`

```python
@dataclass(frozen=True)
class AccountingConfigurationSet:
    config_id: str                    # e.g., "US-GAAP-2026-v1"
    version: int
    checksum: str                     # SHA-256 of canonical serialization
    predecessor: str | None           # Previous config_id (append-only chain)
    scope: ConfigScope
    status: ConfigStatus
    policies: tuple[PolicyDefinition, ...]
    policy_precedence_rules: tuple[PrecedenceRule, ...]
    role_bindings: tuple[RoleBinding, ...]
    ledger_definitions: tuple[LedgerDefinition, ...]
    engine_configs: dict[str, dict[str, Any]]      # engine_name → params
    engine_versions: dict[str, str]                 # engine_name → version constraint
    controls: tuple[ControlRule, ...]
    capabilities: dict[str, bool]                   # feature gates
    subledger_contracts: tuple[SubledgerContract, ...]

@dataclass(frozen=True)
class ConfigScope:
    legal_entity: str
    jurisdiction: str
    regulatory_regime: str           # GAAP, IFRS, DCAA, CAS
    currency: str
    effective_from: date
    effective_to: date | None = None

@dataclass(frozen=True)
class PolicyDefinition:
    """Declarative policy data — no executable logic."""
    name: str
    version: int
    trigger: PolicyTriggerDef        # event_type + where conditions
    meaning: PolicyMeaningDef        # economic_type, quantity_field, dimensions
    ledger_effects: tuple[LedgerEffectDef, ...]
    guards: tuple[GuardDef, ...]     # restricted AST expressions
    effective_from: date
    effective_to: date | None
    required_engines: tuple[str, ...]
    engine_parameters_ref: str | None  # key in engine_configs (resolved at compile time)
    capability_tags: tuple[str, ...]   # e.g., ("DCAA",), ("IFRS",), ("PROJECTS",)
    precedence: PrecedenceDef | None
    description: str

@dataclass(frozen=True)
class RoleBinding:
    role: str                        # e.g., "INVENTORY"
    ledger: str                      # e.g., "GL"
    account_code: str                # e.g., "1200"
    effective_from: date
    effective_to: date | None = None

@dataclass(frozen=True)
class ControlRule:
    name: str
    applies_to: str                  # Event type pattern ("payroll.*" or "*")
    action: str                      # "reject" or "block"
    expression: str                  # restricted AST expression
    reason_code: str
    message: str = ""

@dataclass(frozen=True)
class GuardDef:
    guard_type: str                  # "reject" or "warn"
    expression: str                  # restricted AST (no arbitrary code)
    reason_code: str
    message: str = ""
```

**Key rules:**
- `engine_configs` keyed by engine_name, values are parameter dicts
- `capabilities` evaluated by PolicyAuthority to allow/reject policy groups via `capability_tags`
- `predecessor` creates an append-only version chain
- Guards and control expressions use a restricted AST (section 1H)
- `engine_parameters_ref` is resolved at compile time into typed, frozen parameter objects — no string lookups at runtime

### 1D: Capability tagging and PolicyAuthority filtering

Three distinct concepts that must not be conflated:

1. **Regulatory regime selection** — GAAP vs IFRS vs GAAP+DCAA overlays (set in `scope.regulatory_regime`)
2. **Capability enablement** — Feature gates in `capabilities` dict (e.g., `{"dcaa": true, "inventory": true, "multicurrency": false}`)
3. **Policy admissibility** — PolicyAuthority filters policies by matching `capability_tags` against enabled `capabilities`

**Filtering mechanics:**
- Policies declare `capability_tags` (e.g., `("DCAA",)`, `("IFRS",)`, `("PROJECTS",)`)
- PolicyAuthority filters: a policy is admissible only if ALL its `capability_tags` are enabled in `config.capabilities`
- PolicySelector then resolves precedence among remaining admissible policies

**"Turn DCAA off" means mechanically:**
- Set `capabilities.dcaa = false`
- All DCAA-tagged policies are removed from the admissible set
- Postings that would require those policies either use a GAAP fallback policy or are rejected

**Fallback semantics:**
- If no admissible policy matches an event: REJECT (no silent fallthrough)
- Explicit fallback policy groups may be declared in config (a GAAP policy that covers the same event type as a DCAA policy, with lower precedence)
- The compiler warns if disabling a capability would leave event types with no admissible policy

### 1E: Data vs machinery boundary

**In ConfigurationSet (data — versioned, replayed):**
- Which policies exist and their matching rules
- Policy precedence and effective dating
- Engine parameter values and thresholds
- Guard expressions (restricted AST, numeric thresholds)
- Account role → COA bindings
- Ledger definitions and required roles
- Control rules
- Capabilities and capability tags

**In Code (machinery — tested, rarely changed):**
- PolicySelector precedence algorithm
- Engine algorithms (FIFO/LIFO, variance math, matching)
- Guard evaluation engine
- Role resolution machinery
- JournalWriter balancing and posting enforcement
- Control evaluation engine

**Rules:**
- Configuration must never embed executable logic
- Guards compile to a restricted AST with a fixed operator set
- Arbitrary code, imports, or dynamic evaluation must fail validation

**Rationale:**
- Regulatory change → change ConfigurationSet fragments
- Algorithm improvement → change code
- These must remain orthogonal

### 1F: Build YAML loader and assembler (private)

**File:** `finance_config/loader.py` — loads individual YAML fragment files

**File:** `finance_config/assembler.py` — composes fragments into one `AccountingConfigurationSet`

```python
class _ConfigurationAssembler:
    """Composes fragments into a single AccountingConfigurationSet.

    Build/test tooling only. Runtime never calls this.
    """
    @staticmethod
    def assemble(fragment_dir: Path) -> AccountingConfigurationSet: ...
```

### 1G: Single public entrypoint

**File:** `finance_config/__init__.py`

```python
def get_active_config(
    legal_entity: str,
    as_of_date: date,
    config_dir: Path | None = None,
) -> CompiledPolicyPack:
    """The ONLY public configuration entrypoint.

    No other component may read configuration files, environment
    variables, or feature flags. All configuration flows through here.

    Returns a CompiledPolicyPack — the sole runtime artifact.
    YAML loading is internal build/test tooling.
    """
```

**Hard rule:** Only `finance_config.get_active_config(...)` may load configuration. Architecture tests enforce this. Runtime posting entrypoints accept only `CompiledPolicyPack`.

### 1H: Build validator

**File:** `finance_config/validator.py`

Validates:
- All role bindings reference roles used by policies
- All policies have unique dispatch keys (no overlapping triggers for same scope)
- No overlapping effective ranges for the same scope
- All engine configs have valid parameter ranges per engine schema
- All control rules have parseable restricted-AST expressions
- All guards have parseable restricted-AST expressions
- Balance: every policy's ledger effects use roles that have bindings
- All `capability_tags` reference capabilities declared in config
- Disabling any single capability does not leave event types with zero admissible policies (warning)
- Returns `ConfigValidationResult` with errors and warnings

### 1I: Restricted guard AST

**File:** `finance_config/guard_ast.py`

Fixed operator set for guard/control expressions:
- Comparisons: `<`, `<=`, `>`, `>=`, `==`, `!=`
- Logical: `and`, `or`, `not`
- Field access: `payload.field_name`
- Literals: numbers, strings, booleans, None
- Functions: `abs()`, `len()`, `in`, `not_in`

Validation must reject: imports, function calls (except whitelist), attribute access beyond payload, lambda, eval, exec.

### 1J: CompiledPolicyPack

**File:** `finance_config/compiler.py`

Build-time artifact derived from AccountingConfigurationSet:

```python
@dataclass(frozen=True)
class CompiledPolicyPack:
    config_id: str
    config_version: int
    checksum: str                    # Matches source ConfigurationSet
    scope: ConfigScope
    policies: tuple[CompiledPolicy, ...]     # Validated, indexed
    match_index: PolicyMatchIndex            # Pre-built dispatch index
    role_bindings: tuple[RoleBinding, ...]
    engine_contracts: dict[str, ResolvedEngineContract]
    resolved_engine_params: dict[str, FrozenEngineParams]  # Pre-resolved, typed
    controls: tuple[CompiledControl, ...]
    capabilities: dict[str, bool]
    canonical_fingerprint: str       # Deterministic hash of entire pack
    decision_trace: PolicyDecisionTrace      # Build-time debugging artifact
```

**Runtime must load only CompiledPolicyPack, not raw YAML or ConfigurationSet.**

**Compilation validates:**
- Every policy's `required_engines` exists in engine contracts
- Every policy's `engine_parameters_ref` satisfies the engine's `parameter_schema`
- Guard ASTs parse successfully
- No ambiguous dispatch (see 1K)

**Engine parameter resolution at compile time:**
- The compiler resolves each policy's `engine_parameters_ref` string key against `engine_configs` and the engine contract's `parameter_schema`
- The result is frozen into `resolved_engine_params` as typed, validated parameter objects
- Services receive already-resolved, typed parameter objects — no string lookups at runtime
- This eliminates stringly-typed drift between policies and engine configs

### 1K: Deterministic dispatch guarantee

For each `event_type`, the compiler must produce a deterministic decision procedure:

1. Exact match set (all policies whose trigger matches the event type + where clauses)
2. Filter by effective date
3. Filter by capability tags against enabled capabilities
4. Sort by precedence tuple: (specificity, priority, scope depth)
5. Must yield exactly one winner or fail compilation

The compiler emits a **PolicyDecisionTrace** — a build-time artifact (not runtime logs) that records, for every event_type, the full decision tree. This is for debugging and audit, not runtime.

### 1L: Configuration lifecycle and migration

**File:** `finance_config/lifecycle.py`

```python
class ConfigStatus(str, Enum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
```

**Migration semantics:**
- Configuration sets are append-only
- Each version must declare its predecessor via `predecessor` field
- No overlapping effective ranges for the same scope
- CI must fail if ambiguity is detected
- Only PUBLISHED configs can be used for posting
- Transition rules enforced (no skipping steps)
- Superseded configs remain for replay/audit

### 1M: Generate initial fragments from existing code

Write a migration script that:
1. Reads all registered EconomicProfiles from Python code
2. Reads all 12 module config files for parameters
3. Reads RoleResolver bindings from conftest.py as reference
4. Reads PolicyRegistry defaults for controls
5. Writes `finance_config/sets/US-GAAP-2026-v1/` fragment directory

**Verification:** Assemble fragments → compile to CompiledPolicyPack → register all policies from it → run existing test suite — all pass.

---

## Part 2: Rename Conceptual Layers

Behavior-preserving renames applied everywhere (code, docs, comments, diagrams):

| Old Name | New Name | Reason |
|----------|----------|--------|
| `EconomicProfile` | `AccountingPolicy` | Declarative rule for how events become accounting intent |
| `ProfileRegistry` | `PolicySelector` | Selects exactly one policy for a given event |
| `PolicyRegistry` | `PolicyAuthority` | Governs which policies are admissible |
| `ProfileTrigger` | `PolicyTrigger` | Consistent with parent rename |
| `ProfileMeaning` | `PolicyMeaning` | Consistent with parent rename |
| `ProfilePrecedence` | `PolicyPrecedence` | Consistent with parent rename |
| `ProfileCompiler` | `PolicyCompiler` | Consistent with parent rename |
| `ModuleProfileRegistry` | `ModulePolicyRegistry` | Consistent |
| `ModuleProfileEntry` | `ModulePolicyEntry` | Consistent |
| `profile_bridge.py` | `policy_bridge.py` | Consistent |
| `profile_registry.py` | `policy_selector.py` | Consistent |
| `profile_compiler.py` | `policy_compiler.py` | Consistent |
| `economic_profile.py` | `accounting_policy.py` | Consistent |

### Files affected (~50 files):

**Kernel domain (rename class + file):**
- `finance_kernel/domain/economic_profile.py` → `accounting_policy.py`
- `finance_kernel/domain/profile_registry.py` → `policy_selector.py`
- `finance_kernel/domain/policy_registry.py` → `policy_authority.py`
- `finance_kernel/domain/profile_bridge.py` → `policy_bridge.py`
- `finance_kernel/domain/profile_compiler.py` → `policy_compiler.py`

**Kernel domain (update imports):**
- `meaning_builder.py`, `reference_snapshot.py`, `accounting_intent.py`, `valuation.py`

**Kernel services (update imports):**
- `module_posting_service.py`, `interpretation_coordinator.py`, `journal_writer.py`

**Module profiles (12 files — update imports):**
- All `finance_modules/*/profiles.py`

**Tests (~25 files — update imports and references)**

**Docs:**
- `ARCHITECTURE_VERIFICATION.md`, `MODULE_DEVELOPMENT_GUIDE.md`, `WIRING_PLAN.md`, README files

### Execution:

1. Rename files (`git mv`)
2. Find-and-replace class names in all `.py` files
3. Update all import paths
4. Run full test suite — must pass identically

---

## Part 3: Purity Split — `finance_engines/` vs `finance_services/`

### 3A: Engines that must move to `finance_services/`

Six engines violate the purity rule:

| Engine | Problem | Action |
|--------|---------|--------|
| `ValuationLayer` | Session, LinkGraphService | MOVE to `finance_services/valuation_service.py` |
| `ReconciliationManager` | Session, LinkGraphService | MOVE to `finance_services/reconciliation_service.py` |
| `CorrectionEngine` | Session, LinkGraphService, SQLAlchemy select | MOVE to `finance_services/correction_service.py` |
| `ICEEngine` | `time.monotonic()`, logging I/O | MOVE to `finance_services/ice_service.py` |
| `BillingEngine` | `time.monotonic()`, logging I/O | MOVE to `finance_services/billing_service.py` |
| `SubledgerService` | `time.monotonic()`, `datetime.now()`, `date.today()`, abstract session | MOVE to `finance_services/subledger_service.py` |

Their pure domain objects STAY in `finance_engines/`:
- `valuation/cost_lot.py` (CostLot, CostLayer, ConsumptionResult)
- `reconciliation/domain.py` (ReconciliationState, PaymentApplication)
- `correction/unwind.py` (CompensatingLine, CompensatingEntry)
- `subledger.py` domain types (SubledgerEntry, SubledgerEntryType — enums + frozen dataclasses only)
- `ice.py` pure computation helpers (schedule math, cost pool calculations — if extractable)
- `billing.py` pure computation helpers (milestone calculations, T&M rate calculations — if extractable)

### 3B: Target structure

```
finance_engines/              # PURE ONLY — no session, no I/O, no clock
├── __init__.py
├── variance.py              # VarianceCalculator — pure
├── allocation.py            # AllocationEngine — pure
├── allocation_cascade.py    # AllocationCascade — pure
├── matching.py              # MatchingEngine — pure
├── aging.py                 # AgingCalculator — pure
├── tax.py                   # TaxCalculator — pure
├── tracer.py                # Engine invocation decorator (Part 7)
├── contracts.py             # EngineContract declarations (Part 4)
├── valuation/
│   └── cost_lot.py          # Pure domain: CostLot, ConsumptionResult
├── reconciliation/
│   └── domain.py            # Pure domain: ReconciliationState
├── correction/
│   └── unwind.py            # Pure domain: CompensatingEntry
├── subledger/
│   └── domain.py            # Pure domain: SubledgerEntry + enums
├── ice/
│   └── domain.py            # Pure domain: schedule math, cost pool types
└── billing/
    └── domain.py            # Pure domain: milestone, T&M rate types

finance_services/             # STATEFUL — session, I/O, orchestration
├── __init__.py
├── valuation_service.py     # ValuationLayer (moved)
├── reconciliation_service.py # ReconciliationManager (moved)
├── correction_service.py    # CorrectionEngine (moved)
├── ice_service.py           # ICEEngine (moved)
├── billing_service.py       # BillingEngine (moved)
└── subledger_service.py     # SubledgerService (moved)
```

### 3C: Dependency direction

- Services may call engines
- Engines must NEVER import services, ORM, configuration loaders, or `finance_config/**`
- Kernel never depends upward (no `finance_services`, `finance_config`, `finance_modules`)

### 3D: Update imports

- Module services import stateful code from `finance_services/`
- `finance_engines/__init__.py`: Remove impure exports
- Run full test suite — must pass

---

## Part 4: Engine Contracts

### 4A: EngineContract declaration

**File:** `finance_engines/contracts.py`

Each engine declares a contract in code:

```python
@dataclass(frozen=True)
class EngineContract:
    engine_name: str
    engine_version: str
    parameter_schema: dict[str, Any]      # JSON Schema for required params
    input_fingerprint_rules: tuple[str, ...]  # Fields used in fingerprint
    parameter_key: str                    # Canonical config key (not arbitrary string)

VARIANCE_CONTRACT = EngineContract(
    engine_name="variance",
    engine_version="1.0",
    parameter_schema={"type": "object", "properties": {...}},
    input_fingerprint_rules=("standard_cost", "actual_cost", "quantity"),
    parameter_key="variance",
)

MATCHING_CONTRACT = EngineContract(...)
ALLOCATION_CONTRACT = EngineContract(...)
# ... one per engine
```

**`parameter_key`**: The canonical key that policies use to reference this engine's parameters. Policies reference `parameter_key`, not arbitrary strings. The compiler resolves the key to the concrete parameter dict and freezes it into the CompiledPolicyPack.

### 4B: Compile-time validation

Policy compilation must fail if:
- A policy references a non-existent engine (via `required_engines`)
- A policy's `engine_parameters_ref` does not match any engine contract's `parameter_key`
- Required parameters do not satisfy the engine's `parameter_schema`

This is enforced in `finance_config/compiler.py` during CompiledPolicyPack creation. The result is typed, validated, frozen parameter objects — no runtime string lookups.

---

## Part 5: Wire CompiledPolicyPack into Runtime

### 5A: Single posting facade

One authoritative service entrypoint for posting: `ModulePostingService` (or `InterpretationCoordinator` for Pipeline A). Module services depend on this facade rather than reaching around it.

Architecture gate: forbid imports of `finance_kernel.models.journal` outside the kernel services layer and a narrow allowlist. This prevents "someone wrote journal rows directly in a module service."

### 5B: Updated runtime flow

For every event:

1. `finance_config.get_active_config(scope, as_of_date)` → `CompiledPolicyPack`
2. `PolicyAuthority` filters admissible policies using scope, controls, and capability tags
3. `PolicySelector` selects exactly one `AccountingPolicy` from admissible set
4. `AccountingPolicy` produces `AccountingIntent` and declares required engines
5. Services orchestrate and invoke engines using `pack.resolved_engine_params[engine_name]` (pre-resolved, typed)
6. Kernel validates and records immutably with atomic outcome recording
7. `ReferenceSnapshot` includes `config_set_id`, `version`, and `checksum`

### 5C: Update `ModulePostingService` to accept CompiledPolicyPack

```python
class ModulePostingService:
    def __init__(
        self,
        session: Session,
        config: CompiledPolicyPack,        # The sole runtime config object
        clock: Clock | None = None,
        auto_commit: bool = True,
    ):
        self._role_resolver = self._build_resolver(config.role_bindings)
        self._meaning_builder = MeaningBuilder(
            policy_authority=config.build_policy_authority()
        )
```

### 5D: Update ReferenceSnapshot

Add `CONFIGURATION_SET` to `SnapshotComponentType`. Every JournalEntry records which ConfigurationSet was active, enabling perfect replay.

### 5E: Remove `account_mappings` from module configs

Role bindings come from CompiledPolicyPack. Module configs retain only non-behavioral operational parameters that also flow from `config.resolved_engine_params`.

### 5F: Update test fixtures

```python
@pytest.fixture(scope="session")
def test_config() -> CompiledPolicyPack:
    return get_active_config(
        legal_entity="TEST_ENTITY",
        as_of_date=date(2026, 1, 1),
        config_dir=Path("finance_config/sets"),
    )

@pytest.fixture
def module_posting_service(session, test_config, deterministic_clock):
    return ModulePostingService(
        session=session, config=test_config,
        clock=deterministic_clock, auto_commit=False,
    )
```

**Verification:** Full test suite passes with compiled config.

---

## Part 6: Architecture Enforcement

### 6A: AST import scanner

**Create:** `tests/architecture/test_import_boundaries.py`

Rules:

**`finance_engines/**` may NOT import:**
- SQLAlchemy or DB drivers
- `finance_kernel.models`, `finance_kernel.db`, sessions
- `finance_services/**`
- `finance_config/**`
- `datetime.now`, `time.time`, `time.monotonic`, environment variables

**`finance_kernel/**` (entire package) may NOT import:**
- `finance_services/**`
- `finance_config/**`
- `finance_modules/**`

**`finance_kernel/domain/**` may NOT import:**
- ORM or DB packages
- Network or OS I/O

**`finance_services/**` MAY import:**
- `finance_engines/**`
- `finance_kernel/**`
- ORM/DB layers
- `finance_config/**`

**Configuration enforcement:**
- Only `finance_config.get_active_config` may load configuration files
- Engines must receive parameters as arguments only (pre-resolved from CompiledPolicyPack)
- Services may only read configuration via the in-memory CompiledPolicyPack

**Posting boundary enforcement:**
- `finance_kernel.models.journal` (JournalEntry, JournalLine) may only be imported by files in `finance_kernel/services/` and an explicit test allowlist
- Module services and engine code must never directly import journal models

### 6B: Error output format

Architecture tests must emit:
- File path
- Violating import
- Full import chain (how the forbidden dependency was reached)

### 6C: Boundary violation test

Confirm that introducing a forbidden import in an engine file fails the architecture test suite.

These tests must run in CI.

---

## Part 7: Runtime Tracing

Tracing must make it provable, from logs alone, that the correct layers were used.

Trace chain: ConfigurationSet → PolicyAuthority → PolicySelector → AccountingPolicy → Engines → Kernel → Outcome

### 7A: Trace context (unforgeable correlation)

Implement a per-interpretation **trace context** (trace-local, not global):

```python
@dataclass
class InterpretationTraceContext:
    trace_id: str
    event_id: str
    config_set_id: str
    config_set_version: int
    engines_invoked: list[EngineInvocationRecord]  # Populated by engine tracer
```

- The engine tracer writes to this context on every engine invocation
- The kernel boundary reads from this context to populate `engines_used` in `FINANCE_KERNEL_TRACE`
- This makes `engines_used` unforgeable — it reflects actual tracer events, not manually assembled lists
- Wiring tests assert consistency: tracer events match kernel trace's `engines_used`

### 7B: Configuration trace

Emit `FINANCE_CONFIG_TRACE`:
- `trace_id`, `event_id`
- `config_set_id`, `config_set_version`, `checksum`
- `scope` (legal_entity, jurisdiction, regime)
- `timestamp`

### 7C: Policy trace

Instrument `PolicySelector.find_for_event()` to emit `FINANCE_POLICY_TRACE`:
- `trace_id`, `event_id`
- `config_set_id`, `config_set_version`
- `admissible_policies` (list after PolicyAuthority filtering by capability tags)
- `selected_policy`, `selected_policy_version`
- `precedence_reason` (effective date / specificity / priority)
- `timestamp`

### 7D: Engine invocation trace

**File:** `finance_engines/tracer.py`

Decorator emitting `FINANCE_ENGINE_TRACE` and writing to `InterpretationTraceContext`:
- `trace_id`, `event_id`
- `config_set_id`, `config_set_version`
- `engine_name`, `engine_version`
- `input_fingerprint` (hash of canonicalized inputs, per engine's `input_fingerprint_rules`)
- `policy_name`, `policy_version`
- `service_name` (caller)
- `timestamp`

### 7E: Kernel boundary trace

Instrument `InterpretationCoordinator` / `JournalWriter` to emit `FINANCE_KERNEL_TRACE`:
- `trace_id`, `event_id`
- `config_set_id`, `config_set_version`
- `policy_name`, `policy_version`
- `engines_used` (read from `InterpretationTraceContext.engines_invoked` — unforgeable)
- `reference_snapshot_id`
- `journal_entry_ids`
- `outcome_status` (POSTED / BLOCKED / REJECTED)
- `timestamp`

---

## Part 8: Policy-to-Engine Binding

### 8A: Policy declares engines

Each `AccountingPolicy` / `PolicyDefinition` declares:
- `required_engines: tuple[str, ...]` — engine names that MUST be invoked
- `engine_parameters_ref: str | None` — must match an engine contract's `parameter_key`
- `capability_tags: tuple[str, ...]` — capability gates for policy admissibility

### 8B: Runtime validation

At interpretation time:
- PolicySelector outputs a selected policy
- Orchestrator validates that all `required_engines` exist in the CompiledPolicyPack
- Runtime assertion mode (enabled in tests) fails if a required engine is not invoked (checked via `InterpretationTraceContext`)
- Engine invocation tracing must include policy and config identifiers

---

## Part 9: Tests That Prove Wiring and Prevent Bypass

### 9.1: Architecture boundary tests
- Introduce forbidden imports in engine files → assert test fails
- Introduce journal model import in module service → assert test fails

### 9.2: Configuration centralization tests
- Exactly one config set must match a given scope/date
- Ambiguous matches must fail fast at compile time
- Checksum must be recorded in ReferenceSnapshot and traces
- Fragment assembly produces identical result when re-assembled

### 9.3: Wiring log-capture tests
- Post an event that requires engines
- Capture logs
- Assert: `FINANCE_CONFIG_TRACE` exists, `FINANCE_POLICY_TRACE` exists, `FINANCE_ENGINE_TRACE` exists, `FINANCE_KERNEL_TRACE` exists
- Assert: IDs and versions match across all traces
- Assert: `engines_used` in KERNEL_TRACE matches ENGINE_TRACE events (unforgeable correlation)

### 9.4: Replay determinism tests
- Post event, capture ledger hash and traces
- Replay using stored ReferenceSnapshot + historical ConfigurationSet
- Assert: identical ledger hash, config/policy/engine/fingerprints identical

### 9.5: Negative bypass tests
- Attempt to write JournalEntry rows directly (bypass kernel boundary)
- Assert: DB triggers block, no FINANCE_KERNEL_TRACE with POSTED, audit record exists

### 9.6: Dead component and wiring integrity tests

Focus on **required but not invoked** and **referenced but missing**, not merely "unused exists":

- **FAIL** if a policy declares `required_engines` and wiring tests show no ENGINE_TRACE for that engine during interpretation
- **FAIL** if a policy references a non-existent engine contract
- **FAIL** if an engine has a contract but no tracer/schema
- **WARN** if an engine exists with no policy referencing it (unless marked experimental via a `"experimental": true` flag in the contract)
- **FAIL** if a capability is enabled with no policies declaring that capability tag

### 9.7: Capability toggle tests
- Disable a capability → assert DCAA-tagged policies are not admissible
- Enable a capability → assert DCAA-tagged policies are admissible
- Disable a capability that has no fallback → assert rejection with clear error

---

## Part 10: Wire Remaining 11 Module Services

Using the validated InventoryService template:

| Sub-phase | Modules | Engines Used |
|-----------|---------|-------------|
| 10A | AP, AR | ReconciliationService, AllocationEngine, MatchingEngine, AgingCalculator |
| 10B | Cash, Procurement | ReconciliationService, MatchingEngine, VarianceCalculator |
| 10C | WIP, Payroll | ValuationService, AllocationEngine, VarianceCalculator, AllocationCascade |
| 10D | Contracts, Tax | BillingService, AllocationCascade, ICEService, TaxCalculator |
| 10E | Assets, GL, Expense | VarianceCalculator, AllocationEngine, TaxCalculator |

Each service accepts `CompiledPolicyPack`, uses `finance_services/` for stateful operations, `finance_engines/` for pure calculations. All module services go through the posting facade — no direct journal model imports.

**Create:** 11 service files + 11 test files + 1 cross-module flow test

---

## Part 11: Documentation Updates

Update all architecture documents and diagrams to reflect:

**Layer model:**
1. **Kernel:** structural validity of the ledger (balance, immutability, period rules, link legality). Non-configurable invariants. Never depends upward.
2. **Engines:** generic, stable calculation machinery (pure, deterministic). Declare contracts.
3. **Policy/configuration:** what applies, when, with what parameters. Human-authored fragments assembled into AccountingConfigurationSet, compiled into CompiledPolicyPack.
4. **Services:** orchestration, regulatory workflows, persistence. Stateful. The only layer that touches DB, clock, I/O.

**Explicitly state:**
- Kernel invariants are non-configurable structural law
- Regulatory and company policy changes occur in configuration fragments
- Algorithmic changes occur in code
- These are orthogonal
- Replay uses ReferenceSnapshot + ConfigurationSet
- Runtime traces prove which path was taken (unforgeable via trace context)
- "Turn DCAA off" = disable capability → policies filtered by PolicyAuthority

**Update interpretation flow language to:**
- `get_active_config()` → CompiledPolicyPack (sole runtime artifact)
- PolicyAuthority validates admissible policies by scope, controls, and capability tags
- PolicySelector selects exactly one AccountingPolicy via deterministic precedence
- AccountingPolicy produces AccountingIntent and declares required engines
- Services orchestrate and call engines using pre-resolved typed parameters
- Kernel validates and records immutably with atomic outcome recording
- Trace context ensures unforgeable audit trail

---

## Execution Order

```
Part 0  (Kernel boundary & invariants contract)         — additive tests + architecture rules
Part 1  (ConfigurationSet schema + loader + compiler)   — additive, no existing code changed
Part 2  (Renames)                                        — behavior-preserving find/replace
Part 3  (Purity split)                                   — behavior-preserving file moves
Part 4  (Engine contracts)                               — additive declarations
Part 5  (Wire CompiledPolicyPack into runtime)           — switches config source
Part 6  (Architecture enforcement)                       — additive tests
Part 7  (Runtime tracing)                                — additive instrumentation
Part 8  (Policy-to-engine binding)                       — additive field + validation
Part 9  (Wiring/bypass/dead-component/capability tests)  — additive tests
Part 10 (Wire 11 module services)                        — completes integration
Part 11 (Documentation)                                  — docs update
```

After each part: `python3 -m pytest tests/ -x --timeout=60` — all existing tests pass.

---

## Deliverables

- Kernel boundary contract and dependency rules (Part 0)
- Updated directory tree including `finance_config/`, `finance_services/`
- Renamed classes and registries (Part 2)
- Moved modules (6 impure engines) to `finance_services/` (Part 3)
- Configuration fragment schema, assembler, YAML loader, validator, and CompiledPolicyPack compiler
- Example configuration fragments (`US-GAAP-2026-v1/` directory)
- PolicyDecisionTrace build artifact
- Engine contracts with typed parameter keys (`finance_engines/contracts.py`)
- CompiledPolicyPack with pre-resolved engine parameters
- Posting facade and journal model import gate
- InterpretationTraceContext for unforgeable trace correlation
- Updated architecture diagram and invariants
- Migration notes for downstream modules
- New test suites: kernel boundary, architecture boundary, wiring log-capture, replay path, bypass-negative, dead-component, capability toggle, config centralization
- Verification checklist with exact test commands and expected trace output

---

## Files Summary

**Create (~60):**
- `finance_config/` package (9 files + fragment directory with ~10 YAML files)
- `finance_services/` package (7 files)
- `finance_engines/tracer.py`, `finance_engines/contracts.py`
- Architecture + wiring + dead-component + capability tests (9 files)
- 11 module service files + 11 test files + 1 cross-module test
- Migration script (one-time)

**Rename (5 kernel domain files)**

**Modify (~50):** All files importing renamed classes, module configs, conftest, docs

---

## Verification Checklist

| # | Criterion | How to verify |
|---|-----------|---------------|
| 1 | Kernel invariants non-configurable | `pytest tests/architecture/ -k "kernel_boundary" -v` |
| 2 | `finance_engines/` pure | `pytest tests/architecture/test_import_boundaries.py -v` |
| 3 | Single compiled pack defines all behavior | `pytest tests/ -k "config_centralization" -v` |
| 4 | COA change = edit one fragment | Edit `chart_of_accounts.yaml`, recompile, run full suite |
| 5 | Policy change = edit one fragment | Edit policy fragment, recompile, run suite |
| 6 | JournalEntry records ConfigSet | `pytest tests/ -k "reference_snapshot" -v` |
| 7 | Traces reconstruct full path | `pytest tests/ -k "wiring_log_capture" -v` |
| 8 | Trace correlation unforgeable | `pytest tests/ -k "trace_context" -v` |
| 9 | Replay determinism | `pytest tests/ -k "replay_determinism" -v` |
| 10 | Direct insert blocked | `pytest tests/ -k "negative_bypass" -v` |
| 11 | Required-but-not-invoked detected | `pytest tests/ -k "dead_component" -v` |
| 12 | Capability toggle works | `pytest tests/ -k "capability_toggle" -v` |
| 13 | All 12 modules integrated | `pytest tests/modules/ tests/integration/ -v` |
| 14 | No regressions | `python3 -m pytest tests/ -x --timeout=60` |

---

## Success Criteria

- Kernel invariants are non-configurable structural law, enforced by architecture gates
- `finance_engines/` contains only pure, stateless, deterministic code, enforced by CI
- `finance_services/` contains orchestration and persistence
- Regulatory and company behavior changes are made in configuration fragments, compiled into a single CompiledPolicyPack
- Runtime posting entrypoints accept only CompiledPolicyPack — YAML is build/test tooling
- Engine parameters are pre-resolved typed objects, not stringly-typed lookups
- Capability tags control policy admissibility; "turn DCAA off" is a config change
- Logs can reconstruct: config_set → admissible policies → selected policy → engines invoked → kernel posting → outcome
- Trace correlation is unforgeable (trace context populated by actual engine invocations)
- Replay produces identical ledger hash and identical config/policy/engine trace signature
- Required-but-not-invoked engines and referenced-but-missing contracts fail tests or build
- Only the kernel posting boundary can write journal rows
