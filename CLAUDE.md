# Claude Code Project Instructions

## Plan Persistence (MANDATORY)

Claude has NO memory between conversations. Every session starts from zero.
To prevent loss of progress, follow these rules strictly:

### 1. Always Save Plans to Disk
- At the START of any multi-step task, create or update `plans/CURRENT_PLAN.md`
- The plan file must include:
  - **Objective**: What we're building/fixing
  - **Current Phase**: Which phase we're in right now
  - **Phases**: Numbered list of all phases with status (pending/in-progress/done)
  - **Decisions Made**: Key design decisions and rationale
  - **Completed Work**: What's already been done (files changed, tests passing)
  - **Next Steps**: Exactly what to do next when resuming

### 2. Update Between Phases
- After completing each phase, update `plans/CURRENT_PLAN.md` BEFORE moving on
- Mark the completed phase as `done` with a brief summary of what was accomplished
- Mark the next phase as `in-progress`
- List any new decisions or changes to the plan

### 3. Archive Completed Plans
- When a plan is fully complete, move it to `plans/archive/` with a descriptive name
  (e.g., `plans/archive/2026-01-29_economic-link-primitive.md`)
- Create a fresh `plans/CURRENT_PLAN.md` only when the next task begins

### 4. Session Start Protocol
- At the start of every conversation, check if `plans/CURRENT_PLAN.md` exists
- If it does, read it and summarize the current state to the user before doing anything else
- Ask the user if they want to continue the plan or start something new

---

## Architecture Overview

This is an **event-sourced, append-only double-entry accounting system** following the
**Pure Functional Core / Imperative Shell** pattern. The foundational truth:

> **JournalLines are the only financial truth. Everything else is derived.**

### System Layers (Strict Dependency Rules)

```
finance_modules/      Thin ERP modules (AP, AR, Inventory, Payroll, etc.)
    |                 Declarative: profiles, workflows, config schemas
    v
finance_services/     Stateful orchestration over engines + kernel
    |
    v
finance_engines/      Pure calculation engines (variance, allocation, tax)
    |                 May ONLY import finance_kernel/domain/values
    v
finance_config/       YAML-driven configuration, single entrypoint
    |
    v
finance_kernel/       Core: domain, services, models, db, selectors
```

**FORBIDDEN imports (enforced by tests + invariants.py):**
- `finance_kernel` MUST NOT import from `finance_modules`, `finance_services`, or `finance_config`
- `finance_engines` MUST NOT import from `finance_services`
- Violations are caught by `tests/architecture/test_kernel_boundary.py`

### Within `finance_kernel/`

```
domain/      Pure logic. ZERO I/O. Cannot import from db/, services/, or selectors/
models/      SQLAlchemy ORM models. Can import from db/base.py only
services/    Stateful I/O services. Can import from domain/, models/, db/
selectors/   Read-only query services. Can import from models/, db/
db/          Database infrastructure (engine, base, triggers, immutability)
```

---

## The Pure Core (`finance_kernel/domain/`)

### The Bookkeeper

The central pure transformation engine. **NO side effects, NO database, NO clock.**

```
EventEnvelope + ReferenceData  -->  Bookkeeper  -->  ProposedJournalEntry
      (input)                       (pure fn)            (output)
```

### Posting Strategies (R14, R15)

Strategies are pluggable, versioned transformations from event to journal lines:
- Each strategy handles ONE `event_type`
- Each declares a `version` for replay compatibility
- Implements `_compute_line_specs()` to produce `LineSpec` objects
- **NO `if/switch` on event_type in the posting engine** (R14)
- Adding a new event type requires ONLY a new strategy + registration (R15)

### DTOs and Values

ALL DTOs are **frozen dataclasses** (immutable). Key types:
- `EventEnvelope` -- normalized incoming event
- `LineSpec` -- specification for a single journal line
- `ProposedJournalEntry` -- complete entry ready for persistence
- `ReferenceData` -- frozen snapshot of accounts, rates, dimensions
- `Money` -- `Decimal` amount + ISO 4217 currency. **NEVER uses floats.**

### Clock Injection

Domain NEVER calls `datetime.now()`. All services receive a `Clock` via constructor:
```python
def __init__(self, session: Session, clock: Clock | None = None):
    self._clock = clock or SystemClock()
```
Tests use `DeterministicClock`.

### Accounting Policy System (Pipeline B)

Declarative policy-driven interpretation of business events:
- `accounting_policy.py` -- Policy definitions (trigger, meaning, ledger effects, guards)
- `policy_compiler.py` -- Compiles policies into executable form
- `policy_selector.py` -- Selects exactly one policy per event (where-clause dispatch)
- `policy_authority.py` -- Governs admissibility (effective dates, precedence)
- `ledger_registry.py` -- Maps semantic account ROLES to actual COA codes
- `meaning_builder.py` -- Extracts economic meaning from events
- `accounting_intent.py` -- Intermediate representation using ROLES (not COA codes)
- `policy_bridge.py` -- Connects module policies to kernel selectors

### Economic Links (`economic_link.py`)

First-class artifact relationships modeling the "why pointer" between economic artifacts:
```
PurchaseOrder --(FULFILLED_BY)--> Receipt --(FULFILLED_BY)--> Invoice --(PAID_BY)--> Payment
```

Link types: FULFILLED_BY, PAID_BY, REVERSED_BY, CORRECTED_BY, CONSUMED_BY,
SOURCED_FROM, ALLOCATED_TO, DERIVED_FROM, MATCHED_WITH, ADJUSTED_BY

Links are **immutable** (ORM + DB trigger enforced), acyclic per type, and always
record the `creating_event_id`.

---

## Models (SQLAlchemy ORM)

### Core Models

| Model | Key Fields | Immutability |
|-------|-----------|-------------|
| **JournalEntry** | `source_event_id`, `idempotency_key` (UNIQUE), `seq` (monotonic), `status` (DRAFT/POSTED/REVERSED), R21 snapshot columns | After POSTED: ORM + DB trigger |
| **JournalLine** | `account_id`, `side` (DEBIT/CREDIT), `amount` (Numeric(38,9), always positive), `currency`, `is_rounding`, `line_seq` | When parent is POSTED |
| **Event** | `event_id` (UNIQUE), `event_type`, `payload` (JSON), `payload_hash` | Always (append-only) |
| **AuditEvent** | `seq`, `entity_type`, `entity_id`, `action`, `prev_hash`, `hash` (chain) | Always (append-only, sacred) |
| **Account** | `code` (UNIQUE), `account_type`, `normal_balance`, `parent_id` (hierarchy) | Structural fields when referenced |
| **FiscalPeriod** | `period_code` (UNIQUE), `status` (OPEN/CLOSED), `allows_adjustments` | After CLOSED |
| **EconomicLinkModel** | `link_type`, `parent_artifact_*`, `child_artifact_*`, `creating_event_id` | Always (append-only) |
| **InterpretationOutcome** | `source_event_id` (UNIQUE), `status` (POSTED/BLOCKED/REJECTED) | P15: one per event |
| **Party** | Customer/supplier/employee, credit limits, blocked enforcement | - |
| **Contract** | CLINs, billing types (CPFF, T&M, FFP), DCAA compliance | - |

### Immutability: Three-Layer Defense

1. **ORM Event Listeners** (`db/immutability.py`): `before_update`, `before_delete`, `before_flush` raise `ImmutabilityViolationError`
2. **PostgreSQL Triggers** (`db/triggers.py` + `sql/`): 26 triggers across 11 SQL files catch raw SQL, bulk UPDATE, direct psql access
3. **Session-Level Guards**: `before_flush` catches attempted deletions before flush plan

Both ORM AND database triggers must be bypassed to modify protected data.

---

## Services (Imperative Shell)

### Two Posting Pipelines

**Pipeline A (Legacy -- PostingOrchestrator):**
```
post_event() -> IngestorService -> PeriodService -> ReferenceDataLoader
             -> Bookkeeper (pure) -> LedgerService -> COMMIT
```

**Pipeline B (Recommended -- Interpretation):**
```
interpret_and_post() -> ProfileRegistry -> ReferenceSnapshotService
                     -> MeaningBuilder (pure) -> AccountingIntent
                     -> JournalWriter -> OutcomeRecorder -> COMMIT
```

Pipeline B uses **account ROLES** (not COA codes) resolved at posting time (L1).

### Key Services

| Service | Responsibility |
|---------|---------------|
| **PostingOrchestrator** | Pipeline A entry point. Owns transaction boundary (R7) |
| **IngestorService** | Event ingestion, payload hash verification, protocol violation detection |
| **LedgerService** | Journal entry persistence, idempotency, sequence allocation |
| **AuditorService** | Hash chain maintenance (R11), chain validation |
| **PeriodService** | Fiscal period lifecycle, R12/R13 enforcement |
| **SequenceService** | Monotonic sequence allocation via locked counter row (R9) |
| **InterpretationCoordinator** | Pipeline B orchestrator, L5 atomicity |
| **JournalWriter** | Role-to-COA resolution (L1), balance validation, atomic writes (P11) |
| **OutcomeRecorder** | One outcome per event (P15) |
| **LinkGraphService** | Economic link persistence, cycle detection, graph traversal |

---

## Invariants (NEVER VIOLATE)

### Kernel Non-Negotiable (invariants.py)

These six invariants are enforced unconditionally. No config or policy may override them:
1. **DOUBLE_ENTRY_BALANCE** -- Debits = Credits per currency per entry
2. **IMMUTABILITY** -- Posted records cannot be modified
3. **PERIOD_LOCK** -- No posting to closed periods
4. **LINK_LEGALITY** -- Economic links follow type specs
5. **SEQUENCE_MONOTONICITY** -- Sequences are strictly monotonic, gap-safe
6. **IDEMPOTENCY** -- N retries of same event = 1 entry

### Full Invariant Table (R1-R24)

| Rule | Name | Summary |
|------|------|---------|
| R1 | Event immutability | Payload hash check, ORM + DB triggers |
| R2 | Payload hash verification | Same event_id + different payload = protocol violation |
| R3 | Idempotency key uniqueness | UNIQUE constraint + row locking |
| R4 | Balance per currency | Debits = Credits per currency |
| R5 | Rounding line uniqueness | At most ONE `is_rounding=True` per entry; threshold enforced |
| R6 | Replay safety | No stored balances -- trial balance computed from journal |
| R7 | Transaction boundaries | Each service owns its transaction |
| R8 | Idempotency locking | UniqueConstraint + `with_for_update()` |
| R9 | Sequence safety | Locked counter row. **`MAX(seq)+1` is FORBIDDEN** |
| R10 | Posted record immutability | ORM listeners + 26 PostgreSQL triggers |
| R11 | Audit chain integrity | `hash = H(payload_hash + prev_hash)` |
| R12 | Closed period enforcement | No posting to CLOSED periods |
| R13 | Adjustment policy | `allows_adjustments=True` required |
| R14 | No central dispatch | Strategy registry, no if/switch on event_type |
| R15 | Open/closed compliance | New event type = new strategy only |
| R16 | ISO 4217 enforcement | Currency validation at boundary |
| R17 | Precision-derived tolerance | Rounding tolerance from currency precision |
| R18 | Deterministic errors | Typed exceptions with machine-readable `code` |
| R19 | No silent correction | Failures are explicit or produce `is_rounding=True` |
| R20 | Test class mapping | Every invariant has unit, concurrency, crash, replay tests |
| R21 | Reference snapshot determinism | JournalEntry records version IDs at posting time |
| R22 | Rounding line isolation | Only Bookkeeper may create `is_rounding=True` lines |
| R23 | Strategy lifecycle governance | Version ranges + replay policy per strategy |
| R24 | Canonical ledger hash | Deterministic hash over sorted entries |

### Interpretation Layer (L1-L5, P1/P11/P15)

| Rule | Summary |
|------|---------|
| L1 | Every account role resolves to exactly one COA account |
| L2 | Role bindings unambiguous at interpretation time |
| L4 | Replay using stored ReferenceSnapshot produces identical results |
| L5 | No journal rows without POSTED outcome; no POSTED without all rows |
| P1 | Exactly one EconomicProfile matches any event |
| P11 | Multi-ledger postings from single AccountingIntent are atomic |
| P15 | Exactly one InterpretationOutcome per accepted event |

---

## Concurrency Model

- **Idempotency**: `UNIQUE(idempotency_key)` + `SELECT ... FOR UPDATE`. 200 threads posting same event = exactly 1 entry.
- **Sequences**: Locked counter row (R9). `MAX(seq)+1` is a race condition -- **NEVER use it**.
- **Period close**: Serialized via `SELECT ... FOR UPDATE` on period row.
- **Hash chain**: Serialized through sequence allocation.

---

## Rules for Working in This Codebase

### MUST DO
1. Keep domain code (`finance_kernel/domain/`) pure -- zero I/O, zero imports from db/services/selectors
2. Use frozen dataclasses for all DTOs and value objects
3. Use `Decimal` for all monetary amounts -- **NEVER float**
4. Inject clocks -- **NEVER call `datetime.now()` directly**
5. Use `SequenceService` for sequence allocation -- **NEVER `MAX(seq)+1`**
6. Correct via reversal entries -- **NEVER mutate posted records**
7. Use account ROLES in AccountingIntent, resolve to COA codes at posting time
8. Add new event types via new strategy + registration only (R14/R15)
9. Create audit events for all significant actions
10. Run existing tests before and after changes: `python3 -m pytest tests/ -v --tb=short`

### MUST NOT DO
1. Import outer layers from `finance_kernel/`
2. Add I/O to domain code
3. Use `if/switch` on event_type in the posting engine
4. Use `MAX(seq)+1` for sequence allocation
5. Modify posted journal entries or lines
6. Use floats for money
7. Read `datetime.now()` in domain or service code without clock injection
8. Bypass immutability protections
9. Create rounding lines from strategies (only Bookkeeper may, R22)
10. Skip the audit trail for any state-changing operation

### Configuration
- Single entrypoint: `finance_config.get_active_config(legal_entity, as_of_date)`
- No component may read config files or env vars directly

---

## Database

- **PostgreSQL 15+** (required -- triggers, JSONB, `SELECT ... FOR UPDATE`)
- User: `finance`, DB: `finance_kernel_test`
- Connection: `postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test`

## Running Tests

```bash
python3 -m pytest tests/ -v --tb=short
```

Test categories: unit, posting, audit, concurrency, adversarial, period, replay, crash,
architecture, domain, engines, modules, services, multicurrency, db_security, security,
fuzzing, metamorphic, integration, demo.
