# Finance Kernel Execution Plan (v2)

## Strategic view

> **Profiles define the law. The engine enforces it.**

This plan makes one object the human governance surface — the **Economic Profile** — and keeps the runtime engine mechanical, deterministic, and hostile to ambiguity. Humans author profiles. The system compiles and enforces them as fast, immutable runtime rules.

The design goal is **operational simplicity with audit-grade rigor**.

---

## Core mental model

| Concept     | Plain meaning                   | System role                                       |
| ----------- | ------------------------------- | ------------------------------------------------- |
| **Fact**    | Something happened              | `BusinessEvent`                                   |
| **Meaning** | What it represents economically | `EconomicEvent`                                   |
| **Effect**  | What changes in the system      | `JournalEntry`, `StateChange`, `ProjectionUpdate` |

Profiles govern how facts become meaning and effects.

---

## Layer architecture and contracts

### Economic layer vs finance layer

**Economic layer owns**: BusinessEvent, EconomicProfile, EconomicEvent, InterpretationOutcome, ReferenceSnapshot, Provisional/Settlement logic, ProfileCompiler.

**Finance layer owns**: Ledger, AccountRole, COA, JournalEntry, FiscalPeriod, balancing, sequencing, atomic commit.

**Contract between layers**: Economic layer emits an immutable AccountingIntent (roles, amounts, dimensions, snapshot refs). Finance layer either atomically posts or deterministically blocks/rejects.

---

### AccountRole and role-based posting

* Profiles reference AccountRoles, never COA accounts.
* Ledger registry defines `required_roles_by_economic_type`.
* COA binding table maps AccountRole → COA account by `coa_version` and effective window.

**Invariant L1**: Every POSTED entry must resolve each role to exactly one COA account for the snapshot version.

---

### Deterministic precedence and tie-break

Selection order: **override > scope specificity > priority > stable key**.

Compiler rejects any overlap that cannot be resolved by explicit override or priority.

**Invariant L2**: No runtime ambiguity is allowed.

---

### Provisional settlement invariant

* PROVISIONAL events create a ProvisionalCase with TTL.
* Exactly one settlement event (confirm or reject) per case, idempotent.
* Expired cases must emit an exception or auto-reject per policy.

**Invariant L3**: One case → one settlement → one final outcome.

---

### ReferenceSnapshot contract

* Guards and valuation may only read from a frozen ReferenceSnapshot, not live data.
* Snapshot ID/version stored on EconomicEvent and JournalEntry.

**Invariant L4**: Replay using stored snapshots produces identical results.

---

### Strengthened atomicity

InterpretationOutcome=POSTED must be committed in the same transaction as all JournalEntry writes.

**Invariant L5**: No journal rows without a matching POSTED outcome; no POSTED outcome without all journal rows.

---

## Primary governance object

### EconomicProfile

The **EconomicProfile** is the single source of policy truth. It defines:

1. **Trigger** — Which facts it applies to
2. **Meaning** — What economic meaning is derived
3. **Effects** — What ledgers and lifecycles are impacted
4. **Guards** — When it must reject or block
5. **Lifecycle** — Whether it must flow through a time-based process

### Profile structure (source format)

```yaml
profile: InventoryReceipt
scope: SKU:*
effective: 2026-01-01 → open

precedence:
  mode: normal              # normal | override
  overrides: []             # named profiles this overrides

trigger:
  event_type: MaterialReceived
  where:
    payload.condition: ACCEPTED

meaning:
  economic_type: InventoryIncrease
  quantity: payload.quantity
  dimensions: [sku, location, project]

valuation_model: standard_receipt_v1   # no inline expressions — model only

ledger_effects:
  GL:
    debit: InventoryAsset
    credit: GRNI

guards:
  reject:                   # terminal — invalid economic reality
    - payload.quantity <= 0
  block:                    # resumable — system cannot safely process yet
    - PO.status != Approved
    - reference_data_missing = true
```

**No inline valuation**: Profiles reference `valuation_model` only. Expressions like `payload.quantity * PO.unit_price` live in versioned valuation model definitions, not profiles. This prevents profiles from becoming code and keeps valuation changes separate from meaning changes.

**Guard semantics**:
- **Reject** = Invalid economic reality. Terminal. Event cannot be processed.
- **Block** = Valid reality, system cannot safely process yet. Resumable. Creates backlog.

**Invariant P12**: Rejects are terminal. Blocks are resumable.

---

## Runtime engine (simplified naming)

The runtime is explicitly mechanical. It does not contain business logic. It enforces profiles.

```
Event Intake
  → Profile Matcher
    → Guard Evaluator
      → Process Router
        → Meaning Builder
          → Ledger Router
            → Journal Writer
              → State Driver
                → Outcome Recorder
```

### Component definitions

| Name                 | Responsibility                                                 |
| -------------------- | -------------------------------------------------------------- |
| **Event Intake**     | Validates schema, enforces idempotency, stores `BusinessEvent` |
| **Profile Matcher**  | Finds matching profile using precedence graph (override > normal) |
| **Guard Evaluator**  | Enforces reject and block rules                                |
| **Process Router**   | Routes events into time-based engines when required            |
| **Meaning Builder**  | Constructs `EconomicEvent` deterministically                   |
| **Ledger Router**    | Applies ledger mappings per profile                            |
| **Journal Writer**   | Persists `JournalEntry` and lines                              |
| **State Driver**     | Applies lifecycle transitions                                  |
| **Outcome Recorder** | Records terminal result and trace                              |

---

## Compiler inputs and manifests

These are **not new database tables** in Phase 2B. They are source artifacts and registries that the compiler uses to validate profiles.

### Coverage specs (source artifact)

Lives alongside profiles. Declares what "complete coverage" means.

```yaml
# coverage/material_received.yaml
event_type: MaterialReceived
dimensions: [sku, location]
boundaries:
  quantity: "> 0"
  value: ">= 0"
```

**Invariant P4**: Every declared combination must match exactly one profile or be explicitly rejected.

---

### Ledger registry (small config file)

```yaml
# registry/ledgers.yaml
GL:
  required_accounts_by_type:
    InventoryIncrease: [InventoryAsset, GRNI]
    InventoryDecrease: [COGS, InventoryAsset]
  dimension_requirements: [cost_center]
```

**Invariant P7**: Compiler rejects profiles that don't map to required accounts.

---

### Valuation models (versioned definitions)

```yaml
# valuation/standard_receipt_v1.yaml
model: standard_receipt_v1
version: 1
expression: payload.quantity * PO.unit_price
currency_source: PO.currency
uses_fields: [payload.quantity, PO.unit_price, PO.currency]
```

**Invariant P8**: Profiles reference models by ID. No inline expressions.

---

### Strategy manifests (code, not authored)

Typed declarations in code. Not human-authored YAML.

```python
@strategy_manifest
class InventoryReceiptStrategy:
    produces = [Money, Quantity, Dimensions]
    uses_fields = ["payload.quantity", "PO.unit_price"]
    valuation_model = "standard_receipt_v1"
```

---

### Reviews (in ChangeManifest, not separate table)

Reviews are tracked in `ChangeManifest.reviews[]`, not as a separate domain object.

```yaml
# change_manifest.yaml
profile: InventoryReceipt
version: 2
reviews:
  - type: FINANCE
    reviewer: jane@company.com
    decision: APPROVED
    timestamp: 2026-01-15T10:00:00Z
  - type: OPERATIONS
    reviewer: bob@company.com
    decision: APPROVED
    timestamp: 2026-01-15T11:00:00Z
```

**Invariant P13**: No profile promoted without required reviews in manifest.

---

### DecisionTraceLog (database table)

The **only** new table in this section. Cold-path, append-only.

* `trace_id`
* `artifact_type`
* `artifact_id`
* `inputs_hash`
* `trace_payload`
* `created_at`

Hot-path records store only `trace_id`.

---

## Core data objects

### BusinessEvent (Fact)

Immutable record of something that happened.

**Fields**:

* `event_id`
* `event_key`
* `event_type`
* `occurred_at`
* `ingested_at`
* `source_system`
* `subject_ref`
* `payload`
* `prev_hash`
* `hash`

---

### EconomicEvent (Meaning)

Immutable interpreted fact.

**Fields**:

* `econ_event_id`
* `source_event_id`
* `economic_type`
* `quantity`
* `dimensions`
* `effective_date`
* `accounting_basis_used`
* `accounting_basis_timestamp`
* `profile_id`
* `profile_version`
* `profile_hash`
* `trace_id`
* `prev_hash`
* `hash`

**Valuation fields** (versioned independently):

* `value`
* `currency`
* `valuation_model_id`
* `valuation_model_version`

**Reference snapshot fields** (for audit replay):

* `coa_version`
* `dimension_schema_version`
* `currency_registry_version`
* `fx_policy_version` (if applicable)

**Why snapshots matter**: Without stored reference versions, shadow execution can pass today and fail during audit replay later. These fields make replay deterministic.

**Acceptance criterion**: Replay with stored versions produces identical outputs.

---

### JournalEntry (Effect)

Immutable financial impact.

**Fields**:

* `journal_entry_id`
* `ledger_id`
* `source_economic_event_id`
* `profile_id`
* `profile_version`
* `seq`
* `period`
* `trace_id`

**Reference snapshot fields** (for audit replay):

* `coa_version`
* `dimension_schema_version`
* `currency_registry_version`
* `fx_policy_version` (if applicable)

---

### InterpretationOutcome (Terminal State)

Every accepted BusinessEvent ends here. No exceptions.

**Fields**:

* `outcome_id`
* `source_event_id` — **unique constraint** (one outcome per event)
* `status`: POSTED | BLOCKED | REJECTED | PROVISIONAL | NON_POSTING
* `econ_event_id` — nullable (null if REJECTED)
* `journal_entry_ids` — nullable (null if not POSTED)
* `reason_code` — machine enum (e.g., `GUARD_REJECT_QUANTITY`, `BLOCKED_MISSING_PO`)
* `reason_detail` — structured JSON
* `profile_id`
* `profile_version`
* `profile_hash`
* `trace_id`
* `created_at`
* `updated_at` — only for BLOCKED → POSTED transitions

**Status definitions**:

| Status | Meaning | Transitions |
|--------|---------|-------------|
| **POSTED** | Successfully posted to all required ledgers | Terminal |
| **BLOCKED** | Valid event, cannot process yet (missing ref data, pending approval) | → POSTED or → REJECTED |
| **REJECTED** | Invalid economic reality | Terminal |
| **PROVISIONAL** | Recorded provisionally, awaiting confirmation | → POSTED or → REJECTED |
| **NON_POSTING** | Valid, but no financial effect per policy | Terminal |

**Invariant P15**: Every accepted BusinessEvent has exactly one InterpretationOutcome.

---

### Provisional Events

**Purpose**: Prevent spreadsheet leakage when ops needs to record reality before reference data or approvals exist.

**Rules**:

* Only allowed for explicitly configured event_types
* Mandatory expiration policy (e.g., 5 business days)
* Mandatory settlement event (confirm or reject)
* Excluded from financial reports until finalized
* Visible in operational dashboards with aging

**Example**: Goods received but PO not yet approved. Record as PROVISIONAL. When PO approves, settle to POSTED. If PO rejected, settle to REJECTED.

Without this, ops will record in Excel and you lose system-of-record status.

---

## Ledger commit model

**Decision**: Single database, single transaction for all required ledgers.

This is the **irreversible architectural decision** for Phase 2.

### Options considered

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **Single DB transaction** | Simple, ACID guaranteed, no coordination | All ledgers in one DB | **Selected** |
| Event-sourced commit | Cross-DB possible, audit trail built-in | Complex, eventual consistency | Phase 3+ if needed |
| Two-phase commit (2PC) | Distributed atomicity | Fragile, slow, coordinator SPOF | Avoid |

### Implementation requirements

1. All ledgers (GL, subledgers, management) in same PostgreSQL database
2. Multi-ledger posting wrapped in single transaction
3. Idempotency key per ledger posting: `(econ_event_id, ledger_id, profile_version)`
4. On any failure, entire transaction rolls back — no partial state

### Acceptance criteria

- [ ] No JournalEntry exists without all sibling entries for same EconomicEvent
- [ ] Retry of failed posting is idempotent (same result, no duplicates)
- [ ] Timeout during commit leaves zero JournalEntries for that EconomicEvent

---

## Execution phases

### Phase 2A — Event foundation

**Goal**: Make facts safe.

**Deliverables**:

* Event schema registry
* Hash chaining
* Global idempotency
* Monotonic sequencing

---

### Phase 2B — Profile registry and compiler (minimum)

**Goal**: Make policy safe.

**Minimum deliverables**:

* `EconomicProfile` model (trigger + meaning + ledger refs)
* `ProfileCompiler`:
  * Overlap detection (P1)
  * Field validation against EventSchema (P10)
  * Ledger registry lookup (P7)
* `InterpretationOutcome` model
* Runtime lookup tables

**Invariants enforced**:

* P1 Single profile match
* P7 Ledger semantic completeness
* P10 Field references validated
* P15 One outcome per event

**Deferred to later**:
* Coverage enumeration tooling (P4)
* Profile size limits (P14)
* Override graph validation (P9)

---

### Phase 2C — Interpretation engine (minimum)

**Goal**: Make meaning safe.

**Minimum deliverables**:

* `MeaningBuilder`
* `ValuationResolver` (references valuation_model_id only)
* `OutcomeRecorder` (POSTED | BLOCKED | REJECTED | PROVISIONAL)
* Reference snapshot capture

**Invariants enforced**:

* P12 Reject vs block semantics
* P15 One outcome per event
* Deterministic valuation via versioned models
* Reference snapshots persisted

**Deferred to later**:
* PROVISIONAL expiration automation
* Rich decision trace capture

---

### Phase 2D — Ledger engine (minimum)

**Goal**: Make money safe.

**Minimum deliverables**:

* `JournalWriter` with single-transaction commit model
* Idempotency keys: `(econ_event_id, ledger_id, profile_version)`
* Multi-ledger atomicity enforcement

**Invariants enforced**:

* P11 Multi-ledger atomicity (single transaction)
* Balance per currency
* No partial posting state

**Deferred to later**:
* Period locking enforcement
* Ledger semantic completeness checks at runtime

---

## Deferred phases

The following can land after the kernel proves the architecture:

### Phase 2C.5 — Process engine

**Goal**: Make time safe.

* `ProcessRouter`, `ProcessEngine`
* WIP, accruals, burdens, pools
* P6 Acyclic graph validation

---

### Phase 2E — Projections and corrections

**Goal**: Make views safe.

* `ProjectionEngine`
* Snapshot storage
* Correction generator

---

### Phase 2F — Change governance

**Goal**: Make change safe.

* `ShadowRunner`
* `ChangeManifest` with reviews
* Blast radius enforcement (P5)

---

### Phase 2G — Economic monitoring

**Goal**: Make failure visible.

* `CoverageMonitor`
* `ReconciliationService`
* `ExceptionEvent`

---

## Invariants summary

### Minimum (Phase 2B–2D)

| ID      | Rule                                              | Phase |
| ------- | ------------------------------------------------- | ----- |
| **P1**  | Exactly one profile matches or event is rejected  | 2B |
| **P7**  | Ledger semantic completeness                      | 2B |
| **P10** | Field references validated against EventSchema    | 2B |
| **P11** | Multi-ledger postings are atomic (single transaction) | 2D |
| **P12** | Rejects are terminal; blocks are resumable        | 2C |
| **P15** | Every accepted BusinessEvent has exactly one Outcome | 2B |

### Deferred

| ID      | Rule                                              | Phase |
| ------- | ------------------------------------------------- | ----- |
| **P4**  | Coverage bounded by coverage specs (key + value space) | 2F |
| **P5**  | Promotion respects declared blast radius          | 2F |
| **P6**  | Economic processes are acyclic                    | 2C.5 |
| **P8**  | Strategy usage must be declared                   | 2F |
| **P9**  | Overrides must name what they override            | 2F |
| **P13** | Profiles require human review before promotion    | 2F |
| **P14** | Profiles exceeding size limits must be decomposed | 2F |

---

## Operational posture

### Degraded mode

Under resource exhaustion:

* Events may be accepted
* Interpretation may proceed
* Posting degrades to **BLOCKED**, not **PARTIAL**

This preserves economic integrity under stress.

### Failure handling

* **Dead-letter queue** — Failed events route to DLQ with full context for manual review and resubmission.
* **Circuit breaker** — Per event_type. If error rate exceeds threshold, route to DLQ instead of retry storms.
* **Backpressure** — Queue depth and processing latency are monitored metrics. Alerts fire before exhaustion.

### Multi-ledger atomicity (P11)

If an EconomicEvent requires postings to multiple ledgers, either **all succeed** or **none succeed**. Partial posting across ledgers is never permitted.

See **Ledger commit model** section for implementation decision (single DB, single transaction).

---

## Migration strategy

1. Seed profiles from existing strategies
2. Run in shadow mode
3. Compare ledger deltas
4. Promote by profile
5. Retire direct posting logic

### Migration confidence metadata

Backfilled records carry audit metadata:

* `migration_confidence_score` (0.0–1.0) — Confidence in reconstruction accuracy
* `reference_snapshot_missing` (bool) — True if reference data was unavailable during reconstruction
* `migration_batch_id` — Links to migration run for traceability

This allows auditors to distinguish native records from reconstructed ones.

---

## Systemic risk: Profiles as ERP source code

This design is close to building a real programming language. The constraints help, but complexity will migrate upward.

**Failure mode**:
1. New engineers start adding "just one more guard"
2. Profiles become 500-line policy files
3. Compiler becomes a second ERP
4. No one understands why profiles work the way they do

**Mitigations** (all mandatory):

| Mitigation | Enforcement |
|------------|-------------|
| Hard size limits | `governance.max.*` in profile schema |
| Mandatory decomposition | Compiler rejects oversized profiles |
| Visual diff tooling | Required for all profile changes |
| Required human review | P13 — ProfileReview for promotion |
| Named overrides | P9 — Auditable dependency graph |

This risk must be monitored continuously. If average profile size exceeds 100 lines or override depth exceeds 3, governance has failed.

---

## Design tradeoffs

| Area                  | Benefit               | Risk                  |
| --------------------- | --------------------- | --------------------- |
| Single policy surface | Faster governance     | Larger profile files  |
| Compiler enforcement  | Early error detection | Build-time complexity |
| Named overrides       | Auditable dependencies| Governance overhead   |
| Cold-path tracing     | Performance           | Extra storage         |
| Meaning/valuation split | Smaller blast radius | More versioning       |

---

## Irreversible decisions (made)

| Decision | Choice | Why |
|----------|--------|-----|
| **Commit model** | Single DB, single transaction | Simplest ACID guarantee; distributed later if needed |
| **Outcome record** | First-class InterpretationOutcome | Proves "every event ends somewhere" |
| **Governance objects** | Compiler inputs, not tables | Ship kernel, not governance infrastructure |
| **Inline valuation** | Prohibited; models only | Prevents profiles from becoming code |
| **Provisional status** | Supported with expiration | Prevents spreadsheet bypass |
| **Reference snapshots** | Required fields on EconomicEvent/JournalEntry | Enables deterministic audit replay |

---

## Minimum cut (Phase 2B–2D)

| Phase | Scope | Proves |
|-------|-------|--------|
| **2B** | Profiles + compiler + Outcome record | Policy can be validated |
| **2C** | EconomicEvent + valuation model ref + Outcome states | Meaning is deterministic |
| **2D** | Single-transaction commit + idempotency | Money is safe |

Everything else (coverage tooling, process engine, projections, rich governance) lands after the kernel proves the architecture.

---

## Bottom line

This model keeps the **kernel small, mechanical, and defensible**, while giving humans a **single, legible policy surface**.

It front-loads rigor into the compiler and governance layers so runtime execution can remain simple, fast, and auditable.

**Ship the minimum. Prove the architecture. Iterate.**
