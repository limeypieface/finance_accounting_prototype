# Finance Kernel - Architecture Documentation

## Overview

The Finance Kernel is an **append-only, event-sourced accounting system** designed to serve as the system of record for financial transactions in an ERP environment. It guarantees correctness under retries, concurrency, crashes, replays, and adversarial input.

### Core Philosophy

**"JournalLines are the only financial truth. Everything else is derived."**

This kernel treats financial data as immutable facts. Once a transaction is posted, it cannot be modified or deleted - only reversed through new transactions. This approach provides:

- **Auditability**: Complete history of all financial events
- **Recoverability**: Ability to rebuild any derived state from the journal
- **Integrity**: Cryptographic hash chain prevents undetected tampering
- **Determinism**: Replay using stored snapshots produces identical results

**Kernel primitives (R25):** All monetary amounts, quantities, exchange rates, and artifact identities must use **value types defined in the kernel** (`finance_kernel.domain.values` and related). Modules and engines may not define their own Money, Amount, Currency, or ArtifactRef types. The canonical primitives are:

| Primitive | Module | Description |
|-----------|--------|-------------|
| **Currency** | `domain/values.py` | ISO 4217 code; decimal places and rounding tolerance from CurrencyRegistry (R16, R17). |
| **Money** | `domain/values.py` | Decimal amount + Currency; never raw Decimal/float for money. |
| **Quantity** | `domain/values.py` | Decimal value + unit (e.g. inventory counts, weights); same-unit arithmetic. |
| **ExchangeRate** | `domain/values.py` | 1 from_currency = rate to_currency; positive Decimal; used for conversion. |
| **ArtifactRef** | `domain/economic_link.py` | artifact_type (ArtifactType enum) + artifact_id (UUID); pointer to Event, JournalEntry, Invoice, Payment, etc. | This avoids duplicate notions of “amount” or “currency” across the stack and keeps a single place for precision, rounding, and validation.

---

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         External Modules                                 │
│              (Inventory, Purchasing, AR/AP, Payroll, etc.)              │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼ Business Events
┌─────────────────────────────────────────────────────────────────────────┐
│                      INTERPRETATION LAYER                                │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │              InterpretationCoordinator (L5)                        │  │
│  │  Orchestrates: MeaningBuilder → JournalWriter → OutcomeRecorder   │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────┐ ┌────────────────┐ ┌─────────────────────────────┐   │
│  │PolicySelector │ │ MeaningBuilder │ │    ReferenceSnapshot        │   │
│  │  (P1: unique) │ │  (pure domain) │ │  (R21: determinism)         │   │
│  └──────────────┘ └────────────────┘ └─────────────────────────────┘   │
│  ┌──────────────┐ ┌────────────────┐ ┌─────────────────────────────┐   │
│  │PolicyAuthority│ │AccountingIntent│ │   AccountingPolicy          │   │
│  │  (authority)  │ │  (L1: roles)   │ │  (declarative governance)   │   │
│  └──────────────┘ └────────────────┘ └─────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼ AccountingIntent
┌─────────────────────────────────────────────────────────────────────────┐
│                        SERVICES LAYER (Kernel)                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │            ModulePostingService (canonical posting entry)         │  │
│  │    IngestorService → InterpretationCoordinator (L5) → commit     │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌───────────────────┐    │
│  │JournalWriter│ │OutcomeRec. │ │  Ingestor  │ │   PeriodService   │    │
│  │(P11: atomic)│ │(P15: unique)│ │  Service   │ │                   │    │
│  └────────────┘ └────────────┘ └────────────┘ └───────────────────┘    │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌───────────────────┐    │
│  │  Auditor   │ │ LinkGraph  │ │ RefSnapshot│ │ SequenceService   │    │
│  │  Service   │ │  Service   │ │  Service   │ │                   │    │
│  └────────────┘ └────────────┘ └────────────┘ └───────────────────┘    │
│  (PostingOrchestrator lives in finance_services/; it wires kernel svcs)  │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼ DTOs (Immutable)
┌─────────────────────────────────────────────────────────────────────────┐
│                        DOMAIN LAYER (Pure)                               │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                       Bookkeeper                                   │  │
│  │       EventEnvelope + ReferenceData → ProposedJournalEntry        │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │
│  │  Strategies  │ │    Values    │ │ DTOs (frozen)│ │  Schemas     │   │
│  │  (pluggable) │ │ (Money, etc) │ │              │ │  (registry)  │   │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼ Models
┌─────────────────────────────────────────────────────────────────────────┐
│                        DATABASE LAYER                                    │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  Models: JournalEntry, JournalLine, Event, EconomicEvent,         │  │
│  │  AuditEvent, InterpretationOutcome, EconomicLink, Party, Contract│  │
│  │  Account, FiscalPeriod, Approval, CostLot, Subledger...          │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────┐  ┌──────────────────────────────────────────┐  │
│  │  ORM Immutability  │  │    PostgreSQL Triggers (Defense)         │  │
│  │    (listeners)     │  │         (db/triggers.py)                 │  │
│  └────────────────────┘  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼ Pure Functions
┌─────────────────────────────────────────────────────────────────────────┐
│                      FINANCE ENGINES (Pure)                              │
│  ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌───────┐ ┌──────────────┐   │
│  │ Variance │ │ Allocation │ │ Matching │ │ Aging │ │     Tax      │   │
│  │ (PPV,FX) │ │(FIFO,LIFO) │ │ (3-way)  │ │(AP/AR)│ │(VAT,GST,WHT) │   │
│  └──────────┘ └────────────┘ └──────────┘ └───────┘ └──────────────┘   │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                    Subledger (Base Pattern)                        │  │
│  │          AP, AR, Bank, Inventory, Fixed Assets, Intercompany      │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Key Invariants (Rules)

These invariants are **non-negotiable**. The system is designed to make violations impossible, not just discouraged.

### Core Posting Invariants

| Rule | Name | Description | Enforcement |
|------|------|-------------|-------------|
| **R1** | Event immutability | Events are immutable after ingestion | Payload hash check, ORM listeners |
| **R2** | Payload hash verification | Same event_id + different payload = protocol violation | Hash comparison on ingest |
| **R3** | Idempotency key uniqueness | Exactly one JournalEntry per idempotency_key | Unique constraint + row locking |
| **R4** | Balance per currency | JournalEntry must balance per currency | Bookkeeper validation |
| **R5** | Rounding line uniqueness | Rounding creates exactly one marked line | Bookkeeper + DB triggers |

### Data Integrity Invariants

| Rule | Name | Description | Enforcement |
|------|------|-------------|-------------|
| **R6** | Replay safety | Ledger state reproducible from journal + reference data | No stored balances, computed trial balance |
| **R7** | Transaction boundaries | Each service owns its transaction boundary | Orchestrator pattern, explicit commits |
| **R8** | Idempotency locking | Database uniqueness constraints + row-level locks | UniqueConstraint + with_for_update() |
| **R9** | Sequence safety | Use database sequence or locked counter row; MAX(seq)+1 is FORBIDDEN | SequenceService with locked counter |
| **R10** | Posted record immutability | Posted JournalEntry, JournalLine, AuditEvent are immutable | ORM listeners + DB triggers |
| **R11** | Audit chain integrity | Audit chain must validate end-to-end with hash chain | Hash chain verification |

### Period & Account Invariants

| Rule | Name | Description | Enforcement |
|------|------|-------------|-------------|
| **R12** | Closed period enforcement | No posting to closed fiscal periods | PeriodService validation + DB triggers |
| **R13** | Adjustment policy | Adjustments only when period.allows_adjustments=True | PeriodService.validate_adjustment_allowed() |

### Architecture Invariants

| Rule | Name | Description | Enforcement |
|------|------|-------------|-------------|
| **R14** | No central dispatch | PostingEngine may not branch on event_type; use strategy registry | StrategyRegistry lookup, no if/switch |
| **R15** | Open/closed compliance | Adding new event type requires no engine modification | BasePostingStrategy extension |

### Domain Invariants

| Rule | Name | Description | Enforcement |
|------|------|-------------|-------------|
| **R16** | ISO 4217 enforcement | Currency codes validated at ingestion/domain boundary | CurrencyRegistry.validate() |
| **R17** | Precision-derived tolerance | Rounding tolerance derived from currency precision | CurrencyInfo.rounding_tolerance property |
| **R18** | Deterministic errors | All domain errors use typed exceptions with machine-readable codes | FinanceKernelError.code attribute |
| **R19** | No silent correction | Financial inconsistencies must fail or have traceable rounding line | Explicit is_rounding flag |

### Meta Invariants

| Rule | Name | Description | Enforcement |
|------|------|-------------|-------------|
| **R20** | Test class mapping | Every invariant must have: unit, concurrency, crash, replay tests | test_r20_test_class_mapping.py |

### Replay & Determinism Invariants

| Rule | Name | Description | Enforcement |
|------|------|-------------|-------------|
| **R21** | Reference snapshot determinism | Every posted JournalEntry must record immutable version identifiers | JournalEntry fields; ReferenceSnapshot |
| **R22** | Rounding line isolation | Only Bookkeeper may generate `is_rounding=true` JournalLines | BasePostingStrategy validation; DB triggers |
| **R23** | Strategy lifecycle governance | Each strategy must declare lifecycle versions and replay_policy | StrategyRegistry._validate_lifecycle() |
| **R24** | Canonical ledger hash | Deterministic hash computable over sorted entries | LedgerSelector.canonical_hash() |

### Governance & Authority Invariants

| Rule | Name | Description | Enforcement |
|------|------|-------------|-------------|
| **R25** | Kernel primitives only | Money, ArtifactRef, Quantity, ExchangeRate from kernel; no parallel types in modules | Architecture tests; R25 boundary checks |
| **R26** | Journal is system of record | Module ORM is derivable projection; financial truth in journal + link graph | Design; no module balance as source of truth |
| **R27** | Matching is operational | Variance/ledger impact from kernel policy (profiles, guards), not module logic | Policy/guard wiring; module calls engines only |
| **R28** | No generic workflows | Each action binds to a specific lifecycle workflow; no catch-all workflows | WORKFLOW_DIRECTIVE; architecture tests |
| **R29** | Posting status authority | Only kernel may assert POSTED; services return transition/guard outcomes | ModulePostingResult.is_ledger_fact vs is_transition |

### Interpretation Layer Invariants

| Rule | Name | Description | Enforcement |
|------|------|-------------|-------------|
| **L1** | Role resolution | Every account role resolves to exactly one COA account | JournalWriter.resolve_roles() |
| **L2** | No runtime ambiguity | Role bindings must be unambiguous at interpretation time | RoleBinding validation |
| **L4** | Snapshot replay | Replay using stored ReferenceSnapshot produces identical results | Component versions in EconomicEvent |
| **L5** | Outcome atomicity | No journal rows without POSTED outcome; no POSTED without all rows | InterpretationCoordinator atomic txn |

### Profile Invariants

| Rule | Name | Description | Enforcement |
|------|------|-------------|-------------|
| **P1** | Profile uniqueness | Exactly one AccountingPolicy matches any event or event is rejected | PolicySelector precedence resolution |
| **P11** | Multi-ledger atomicity | Multi-ledger postings from single AccountingIntent are atomic | JournalWriter atomic transaction |
| **P15** | Outcome uniqueness | Every accepted BusinessEvent has exactly one InterpretationOutcome | Unique constraint on source_event_id |

*Full invariant table (R1–R29): see `../CLAUDE.md`.*

---

## Specifications

These sections specify behavior that invariants reference. They ensure independent implementations and audits can reproduce the same semantics.

### Snapshot boundary (R21)

A **ReferenceSnapshot** is a frozen set of component versions captured at posting time. Each component has a single **version** (integer) and **content_hash** (SHA-256 of that component’s state).

| Component | Scope | Granularity |
|-----------|--------|--------------|
| **COA** | Chart of accounts | One version per snapshot; full COA tree included via that version. |
| **DIMENSION_SCHEMA** | Dimension definitions and hierarchy | One version; dimension set is versioned as a whole. |
| **FX_RATES** | Exchange rates | One version for the **entire rate table** (all currency pairs); not per-pair. |
| **TAX_RULES** | Tax rule definitions | One version; tax rules versioned independently of policy. |
| **POLICY_REGISTRY** | Economic policies (profiles) | One version. |
| **ROUNDING_POLICY** | Rounding rules | One version. |
| **ACCOUNT_ROLES** | Role-to-account bindings | One version. |
| **CONFIGURATION_SET** | Configuration set ID + checksum | Optional; links snapshot to a named config set. |

Replay (L4) uses the same snapshot component versions to produce identical results. Dimension hierarchies are part of DIMENSION_SCHEMA, not separate.

### Canonical ledger hash (R24)

The canonical hash is a **SHA-256** digest over all **posted** journal lines in a deterministic order. Same ledger state always yields the same hash.

- **Scope**: All posted `JournalLine` rows (optionally filtered by `as_of_date` and/or `currency`).
- **Sort order**: Lines are ordered by `(account_id, currency, dimensions_json, entry_seq, line_seq)`. `dimensions_json` is JSON with **sorted keys**; empty dimensions = `""`.
- **Line representation**: Each line is a single JSON object (sorted keys) with: `account_id`, `currency`, `dimensions`, `entry_seq`, `line_seq`, `side`, `amount`, `is_rounding`. Fed into the hash as `json.dumps(line, sort_keys=True, separators=(",", ":"))` plus newline.
- **Decimal normalization**: Amounts are stringified from the stored `Numeric(38,9)` value (e.g. `str(row.amount)`). No float; trailing zeros are preserved by the DB type.
- **Hash**: SHA-256 over the concatenation of these line strings in sort order; result is 64-character hex.

Used for: post-replay verification, tamper detection, distributed consistency checks. Implementation: `LedgerSelector.canonical_hash()`, `_get_canonical_lines()`, `_compute_hash()`.

### Multi-ledger balancing (R4, P11)

- **Per entry**: Every `JournalEntry` must balance **per currency**: sum of debits = sum of credits for each currency in that entry (R4). Rounding may introduce one `is_rounding=true` line per currency (R5).
- **Per ledger**: An `AccountingIntent` may contain multiple **LedgerIntent**s (one per ledger, e.g. GL, AP, AR). Each `LedgerIntent` must balance **per currency** (`LedgerIntent.is_balanced(currency)`). JournalWriter validates each ledger intent before writing (R4).
- **Atomicity**: All ledgers in a single intent are written in one transaction (P11). If any ledger fails (e.g. balance check, role resolution), the entire write is rolled back; no partial multi-ledger postings.
- **Cross-ledger**: There is no requirement that balances *across* ledgers net to zero (e.g. statutory vs management, or local vs functional). Each ledger balances in isolation. FX or inter-ledger bridge entries are modeled as separate lines in the appropriate ledgers.

### Sequence semantics (R9)

- **Source of truth**: A **locked counter row** per named sequence (e.g. `journal_entry`, `audit_event`). `MAX(seq)+1` or any aggregate-based next value is **forbidden** (R9).
- **Gap policy**: Under normal operation, no values are skipped. If a transaction **rolls back**, the allocated value is **not** reused—the next successful call gets the next number. Gaps are allowed; monotonicity is strict.
- **Rollback**: Sequence increment is committed only with the caller’s transaction. On rollback, the counter is rolled back; the “consumed” value is effectively discarded and will appear as a gap.
- **Scope**: Sequences are **global** per name (e.g. one `journal_entry` sequence for the kernel), not per-ledger. Journal entry `seq` is a single monotonic stream across all ledgers.
- **Well-known names**: `SequenceService.JOURNAL_ENTRY`, `SequenceService.AUDIT_EVENT`. Implementation: `SequenceService.next_value(sequence_name)` with `SELECT ... FOR UPDATE` on the counter row.

### Ingestion and trust boundary

What the kernel **enforces** at ingestion (R1–R3, schema):

- **Event identity and immutability (R1, R2)**: `event_id` unique; same `event_id` with different payload → `PayloadMismatchError` (protocol violation). Payload hash stored and checked.
- **Idempotency (R3)**: One journal entry per `idempotency_key`; duplicate key returns existing result or blocks until one writer wins.
- **Schema**: Event payload validated against registered schema for `event_type`; version and field references checked (e.g. `SchemaValidationError`, `InvalidFieldReferenceError`).

**Out of scope** (or deferred): producer authentication, clock skew tolerance, duplicate external IDs beyond `event_id`/idempotency. Adversarial input is mitigated by payload hashing and idempotency; producer identity and clock are caller responsibilities.

---

## Failure outcome matrix and lifecycles

### Failure outcome matrix

| Failure mode | Outcome / status | EconomicEvent? | JournalEntry? | Recoverable? |
|--------------|------------------|----------------|---------------|--------------|
| **Guard REJECT** | InterpretationOutcome(REJECTED) | No | No | No (terminal). |
| **Guard BLOCK** | InterpretationOutcome(BLOCKED) | Yes | No | Yes, via transition_blocked_to_posted(). |
| **Role resolution failure** | Posting fails; REJECTED or service error | Yes (if intent built) | No | Retry after fixing role bindings. |
| **Balance failure** (unbalanced intent) | UnbalancedIntentError; transaction rolled back | Yes | No | Fix intent or rounding. |
| **Closed period** | PeriodError (e.g. ClosedPeriodError); REJECTED | Depends on where checked | No | No for that period. |
| **Adjustments not allowed** | AdjustmentsNotAllowedError | Depends | No | No unless period allows adjustments. |
| **Idempotent retry** (same idempotency_key) | AlreadyPostedError or success (no duplicate) | N/A | One entry only | N/A. |
| **Payload mismatch** (same event_id, different payload) | PayloadMismatchError; protocol violation | No | No | No. |

### Lifecycle: BLOCK → POST

```
BLOCKED (EconomicEvent exists, no journal rows)
    │
    │  transition_blocked_to_posted() when guards no longer block
    │  (e.g. approval granted, document attached)
    ▼
POSTED (InterpretationOutcome.POSTED, JournalEntry + JournalLines created)
```

### Lifecycle: Reversal

```
Posted JournalEntry
    │
    │  ReversalService.create_reversal_entry(...)
    │  New entry with inverted lines; link via EconomicLink (REVERSED_BY)
    ▼
Reversal JournalEntry (status POSTED); original entry unchanged (immutable)
```

---

## Data Flow: Event Interpretation & Posting

Modules typically call **ModulePostingService.post_event()** (canonical entry point), which delegates to InterpretationCoordinator. The flow below is the kernel interpretation pipeline.

```
1. External Module produces Business Event
   │
   ▼
2. ModulePostingService.post_event() → InterpretationCoordinator.interpret_and_post()
   │
   ├─► PolicySelector.find_for_event()
   │   - Match event_type to AccountingPolicy (P1: exactly one match)
   │   - Apply precedence rules (override > scope > priority)
   │
   ├─► ReferenceSnapshotService.capture()
   │   - Freeze COA, dimensions, FX rates, policies
   │   - Return immutable ReferenceSnapshot (R21)
   │
   ├─► MeaningBuilder.extract()
   │   - Evaluate guard conditions (REJECT/BLOCK)
   │   - Extract economic meaning from profile
   │   - Validate against PolicyAuthority (authority check)
   │   - Produce EconomicEventData
   │
   ├─► AccountingIntent.from_profile()
   │   - Build intent with account roles (not COA codes)
   │   - Include ledger effects per profile
   │   - Attach ReferenceSnapshot versions
   │
   ├─► JournalWriter.write()  [L5 Atomic]
   │   - Resolve roles to COA accounts (L1)
   │   - Validate balance per ledger
   │   - Create JournalEntry + JournalLines (P11: atomic)
   │   - Allocate sequence numbers
   │
   ├─► OutcomeRecorder.record()
   │   - Create InterpretationOutcome (P15: one per event)
   │   - Link EconomicEvent → JournalEntries
   │   - Set status: POSTED, BLOCKED, or REJECTED
   │
   └─► COMMIT or ROLLBACK (L5: all or nothing)
```

### Guard Evaluation Flow

```
Business Event
    │
    ▼
Profile.guards evaluated
    │
    ├─► REJECT condition true → InterpretationOutcome(REJECTED)
    │                          No EconomicEvent, No JournalEntry
    │
    ├─► BLOCK condition true  → InterpretationOutcome(BLOCKED)
    │                          EconomicEvent created, No JournalEntry
    │                          Can be resumed via transition_blocked_to_posted()
    │
    └─► All guards pass       → Continue to posting
```

---

## Directory Structure

```
finance_kernel/
├── domain/                    # Pure business logic (no I/O, no state)
│   ├── bookkeeper.py              # Transforms events → journal entries
│   ├── strategy.py                # Base class for posting strategies
│   ├── strategy_registry.py       # Strategy lookup by event_type
│   ├── dtos.py                    # Immutable data transfer objects
│   ├── values.py                  # Value objects (Money, etc.)
│   ├── currency.py                # ISO 4217 currency handling
│   ├── clock.py                   # Time abstraction for testability
│   │
│   │   # === Interpretation Layer ===
│   ├── accounting_policy.py       # Declarative governance profiles
│   ├── accounting_intent.py       # Contract between economic/finance layers
│   ├── meaning_builder.py         # Extract economic meaning from events
│   ├── reference_snapshot.py      # Frozen reference data for replay
│   ├── policy_selector.py         # Profile lookup with precedence
│   ├── policy_authority.py        # Authority/governance control
│   ├── policy_compiler.py         # Profile compilation/validation
│   ├── policy_bridge.py           # Bridge between policy and posting
│   ├── ledger_registry.py         # Ledger definitions
│   ├── valuation.py               # Valuation models
│   ├── subledger_control.py       # Subledger control contracts
│   ├── economic_link.py           # Event linkage domain logic
│   ├── event_validator.py         # Event validation logic
│   ├── approval.py                # Approval domain types
│   ├── control.py                 # Control rule evaluation
│   ├── policy_source.py           # Policy source abstraction
│   ├── validation.py              # Validation helpers
│   ├── workflow.py                # Workflow/lifecycle types
│   │
│   ├── schemas/                   # Event schema definitions
│   │   ├── registry.py                # Schema registry
│   │   ├── base.py                    # Base schema classes
│   │   └── definitions/               # Per-event-type schemas
│   │       ├── generic.py                 # Generic posting
│   │       ├── ap.py                      # AP invoice/payment
│   │       ├── ar.py                      # AR invoice/payment/credit
│   │       ├── asset.py                   # Acquisition/depreciation/disposal
│   │       ├── bank.py                    # Deposit/withdrawal/transfer
│   │       ├── deferred.py                # Revenue/expense recognition
│   │       ├── fx.py                      # FX revaluation
│   │       ├── inventory.py               # Receipt/issue/adjustment
│   │       ├── payroll.py                 # Timesheet/labor distribution
│   │       ├── contract.py                # Contract billing events
│   │       └── dcaa.py                    # DCAA compliance events
│   │   # Profiles live in finance_modules/*/profiles.py
│   │
│   └── strategies/                # Posting strategies
│       ├── __init__.py
│       └── generic_strategy.py        # Generic posting strategy
│
├── services/                  # Stateful services with I/O (kernel only)
│   ├── module_posting_service.py     # Canonical posting entry (modules call this)
│   ├── ingestor_service.py           # Event ingestion
│   ├── interpretation_coordinator.py # L5 atomicity: MeaningBuilder → JournalWriter → OutcomeRecorder
│   ├── journal_writer.py             # Atomic multi-ledger posting
│   ├── outcome_recorder.py           # InterpretationOutcome management
│   ├── reference_snapshot_service.py # Snapshot capture/retrieval
│   ├── auditor_service.py            # Audit trail, hash chain
│   ├── period_service.py             # Fiscal period management
│   ├── sequence_service.py           # Monotonic sequence allocation (R9)
│   ├── link_graph_service.py         # Economic event linkage
│   ├── contract_service.py            # Contract lifecycle management
│   ├── party_service.py               # Party (customer/supplier) management
│   ├── approval_service.py           # Approval requests and decisions
│   ├── reversal_service.py           # Reversal entry creation
│   ├── retry_service.py              # Retry/backoff for transient failures
│   └── log_capture.py                # Structured log capture for outcomes
│   (PostingOrchestrator, WorkflowExecutor, etc. live in finance_services/)
│
├── models/                    # SQLAlchemy ORM models
│   ├── journal.py                 # JournalEntry, JournalLine
│   ├── event.py                   # Event store
│   ├── audit_event.py             # Audit trail with hash chain
│   ├── account.py                 # Chart of accounts
│   ├── fiscal_period.py           # Period management
│   ├── dimensions.py              # Dimension/DimensionValue
│   ├── exchange_rate.py           # Currency exchange rates
│   │
│   │   # === Interpretation Models ===
│   ├── economic_event.py          # Interpreted economic meaning
│   ├── interpretation_outcome.py  # Terminal interpretation state
│   ├── economic_link.py           # Event linkage records
│   ├── party.py                   # Party (Customer/Supplier) with status
│   ├── contract.py                # Contract, ContractLineItem (DCAA)
│   ├── approval.py                 # Approval request/decision records
│   ├── cost_lot.py                # Cost lot (valuation) records
│   └── subledger.py               # Subledger control/balance records
│
├── db/                        # Database infrastructure
│   ├── base.py                    # Declarative base, session hooks
│   ├── engine.py                  # Connection management
│   ├── immutability.py            # ORM-level immutability listeners
│   ├── triggers.py                # PostgreSQL triggers (defense in depth)
│   ├── types.py                   # Custom SQLAlchemy types
│   └── sql/                       # SQL trigger scripts
│       ├── 01_journal_entry.sql
│       ├── ... (through 08_exchange_rate.sql)
│       ├── 09_event_immutability.sql
│       ├── 10_balance_enforcement.sql
│       ├── 11_economic_link_immutability.sql
│       ├── 12_cost_lot.sql
│       ├── 13_outcome_exception_lifecycle.sql
│       └── 99_drop_all.sql
│
├── selectors/                 # Read-only query services
│   ├── __init__.py
│   ├── base.py                    # Base selector class
│   ├── ledger_selector.py         # Ledger queries, trial balance
│   ├── journal_selector.py        # Journal entry queries
│   ├── subledger_selector.py      # Subledger balance/open-item queries
│   └── trace_selector.py          # Trace/audit query support
│
├── exceptions.py              # Hierarchical exception system
└── utils/                     # Utilities (hashing, etc.)

finance_engines/               # Pure Calculation Engines
├── __init__.py
├── variance.py                    # Price, quantity, FX variance calculations
├── allocation.py                  # FIFO, LIFO, prorata, weighted allocation
├── allocation_cascade.py          # DCAA multi-step indirect cost allocation
├── matching.py                    # 3-way match, bank reconciliation
├── aging.py                       # AP/AR aging buckets
├── subledger.py                   # Base subledger pattern
├── tax.py                         # VAT, GST, withholding, compound tax
├── billing.py                     # Government contract billing (CPFF, T&M, FFP)
├── ice.py                         # DCAA Incurred Cost Electronically (Schedules A-J)
├── contracts.py                   # Contract engine utilities
├── tracer.py                      # Engine tracing/diagnostics
├── correction/                    # Correction/reversal engines
│   └── unwind.py                      # Unwind logic
├── reconciliation/                # Document reconciliation
│   ├── domain.py                      # Reconciliation domain models
│   ├── checker.py                     # Match/reconciliation checker
│   ├── bank_checker.py                # Bank reconciliation checker
│   ├── bank_recon_types.py            # Bank recon types
│   └── lifecycle_types.py             # Lifecycle state types
├── approval.py                   # Approval engine (pure)
├── expense_compliance.py         # Expense/DCAA compliance
├── rate_compliance.py            # Rate compliance
├── timesheet_compliance.py       # Timesheet compliance
└── valuation/                     # Cost layer valuation
    └── cost_lot.py                    # Cost lot tracking

finance_services/              # Stateful Services
├── correction_service.py         # CorrectionEngine (stateful)
├── reconciliation_service.py     # ReconciliationManager (stateful)
├── subledger_service.py          # SubledgerService (stateful)
└── valuation_service.py          # ValuationLayer (stateful)

docs/                          # Design Documentation
└── MODULE_DEVELOPMENT_GUIDE.md    # How to build ERP modules
```

---

## Finance Engines (Pure Functions)

The **finance_engines/** package provides pure calculation engines: no I/O, no database, no session. They are deterministic and reusable across all ERP modules. Stateful orchestration (e.g. CorrectionService, ValuationService) lives in **finance_services** and calls these engines.

**Engine catalog, one-line descriptions, and usage:** see **finance_engines/README.md**. When you add or change an engine, update that file and `finance_engines/__init__.py`; this README does not duplicate the catalog.

---

## Key Design Decisions

### 1. Pure Domain Layer

The `domain/` package contains **zero I/O operations**. The Bookkeeper, MeaningBuilder, and strategies:
- Take immutable inputs (EventEnvelope, ReferenceData, ReferenceSnapshot)
- Return immutable outputs (ProposedJournalEntry, AccountingIntent)
- Never access the database, network, or clock directly

**Why?** This makes the core accounting logic:
- **Testable**: Unit tests with no mocking required
- **Deterministic**: Same inputs always produce same outputs
- **Replayable**: Historical events can be replayed identically

### 2. Declarative Economic Profiles

Instead of procedural code, event interpretation is driven by **declarative AccountingPolicies**:
- **Trigger**: Which events match this profile
- **Meaning**: What economic type and dimensions to extract
- **Ledger Effects**: What debits/credits to generate
- **Guards**: REJECT (terminal) or BLOCK (resumable) conditions

**Why?** This approach:
- Separates policy from mechanism
- Enables non-programmers to configure behavior
- Creates auditable governance records
- Supports precedence and override rules

### 3. Kernel primitives (R25)

Monetary values, quantities, exchange rates, and artifact identities use **kernel value types only**. The full set: **Currency**, **Money**, **Quantity**, **ExchangeRate** (`domain/values.py`), **ArtifactRef** (`domain/economic_link.py`). Modules and engines must not introduce parallel types (no module-defined Money or Currency). This keeps precision, rounding, and validation in one place and is enforced by architecture tests. See the Overview table above for a one-line description of each primitive.

### 4. Account Roles vs COA Codes

AccountingIntent uses **semantic account roles** (e.g., `CONTROL_AP`, `EXPENSE_PPV`) instead of COA codes:
- Roles are resolved to actual accounts at posting time
- Same intent works across different COA structures
- Supports multi-entity/multi-book scenarios

### 5. Reference Snapshots for Replay

Every posted entry records a **ReferenceSnapshot** containing version identifiers for COA, dimension schema, FX rates, tax rules, policy registry, rounding policy, and account roles. **Why?** Deterministic replay (R21), audit trail of "what rules were in effect," and safe schema evolution. See **Specifications § Snapshot boundary (R21)** for the full component list and granularity (e.g. FX as whole table, dimensions as whole schema).

### 6. Defense in Depth for Immutability

Immutability is enforced at **three levels**:

1. **ORM Level** (`db/immutability.py`): SQLAlchemy `before_update`, `before_delete`, and `before_flush` listeners.

2. **Database Level** (`db/triggers.py`): PostgreSQL triggers catch raw SQL and direct database access.

3. **Session Level**: `before_flush` events catch deletions before the flush plan is finalized.

### 7. Hash Chain Audit Trail

Every AuditEvent and EconomicEvent contains:
- `payload_hash`: Hash of the event data
- `prev_hash`: Hash of the previous record
- `hash`: Hash of (payload_hash + prev_hash)

This creates a blockchain-like structure where any tampering breaks the chain.

### 8. Pure Calculation Engines

All calculation logic in `finance_engines/` is **pure functions**:
- No database access
- No session injection
- Deterministic outputs
- Easily testable

---

## Exception Hierarchy

All kernel exceptions inherit from `FinanceKernelError` (R18) and include:
- `code`: Machine-readable error code
- Human-readable message

Profile/role resolution errors: **PolicyNotFoundError** lives in `domain.policy_selector`; **RoleResolutionError** is raised by `JournalWriter` (in `services.journal_writer`). Domain validation uses the **ValidationError** dataclass in `domain.dtos` (not an exception).

```
FinanceKernelError
├── EventError                   # Event ingestion, schema, payload
│   ├── PayloadMismatchError, SchemaValidationError, EventNotFoundError, ...
├── PostingError                 # Posting-specific errors
│   ├── UnbalancedEntryError, AlreadyPostedError, InvalidAccountError, ...
├── PeriodError                  # Fiscal period
│   ├── ClosedPeriodError, AdjustmentsNotAllowedError, PeriodNotFoundError, ...
├── ImmutabilityError            # Attempted modification of immutable data
│   └── ImmutabilityViolationError
├── AccountError                 # Account lifecycle, references
│   └── AccountReferencedError, AccountNotFoundError, ...
├── AuditError
│   └── AuditChainBrokenError
├── ReversalError, ConcurrencyError, RoundingError, ReferenceSnapshotError
├── StrategyLifecycleError       # Strategy version, rounding (R22/R23)
├── EconomicLinkError            # Links: cycle, duplicate, immutable, ...
├── ReconciliationError, ValuationError, CorrectionError
├── PartyError, ContractError, ActorError, ApprovalError
└── BatchError, ScheduleError
```

---

## Getting Started

### Canonical Posting Entry (Recommended)

All modules should call **ModulePostingService.post_event()**. Inject a **Clock** for testability (avoid `datetime.now()`/`date.today()` in production code).

```python
from uuid import uuid4
from datetime import datetime, date
from zoneinfo import ZoneInfo

from finance_kernel.db.engine import get_session
from finance_kernel.domain.clock import SystemClock
from finance_kernel.services.module_posting_service import ModulePostingService
from finance_kernel.services.journal_writer import RoleResolver

with get_session() as session:
    clock = SystemClock()  # or DeterministicClock in tests
    role_resolver = RoleResolver()  # register_binding(...) from config/ledger registry
    poster = ModulePostingService(session, role_resolver=role_resolver, clock=clock)

    result = poster.post_event(
        event_id=uuid4(),
        event_type="ap.invoice.received",
        occurred_at=clock.now(),
        effective_date=clock.today(),
        actor_id=user_id,
        producer="ap-module",
        payload={...},
    )

    if result.is_ledger_fact:
        print(f"Posted entries: {result.journal_entry_ids}")
    elif result.is_transition:
        print(f"Governance: {result.status}")
    else:
        print(f"Rejected/blocked: {result.status} {result.reason_detail}")
```

### Low-Level: InterpretationCoordinator

For callers that need direct access to the interpretation pipeline (e.g. no approval/guard orchestration):

```python
from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator
from finance_kernel.db.engine import get_session

with get_session() as session:
    coordinator = InterpretationCoordinator(session)
    result = coordinator.interpret_and_post(
        event_id=uuid4(),
        event_type="ap.invoice.received",
        occurred_at=datetime.now(UTC),
        effective_date=date.today(),
        actor_id=user_id,
        producer="ap-module",
        payload={...},
    )
    # result.outcome.status: POSTED | BLOCKED | REJECTED
```

### PostingOrchestrator (finance_services)

**PostingOrchestrator** lives in **finance_services** and wires kernel services (ModulePostingService, policy source, approval). Use it when you need the full DI/configuration stack.

```python
from finance_services import PostingOrchestrator
from finance_kernel.db.engine import get_session

with get_session() as session:
    orchestrator = PostingOrchestrator(session)  # or from_config(...)
    result = orchestrator.post_event(...)
```

### Creating an Accounting Policy

```python
from finance_kernel.domain.accounting_policy import (
    AccountingPolicy, PolicyTrigger, PolicyMeaning,
    LedgerEffect, GuardCondition, GuardType
)

profile = AccountingPolicy(
    name="ap.invoice.standard",
    version=1,
    trigger=PolicyTrigger(
        event_type="ap.invoice.received",
        conditions={"invoice_type": "standard"},
    ),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_RECOGNITION",
        dimension_extraction={"vendor": "$.vendor_id"},
    ),
    ledger_effects=[
        LedgerEffect(
            ledger="GL",
            debit_role="EXPENSE_ACCOUNT",
            credit_role="CONTROL_AP",
        ),
    ],
    guards=[
        GuardCondition(
            type=GuardType.REJECT,
            condition="$.amount <= 0",
            reason="Invoice amount must be positive",
        ),
    ],
)

PolicySelector.register(profile)
```

---

## Engine status and kernel capabilities

**Pure engines (catalog and status):** see **finance_engines/README.md**. That file is the single source of truth for the engine list; update it when adding or changing engines.

**Kernel capabilities** (not in finance_engines):

| Capability | Location | Description |
|------------|----------|-------------|
| **EconomicLink** | finance_kernel | First-class artifact links (Receipt→PO, Payment→Invoice); LinkGraphService, cycle detection. |
| **Interpretation layer** | finance_kernel | AccountingPolicy, MeaningBuilder, JournalWriter, OutcomeRecorder (L1–L5, P1/P11/P15). |
| **Schema system** | finance_kernel.domain.schemas | Event schema definitions per domain. |
| **Profile system** | finance_modules/*/profiles | AccountingPolicy profiles and guards. |

### Potential future extensions

| Engine | Assessment | Recommendation |
|--------|-----------|----------------|
| **AccumulatorEngine** | Gathers costs over time for WIP and Accruals | Defer until concrete WIP use case emerges |
| **GaplessSequenceEngine** | Strictly monotonic document numbering | Extend SequenceService if needed |
| **DimensionConstraintEngine** | Cross-dimension rules | Can be validation rules in EventValidator |
| **EffectivityResolver** | Policy/ExchangeRate lookup by timestamp | Already in PolicyAuthority; extend if needed |

---

## Invariant Test Coverage

All invariants have comprehensive test coverage. Counts below are approximate; run `python3 -m pytest tests/ --collect-only -q` for current numbers.

| Category | Directory | Files | Test Classes |
|----------|-----------|-------|--------------|
| **Unit** | `tests/unit/` | 2 | 10 |
| **Domain** | `tests/domain/` | 20 | 153 |
| **Architecture** | `tests/architecture/` | 8 | 15 |
| **Engines** | `tests/engines/` | 12 | 104 |
| **Modules** | `tests/modules/` | 31 | 163 |
| **Services** | `tests/services/` | 3 | 27 |
| **Posting** | `tests/posting/` | 4 | 11 |
| **Audit** | `tests/audit/` | 9 | 37 |
| **Adversarial** | `tests/adversarial/` | 10 | 36 |
| **Concurrency** | `tests/concurrency/` | 5 | 22 |
| **Replay** | `tests/replay/` | 4 | 18 |
| **Crash** | `tests/crash/` | 2 | 9 |
| **Fuzzing** | `tests/fuzzing/` | 2 | 10 |
| **Multi-Currency** | `tests/multicurrency/` | 2 | 13 |
| **Database Security** | `tests/database_security/` | 1 | 5 |
| **Security** | `tests/security/` | 1 | 8 |
| **Integration** | `tests/integration/` | 2 | - |
| **Demo** | `tests/demo/` | 1 | - |
| **Other** | Various | 5 | 17 |
| **Total** | | **122** | **717** |

---

## See Also

- `invariants.py` - Kernel invariant enum and R25–R27 notes
- `db/README.md` - Database layer details
- `../finance_engines/README.md` - Engine catalog (single source of truth for pure engines)
- `../finance_services/README.md` - Orchestration and DI; consumes kernel and config
- `../finance_config/README.md` - Configuration single entrypoint; no kernel import of config
- `../CLAUDE.md` - Full invariant table (R1–R29), layers, rules
- `../docs/MODULE_DEVELOPMENT_GUIDE.md` - How to build ERP modules
- `../docs/TRACE.md` - Trace bundle and audit investigation (TraceSelector, LogQueryPort)
- `../docs/EXTENSIBILITY.md` - Custom features without changing core (config, modules, strategies, engines)
- `../finance_ingestion/README.md` - ERP data ingestion (staging, mapping, promotion); distinct from kernel event ingestion
