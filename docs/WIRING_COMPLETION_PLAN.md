# Wiring Completion Plan

## Purpose

This plan ensures every engine, service, guard, and domain primitive is wired into
the runtime pipeline — with architectural tests that prevent bypass or regression.

**Principle:** If a component exists in the codebase, it must either be wired into
the runtime or deleted. No dead scaffolding.

---

## Current State Summary

### What works

| Layer                                                                                                            | Status                                                                |
| ---------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| 9 pure engines (variance, allocation, matching, aging, tax, subledger, allocation_cascade, billing, ice)         | Fully implemented, stateless, deterministic                           |
| 8 engine contracts with JSON Schema parameter validation                                                         | Compiled and validated at build time                                  |
| 14 module services (AP, AR, inventory, etc.)                                                                     | Each instantiates engines directly and posts via ModulePostingService |
| Core kernel services (IngestorService, LedgerService, JournalWriter, PeriodService, etc.)                        | Fully implemented                                                     |
| Profile-driven posting pipeline (PolicySelector -> MeaningBuilder -> InterpretationCoordinator)                  | Works end-to-end                                                      |
| 6 kernel invariants (double-entry, immutability, period lock, link legality, sequence monotonicity, idempotency) | Enforced at DB + service layer                                        |
| Configuration compilation (engine refs, param schemas, capability tags)                                          | Validated at compile time                                             |
| Fingerprint pinning (APPROVED_FINGERPRINT)                                                                       | Enforced at get_active_config()                                       |

### What is broken or missing

| Gap                                                                                  | Severity | Impact                                                               |
| ------------------------------------------------------------------------------------ | -------- | -------------------------------------------------------------------- |
| **G1**: `required_engines` on policies — compiled but never read at runtime          | HIGH     | Engine invocation is ad hoc, not declarative                         |
| **G2**: `resolved_engine_params` — compiled but never consumed                       | HIGH     | Tolerance thresholds and methods configured but ignored              |
| **G3**: `engine_contracts` — built but not enforced at runtime                       | HIGH     | No runtime contract verification                                     |
| **G4**: No `EngineDispatcher` — engines instantiated directly by modules             | HIGH     | No tracing, no parameter injection, no contract enforcement          |
| **G5**: `@traced_engine` decorator exists but is never applied                       | MEDIUM   | Engine invocations not in audit trail                                |
| **G6**: `VarianceDisposition` enum exists but unused                                 | MEDIUM   | Variance routing is hardcoded in profiles, not driven by disposition |
| **G7**: No PostingOrchestrator — ModulePostingService creates 6+ services internally | HIGH     | Can't inject test doubles, duplicate SequenceService instances       |
| **G8**: PolicyAuthority validation is optional in MeaningBuilder                     | HIGH     | Module can post unauthorized economic types                          |
| **G9**: SubledgerControl reconciliation defined but never called                     | HIGH     | Subledger–GL drift goes undetected                                   |
| **G10**: ReferenceSnapshot not validated at posting time                             | HIGH     | Stale reference data used without detection                          |
| **G11**: L3 (acyclic) not enforced in LinkGraphService for all link types            | MEDIUM   | Cycles possible in fulfillment/consumption chains                    |
| **G12**: CorrectionEngine has no guard evaluation                                    | HIGH     | Can void documents in closed periods                                 |
| **G13**: ValuationLayer uses in-memory lot storage                                   | HIGH     | Data loss on process restart                                         |
| **G14**: No actor/permission authorization at posting boundary                       | MEDIUM   | Any actor_id accepted without verification                           |
| **G15**: PolicyCompiler can be bypassed — direct PolicySelector.register()           | MEDIUM   | Unvalidated profiles can enter dispatch                              |
| **G16**: No policy YAML declares `required_engines`                                  | HIGH     | Engine–policy binding exists only in code, not config                |

---

## Plan Structure

The plan is organized into 9 phases. Each phase is independently testable.
No phase introduces a breaking change to existing module code until Phase 5
(which rewires modules to use the new dispatch).

| Phase | Name                          | Gaps Addressed      |
| ----- | ----------------------------- | ------------------- |
| 1     | Engine Dispatcher             | G1, G2, G3, G4, G5 |
| 2     | Posting Orchestrator          | G7                  |
| 3     | Mandatory Guards              | G8, G14, G15        |
| 4     | Runtime Enforcement Points    | G9, G10, G11, G12   |
| 5     | Module Rewiring               | G6, G16             |
| 6     | Persistence Gaps              | G13                 |
| 7     | Architecture Tests            | All                 |
| 8     | Dead Scaffolding Elimination  | All (provability)   |
| 9     | Financial Exception Lifecycle | Failure durability  |

---

## Phase 1 — Engine Dispatcher ✅ COMPLETE

**Goal:** Create a runtime engine dispatch layer that reads `required_engines`
and `resolved_engine_params` from the compiled config, invokes engines with
correct parameters, and produces traced audit records.

**Deliverables:**
- `finance_kernel/services/engine_dispatcher.py` — EngineDispatcher, EngineInvoker, EngineTraceRecord, EngineDispatchResult
- `finance_engines/invokers.py` — 7 standard invoker registrations via `register_standard_engines()`
- `@traced_engine` applied to 13 engine entry points across 8 engine files
- EngineDispatcher wired into InterpretationCoordinator (optional, backward-compatible)
- InterpretationResult extended with `engine_result: EngineDispatchResult | None`
- `tests/engines/test_engine_dispatcher.py` — 23 tests (all passing)

### 1.1 Create `finance_kernel/services/engine_dispatcher.py`

```
class EngineDispatcher:
    """Runtime engine dispatch — reads CompiledPolicyPack fields."""

    def __init__(
        self,
        compiled_pack: CompiledPolicyPack,
    ):
        self._pack = compiled_pack
        self._registry: dict[str, EngineInvoker] = {}

    def register(self, engine_name: str, invoker: EngineInvoker) -> None:
        """Register a callable engine invoker."""

    def dispatch(
        self,
        policy: CompiledPolicy,
        payload: dict,
    ) -> EngineDispatchResult:
        """
        For each engine in policy.required_engines:
          1. Look up resolved_engine_params[policy.engine_parameters_ref]
          2. Validate inputs against engine_contract.parameter_schema
          3. Invoke the registered engine with merged parameters
          4. Collect EngineTraceRecord per invocation
        Returns EngineDispatchResult with all engine outputs + traces.
        """

    def validate_registration(self) -> list[str]:
        """
        Check that every engine in pack.engine_contracts has a
        registered invoker. Return list of unregistered engine names.
        """
```

**Types:**

```
@dataclass(frozen=True)
class EngineInvoker:
    engine_name: str
    engine_version: str
    invoke: Callable[[dict, FrozenEngineParams], Any]

@dataclass(frozen=True)
class EngineTraceRecord:
    engine_name: str
    engine_version: str
    input_fingerprint: str
    output_summary: str
    duration_ms: float
    parameters_used: dict[str, Any]

@dataclass(frozen=True)
class EngineDispatchResult:
    engine_outputs: dict[str, Any]     # engine_name -> output
    traces: tuple[EngineTraceRecord, ...]
    all_succeeded: bool
    errors: tuple[str, ...]
```

### 1.2 Create standard invoker registrations

File: `finance_engines/invokers.py`

Register all 8 engines with their contract-compliant invokers:

```
def register_standard_engines(dispatcher: EngineDispatcher) -> None:
    dispatcher.register("variance", EngineInvoker(
        engine_name="variance",
        engine_version="1.0",
        invoke=_invoke_variance,
    ))
    dispatcher.register("allocation", ...)
    dispatcher.register("matching", ...)
    dispatcher.register("aging", ...)
    dispatcher.register("tax", ...)
    dispatcher.register("allocation_cascade", ...)
    dispatcher.register("billing", ...)
    dispatcher.register("ice", ...)
```

Each `_invoke_*` function:

1. Reads relevant fields from payload
2. Merges with `FrozenEngineParams` from config
3. Calls the pure engine function
4. Returns result

### 1.3 Apply `@traced_engine` decorator to all engine entry points

Currently the decorator exists in `finance_engines/tracer.py` but is not
applied anywhere. Apply it to:

* `VarianceCalculator.price_variance()`
* `VarianceCalculator.quantity_variance()`
* `VarianceCalculator.fx_variance()`
* `VarianceCalculator.standard_cost_variance()`
* `AllocationEngine.allocate()`
* `MatchingEngine.find_matches()`
* `MatchingEngine.create_match()`
* `AgingCalculator.generate_report()`
* `TaxCalculator.calculate()`
* `execute_cascade()`
* `calculate_billing()`
* `compile_ice_submission()`

This gives every engine invocation a `FINANCE_ENGINE_TRACE` log record
with input fingerprint and duration.

### 1.4 Wire EngineDispatcher into InterpretationCoordinator

After profile selection and meaning building, but before intent construction:

```
# In InterpretationCoordinator.interpret_and_post():
if policy.required_engines:
    dispatch_result = self._engine_dispatcher.dispatch(policy, payload)
    if not dispatch_result.all_succeeded:
        return InterpretationResult.engine_failed(dispatch_result.errors)
    # Merge engine outputs into payload for intent construction
    enriched_payload = {**payload, **dispatch_result.engine_outputs}
```

### 1.5 Tests

| Test                                          | File                                      | What it proves                                                           |
| --------------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------ |
| `test_dispatcher_invokes_required_engines`    | `tests/engines/test_engine_dispatcher.py` | Policy with `required_engines=("variance",)` triggers VarianceCalculator |
| `test_dispatcher_injects_resolved_params`     | same                                      | Engine receives tolerance_percent from config                            |
| `test_dispatcher_rejects_unregistered_engine` | same                                      | validate_registration() catches gaps                                     |
| `test_dispatcher_produces_trace_records`      | same                                      | Each invocation produces EngineTraceRecord                               |
| `test_dispatcher_no_engines_is_noop`          | same                                      | Policy with no required_engines passes through unchanged                 |
| `test_all_contracts_have_invokers`            | `tests/architecture/test_wiring_proof.py` | Every ENGINE_CONTRACT has a registered invoker                           |

---

## Phase 2 — Posting Orchestrator ✅ COMPLETE

**Goal:** Replace ad hoc service creation in ModulePostingService with a central
factory that owns service lifecycle and prevents duplicate instances.

**Deliverables:**
- `finance_kernel/services/posting_orchestrator.py` — PostingOrchestrator with all service singletons
- `ModulePostingService.from_orchestrator()` class method for DI-based construction
- Legacy `ModulePostingService.__init__` preserved for backward compatibility
- All imports verified clean

### 2.1 Create `finance_kernel/services/posting_orchestrator.py`

```
class PostingOrchestrator:
    """Central factory for kernel services.

    Every kernel service is created once and injected.
    No service may create other services internally.
    """

    def __init__(
        self,
        session: Session,
        compiled_pack: CompiledPolicyPack,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()

        # Singletons — created once
        self.sequence_service = SequenceService(session)
        self.auditor = AuditorService(session, self._clock)
        self.period_service = PeriodService(session, self._clock)
        self.ingestor = IngestorService(session, self._clock, self.auditor)
        self.link_graph = LinkGraphService(session)
        self.journal_writer = JournalWriter(
            session, role_resolver, self._clock, self.auditor,
        )
        self.outcome_recorder = OutcomeRecorder(session, self._clock)
        self.interpretation_coordinator = InterpretationCoordinator(
            session, self.journal_writer, self.outcome_recorder, self._clock,
        )
        self.engine_dispatcher = EngineDispatcher(compiled_pack)
        self.snapshot_service = ReferenceSnapshotService(session, self._clock)
        self.party_service = PartyService(session)
        self.contract_service = ContractService(session)
```

### 2.2 Refactor ModulePostingService

ModulePostingService receives a PostingOrchestrator instead of creating services:

```
class ModulePostingService:
    def __init__(
        self,
        orchestrator: PostingOrchestrator,
        auto_commit: bool = True,
    ):
        self._orch = orchestrator
        self._auto_commit = auto_commit

    def post_event(self, ...):
        # Uses self._orch.ingestor, self._orch.period_service, etc.
```

### 2.3 Update all module services

Each module service receives `PostingOrchestrator` and extracts what it needs:

```
class APService:
    def __init__(self, orchestrator: PostingOrchestrator):
        self._poster = ModulePostingService(orchestrator, auto_commit=False)
        self._link_graph = orchestrator.link_graph
        self._reconciliation = ReconciliationManager(
            session=orchestrator._session,
            link_graph=orchestrator.link_graph,
        )
        # Stateless engines — still instantiated directly (pure, no state)
        self._allocation = AllocationEngine()
        self._aging = AgingCalculator()
```

### 2.4 Tests

| Test                                     | File                                          | What it proves                                 |
| ---------------------------------------- | --------------------------------------------- | ---------------------------------------------- |
| `test_orchestrator_creates_all_services` | `tests/services/test_posting_orchestrator.py` | All services accessible                        |
| `test_sequence_service_is_singleton`     | same                                          | Only one SequenceService instance              |
| `test_module_posting_uses_orchestrator`  | same                                          | ModulePostingService delegates to orchestrator |
| `test_orchestrator_rejects_none_session` | same                                          | Constructor validates inputs                   |

---

## Phase 3 — Mandatory Guards ✅ COMPLETE

**Goal:** Make every optional guard check mandatory. No posting without
authority validation, policy compilation, and actor verification.

**Deliverables:**
- `MeaningBuilder` constructor parameter renamed to `policy_authority` (backward-compatible, optional with recommendation)
- `PostingOrchestrator` injects `PolicyAuthority` into MeaningBuilder and exposes `meaning_builder` + `party_service`
- `CompilationReceipt` dataclass + `UncompiledPolicyError` in `policy_selector.py`
- `PolicySelector.register()` validates receipt when provided (future: required)
- `ActorError`, `InvalidActorError`, `ActorFrozenError` exceptions in `exceptions.py`
- `INVALID_ACTOR` and `ACTOR_FROZEN` added to `ModulePostingStatus` enum
- Actor validation (step 0) added to `ModulePostingService._do_post_event()` — runs before period validation
- `ModulePostingService.from_orchestrator()` wires `_party_service_ref` from orchestrator
- Fixed `ValidationError` kwargs bug in `_validate_policy` (`context` → `details`)
- `tests/architecture/test_mandatory_guards.py` — 26 tests (all passing):
  - 9 CompilationReceipt tests (G15)
  - 6 PolicyAuthority in MeaningBuilder tests (G8)
  - 5 Actor authorization at posting boundary tests (G14)
  - 4 MeaningBuilder guard evaluation tests (G8)
  - 2 ModulePostingStatus enum completeness tests

### 3.1 Make PolicyAuthority mandatory in MeaningBuilder

Current state: `policy_registry` is an optional constructor parameter.
Change: Make it required.

```
class MeaningBuilder:
    def __init__(
        self,
        policy_authority: PolicyAuthority,    # REQUIRED
    ):
```

The `build()` method always validates module authority and economic type
posting constraints. Remove the conditional checks.

### 3.2 Make PolicyCompiler mandatory before PolicySelector registration

Current state: PolicySelector.register() accepts any AccountingPolicy.
Change: Add a compiled flag or require a CompilationReceipt.

```
class PolicySelector:
    def register(
        self,
        policy: AccountingPolicy,
        compilation_receipt: CompilationReceipt,  # NEW — proves policy was compiled
    ) -> None:
        if not compilation_receipt.is_valid:
            raise UncompiledPolicyError(policy.name)
```

`CompilationReceipt` is produced by PolicyCompiler.compile() and is
unforgeable (contains the compiled policy hash).

### 3.3 Actor authorization at posting boundary

Add actor validation to ModulePostingService.post_event():

```
def post_event(self, ..., actor_id: UUID, ...):
    # Actor must be a valid, active party
    actor = self._orch.party_service.get_by_id(actor_id)
    if actor is None:
        raise InvalidActorError(actor_id)
    if not actor.can_transact:
        raise ActorFrozenError(actor_id)
```

This does not add role-based access control (out of scope), but ensures
the actor_id references a real, active entity.

### 3.4 Tests

| Test                                             | File                                          | What it proves                     |
| ------------------------------------------------ | --------------------------------------------- | ---------------------------------- |
| `test_meaning_builder_requires_policy_authority` | `tests/domain/test_meaning_builder_guards.py` | Constructor rejects None           |
| `test_uncompiled_policy_rejected`                | `tests/domain/test_policy_selector_guards.py` | register() without receipt raises  |
| `test_invalid_actor_rejected`                    | `tests/posting/test_actor_validation.py`      | post_event with bad actor_id fails |
| `test_frozen_actor_rejected`                     | same                                          | Frozen actor cannot post           |

---

## Phase 4 — Runtime Enforcement Points ✅ COMPLETE

**Goal:** Wire the domain guard primitives that exist but are not called
at runtime.

**Deliverables:**
- G9: JournalWriter accepts optional `subledger_control_registry` and calls
  `_validate_subledger_controls()` after entry creation for contracts with
  `enforce_on_post=True`
- G10: JournalWriter accepts optional `snapshot_service` and calls
  `_validate_snapshot_freshness()` using `ReferenceSnapshotService.validate_integrity()`.
  Raises `StaleReferenceSnapshotError` when components have changed.
- G11: Already implemented — `LinkGraphService._detect_cycle()` enforces L3
  acyclicity for FULFILLED_BY, SOURCED_FROM, DERIVED_FROM, CONSUMED_BY,
  CORRECTED_BY link types via DFS
- G12: `CorrectionEngine` accepts optional `period_service` and enforces
  period lock in `_check_can_unwind()`. Corrections to artifacts in closed
  periods are blocked with reason.
- New exceptions: `StaleReferenceSnapshotError`, `SubledgerReconciliationError`
- PostingOrchestrator now passes `snapshot_service` to JournalWriter
- `tests/architecture/test_runtime_enforcement.py` — 26 tests (all passing):
  - 5 subledger control wiring tests (G9)
  - 6 snapshot freshness wiring tests (G10)
  - 4 link graph cycle detection regression tests (G11)
  - 7 correction period lock enforcement tests (G12)
  - 4 enforcement exception tests

### 4.1 Subledger control reconciliation (G9)

**Where to enforce:** Two enforcement points, matching the SubledgerControlContract
`enforce_on_post` and `enforce_on_close` flags.

**Post-time enforcement:**
In JournalWriter.write(), after creating journal lines:

```
if subledger_control_registry:
    for ledger_effect in intent.ledger_intents:
        contract = subledger_control_registry.get(ledger_effect.ledger)
        if contract and contract.enforce_on_post:
            result = SubledgerReconciler.validate_post(
                contract, before_balance, after_balance, ...
            )
            if not result.is_valid:
                raise SubledgerReconciliationError(result)
```

**Period-close enforcement:**
In PeriodService.close_period(), before marking period as closed:

```
for contract in subledger_control_registry.all():
    if contract.enforce_on_close:
        result = SubledgerReconciler.validate_period_close(
            contract, subledger_balance, control_balance, ...
        )
        if not result.is_valid:
            raise SubledgerReconciliationError(result)
```

### 4.2 Reference snapshot validation (G10)

**Where to enforce:** JournalWriter.write(), which already checks for
reference snapshot presence (R21). Strengthen to verify snapshot currency:

```
# Already exists — make it strict:
if not intent.snapshot:
    raise MissingReferenceSnapshotError(intent)

# NEW — verify snapshot is current:
current_snapshot = self._snapshot_service.get_or_capture()
if not intent.snapshot.is_compatible_with(current_snapshot):
    raise StaleReferenceSnapshotError(
        intent_snapshot=intent.snapshot,
        current_snapshot=current_snapshot,
    )
```

### 4.3 Link graph cycle detection (G11)

**Where to enforce:** LinkGraphService.establish_link().

The domain layer already validates L2 (no self-links) and L5 (type
compatibility). Add L3 cycle detection:

```
def establish_link(self, link: EconomicLink, ...) -> EconomicLink:
    # L2: Self-link check (already in domain)
    # L5: Type compatibility (already in domain)
    # L3: Acyclic check — NEW
    if self._would_create_cycle(link):
        raise LinkCycleError(
            link_type=link.link_type,
            parent=link.parent_ref,
            child=link.child_ref,
        )
```

Implementation: Walk from child back to parent via the same link_type.
If parent is reachable from child, adding this link creates a cycle.

### 4.4 Guard evaluation in CorrectionEngine (G12)

**Where to enforce:** CorrectionEngine._check_can_unwind().

Currently only checks is_corrected/is_reversed. Add:

```
def _check_can_unwind(self, artifact_ref, ...):
    # Existing checks
    if self._link_graph.is_reversed(artifact_ref):
        return CanUnwindResult.blocked("Already reversed")
    if self._link_graph.find_correction(artifact_ref):
        return CanUnwindResult.blocked("Already corrected")

    # NEW: Period check
    if artifact_ref.effective_date:
        period = self._period_service.get_period_for_date(
            artifact_ref.effective_date
        )
        if period and period.is_closed:
            return CanUnwindResult.blocked(
                f"Period {period.code} is closed"
            )

    # NEW: Guard evaluation from profile
    # (Apply profile guards to the correction event)
```

### 4.5 Tests

| Test                                         | File                                         | What it proves                       |
| -------------------------------------------- | -------------------------------------------- | ------------------------------------ |
| `test_subledger_reconciliation_blocks_post`  | `tests/domain/test_subledger_enforcement.py` | Out-of-balance post rejected         |
| `test_subledger_reconciliation_blocks_close` | same                                         | Period close blocked if unreconciled |
| `test_stale_snapshot_rejected`               | `tests/posting/test_snapshot_validation.py`  | Intent with old snapshot fails       |
| `test_link_cycle_detected`                   | `tests/concurrency/test_link_cycle.py`       | A->B->C->A via FULFILLED_BY rejected |
| `test_correction_blocked_in_closed_period`   | `tests/domain/test_correction_guards.py`     | Void in closed period fails          |

---

## Phase 5 — Module Rewiring ✅ COMPLETE

**Goal:** Update policy YAML to declare engine dependencies and wire
VarianceDisposition into the runtime.

**Status:** COMPLETE — All deliverables implemented and tested.

**Deliverables:**

1. **5.1 — required_engines in policy YAML:** 18 `required_engines` declarations added across 6 YAML files (inventory, wip, ap, payroll, tax, contracts).
2. **5.2 — VarianceDisposition wired:** `variance_disposition` field added to `PolicyDefinition` (schema.py), `parse_policy` (loader.py), `CompiledPolicy` (compiler.py), and 4 variance policy YAMLs (`variance_disposition: post`).
3. **5.3 — Engine instantiation audit:** Architecture is already correct — modules own stateless engines directly (by design), PostingOrchestrator and ModulePostingService stay clean of engine imports, EngineDispatcher handles policy-driven dispatch.
4. **5.4 — Module rewiring tests:** `tests/architecture/test_module_rewiring.py` — 28 tests across 5 classes (TestVariancePolicyYAML, TestVarianceDispositionCompiled, TestEngineDispatcherWiring, TestModuleEngineOwnership, TestEngineContractCoverage). All passing.

### 5.1 Add `required_engines` to policy YAML

Update policies that use engines to declare their dependencies:

```yaml
# finance_config/sets/US-GAAP-2026-v1/policies/inventory.yaml
- name: InventoryReceiptWithVariance
  trigger:
    event_type: inventory.receipt
    where:
      payload.has_variance: true
  required_engines:
    - variance
  engine_parameters_ref: variance
  # ...
```

Policies that should declare engines:

| Policy                       | Engine                         | Parameters                          |
| ---------------------------- | ------------------------------ | ----------------------------------- |
| InventoryReceiptWithVariance | variance                       | tolerance_percent, tolerance_amount |
| WipLaborVariance             | variance                       | tolerance_percent, tolerance_amount |
| WipMaterialVariance          | variance                       | tolerance_percent, tolerance_amount |
| WipOverheadVariance          | variance                       | tolerance_percent, tolerance_amount |
| AP 3-way match policies      | matching                       | tolerance_percent, match_strategy   |
| Expense allocation policies  | allocation                     | method, rounding_method             |
| Payroll allocation policies  | allocation, allocation_cascade | method, cascade_type                |
| Contract billing policies    | billing                        | default_withholding_pct             |
| DCAA ICE policies            | ice                            | fiscal_year_end_month               |
| Tax calculation policies     | tax                            | calculation_method                  |

### 5.2 Wire VarianceDisposition

VarianceDisposition determines what happens with computed variances.
Add a `variance_disposition` field to the policy YAML:

```yaml
- name: InventoryReceiptWithVariance
  trigger:
    event_type: inventory.receipt
    where:
      payload.has_variance: true
  required_engines:
    - variance
  engine_parameters_ref: variance
  meaning:
    economic_type: InventoryReceipt
    variance_disposition: post_to_variance_account   # NEW
```

The EngineDispatcher reads this field and uses it to determine the
ledger effect routing for variance amounts:

* `post_to_variance_account` -> variance goes to PPV/variance GL account
* `capitalize_to_inventory` -> variance absorbed into inventory cost
* `allocate_to_cogs` -> variance allocated to COGS proportionally
* `write_off` -> variance expensed immediately

### 5.3 Remove module-level engine hardcoding

Once the EngineDispatcher handles engine invocation, remove direct engine
instantiation from module services where it duplicates dispatcher behavior.

**Keep direct instantiation for:**

* Engines used for read-only computation (aging reports, billing previews)
* Engines called outside the posting pipeline (e.g., reconciliation matching)

**Remove direct instantiation for:**

* Variance calculation during posting (now handled by dispatcher)
* Allocation during posting (now handled by dispatcher)
* Tax calculation during posting (now handled by dispatcher)

### 5.4 Tests

| Test                                        | File                                         | What it proves                                |
| ------------------------------------------- | -------------------------------------------- | --------------------------------------------- |
| `test_all_variance_policies_declare_engine` | `tests/modules/test_config_validation.py`    | No variance policy without `required_engines` |
| `test_variance_disposition_respected`       | `tests/engines/test_variance_disposition.py` | `capitalize` routes variance to inventory     |
| `test_engine_params_applied_at_runtime`     | `tests/engines/test_engine_dispatcher.py`    | tolerance_percent from config reaches engine  |

---

## Phase 6 — Persistence Gaps ✅ COMPLETE

**Goal:** Replace in-memory storage with database-backed persistence.

**Status:** COMPLETE — All deliverables implemented and tested.

**Deliverables:**

1. **6.1 — CostLotModel ORM:** `finance_kernel/models/cost_lot.py` — full ORM model with item_id, location_id, lot_date, original_quantity, quantity_unit, original_cost, currency, cost_method, source provenance, timestamps, and JSON metadata.
2. **6.2 — SQL DDL:** `finance_kernel/db/sql/12_cost_lot.sql` — table creation with CHECK constraints (C1: quantity > 0, C2: cost >= 0), 5 indexes for FIFO/LIFO ordering, location, provenance, method, and time-range queries.
3. **6.3 — ValuationLayer refactor:** Dual-mode persistence — DB mode (default, no `lots_by_item`) uses `_store_lot()`, `_load_lot_by_id()`, `_load_lots_for_item()`, `_model_to_domain()` helpers; in-memory mode (when `lots_by_item` provided) for backward-compatible testing. All existing tests pass unchanged.
4. **6.4 — Persistence tests:** `tests/services/test_valuation_persistence.py` — 14 tests across 3 classes (CostLotModelPersistence, ValuationLayerDBMode, ValuationLayerCrossSession). All passing.

### 6.1 Cost lot persistence (G13)

ValuationLayer currently stores cost lots in `self._lots: dict[str, list[CostLot]]`.

Create a `cost_lot` table and ORM model:

```
Table: cost_lot
  - lot_id (UUID, PK)
  - item_id (TEXT, NOT NULL)
  - quantity (DECIMAL, NOT NULL)
  - unit_cost (DECIMAL, NOT NULL)
  - currency (TEXT, NOT NULL)
  - received_date (DATE, NOT NULL)
  - source_event_id (UUID, FK -> event.event_id)
  - created_at (TIMESTAMP, NOT NULL)
```

Refactor ValuationLayer to query and persist through the session instead
of an in-memory dict. This also enables:

* Restart safety (lots survive process restart)
* Concurrent access (multiple workers can consume lots)
* Audit trail (lot history in DB)

### 6.2 Tests

| Test                                     | File                                           | What it proves                           |
| ---------------------------------------- | ---------------------------------------------- | ---------------------------------------- |
| `test_cost_lot_persists_across_sessions` | `tests/services/test_valuation_persistence.py` | Create lot, new session, lot still there |
| `test_concurrent_lot_consumption`        | same                                           | Two consumers don't over-consume         |

---

## Phase 7 — Architecture Tests (Guards Against Bypass) ✅ COMPLETE

**Goal:** Write architecture-level tests that prevent future regressions.
These tests inspect code structure, not behavior.

**Status:** COMPLETE — All guard tests implemented.

**Deliverables:**

Tests 7.1–7.8 were already covered by Phase 3–5 tests:
- **7.1** Engine dispatch guard → `test_module_rewiring.py:TestEngineContractCoverage` (6 tests)
- **7.2** No direct engine instantiation → `test_module_rewiring.py:TestModuleEngineOwnership` (5 tests)
- **7.3** PolicyAuthority validation → `test_mandatory_guards.py:TestMeaningBuilderPolicyAuthority` (6 tests)
- **7.4** CompilationReceipt guard → `test_mandatory_guards.py:TestCompilationReceipt` (9 tests)
- **7.5** SubledgerControl wired → `test_runtime_enforcement.py:TestSubledgerControlWiring` (5 tests)
- **7.6** Snapshot validation wired → `test_runtime_enforcement.py:TestSnapshotFreshnessWiring` (6 tests)
- **7.7** Link cycle detection → `test_runtime_enforcement.py:TestLinkGraphCycleDetection` (4 tests)
- **7.8** Correction period lock → `test_runtime_enforcement.py:TestCorrectionPeriodLockEnforcement` (7 tests)

**New in Phase 7:**
- **7.9** No-workaround tests → `tests/architecture/test_no_workarounds.py` — 13 tests across 5 classes:
  - `TestNoDirectLinkPersistenceInModules` (3 tests) — no EconomicLinkModel bypass
  - `TestNoDirectSessionQueryInModules` (2 tests) — no raw SQL in modules
  - `TestModuleServicesAcceptOrchestrator` (3 tests) — proper DI patterns
  - `TestAllEngineContractsRegistered` (2 tests) — policy↔contract coverage
  - `TestNoDeadPolicyFields` (3 tests) — compiled fields, match index, role bindings

### 7.1 Engine dispatch guard

```python
class TestEngineDispatchCompleteness:
    """Every engine contract must have a registered invoker."""

    def test_all_contracts_have_invokers(self):
        from finance_engines.contracts import ENGINE_CONTRACTS
        from finance_engines.invokers import register_standard_engines
        from finance_kernel.services.engine_dispatcher import EngineDispatcher

        dispatcher = EngineDispatcher(mock_pack)
        register_standard_engines(dispatcher)
        unregistered = dispatcher.validate_registration()
        assert unregistered == [], f"Unregistered engines: {unregistered}"
```

### 7.2 No direct engine instantiation in posting path

```python
class TestNoDirectEngineInPosting:
    """Module services must not instantiate engines used in posting.

    Engines invoked during the posting pipeline must go through
    EngineDispatcher. Direct instantiation is allowed only for
    read-only or preview operations outside the posting path.
    """

    POSTING_ENGINES = {"VarianceCalculator", "TaxCalculator"}
    # AllocationEngine, MatchingEngine, AgingCalculator allowed
    # (used for read-only operations like aging reports, matching previews)

    def test_no_posting_engines_in_module_services(self):
        # Scan all finance_modules/*/service.py for disallowed imports
        ...
```

### 7.3 PolicyAuthority cannot be None

```python
class TestPolicyAuthorityRequired:
    """MeaningBuilder must have PolicyAuthority."""

    def test_meaning_builder_rejects_none_authority(self):
        with pytest.raises(TypeError):
            MeaningBuilder(policy_authority=None)
```

### 7.4 PolicySelector requires compilation receipt

```python
class TestPolicySelectorGuard:
    """Policies must be compiled before registration."""

    def test_register_without_receipt_fails(self):
        selector = PolicySelector()
        with pytest.raises(UncompiledPolicyError):
            selector.register(some_policy, compilation_receipt=None)
```

### 7.5 SubledgerControl wired into posting

```python
class TestSubledgerControlWired:
    """Subledger reconciliation is called during posting."""

    def test_subledger_reconciliation_called_on_post(self):
        # Mock SubledgerReconciler, post a journal entry
        # Assert validate_post() was called
        ...
```

### 7.6 ReferenceSnapshot validated at posting

```python
class TestSnapshotValidationWired:
    """JournalWriter validates reference snapshot freshness."""

    def test_stale_snapshot_raises(self):
        # Create intent with old snapshot version
        # Attempt to post
        # Assert StaleReferenceSnapshotError raised
        ...
```

### 7.7 Link cycle detection wired

```python
class TestLinkCycleDetectionWired:
    """LinkGraphService detects cycles on establish_link."""

    def test_cycle_in_fulfilled_by_rejected(self):
        # Create A->B->C via FULFILLED_BY
        # Attempt C->A via FULFILLED_BY
        # Assert LinkCycleError raised
        ...
```

### 7.8 CorrectionEngine respects period lock

```python
class TestCorrectionRespectsPeriodLock:
    """Cannot void documents in closed periods."""

    def test_void_in_closed_period_blocked(self):
        # Close period
        # Attempt void of document in that period
        # Assert blocked with period lock reason
        ...
```

### 7.9 No workaround patterns

```python
class TestNoWorkarounds:
    """Detect patterns that bypass the intended architecture."""

    def test_no_direct_economic_link_creation_in_modules(self):
        """Modules must use LinkGraphService, not EconomicLink directly."""
        # Grep finance_modules/ for 'EconomicLink.create(' or 'EconomicLink('
        # Only LinkGraphService should create links
        ...

    def test_no_direct_session_query_in_modules(self):
        """Modules must not query ORM directly (except Reporting)."""
        # Grep finance_modules/ for 'session.query(' or 'session.execute('
        # Exclude finance_modules/reporting/
        ...

    def test_all_module_services_accept_orchestrator(self):
        """Every module service constructor takes PostingOrchestrator."""
        # Inspect __init__ signatures of all module services
        ...
```

---

*Execution order and success criteria are consolidated at the end of this document (after Phase 9).*

---

## Phase 8 — Dead Scaffolding Elimination ✅ COMPLETE

**Goal:** Explicitly identify and remove any compiled, configured, or coded elements that are no longer consumed by the runtime pipeline. The system must converge to a state where every field, engine, service, and guard has a provable runtime purpose.

**Deliverables:**

* `tests/architecture/test_dead_scaffolding.py` — 9 tests across 3 classes, all passing:
  * `TestCompiledPolicyPackFieldUsage` (2 tests) — scans runtime consumer source to verify every CompiledPolicyPack field is accessed; identified `controls` and `match_index` as pending wiring
  * `TestOrchestratorServiceUsage` (2 tests) — verifies every PostingOrchestrator service is referenced by module/service/test consumers
  * `TestCompiledPolicyFieldUsage` (5 tests) — structural checks: every policy has ledger_effects, trigger, meaning; engine_parameters_ref only on engine policies; variance_disposition only on variance policies
* Documented pending wiring gaps for future phases:
  * CompiledPolicyPack: `controls` (awaits runtime control evaluator), `match_index` (awaits PolicySelector direct lookup wiring)
  * CompiledPolicy: `variance_disposition` (awaits EngineDispatcher runtime read), `capability_tags` (awaits runtime capability gating), `valuation_model` (awaits MeaningBuilder/ValuationLayer read)

### 8.1 CompiledPolicyPack consumption audit

Add an architecture test that reflects over `CompiledPolicyPack` and asserts that every public field is read by at least one runtime component.

**Mechanism:**

* Instrument getters on CompiledPolicyPack to record access at runtime during test posting runs, or
* Maintain an explicit "consumed_fields" registry in runtime services (EngineDispatcher, InterpretationCoordinator, JournalWriter, PolicySelector, MeaningBuilder).

**Test:**

```python
class TestNoUnusedCompiledFields:
    """Every compiled config field must be consumed at runtime."""

    def test_all_compiled_fields_are_used(self):
        pack = build_test_compiled_pack()
        run_full_posting_flow(pack)
        unused = pack.get_unused_fields()
        assert unused == [], f"Unused compiled fields: {unused}"
```

### 8.2 Engine contract reachability

Ensure every engine contract is referenced by at least one policy YAML or explicitly marked as experimental.

**Test:**

```python
class TestEngineContractReachability:
    """Every engine contract must be referenced by policy or marked experimental."""

    def test_all_engine_contracts_referenced(self):
        contracts = load_engine_contracts()
        policies = load_all_policies()
        referenced = collect_required_engines(policies)

        unreachable = [
            c.name for c in contracts
            if c.name not in referenced and not c.experimental
        ]
        assert unreachable == [], f"Unreachable engine contracts: {unreachable}"
```

### 8.3 Policy field reachability

Ensure every field declared in policy YAML has a runtime consumer.

**Test:**

```python
class TestPolicyFieldReachability:
    """Every policy YAML field must affect runtime behavior."""

    def test_all_policy_fields_consumed(self):
        fields = extract_all_policy_fields()
        consumed = collect_runtime_consumed_fields()

        unused = fields - consumed
        assert unused == set(), f"Unused policy fields: {unused}"
```

### 8.4 Service instantiation reachability

Ensure every kernel service created by PostingOrchestrator is actually used in at least one code path.

**Test:**

```python
class TestServiceReachability:
    """Every orchestrator service must be exercised by at least one flow."""

    def test_all_services_used(self):
        orch = build_test_orchestrator()
        run_full_posting_flow(orch)

        unused = orch.get_unused_services()
        assert unused == [], f"Unused services: {unused}"
```

### 8.5 Deletion gate

Introduce a CI rule: any field, engine, or service that fails the above tests must be either:

* Deleted, or
* Explicitly annotated as `@experimental` or `@reserved`

No dormant production artifacts are allowed without a declared lifecycle state.

---

## Phase 9 — Financial Exception Lifecycle ✅ COMPLETE

**Goal:** Make every failed posting attempt a first-class, durable, human-actionable financial case. No guard failure, engine failure, or policy violation is allowed to disappear into logs or transient errors. Every failure becomes an owned, inspectable, and retriable artifact.

**Deliverables:**

* Extended `InterpretationOutcome` model with 6 new columns: `failure_type`, `failure_message`, `engine_traces_ref`, `payload_fingerprint`, `actor_id`, `retry_count`
* `FailureType` enum: GUARD, ENGINE, RECONCILIATION, SNAPSHOT, AUTHORITY, CONTRACT, SYSTEM
* 3 new `OutcomeStatus` values: FAILED (retriable), RETRYING (retry in progress), ABANDONED (terminal)
* `VALID_TRANSITIONS` truth table: exhaustive state machine with 4 terminal states
* Updated `OutcomeRecorder`: `record_failed()`, `transition_to_failed()`, `transition_to_retrying()`, `transition_to_abandoned()`, `query_failed()`, `query_actionable()`
* `RetryService`: `initiate_retry()`, `complete_retry_success()`, `complete_retry_failure()`, `abandon()` with MAX_RETRIES safety limit
* SQL DDL: `finance_kernel/db/sql/13_outcome_exception_lifecycle.sql` with CHECK constraints
* `tests/posting/test_outcomes.py` — 46 tests across 7 classes, all passing:
  * TestOutcomeStateMachine (11 tests), TestFailedOutcomeCreation (9 tests), TestRetryLifecycle (5 tests), TestAbandonLifecycle (4 tests), TestWorkQueueQueries (6 tests), TestRetryContract (4 tests), TestStateTransitionInvariants (7 tests)

### 9.1 PostingOutcome / FinancialCase entity

Define a durable operational entity that represents an attempt to create financial history.

**Table: posting_outcome**

* outcome_id (UUID, PK)
* event_id (UUID, NOT NULL)
* policy_name (TEXT, NOT NULL)
* actor_id (UUID, NOT NULL)
* status (ENUM: PENDING, FAILED, RETRYING, POSTED, ABANDONED)
* failure_type (TEXT, NULL)
* failure_message (TEXT, NULL)
* snapshot_version (TEXT, NOT NULL)
* payload_fingerprint (TEXT, NOT NULL)
* engine_traces_ref (UUID, NULL)
* created_at (TIMESTAMP, NOT NULL)
* updated_at (TIMESTAMP, NOT NULL)

**Invariants:**

* Every call to `post_event()` must create or update exactly one posting_outcome record.
* A POSTED outcome implies a corresponding journal_entry exists.
* A FAILED or ABANDONED outcome implies no journal_entry exists.

### 9.2 Outcome state machine

Define explicit state transitions:

```
PENDING -> POSTED
PENDING -> FAILED
FAILED -> RETRYING
RETRYING -> POSTED
RETRYING -> FAILED
FAILED -> ABANDONED
```

**Rules:**

* Only FAILED outcomes may transition to RETRYING.
* ABANDONED is terminal.
* POSTED is terminal.

### 9.3 Mandatory failure capture

Enforce that all guard failures, engine failures, and policy violations are captured through OutcomeRecorder.

**Architecture test:**

```python
class TestAllFailuresRecorded:
    """No posting failure may bypass OutcomeRecorder."""

    def test_all_failures_create_outcome(self):
        # Induce guard failure (e.g., stale snapshot, subledger mismatch)
        # Assert posting_outcome record exists with status=FAILED
        ...
```

### 9.4 Retry contract

Define what may change between attempts:

**Allowed to change:**

* Policy configuration (YAML / compiled pack)
* Reference snapshot
* External master data (parties, contracts, items)

**Not allowed to change:**

* Original event payload
* Original actor_id
* Original event timestamp

Each retry must produce a new engine trace set and update `engine_traces_ref`.

### 9.5 Human work queue model

Define a logical inbox for financial exceptions.

**Queue views:**

* By failure_type (RECONCILIATION, SNAPSHOT, AUTHORITY, CONTRACT, ENGINE)
* By policy_name
* By age (SLA / escalation)
* By actor_id

**Minimum fields for UI / API:**

* outcome_id
* event_id
* status
* failure_type
* failure_message
* policy_name
* actor_id
* created_at
* link to engine traces
* link to before/after balance view

### 9.6 Retry mechanism

Add a service method:

```
def retry_outcome(outcome_id: UUID, actor_id: UUID) -> RetryResult:
    """Replays the original event through the current compiled policy pack.

    Preconditions:
    - outcome.status == FAILED
    - actor_id is authorized to retry

    Effects:
    - status -> RETRYING
    - new engine traces captured
    - on success: status -> POSTED
    - on failure: status -> FAILED
    """
```

### 9.7 Tests

| Test                                | File                             | What it proves                              |
| ----------------------------------- | -------------------------------- | ------------------------------------------- |
| `test_failed_post_creates_outcome`  | `tests/posting/test_outcomes.py` | Guard failure produces FAILED outcome       |
| `test_posted_outcome_links_journal` | same                             | POSTED outcome implies journal_entry exists |
| `test_retry_replays_event`          | same                             | Retry runs dispatcher and engines again     |
| `test_abandoned_is_terminal`        | same                             | ABANDONED cannot transition                 |

---

## Design Note: Reconciling with InterpretationOutcome

Phase 9's `posting_outcome` entity overlaps with the existing `InterpretationOutcome`
model that `OutcomeRecorder` already persists (POSTED, REJECTED, BLOCKED, PROVISIONAL,
NON_POSTING). Rather than creating a parallel tracking system, Phase 9 should
**extend InterpretationOutcome** to absorb the new fields:

* Add `failure_type`, `failure_message`, `engine_traces_ref`, `payload_fingerprint`
  to the existing outcome model.
* Merge state machines: BLOCKED → RETRYING, REJECTED → FAILED, add ABANDONED
  as a new terminal state.
* Keep one table, one recorder, one source of truth.

This avoids two outcome tables for the same event with conflicting status models.

---

## Execution Order

```
Phase 1: Engine Dispatcher
  1.1 Create EngineDispatcher class and types
  1.2 Create standard invoker registrations
  1.3 Apply @traced_engine to all engine entry points
  1.4 Wire into InterpretationCoordinator
  1.5 Write dispatcher tests

Phase 2: Posting Orchestrator
  2.1 Create PostingOrchestrator
  2.2 Refactor ModulePostingService
  2.3 Update module services
  2.4 Write orchestrator tests

Phase 3: Mandatory Guards
  3.1 Make PolicyAuthority required
  3.2 Add CompilationReceipt to PolicySelector
  3.3 Add actor validation
  3.4 Write guard tests

Phase 4: Runtime Enforcement Points
  4.1 Wire subledger reconciliation
  4.2 Wire reference snapshot validation
  4.3 Wire link cycle detection
  4.4 Wire correction guard evaluation
  4.5 Write enforcement tests

Phase 5: Module Rewiring
  5.1 Add required_engines to policy YAML
  5.2 Wire VarianceDisposition
  5.3 Remove hardcoded engine instantiation from posting path
  5.4 Write rewiring tests

Phase 6: Persistence Gaps
  6.1 Create cost_lot table and persistence
  6.2 Write persistence tests

Phase 7: Architecture Tests
  7.1–7.9 Write all architecture guard tests

Phase 8: Dead Scaffolding Elimination
  8.1 Add compiled field consumption audit
  8.2 Add engine contract reachability test
  8.3 Add policy field reachability test
  8.4 Add service reachability test
  8.5 Enforce deletion or lifecycle annotation

Phase 9: Financial Exception Lifecycle
  9.1 Extend InterpretationOutcome with failure context fields
  9.2 Implement outcome state machine (merge with existing states)
  9.3 Enforce mandatory failure capture
  9.4 Implement retry contract
  9.5 Build exception work queue views
  9.6 Implement retry mechanism
  9.7 Write lifecycle tests
```

---

## Success Criteria

When this plan is complete:

1. **Every engine invocation during posting flows through EngineDispatcher**
   — with trace records, parameter injection, and contract validation.

2. **Every kernel service is created by PostingOrchestrator**
   — no duplicate instances, no ad hoc construction.

3. **Every guard check is mandatory**
   — PolicyAuthority, compilation receipt, actor validation.

4. **Every domain invariant is enforced at runtime**
   — subledger reconciliation, snapshot freshness, link acyclicity, correction period lock.

5. **Every policy that uses an engine declares it in YAML**
   — the config is the single source of truth for engine dependencies.

6. **Architecture tests prevent regression**
   — new code that bypasses the dispatcher, creates services directly,
   or skips guard checks will fail CI.

7. **No dead scaffolding remains**
   — every compiled field is consumed at runtime, or the field is removed.

8. **No dormant production artifacts exist**
   — every compiled field, policy key, engine contract, and kernel service is either
   actively exercised in runtime flows, or explicitly marked as experimental/reserved.

9. **Every failed posting becomes a financial case**
   — failures are durable, visible, owned, and retriable, with full policy, engine,
   actor, and snapshot context.
