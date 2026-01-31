# Services Layer - Stateful Operations

## Overview

The services layer contains **stateful operations** that interact with the database, manage transactions, and coordinate workflows. Unlike the domain layer, services:

- Perform I/O (database reads/writes)
- Manage transaction boundaries
- Maintain audit trails
- Enforce business rules that require state

---

## Core Services

### PostingOrchestrator (`posting_orchestrator.py`)

The **main entry point** for posting events to the journal. It coordinates the complete posting workflow.

**R7 Compliance**: The orchestrator owns its transaction boundary. Each `post_event()` call is atomic - it either commits completely or rolls back entirely.

```
post_event()
    │
    ├─► Ingestor.ingest()         # Validate and store event
    │
    ├─► PeriodService.validate()   # Check period is open
    │
    ├─► ReferenceDataLoader.load() # Load accounts, rates, etc.
    │
    ├─► Bookkeeper.propose()       # Pure transformation (domain layer)
    │
    ├─► Ledger.persist()           # Store journal entry
    │
    └─► COMMIT or ROLLBACK
```

**Usage:**
```python
from finance_services.posting_orchestrator import PostingOrchestrator

orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

result = orchestrator.post_event(
    event_id=uuid4(),
    event_type="sales.invoice.created",
    occurred_at=clock.now(),
    effective_date=date.today(),
    actor_id=user_id,
    producer="sales-module",
    payload={"invoice_id": "INV-001", "amount": "1000.00"},
)

if result.is_success:
    print(f"Journal Entry: {result.journal_entry_id}")
    print(f"Sequence: {result.seq}")
else:
    print(f"Failed: {result.status} - {result.message}")
```

**Return statuses:**
| Status | Meaning |
|--------|---------|
| `POSTED` | New entry created successfully |
| `ALREADY_POSTED` | Idempotent success - entry already exists |
| `VALIDATION_FAILED` | Business rule validation failed |
| `PERIOD_CLOSED` | Effective date is in a closed period |
| `ADJUSTMENTS_NOT_ALLOWED` | R13 - period doesn't allow adjustments |
| `INGESTION_FAILED` | Event validation/ingestion failed |

---

### IngestorService (`ingestor_service.py`)

Handles **event ingestion** at the system boundary. This is where external events enter the finance kernel.

**Key responsibilities:**
1. Validate event envelope structure
2. Compute and verify payload_hash
3. Detect protocol violations (same event_id, different payload)
4. Store the Event record
5. Create INGESTED audit event

**Protocol Violation Detection:**
```python
# First submission
ingestor.ingest(event_id="A", payload_hash="HASH_1")  # OK

# Second submission with SAME event_id but DIFFERENT payload
ingestor.ingest(event_id="A", payload_hash="HASH_2")  # REJECTED!
# This is a protocol violation - events are immutable
```

**Why is this important?**
- Prevents replay attacks with modified data
- Ensures event immutability after first ingestion
- Creates audit trail for violation attempts

---

### AuditorService (`auditor_service.py`)

Maintains the **cryptographic audit trail** (R11 compliance).

**Hash chain structure:**
```
AuditEvent[1]           AuditEvent[2]           AuditEvent[3]
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│ seq: 1      │         │ seq: 2      │         │ seq: 3      │
│ payload_hash│───┐     │ payload_hash│───┐     │ payload_hash│
│ prev_hash:∅ │   │     │ prev_hash ──│───│     │ prev_hash ──│
│ hash ───────│───┼────►│ hash ───────│───┼────►│ hash        │
└─────────────┘   │     └─────────────┘   │     └─────────────┘
                  │                       │
                  └───────────────────────┘
```

Each `hash` is computed from `payload_hash + prev_hash`, creating an unbreakable chain.

**Chain validation:**
```python
auditor.validate_chain()  # Returns True or raises AuditChainBrokenError
```

If any record is tampered with, the chain breaks and validation fails.

**Audit event types:**
| Action | When Created |
|--------|--------------|
| `INGESTED` | Event accepted into the system |
| `REJECTED` | Event rejected (validation failure) |
| `POSTED` | Journal entry created |
| `REVERSED` | Journal entry reversed |
| `PERIOD_CLOSED` | Fiscal period closed |
| `PROTOCOL_VIOLATION` | Same event_id with different payload detected |

---

### PeriodService (`period_service.py`)

Manages **fiscal period lifecycle** (R12, R13 compliance).

**Period states:**
```
OPEN  ──────►  CLOSED
       close()
       (one-way, irreversible)
```

**Key rules:**
1. **R12**: No posting to closed periods
2. **R13**: Adjustments only when `allows_adjustments=True`
3. Periods cannot be reopened once closed
4. Period date ranges cannot overlap

**Usage:**
```python
# Create a period
period_service.create_period(
    period_code="2024-Q1",
    name="Q1 2024",
    start_date=date(2024, 1, 1),
    end_date=date(2024, 3, 31),
    allows_adjustments=True,
    actor_id=admin_id,
)

# Check if posting is allowed
period_service.validate_effective_date(date(2024, 2, 15))  # OK
period_service.validate_effective_date(date(2023, 12, 15))  # PeriodClosedError

# Check adjustment policy (R13)
period_service.validate_adjustment_allowed(
    effective_date=date(2024, 2, 15),
    is_adjustment=True,
)  # OK if allows_adjustments=True

# Close a period (irreversible)
period_service.close_period("2024-Q1", actor_id=admin_id)
```

---

### SequenceService (`sequence_service.py`)

Allocates **globally unique, monotonic sequence numbers**.

**Key invariants:**
- Sequences are strictly increasing (never reused)
- Sequences are unique across all entries
- Rollbacks do not reuse sequence numbers
- Concurrent allocation never duplicates

**Why sequences matter:**
- Establishes global ordering of all financial events
- Enables deterministic replay: process entries in seq order
- Allows efficient incremental queries: "all entries since seq X"

```python
seq_service = SequenceService(session)
next_seq = seq_service.next_sequence("journal_entry")  # Returns 42
next_seq = seq_service.next_sequence("journal_entry")  # Returns 43

# Even if a transaction rolls back, the sequence is not reused
# This prevents gaps from being filled later (audit requirement)
```

---

### ReferenceDataLoader (`reference_data_loader.py`)

Loads **read-only reference data** for the Bookkeeper.

Returns a frozen `ReferenceData` DTO containing:
- Active accounts (by code)
- Active exchange rates
- Active dimensions and their values

```python
loader = ReferenceDataLoader(session)
ref_data = loader.load(required_dimensions={"cost_center", "project"})

# ref_data is immutable - safe to pass to pure domain layer
```

**Why load before transformation?**
- Ensures the Bookkeeper sees a consistent snapshot
- Prevents I/O during pure transformation
- Makes reference data explicit (no hidden queries)

---

### LinkGraphService (`link_graph_service.py`)

Manages the **economic link graph** — artifact relationships (PO → Receipt → Invoice → Payment) with graph traversal, cycle detection, and unconsumed value calculation.

**Key responsibilities:**
1. Establish links between artifacts
2. Walk graph paths (children/parents, filtered by link type)
3. Detect cycles (L3 invariant)
4. Calculate unconsumed value (e.g., open PO balance)
5. Check reversal status

```python
from finance_kernel.services.link_graph_service import LinkGraphService

link_service = LinkGraphService(session)
link_service.establish_link(economic_link)
paths = link_service.walk_path(query)
unconsumed = link_service.get_unconsumed_value(parent_ref, original_amount, ...)
is_reversed = link_service.is_reversed(artifact_ref)
```

---

### ContractService (`contract_service.py`)

Manages **contract lifecycle** — creation, modification, and billing for government and commercial contracts.

---

### PartyService (`party_service.py`)

Manages **party records** (customers, suppliers, employees) including creation, freezing, credit limits, and blocked-party enforcement.

---

### InterpretationCoordinator (`interpretation_coordinator.py`)

Coordinates the **profile-based interpretation pipeline**. Takes an economic meaning result and accounting intent, writes journal entries, and records the outcome — all within a single transaction (L5 atomicity).

```python
from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator

coordinator = InterpretationCoordinator(session, journal_writer, outcome_recorder)
result = coordinator.interpret_and_post(meaning_result, accounting_intent, actor_id)
```

---

### JournalWriter (`journal_writer.py`)

Writes journal entries to the database, used by the `InterpretationCoordinator` for profile-based posting.

---

### OutcomeRecorder (`outcome_recorder.py`)

Records `InterpretationOutcome` records for audit trail and debugging — tracks whether each event interpretation succeeded, failed, or was rejected by guards.

---

### ReferenceSnapshotService (`reference_snapshot_service.py`)

Captures and retrieves **reference data snapshots** at posting time. Supports replay determinism (R21) by preserving the exact reference data used for each posting.

---

### ModulePostingService (`module_posting_service.py`)

The **posting orchestrator** for profile-based module posting. `ModulePostingService` handles events that flow through the policy/profile interpretation pipeline.

**Key responsibilities:**
1. Receive an economic event from a finance module
2. Ingest the event via `IngestorService`
3. Validate fiscal period via `PeriodService`
4. Select the matching accounting policy via `PolicySelector`
5. Build economic meaning via `MeaningBuilder`
6. Delegate to `InterpretationCoordinator` for journal writing and outcome recording

All steps execute within a single transaction boundary (L5 atomicity).

```python
from finance_kernel.services.module_posting_service import ModulePostingService

service = ModulePostingService(session, clock)
result = service.post_module_event(economic_event, actor_id)
```

---

## Read-Only Query Services (`selectors/`)

Read-only query services are separated into the `selectors/` package to enforce the command/query boundary. These services perform database reads but never mutate state.

| File | Purpose |
|------|---------|
| `ledger_selector.py` | Query journal entries, balances, and ledger data |
| `journal_selector.py` | Query journal lines, entry history, and audit trails |

```python
from finance_kernel.selectors.ledger_selector import LedgerSelector

selector = LedgerSelector(session)
balance = selector.get_account_balance(account_code, as_of_date)
```

---

## Transaction Management

### R7: Each Service Owns Its Boundary

The PostingOrchestrator manages the transaction for the entire posting workflow:

```python
def post_event(self, ...):
    try:
        result = self._do_post_event(...)

        if self._auto_commit and result.is_success:
            self._session.commit()  # Commit only on success

        return result

    except Exception:
        if self._auto_commit:
            self._session.rollback()  # Rollback on any failure
        raise
```

**Testing mode:**
Pass `auto_commit=False` to let tests manage transactions:
```python
orchestrator = PostingOrchestrator(session, clock, auto_commit=False)
# Test can now inspect uncommitted state and manually commit/rollback
```

---

## Error Handling

Services raise specific exceptions for different failure modes:

```python
from finance_kernel.exceptions import (
    PeriodClosedError,
    AdjustmentsNotAllowedError,
    UnbalancedEntryError,
    IdempotencyKeyConflictError,
)

try:
    result = orchestrator.post_event(...)
except PeriodClosedError as e:
    # Effective date is in a closed period
    log.warning(f"Period closed: {e.period_code}")
except AdjustmentsNotAllowedError as e:
    # R13 violation - period doesn't allow adjustments
    log.warning(f"Adjustments not allowed: {e}")
```

---

## Service Dependencies

```
PostingOrchestrator (DI container / service factory)
    ├── AuditorService
    ├── PeriodService
    ├── IngestorService
    ├── ReferenceSnapshotService
    ├── JournalWriter
    ├── OutcomeRecorder
    ├── InterpretationCoordinator
    ├── EngineDispatcher
    └── Subledger services (AP, AR, Bank, Inventory, Contract)

ModulePostingService (posting orchestrator)
    ├── IngestorService
    │       └── AuditorService
    ├── PeriodService
    ├── PolicySelector (policy lookup with where-clause dispatch)
    ├── MeaningBuilder (policy → economic meaning)
    └── InterpretationCoordinator
            ├── JournalWriter
            ├── OutcomeRecorder
            └── ReferenceSnapshotService

LinkGraphService (standalone - session only)

ContractService (standalone - session only)

PartyService (standalone - session only)
```

All services receive their dependencies through constructor injection.
`PostingOrchestrator` creates every kernel service once and exposes them as attributes.

---

## Extending the Services Layer

### Adding a New Service

1. Create the service class with explicit dependencies:
```python
class NewService:
    def __init__(self, session: Session, clock: Clock, auditor: AuditorService):
        self._session = session
        self._clock = clock
        self._auditor = auditor

    def do_something(self, ...):
        # Business logic with I/O
        ...
        # Create audit event
        self._auditor.record_event(...)
```

2. Inject into PostingOrchestrator if needed.

3. Write integration tests with real database.

### Testing Services

Services require integration tests with a real database:

```python
def test_posting_to_closed_period_rejected(
    session,
    posting_orchestrator,
    period_service,
    test_actor_id,
):
    # Arrange - create and close a period
    period_service.create_period(...)
    period_service.close_period(...)

    # Act - try to post to closed period
    result = posting_orchestrator.post_event(
        effective_date=date_in_closed_period,
        ...
    )

    # Assert
    assert result.status == PostingStatus.PERIOD_CLOSED
```
