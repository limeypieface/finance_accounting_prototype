# Domain Layer - Pure Business Logic

## Overview

The domain layer contains the **pure business logic** of the finance kernel. Everything in this package:

- Has **no I/O operations** (no database, no network, no file system)
- Has **no side effects** (no mutations, no global state)
- Is **deterministic** (same inputs always produce same outputs)
- Is **testable without mocking** (pure functions only)

This architectural choice ensures that the core accounting logic can be:
- **Replayed**: Historical events produce identical results
- **Audited**: Calculations are reproducible and verifiable
- **Tested**: Unit tests are fast, simple, and reliable

---

## Core Components

### Bookkeeper (`bookkeeper.py`)

The Bookkeeper is the **pure transformation engine**. It takes an event and reference data, and produces a proposed journal entry.

```
EventEnvelope + ReferenceData  →  Bookkeeper  →  ProposedJournalEntry
     (input)                        (pure)            (output)
```

**Key responsibilities:**
- Delegate to the appropriate strategy based on event_type
- Validate the proposed entry balances
- Add rounding lines if currency conversion produces remainders
- Return validation errors without throwing exceptions

**What it does NOT do:**
- Access the database
- Modify any state
- Read the current time

```python
# Example usage
bookkeeper = Bookkeeper()
result: BookkeeperResult = bookkeeper.propose(event_envelope, reference_data)

if result.is_valid:
    proposed_entry = result.proposed_entry
else:
    errors = result.validation.errors
```

---

### Strategies (`strategy.py`, `strategy_registry.py`)

Strategies define **how specific event types map to journal entries**. Each strategy is a pluggable, versioned transformation.

**Why versioning?**
- Historical events must replay with their original strategy version
- New strategy versions apply only to new events
- This ensures the ledger remains reconstructable

```python
class BasePostingStrategy(ABC):
    @property
    @abstractmethod
    def event_type(self) -> str:
        """The event type this strategy handles (e.g., 'sales.invoice.created')"""
        pass

    @property
    @abstractmethod
    def version(self) -> int:
        """Strategy version for replay compatibility"""
        pass

    @abstractmethod
    def _compute_line_specs(
        self, event: EventEnvelope, ref: ReferenceData
    ) -> tuple[LineSpec, ...]:
        """Compute the journal lines for this event (pure function)"""
        pass
```

**Strategy Registry:**
```python
# Register at application startup
StrategyRegistry.register(InvoiceCreatedStrategy())

# Lookup by event type
strategy = StrategyRegistry.get("sales.invoice.created")

# Lookup specific version (for replay)
strategy = StrategyRegistry.get("sales.invoice.created", version=1)
```

---

### Data Transfer Objects (`dtos.py`)

All DTOs are **frozen dataclasses** - immutable after construction. This prevents accidental mutation and ensures thread safety.

**Sindri compatibility**: DTOs include `from_model()` class methods for converting from ORM models:

```python
# Convert ORM model to DTO
entry_record = JournalEntryRecord.from_model(journal_entry_orm)
event_envelope = EventEnvelope.from_model(event_orm)
period_info = FiscalPeriodInfo.from_model(fiscal_period_orm)
```

#### EventEnvelope
The normalized representation of an incoming event:
```python
@dataclass(frozen=True)
class EventEnvelope:
    event_id: UUID
    event_type: str
    occurred_at: datetime
    effective_date: date
    actor_id: UUID
    producer: str
    payload: Mapping[str, Any]  # Read-only
    payload_hash: str
    schema_version: int
```

#### LineSpec
A specification for a single journal line:
```python
@dataclass(frozen=True)
class LineSpec:
    account_code: str
    side: LineSide  # DEBIT or CREDIT
    money: Money
    dimensions: Mapping[str, str] | None = None
    memo: str | None = None
```

#### ProposedJournalEntry
The output of the Bookkeeper - a complete journal entry ready for persistence:
```python
@dataclass(frozen=True)
class ProposedJournalEntry:
    event_envelope: EventEnvelope
    lines: tuple[LineSpec, ...]
    strategy_version: int

    def is_balanced(self) -> bool:
        """Check if debits equal credits for each currency"""
        ...

    def get_imbalance(self) -> dict[str, Decimal]:
        """Get the imbalance per currency (should be empty or within rounding tolerance)"""
        ...
```

#### ReferenceData
Read-only reference data loaded before transformation:
```python
@dataclass(frozen=True)
class ReferenceData:
    accounts: Mapping[str, AccountInfo]        # account_code → info
    exchange_rates: Mapping[str, ExchangeRate] # rate lookup
    active_dimensions: frozenset[str]          # active dimension codes
    active_dimension_values: Mapping[str, frozenset[str]]  # dimension → values

    def get_account(self, code: str) -> AccountInfo | None:
        ...

    def is_dimension_active(self, dimension_code: str) -> bool:
        ...
```

#### ValidationResult
Structured validation errors:
```python
@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[ValidationError, ...]

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0
```

---

### Value Objects (`values.py`)

Value objects represent domain concepts with specific validation and behavior.

#### Money
Represents a monetary amount with currency. **Never uses floats.**

```python
# Construction (validates currency and precision)
amount = Money.of(Decimal("100.00"), "USD")

# Operations
total = amount1 + amount2  # Must be same currency
doubled = amount * 2

# Invalid operations raise exceptions
mixed = usd_amount + eur_amount  # CurrencyMismatchError!
```

**Key invariants:**
- Amount is always `Decimal` with defined precision
- Currency is always ISO 4217 validated
- Same-currency operations only (no implicit conversion)

---

### Currency (`currency.py`)

ISO 4217 currency validation and information.

```python
# Validation
Currency.validate("USD")  # OK
Currency.validate("XXX")  # InvalidCurrencyError

# Currency info
info = Currency.get_info("USD")
info.code       # "USD"
info.precision  # 2 (decimal places)
info.name       # "US Dollar"
```

---

### Clock (`clock.py`)

Time abstraction for testability. The domain layer **never reads wall-clock time directly**.

```python
class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current timestamp"""
        ...

class SystemClock(Clock):
    """Uses real system time - for production"""
    def now(self) -> datetime:
        return datetime.now(UTC)

class DeterministicClock(Clock):
    """Returns controlled time - for testing"""
    def __init__(self, fixed_time: datetime):
        self._time = fixed_time

    def now(self) -> datetime:
        return self._time

    def advance(self, delta: timedelta) -> None:
        self._time += delta
```

**Why?**
- Tests are deterministic and reproducible
- Replay scenarios use original timestamps
- No hidden time dependencies in business logic

---

## Extended Components

The domain layer has grown beyond the core components above. The following subsystems provide economic interpretation, profile-driven posting, and schema governance.

### Accounting Policy (`accounting_policy.py`)

Declarative definitions that govern how events become journal entries. Each policy specifies a trigger (which events it matches), meaning (economic interpretation), ledger effects (debit/credit roles), guards (reject/block conditions), and engine bindings.

```python
from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    PolicyTrigger,
    PolicyMeaning,
    LedgerEffect,
    GuardCondition,
    GuardType,
)
```

### Policy Infrastructure

| File | Purpose |
|------|---------|
| `policy_compiler.py` | Compiles raw policy definitions into executable form |
| `policy_selector.py` | Selects exactly one policy for a given event (with where-clause dispatch) |
| `policy_authority.py` | Governs which policies are admissible (effective dates, precedence, controls) |
| `ledger_registry.py` | Maps account roles to actual COA codes per ledger |
| `meaning_builder.py` | Extracts economic meaning from events using policies |

### Accounting Intent (`accounting_intent.py`)

The intermediate representation between economic meaning and journal entries. A `MeaningBuilder` produces an `AccountingIntent`, which the `InterpretationCoordinator` (services layer) converts into actual journal entries.

### Policy Bridge (`policy_bridge.py`)

Connects `AccountingPolicy` definitions (from modules) to the kernel's `PolicySelector` and `MeaningBuilder`. Provides `register_rich_profile()` for registering policies with their line mappings, and `build_accounting_intent()` for multi-ledger intent construction.

### Economic Links (`economic_link.py`)

First-class artifact relationships forming a knowledge graph. Links connect documents (PO → Receipt → Invoice → Payment) with typed, immutable edges. Supports cycle detection, max-children constraints, and unconsumed value tracking.

```python
from finance_kernel.domain.economic_link import (
    ArtifactRef, ArtifactType, EconomicLink, LinkType, LinkQuery,
)
```

### Event Validation (`event_validator.py`)

Validates incoming events against registered schemas before they enter the posting pipeline.

### Reference Snapshots (`reference_snapshot.py`)

Captures a frozen snapshot of reference data (accounts, rates, dimensions) at posting time. Ensures replay determinism by allowing historical events to be re-processed against their original reference data.

### Valuation (`valuation.py`)

Domain-level valuation primitives used by the `ValuationLayer` engine for cost lot management.

### Subledger Control (`subledger_control.py`)

Defines control account relationships for subledger reconciliation (e.g., AP subledger must reconcile to the AP control account in the GL).

### Schemas (`schemas/`)

Event payload schema definitions organized by business domain:

```
schemas/
├── __init__.py
├── base.py                    # Base schema classes and validation
├── registry.py                # Schema registry (lookup by event type + version)
└── definitions/
    ├── __init__.py
    ├── ap.py                  # Accounts Payable event schemas
    ├── ar.py                  # Accounts Receivable event schemas
    ├── asset.py               # Fixed Asset event schemas
    ├── bank.py                # Banking/Cash event schemas
    ├── contract.py            # Contract billing event schemas
    ├── dcaa.py                # DCAA compliance event schemas
    ├── deferred.py            # Deferred revenue/expense schemas
    ├── fx.py                  # Foreign exchange event schemas
    ├── generic.py             # Generic/cross-cutting event schemas
    ├── inventory.py           # Inventory event schemas
    └── payroll.py             # Payroll event schemas
```

### Policy Definitions (in `finance_modules/`)

Accounting policy definitions now live in their respective modules (not in the kernel):

```
finance_modules/
├── ap/profiles.py             # AP event → journal entry policies
├── ar/profiles.py             # AR event → journal entry policies
├── assets/profiles.py         # Fixed asset event policies
├── cash/profiles.py           # Cash/banking event policies
├── contracts/profiles.py      # Contract billing + DCAA policies
├── expense/profiles.py        # Expense event policies
├── gl/profiles.py             # GL + deferred + FX policies
├── inventory/profiles.py      # Inventory event policies
├── payroll/profiles.py        # Payroll event policies
├── procurement/profiles.py    # Procurement event policies
├── tax/profiles.py            # Tax event policies
└── wip/profiles.py            # WIP event policies
```

Each module's `profiles.py` exports a `register()` function that registers all
policies via `PolicyBridge.register_rich_profile()`. All policies are also
declared in the centralized YAML configuration (`finance_config/sets/`), which
is the source of truth at runtime.

### Strategies (`strategies/`)

Posting strategies beyond the base framework:

```
strategies/
├── __init__.py
└── generic_strategy.py        # Generic posting strategy implementation
```

---

## Design Principles

### 1. Immutability by Default

All data structures are frozen. If you need to "modify" something, create a new instance:

```python
# Wrong - will raise FrozenInstanceError
entry.status = "posted"

# Right - create new instance (if the DTO supports it)
new_entry = dataclasses.replace(entry, status="posted")
```

### 2. No Exceptions for Validation

Validation failures return structured results, not exceptions:

```python
# Good - returns ValidationResult
result = bookkeeper.propose(event, ref_data)
if not result.is_valid:
    for error in result.validation.errors:
        log.warning(f"Validation error: {error.code} - {error.message}")

# Avoid - throwing exceptions for expected validation failures
```

### 3. Explicit Dependencies

All dependencies are passed as arguments, never imported from global state:

```python
# Good - explicit dependencies
def propose(event: EventEnvelope, ref_data: ReferenceData) -> BookkeeperResult:
    ...

# Avoid - hidden dependencies
def propose(event: EventEnvelope) -> BookkeeperResult:
    ref_data = load_from_database()  # Hidden I/O!
    ...
```

### 4. Type Safety

All public APIs have full type annotations. Use `mypy` with strict mode to catch errors at development time.

---

## Testing the Domain Layer

Because the domain layer is pure, testing is straightforward:

```python
def test_invoice_strategy_produces_balanced_entry():
    # Arrange - create inputs directly (no database, no mocking)
    event = EventEnvelope(
        event_id=uuid4(),
        event_type="sales.invoice.created",
        occurred_at=datetime(2024, 1, 15, 12, 0, 0),
        effective_date=date(2024, 1, 15),
        actor_id=uuid4(),
        producer="test",
        payload={"amount": "1000.00", "currency": "USD"},
        payload_hash="abc123",
        schema_version=1,
    )

    ref_data = ReferenceData(
        accounts={"1200": ar_account, "4000": revenue_account},
        exchange_rates={},
        active_dimensions=frozenset(),
        active_dimension_values={},
    )

    # Act - call pure function
    bookkeeper = Bookkeeper()
    result = bookkeeper.propose(event, ref_data)

    # Assert - verify output
    assert result.is_valid
    assert result.proposed_entry.is_balanced()
    assert len(result.proposed_entry.lines) == 2
```

---

## Extending the Domain Layer

### Adding a New Strategy

1. Create a new strategy class:
```python
class NewEventStrategy(BasePostingStrategy):
    @property
    def event_type(self) -> str:
        return "module.new_event"

    @property
    def version(self) -> int:
        return 1

    def _compute_line_specs(self, event, ref) -> tuple[LineSpec, ...]:
        # Pure transformation logic
        ...
```

2. Register at application startup:
```python
StrategyRegistry.register(NewEventStrategy())
```

3. Write tests:
```python
def test_new_event_strategy():
    strategy = NewEventStrategy()
    lines = strategy._compute_line_specs(event, ref_data)
    assert sum(l.money.amount for l in lines if l.side == LineSide.DEBIT) == \
           sum(l.money.amount for l in lines if l.side == LineSide.CREDIT)
```

### Adding a New Value Object

1. Create a frozen dataclass with validation:
```python
@dataclass(frozen=True)
class NewValue:
    field1: str
    field2: Decimal

    def __post_init__(self):
        if self.field2 < 0:
            raise ValueError("field2 must be non-negative")
```

2. Add to the domain's `__init__.py` exports.

---

## Common Pitfalls

### 1. Importing I/O in Domain Code

```python
# WRONG - don't do this in domain layer
from finance_kernel.db.engine import get_session

# RIGHT - receive data as arguments
def compute(event: EventEnvelope, ref_data: ReferenceData) -> Result:
    ...
```

### 2. Using datetime.now() Directly

```python
# WRONG - non-deterministic
posted_at = datetime.now()

# RIGHT - use injected clock (in services layer)
posted_at = clock.now()
```

### 3. Mutating Inputs

```python
# WRONG - mutates input
def process(data: dict):
    data["processed"] = True

# RIGHT - return new data
def process(data: Mapping) -> dict:
    return {**data, "processed": True}
```
