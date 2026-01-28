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
│  │ProfileRegistry│ │ MeaningBuilder │ │    ReferenceSnapshot        │   │
│  │  (P1: unique) │ │  (pure domain) │ │  (R21: determinism)         │   │
│  └──────────────┘ └────────────────┘ └─────────────────────────────┘   │
│  ┌──────────────┐ ┌────────────────┐ ┌─────────────────────────────┐   │
│  │PolicyRegistry │ │AccountingIntent│ │   EconomicProfile           │   │
│  │  (authority)  │ │  (L1: roles)   │ │  (declarative governance)   │   │
│  └──────────────┘ └────────────────┘ └─────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼ AccountingIntent
┌─────────────────────────────────────────────────────────────────────────┐
│                        SERVICES LAYER                                    │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                PostingOrchestrator (R7)                            │  │
│  │    Coordinates: Ingestor → Bookkeeper → Ledger → Auditor          │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌───────────────────┐    │
│  │JournalWriter│ │OutcomeRec. │ │  Ledger    │ │   PeriodService   │    │
│  │(P11: atomic)│ │(P15: unique)│ │            │ │                   │    │
│  └────────────┘ └────────────┘ └────────────┘ └───────────────────┘    │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌───────────────────┐    │
│  │  Ingestor  │ │  Auditor   │ │ LinkGraph  │ │ RefSnapshotSvc    │    │
│  └────────────┘ └────────────┘ └────────────┘ └───────────────────┘    │
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
│  │  Models: JournalEntry, JournalLine, EconomicEvent, AuditEvent...  │  │
│  │          InterpretationOutcome, EconomicLink                       │  │
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
| **P1** | Profile uniqueness | Exactly one EconomicProfile matches any event or event is rejected | ProfileRegistry precedence resolution |
| **P11** | Multi-ledger atomicity | Multi-ledger postings from single AccountingIntent are atomic | JournalWriter atomic transaction |
| **P15** | Outcome uniqueness | Every accepted BusinessEvent has exactly one InterpretationOutcome | Unique constraint on source_event_id |

---

## Data Flow: Event Interpretation & Posting

```
1. External Module produces Business Event
   │
   ▼
2. InterpretationCoordinator.interpret_and_post()
   │
   ├─► ProfileRegistry.find_for_event()
   │   - Match event_type to EconomicProfile (P1: exactly one match)
   │   - Apply precedence rules (override > scope > priority)
   │
   ├─► ReferenceSnapshotService.capture()
   │   - Freeze COA, dimensions, FX rates, policies
   │   - Return immutable ReferenceSnapshot (R21)
   │
   ├─► MeaningBuilder.extract()
   │   - Evaluate guard conditions (REJECT/BLOCK)
   │   - Extract economic meaning from profile
   │   - Validate against PolicyRegistry (authority check)
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
│   ├── economic_profile.py        # Declarative governance profiles
│   ├── accounting_intent.py       # Contract between economic/finance layers
│   ├── meaning_builder.py         # Extract economic meaning from events
│   ├── reference_snapshot.py      # Frozen reference data for replay
│   ├── profile_registry.py        # Profile lookup with precedence
│   ├── policy_registry.py         # Authority/governance control
│   ├── profile_compiler.py        # Profile compilation/validation
│   ├── ledger_registry.py         # Ledger definitions
│   ├── valuation.py               # Valuation models
│   ├── subledger_control.py       # Subledger control contracts
│   ├── economic_link.py           # Event linkage domain logic
│   │
│   └── schemas/                   # Event schema definitions
│       ├── registry.py                # Schema registry
│       ├── base.py                    # Base schema classes
│       └── definitions/               # Per-event-type schemas
│
├── services/                  # Stateful services with I/O
│   ├── posting_orchestrator.py    # Main entry point (legacy)
│   ├── ingestor_service.py        # Event ingestion
│   ├── ledger_service.py          # Journal persistence
│   ├── auditor_service.py         # Audit trail
│   ├── period_service.py          # Fiscal period management
│   ├── sequence_service.py        # Monotonic sequence allocation
│   ├── reference_data_loader.py   # Load reference data
│   │
│   │   # === Interpretation Services ===
│   ├── interpretation_coordinator.py  # L5 atomicity orchestrator
│   ├── journal_writer.py              # Atomic multi-ledger posting
│   ├── outcome_recorder.py            # InterpretationOutcome management
│   ├── reference_snapshot_service.py  # Snapshot capture/retrieval
│   └── link_graph_service.py          # Economic event linkage
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
│   └── economic_link.py           # Event linkage records
│
├── db/                        # Database infrastructure
│   ├── engine.py                  # Connection management
│   ├── immutability.py            # ORM-level immutability listeners
│   ├── triggers.py                # PostgreSQL triggers (defense in depth)
│   ├── types.py                   # Custom SQLAlchemy types
│   └── sql/                       # SQL trigger scripts
│       ├── 01_hash_chain.sql
│       ├── ...
│       ├── 09_event_immutability.sql
│       ├── 10_balance_enforcement.sql
│       └── 11_economic_link_immutability.sql
│
├── selectors/                 # Read-only query services
│   ├── ledger_selector.py         # Ledger queries, trial balance
│   └── journal_selector.py        # Journal entry queries
│
├── exceptions.py              # Hierarchical exception system
└── utils/                     # Utilities (hashing, etc.)

finance_engines/               # Pure Calculation Engines
├── __init__.py
├── variance.py                    # Price, quantity, FX variance calculations
├── allocation.py                  # FIFO, LIFO, prorata, weighted allocation
├── matching.py                    # 3-way match, bank reconciliation
├── aging.py                       # AP/AR aging buckets
├── subledger.py                   # Base subledger pattern
└── tax.py                         # VAT, GST, withholding, compound tax

docs/                          # Design Documentation
├── MODULE_DEVELOPMENT_GUIDE.md    # How to build ERP modules
├── ap_module_design.md            # AP module reference design
└── reusable_modules.md            # Reusable engine patterns
```

---

## Finance Engines (Pure Functions)

The `finance_engines/` package contains **pure calculation engines** with no I/O or database access. These are reusable across all ERP modules.

### Variance Engine (`variance.py`)

Calculates price, quantity, and FX variances:
- **PPV (Purchase Price Variance)**: Standard cost vs actual cost
- **Sales Price Variance**: Expected vs actual selling price
- **Material Usage Variance**: Standard quantity vs actual quantity
- **Exchange Rate Variance**: Budgeted vs actual FX rates

```python
from finance_engines.variance import VarianceCalculator, VarianceType

result = VarianceCalculator.calculate_price_variance(
    expected_price=Money.of(Decimal("100.00"), "USD"),
    actual_price=Money.of(Decimal("105.00"), "USD"),
    quantity=Decimal("10"),
)
# result.variance = Money(-50.00, USD)  # Unfavorable
```

### Allocation Engine (`allocation.py`)

Distributes amounts across targets using various methods:
- **PRORATA**: Proportional by weight
- **FIFO**: First-in-first-out by date
- **LIFO**: Last-in-first-out by date
- **SPECIFIC**: Explicit target specification
- **WEIGHTED**: Custom weights
- **EQUAL**: Even distribution

### Matching Engine (`matching.py`)

Document matching for:
- **3-Way Match**: PO ↔ Receipt ↔ Invoice
- **2-Way Match**: PO ↔ Invoice
- **Shipment Match**: Order ↔ Shipment
- **Bank Reconciliation**: Statement ↔ Transactions

### Aging Engine (`aging.py`)

Aged analysis for AP, AR, and inventory:
- Configurable buckets (Current, 1-30, 31-60, 61-90, Over 90)
- Multiple aggregation methods
- Slow-moving inventory analysis

### Tax Engine (`tax.py`)

Tax calculations supporting:
- Sales Tax, VAT, GST
- Withholding Tax
- Compound Taxes (tax-on-tax)
- Inclusive/Exclusive calculations

### Subledger Engine (`subledger.py`)

Base pattern for all subledger implementations:
- AP, AR, Bank, Inventory, Fixed Assets, Intercompany
- Open item tracking and reconciliation
- Balance calculation

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

Instead of procedural code, event interpretation is driven by **declarative EconomicProfiles**:
- **Trigger**: Which events match this profile
- **Meaning**: What economic type and dimensions to extract
- **Ledger Effects**: What debits/credits to generate
- **Guards**: REJECT (terminal) or BLOCK (resumable) conditions

**Why?** This approach:
- Separates policy from mechanism
- Enables non-programmers to configure behavior
- Creates auditable governance records
- Supports precedence and override rules

### 3. Account Roles vs COA Codes

AccountingIntent uses **semantic account roles** (e.g., `CONTROL_AP`, `EXPENSE_PPV`) instead of COA codes:
- Roles are resolved to actual accounts at posting time
- Same intent works across different COA structures
- Supports multi-entity/multi-book scenarios

### 4. Reference Snapshots for Replay

Every posted entry records a **ReferenceSnapshot** containing version identifiers for:
- Chart of Accounts (coa_version)
- Dimension Schema (dimension_schema_version)
- FX Rates (currency_registry_version)
- Policies, Rounding Rules, Tax Rules

**Why?** This ensures:
- Deterministic replay (R21)
- Audit trail of "what rules were in effect"
- Safe schema evolution

### 5. Defense in Depth for Immutability

Immutability is enforced at **three levels**:

1. **ORM Level** (`db/immutability.py`): SQLAlchemy `before_update`, `before_delete`, and `before_flush` listeners.

2. **Database Level** (`db/triggers.py`): PostgreSQL triggers catch raw SQL and direct database access.

3. **Session Level**: `before_flush` events catch deletions before the flush plan is finalized.

### 6. Hash Chain Audit Trail

Every AuditEvent and EconomicEvent contains:
- `payload_hash`: Hash of the event data
- `prev_hash`: Hash of the previous record
- `hash`: Hash of (payload_hash + prev_hash)

This creates a blockchain-like structure where any tampering breaks the chain.

### 7. Pure Calculation Engines

All calculation logic in `finance_engines/` is **pure functions**:
- No database access
- No session injection
- Deterministic outputs
- Easily testable

---

## Exception Hierarchy

All exceptions inherit from `FinanceKernelError` and include:
- `code`: Machine-readable error code
- `message`: Human-readable description

```
FinanceKernelError
├── ValidationError              # Input validation failures
├── PostingError                 # Posting-specific errors
│   ├── PeriodClosedError
│   ├── AdjustmentsNotAllowedError
│   ├── UnbalancedEntryError
│   └── ...
├── ImmutabilityError            # Attempted modification of immutable data
│   ├── ImmutabilityViolationError
│   └── AccountReferencedError   # Account has posted references
├── AuditChainBrokenError        # Hash chain validation failure
├── InterpretationError          # Interpretation layer errors
│   ├── ProfileNotFoundError
│   ├── MultipleProfilesMatchError
│   ├── RoleResolutionError
│   └── GuardRejectionError
└── ...
```

---

## Getting Started

### Interpretation Flow (Recommended)

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

    match result.outcome.status:
        case OutcomeStatus.POSTED:
            print(f"Posted entries: {result.journal_result.entry_ids}")
        case OutcomeStatus.BLOCKED:
            print(f"Blocked: {result.outcome.reason_code}")
        case OutcomeStatus.REJECTED:
            print(f"Rejected: {result.outcome.reason_detail}")
```

### Legacy Posting Flow

```python
from finance_kernel.services.posting_orchestrator import PostingOrchestrator
from finance_kernel.db.engine import get_session

with get_session() as session:
    orchestrator = PostingOrchestrator(session)

    result = orchestrator.post_event(
        event_id=uuid4(),
        event_type="sales.invoice.created",
        occurred_at=datetime.now(UTC),
        effective_date=date.today(),
        actor_id=user_id,
        producer="sales-module",
        payload={"invoice_id": "INV-001", "amount": "1000.00", "currency": "USD"},
    )

    if result.is_success:
        print(f"Posted: {result.journal_entry_id}")
```

### Creating an Economic Profile

```python
from finance_kernel.domain.economic_profile import (
    EconomicProfile, ProfileTrigger, ProfileMeaning,
    LedgerEffect, GuardCondition, GuardType
)

profile = EconomicProfile(
    name="ap.invoice.standard",
    version=1,
    trigger=ProfileTrigger(
        event_type="ap.invoice.received",
        conditions={"invoice_type": "standard"},
    ),
    meaning=ProfileMeaning(
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

ProfileRegistry.register(profile)
```

---

## Future Engines Roadmap

### Completed Primitives

| Primitive | Status | Description |
|-----------|--------|-------------|
| **EconomicLink** | Done | First-class pointer connecting artifacts (Receipt→PO, Payment→Invoice). Includes `LinkGraphService` with cycle detection, graph traversal, and unconsumed value calculation. |
| **Finance Engines** | Done | Pure function engines: Variance, Allocation, Matching, Aging, Tax, Subledger. |
| **Interpretation Layer** | Done | EconomicProfile, MeaningBuilder, AccountingIntent, JournalWriter, OutcomeRecorder with L1-L5/P1/P11/P15 invariants. |

### Build Next (High Priority)

| Engine | Why Build | Notes |
|--------|-----------|-------|
| **ReconciliationManager** | Direct consumer of EconomicLink. Tracks Open/Matched state for invoices and payments. | Natural next step after EconomicLink |
| **ValuationLayer** | Tracks specific lots of value (e.g., 10 units at $5) for FIFO/LIFO costing. | Orthogonal to EconomicLink; needed for inventory |

### Defer or Extend Existing

| Engine | Assessment | Recommendation |
|--------|-----------|----------------|
| **AccumulatorEngine** | Gathers costs over time for WIP and Accruals | Defer until concrete WIP use case emerges |
| **CorrectionEngine** | Recursive reversals via EconomicLink graph | Start with manual reversals; automate later |
| **GaplessSequenceEngine** | Strictly monotonic document numbering | Extend SequenceService if needed |
| **DimensionConstraintEngine** | Cross-dimension rules | Can be validation rules in EventValidator |
| **EffectivityResolver** | Policy/ExchangeRate lookup by timestamp | Already in PolicyRegistry; extend if needed |

---

## Invariant Test Coverage

All invariants have comprehensive test coverage:

| Category | Directory | Purpose |
|----------|-----------|---------|
| **Unit** | `tests/unit/`, `tests/domain/` | Local correctness |
| **Concurrency** | `tests/concurrency/` | Race safety |
| **Crash** | `tests/crash/` | Durability |
| **Replay** | `tests/replay/` | Determinism |
| **Adversarial** | `tests/adversarial/` | Attack resistance |
| **Audit** | `tests/audit/` | Audit trail integrity |
| **Architecture** | `tests/architecture/` | Architectural compliance |
| **Fuzzing** | `tests/fuzzing/` | Fuzz testing |
| **Database Security** | `tests/database_security/` | PostgreSQL triggers, isolation |
| **Security** | `tests/security/` | SQL injection prevention |

---

## See Also

- `db/README.md` - Database layer details
- `../docs/MODULE_DEVELOPMENT_GUIDE.md` - How to build ERP modules
- `../docs/ap_module_design.md` - AP module reference design
- `../docs/reusable_modules.md` - Reusable engine patterns
