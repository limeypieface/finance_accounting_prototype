# Finance Kernel - Architecture Documentation

## Overview

The Finance Kernel is an **append-only, event-sourced accounting system** designed to serve as the system of record for financial transactions in an ERP environment. It guarantees correctness under retries, concurrency, crashes, replays, and adversarial input.

### Core Philosophy

**"JournalLines are the only financial truth. Everything else is derived."**

This kernel treats financial data as immutable facts. Once a transaction is posted, it cannot be modified or deleted - only reversed through new transactions. This approach provides:

- **Auditability**: Complete history of all financial events
- **Recoverability**: Ability to rebuild any derived state from the journal
- **Integrity**: Cryptographic hash chain prevents undetected tampering

---

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                        External Systems                          │
│                    (Inventory, Purchasing, AR/AP)                │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼ Events
┌─────────────────────────────────────────────────────────────────┐
│                      SERVICES LAYER                              │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              PostingOrchestrator (R7)                    │    │
│  │  Coordinates: Ingestor → Bookkeeper → Ledger → Auditor  │    │
│  └─────────────────────────────────────────────────────────┘    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────────┐    │
│  │ Ingestor │ │  Ledger  │ │ Auditor  │ │  PeriodService  │    │
│  └──────────┘ └──────────┘ └──────────┘ └─────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼ DTOs (Immutable)
┌─────────────────────────────────────────────────────────────────┐
│                       DOMAIN LAYER (Pure)                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    Bookkeeper                             │   │
│  │  EventEnvelope + ReferenceData → ProposedJournalEntry    │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────┐ ┌──────────────┐ ┌───────────────────────┐   │
│  │  Strategies  │ │    Values    │ │    DTOs (frozen)      │   │
│  │  (pluggable) │ │ (Money, etc) │ │ (EventEnvelope, etc)  │   │
│  └──────────────┘ └──────────────┘ └───────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼ Models
┌─────────────────────────────────────────────────────────────────┐
│                      DATABASE LAYER                              │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │   Models: JournalEntry, JournalLine, AuditEvent, etc.    │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌────────────────────┐  ┌──────────────────────────────────┐   │
│  │  ORM Immutability  │  │  PostgreSQL Triggers (Defense)   │   │
│  │    (listeners)     │  │       (db/triggers.py)           │   │
│  └────────────────────┘  └──────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
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
| **R6** | Replay safety | Ledger state reproducible from journal + reference data; projections are disposable | No stored balances, computed trial balance |
| **R7** | Transaction boundaries | Each service owns its transaction boundary | Orchestrator pattern, explicit commits |
| **R8** | Idempotency locking | Database uniqueness constraints + row-level locks (SELECT FOR UPDATE) | UniqueConstraint + with_for_update() |
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
| **R16** | ISO 4217 enforcement | Currency codes validated at ingestion/domain boundary; invalid codes rejected | CurrencyRegistry.validate() |
| **R17** | Precision-derived tolerance | Rounding tolerance derived from currency precision; fixed tolerances FORBIDDEN | CurrencyInfo.rounding_tolerance property |
| **R18** | Deterministic errors | All domain errors use typed exceptions with machine-readable codes; no string-matching | FinanceKernelError.code attribute |
| **R19** | No silent correction | Financial inconsistencies must fail or have traceable rounding/adjustment line | Explicit is_rounding flag, fail on large imbalance |

### Meta Invariants

| Rule | Name | Description | Enforcement |
|------|------|-------------|-------------|
| **R20** | Test class mapping | Every invariant must have: unit tests, concurrency tests, crash tests, replay tests | test_r20_test_class_mapping.py |

### Invariant Test Coverage

All invariants have comprehensive test coverage across four categories:

| Category | Directory | Purpose |
|----------|-----------|---------|
| **Unit** | `tests/unit/`, `tests/domain/` | Local correctness |
| **Concurrency** | `tests/concurrency/` | Race safety |
| **Crash** | `tests/crash/` | Durability |
| **Replay** | `tests/replay/` | Determinism |

Additional test categories:
- `tests/adversarial/` - Attack resistance
- `tests/audit/` - Audit trail integrity
- `tests/architecture/` - Architectural compliance
- `tests/fuzzing/` - Fuzz testing

---

## Data Flow: Posting an Event

```
1. External System produces Event
   │
   ▼
2. PostingOrchestrator.post_event()
   │
   ├─► IngestorService.ingest()
   │   - Validate event envelope
   │   - Check payload_hash uniqueness
   │   - Store Event record
   │   - Create AuditEvent (INGESTED)
   │
   ├─► PeriodService.validate_adjustment_allowed()
   │   - Check effective_date has open period
   │   - Check allows_adjustments if is_adjustment=True
   │
   ├─► ReferenceDataLoader.load()
   │   - Load accounts, dimensions, exchange rates
   │   - Return frozen ReferenceData DTO
   │
   ├─► Bookkeeper.propose() [PURE - no I/O]
   │   - Select strategy by event_type
   │   - Compute LineSpecs
   │   - Validate balance
   │   - Add rounding if needed
   │   - Return ProposedJournalEntry
   │
   ├─► LedgerService.persist()
   │   - Check idempotency (existing entry?)
   │   - Allocate sequence number
   │   - Create JournalEntry + JournalLines
   │   - Create AuditEvent (POSTED)
   │
   └─► COMMIT or ROLLBACK
```

---

## Directory Structure

```
finance_kernel/
├── domain/           # Pure business logic (no I/O, no state)
│   ├── bookkeeper.py     # Transforms events → journal entries
│   ├── strategy.py       # Base class for posting strategies
│   ├── strategy_registry.py  # Strategy lookup by event_type
│   ├── dtos.py           # Immutable data transfer objects
│   ├── values.py         # Value objects (Money, etc.)
│   ├── currency.py       # ISO 4217 currency handling
│   └── clock.py          # Time abstraction for testability
│
├── services/         # Stateful services with I/O
│   ├── posting_orchestrator.py  # Main entry point
│   ├── ingestor_service.py      # Event ingestion
│   ├── ledger_service.py        # Journal persistence
│   ├── auditor_service.py       # Audit trail
│   ├── period_service.py        # Fiscal period management
│   ├── sequence_service.py      # Monotonic sequence allocation
│   └── reference_data_loader.py # Load reference data
│
├── models/           # SQLAlchemy ORM models
│   ├── journal.py        # JournalEntry, JournalLine
│   ├── event.py          # Event store
│   ├── audit_event.py    # Audit trail with hash chain
│   ├── account.py        # Chart of accounts
│   ├── fiscal_period.py  # Period management
│   ├── dimensions.py     # Dimension/DimensionValue
│   └── exchange_rate.py  # Currency exchange rates
│
├── db/               # Database infrastructure
│   ├── engine.py         # Connection management
│   ├── immutability.py   # ORM-level immutability listeners
│   ├── triggers.py       # PostgreSQL triggers (defense in depth)
│   └── types.py          # Custom SQLAlchemy types
│
├── selectors/        # Read-only query services
│   ├── ledger_selector.py    # Ledger queries, trial balance
│   └── journal_selector.py   # Journal entry queries
│
├── exceptions.py     # Hierarchical exception system
└── utils/            # Utilities (hashing, etc.)
```

---

## Key Design Decisions

### 1. Pure Domain Layer

The `domain/` package contains **zero I/O operations**. The Bookkeeper and strategies:
- Take immutable inputs (EventEnvelope, ReferenceData)
- Return immutable outputs (ProposedJournalEntry)
- Never access the database, network, or clock directly

**Why?** This makes the core accounting logic:
- **Testable**: Unit tests with no mocking required
- **Deterministic**: Same inputs always produce same outputs
- **Replayable**: Historical events can be replayed identically

### 2. Event Sourcing with Idempotency

Events are the source of truth. The `idempotency_key` (producer:event_type:event_id) ensures:
- Retries are safe (same result every time)
- Concurrent submissions deduplicate correctly
- No duplicate financial effects

### 3. Defense in Depth for Immutability

Immutability is enforced at **two levels**:

1. **ORM Level** (`db/immutability.py`): SQLAlchemy `before_update` and `before_delete` listeners catch modifications through Python code.

2. **Database Level** (`db/triggers.py`): PostgreSQL triggers catch raw SQL, bulk updates, and direct database access.

**Why both?** ORM listeners can be bypassed with raw SQL. Database triggers cannot be bypassed without removing them (which requires elevated privileges and leaves an audit trail).

### 4. Hash Chain Audit Trail

Every AuditEvent contains:
- `payload_hash`: Hash of the event data
- `prev_hash`: Hash of the previous AuditEvent
- `hash`: Hash of (payload_hash + prev_hash)

This creates a blockchain-like structure where any tampering breaks the chain and is immediately detectable.

### 5. Clock Injection

All time-dependent operations receive a `Clock` interface:
- `SystemClock`: Uses real time (production)
- `DeterministicClock`: Returns controlled time (testing)

**Why?** This ensures:
- Tests are deterministic and reproducible
- No hidden time dependencies in business logic
- Replay scenarios use original timestamps

---

## Exception Hierarchy

All exceptions inherit from `FinanceKernelError` and include:
- `code`: Machine-readable error code (e.g., `PERIOD_CLOSED`)
- `message`: Human-readable description

```
FinanceKernelError
├── ValidationError        # Input validation failures
├── PostingError           # Posting-specific errors
│   ├── PeriodClosedError
│   ├── AdjustmentsNotAllowedError
│   ├── UnbalancedEntryError
│   └── ...
├── ImmutabilityError      # Attempted modification of immutable data
├── AuditChainBrokenError  # Hash chain validation failure
└── ...
```

---

## Getting Started

### Posting an Event

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
    else:
        print(f"Failed: {result.status} - {result.message}")
```

### Creating a Posting Strategy

```python
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry

class InvoiceCreatedStrategy(BasePostingStrategy):
    @property
    def event_type(self) -> str:
        return "sales.invoice.created"

    @property
    def version(self) -> int:
        return 1

    def _compute_line_specs(self, event: EventEnvelope, ref: ReferenceData) -> tuple[LineSpec, ...]:
        amount = Money.of(Decimal(event.payload["amount"]), event.payload["currency"])
        return (
            LineSpec(account_code="1200", side=LineSide.DEBIT, money=amount),   # AR
            LineSpec(account_code="4000", side=LineSide.CREDIT, money=amount),  # Revenue
        )

# Register at application startup
StrategyRegistry.register(InvoiceCreatedStrategy())
```

---

## See Also

- `domain/README.md` - Domain layer details
- `services/README.md` - Services layer details
- `db/README.md` - Database layer details
- `../tests/README.md` - Testing philosophy
