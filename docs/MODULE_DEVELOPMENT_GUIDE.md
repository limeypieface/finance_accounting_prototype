# Module Development Guide

This guide explains how to build new modules (AP, AR, Inventory, WIP) on top of `finance_kernel`. Following these patterns ensures consistency, prevents code duplication, and maintains the invariants required for audit compliance.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Your Module (AP, AR, Inventory, etc.)         │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │  Event Schemas  │  │   Strategies    │  │    Services     │  │
│  │  (your events)  │  │ (or Profiles)   │  │  (your logic)   │  │
│  └────────┬────────┘  └────────┬────────┘  └───────┬─────────┘  │
└───────────┼────────────────────┼───────────────────┼────────────┘
            │                    │                   │
            ▼                    ▼                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                         finance_engines                          │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────────┐  │
│  │ Variance   │ │ Matching   │ │   Aging    │ │  Allocation  │  │
│  │ Calculator │ │  Engine    │ │ Calculator │ │   Engine     │  │
│  └────────────┘ └────────────┘ └────────────┘ └──────────────┘  │
│  ┌────────────┐ ┌────────────┐                                  │
│  │ Subledger  │ │    Tax     │   (Pure functions, no I/O)       │
│  │  Service   │ │ Calculator │                                  │
│  └────────────┘ └────────────┘                                  │
└─────────────────────────────────┬───────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                         finance_kernel                           │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────┐  │
│  │ Value Objects│  │   Economic    │  │ PostingOrchestrator  │  │
│  │ Money, etc.  │  │   Profiles    │  │ InterpretationCoord. │  │
│  └──────────────┘  └───────────────┘  └──────────────────────┘  │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────┐  │
│  │  Bookkeeper  │  │ MeaningBuilder│  │  JournalWriter       │  │
│  │  (pure)      │  │    (pure)     │  │  OutcomeRecorder     │  │
│  └──────────────┘  └───────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**Your module provides**: Event schemas, posting strategies OR profiles, business logic
**finance_engines provides**: Shared calculation engines (variance, matching, aging, etc.)
**finance_kernel provides**: Value objects, validation, posting, audit trail, profiles

---

## ⚠️ Engine Readiness Status

**DO NOT start building business modules (AP, AR, Inventory, WIP, T&E, Cash) until the required engines are complete.** Building modules without these engines leads to duplicated logic, inconsistent behavior, and significant rework.

### Engine Status

| Engine | Status | Location | What It Provides |
|--------|--------|----------|------------------|
| **EconomicLink** | ✅ Complete | `finance_kernel.domain.economic_link` | First-class artifact relationships (PO→Receipt→Invoice). Knowledge graph replacing brittle foreign keys. |
| **LinkGraphService** | ✅ Complete | `finance_kernel.services.link_graph_service` | Graph traversal, cycle detection (L3), unconsumed value calculation, reversal detection. |
| **ReconciliationManager** | 🔴 Not Started | TBD | Open/Matched state tracking, partial payment application, 3-way match state machine. |
| **ValuationLayer** | 🔴 Not Started | TBD | FIFO/LIFO/Weighted Average costing, cost lot tracking, layer consumption. |
| **CorrectionEngine** | 🔴 Not Started | TBD | Recursive reversals using EconomicLink graph, cascade unwind logic. |
| **AccumulatorEngine** | 🔴 Not Started | TBD | WIP cost accumulation, burden application, job relief calculation. |

### Module Dependencies

| Module | Required Engines | Can Start? |
|--------|------------------|------------|
| **AP (Accounts Payable)** | EconomicLink ✅, ReconciliationManager 🔴, CorrectionEngine 🔴 | ❌ No |
| **AR (Accounts Receivable)** | EconomicLink ✅, ReconciliationManager 🔴, CorrectionEngine 🔴 | ❌ No |
| **Inventory** | EconomicLink ✅, ValuationLayer 🔴, CorrectionEngine 🔴 | ❌ No |
| **WIP** | EconomicLink ✅, ValuationLayer 🔴, AccumulatorEngine 🔴 | ❌ No |
| **T&E (Travel & Expense)** | EconomicLink ✅, ReconciliationManager 🔴 | ❌ No |
| **Cash** | EconomicLink ✅, ReconciliationManager 🔴 | ❌ No |

### Why Wait?

Building modules without these engines means:

1. **Duplicated Logic** - Each module builds its own matching/reconciliation/costing code
2. **Inconsistent Behavior** - AP matching works differently than AR matching
3. **Rework** - When engines are built, modules need refactoring to use them
4. **Bug Multiplication** - Same bug in 6 places instead of 1
5. **Audit Complexity** - Auditors learn 6 different patterns instead of 1

**Estimated effort savings with engines: 30-40% per module** (see `finance_kernel/README.md` for detailed analysis).

### Build Order

1. ✅ **EconomicLink** - Foundation for all relationship tracking (DONE)
2. 🎯 **ReconciliationManager** - Unlocks AP, AR, T&E, Cash
3. 🎯 **ValuationLayer** - Unlocks Inventory, WIP
4. ⏳ **CorrectionEngine** - Unlocks safe reversals across all modules
5. ⏳ **AccumulatorEngine** - Only needed for WIP

---

## Required Imports

Every module should import primitives from `finance_kernel`, never recreate them:

```python
# Value Objects (R4 Compliance - ALWAYS use these for financial data)
from finance_kernel.domain.values import Currency, Money, Quantity, ExchangeRate

# DTOs (immutable data structures)
from finance_kernel.domain.dtos import (
    EventEnvelope,
    LineSpec,
    LineSide,
    ReferenceData,
    ValidationError,
    ValidationResult,
)

# Strategy Framework
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry

# Exceptions (extend these, don't create parallel hierarchies)
from finance_kernel.exceptions import FinanceKernelError
```

---

## Core Primitives Reference

### Money

**Always use `Money` for monetary amounts. Never use raw `Decimal` or `float`.**

```python
from decimal import Decimal
from finance_kernel.domain.values import Money, Currency

# Creating Money
amount = Money.of(Decimal("100.00"), "USD")
amount = Money.of("100.00", Currency("USD"))  # Also accepts strings
zero = Money.zero("USD")

# Operations (all return new Money - immutable)
total = amount1 + amount2      # Must be same currency
half = amount * Decimal("0.5")
rounded = amount.round()

# Properties
amount.is_zero
amount.is_positive
amount.is_negative
amount.currency  # Returns Currency object
amount.amount    # Returns Decimal
```

### Currency

```python
from finance_kernel.domain.values import Currency

usd = Currency("USD")  # Validates ISO 4217 on construction
usd.decimal_places     # 2 for USD
usd.code               # "USD"
```

### Quantity

**Use for non-monetary quantities (inventory counts, units, etc.):**

```python
from finance_kernel.domain.values import Quantity

qty = Quantity(Decimal("10"), "units")
qty = Quantity(Decimal("2.5"), "kg")
```

### ExchangeRate

```python
from finance_kernel.domain.values import ExchangeRate

rate = ExchangeRate(
    from_currency=Currency("EUR"),
    to_currency=Currency("USD"),
    rate=Decimal("1.10")
)
usd_amount = rate.convert(eur_money)
inverse = rate.inverse()  # USD → EUR
```

---

## Creating a Posting Strategy

Strategies transform business events into journal entries. They are **pure functions** with no side effects.

### Minimal Example

```python
from decimal import Decimal
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.dtos import EventEnvelope, ReferenceData, LineSpec, LineSide
from finance_kernel.domain.values import Money


class VendorInvoiceStrategy(BasePostingStrategy):
    """
    Transform ap.invoice.received events into GL posting.

    GL Effect:
        Dr: Expense (or Inventory)
        Cr: Accounts Payable
    """

    @property
    def event_type(self) -> str:
        return "ap.invoice.received"

    @property
    def version(self) -> int:
        return 1  # Increment when posting logic changes

    def _compute_line_specs(
        self,
        event: EventEnvelope,
        reference_data: ReferenceData,
    ) -> list[LineSpec]:
        """Transform event payload into balanced line specifications."""
        payload = event.payload

        invoice_amount = Money.of(
            Decimal(str(payload["amount"])),
            payload["currency"]
        )

        return [
            LineSpec(
                account_code=payload["expense_account"],
                side=LineSide.DEBIT,
                money=invoice_amount,
                dimensions={"cost_center": payload.get("cost_center")}
            ),
            LineSpec(
                account_code="2100",  # Accounts Payable
                side=LineSide.CREDIT,
                money=invoice_amount,
            ),
        ]
```

### What BasePostingStrategy Handles Automatically

You only implement `_compute_line_specs()`. The base class handles:

- Currency validation (ISO 4217)
- Account code → ID resolution
- Account active status checking
- Required dimensions validation
- Entry balance checking (debits = credits per currency)
- Rounding application when needed
- Rounding fraud prevention (R22)
- Reference snapshot versioning (R21)

### Strategy Rules

| Do | Don't |
|----|-------|
| Return balanced `LineSpec` lists | Set `is_rounding=True` (only Bookkeeper can) |
| Use `Money` for all amounts | Access database or I/O |
| Keep logic deterministic | Use `datetime.now()` or random values |
| Document the GL effect | Maintain internal state |

### Registering Strategies

```python
from finance_kernel.domain.strategy_registry import StrategyRegistry

# In your module's __init__.py or setup
StrategyRegistry.register(VendorInvoiceStrategy())
StrategyRegistry.register(VendorPaymentStrategy())
```

---

## Using Kernel Primitives (Ready Now)

These primitives are complete and should be used by all modules.

### EconomicLink - Artifact Relationships

Use `EconomicLink` to connect artifacts (PO→Receipt→Invoice, Invoice→Payment, etc.). This replaces foreign keys with a traversable knowledge graph.

```python
from uuid import uuid4
from finance_kernel.domain.economic_link import (
    ArtifactRef,
    ArtifactType,
    EconomicLink,
    LinkType,
    LinkQuery,
)
from finance_kernel.services.link_graph_service import LinkGraphService

# Create artifact references
po_ref = ArtifactRef.purchase_order(po_id)
receipt_ref = ArtifactRef.goods_receipt(receipt_id)
invoice_ref = ArtifactRef.vendor_invoice(invoice_id)

# Establish links (Receipt fulfills PO, Invoice references Receipt)
link_service = LinkGraphService(session)

# Link receipt to PO
link1 = EconomicLink.create(
    link_id=uuid4(),
    parent_ref=po_ref,
    child_ref=receipt_ref,
    link_type=LinkType.FULFILLED_BY,
    event_ref=ArtifactRef.event(receipt_event_id),
)
link_service.establish_link(link1)

# Link invoice to receipt
link2 = EconomicLink.create(
    link_id=uuid4(),
    parent_ref=receipt_ref,
    child_ref=invoice_ref,
    link_type=LinkType.FULFILLED_BY,
    event_ref=ArtifactRef.event(invoice_event_id),
)
link_service.establish_link(link2)
```

### LinkGraphService - Graph Traversal

Use `LinkGraphService` for traversing relationships and calculating unconsumed values.

```python
from finance_kernel.domain.values import Money
from finance_kernel.services.link_graph_service import LinkGraphService

link_service = LinkGraphService(session)

# Walk the graph from PO to find all linked documents
query = LinkQuery(
    starting_ref=po_ref,
    link_types=(LinkType.FULFILLED_BY,),
    max_depth=5,
    direction="children",
)
paths = link_service.walk_path(query)

# Check unconsumed value (e.g., how much of PO is not yet receipted)
unconsumed = link_service.get_unconsumed_value(
    parent_ref=po_ref,
    original_amount=Money.of("10000.00", "USD"),
    link_type=LinkType.FULFILLED_BY,
    amount_metadata_key="amount",
)
print(f"Open PO balance: {unconsumed.unconsumed_amount}")

# Check if an artifact has been reversed
is_reversed = link_service.is_reversed(invoice_ref)

# Find what reversed it
reversal = link_service.find_reversal(invoice_ref)
if reversal:
    print(f"Reversed by: {reversal.child_ref}")
```

### Available ArtifactTypes

```python
from finance_kernel.domain.economic_link import ArtifactType

# Document types
ArtifactType.PURCHASE_ORDER
ArtifactType.GOODS_RECEIPT
ArtifactType.VENDOR_INVOICE
ArtifactType.SALES_ORDER
ArtifactType.SHIPMENT
ArtifactType.CUSTOMER_INVOICE
ArtifactType.PAYMENT
ArtifactType.CREDIT_MEMO
ArtifactType.DEBIT_MEMO

# Kernel types
ArtifactType.JOURNAL_ENTRY
ArtifactType.EVENT
ArtifactType.COST_LOT
ArtifactType.INVENTORY_LOT
```

### Available LinkTypes

```python
from finance_kernel.domain.economic_link import LinkType

LinkType.FULFILLED_BY    # PO→Receipt, SO→Shipment (acyclic, no max)
LinkType.PAID_BY         # Invoice→Payment (acyclic, no max)
LinkType.REVERSED_BY     # Original→Reversal (acyclic, max 1 child)
LinkType.CORRECTED_BY    # Original→Correction (acyclic, max 1 child)
LinkType.DERIVED_FROM    # JournalEntry→Event (acyclic, no max)
LinkType.CONSUMED_FROM   # Issue→CostLot for FIFO/LIFO (acyclic, no max)
LinkType.ALLOCATED_TO    # Payment→Multiple Invoices (acyclic, no max)
LinkType.REFERENCES      # General relationship (NOT acyclic)
```

---

## Using Finance Engines

Before building calculation logic, check if a shared engine exists. These are pure function engines in `finance_engines/` that operate on kernel primitives.

### Available Engines

| Engine | Import | Use Case |
|--------|--------|----------|
| `VarianceCalculator` | `from finance_engines import VarianceCalculator` | PPV, quantity variance, FX variance |
| `MatchingEngine` | `from finance_engines import MatchingEngine` | 3-way match, 2-way match, bank recon |
| `AgingCalculator` | `from finance_engines import AgingCalculator` | AP/AR aging, slow-moving inventory |
| `AllocationEngine` | `from finance_engines import AllocationEngine` | Payment application, cost allocation |
| `SubledgerService` | `from finance_engines import SubledgerService` | Open item tracking, reconciliation |
| `TaxCalculator` | `from finance_engines import TaxCalculator` | Tax calculations |

### Example: Using the Matching Engine

```python
from decimal import Decimal
from finance_engines import MatchingEngine, MatchCandidate, MatchTolerance, MatchType
from finance_kernel.domain.values import Money

# Create matching candidates
po = MatchCandidate(
    document_type="PO",
    document_id="PO-001",
    amount=Money.of("1000.00", "USD"),
    quantity=Decimal("10"),
    dimensions={"vendor_id": "V001", "item_id": "ITEM-001"},
)

receipt = MatchCandidate(
    document_type="RECEIPT",
    document_id="RCV-001",
    amount=Money.of("1000.00", "USD"),
    quantity=Decimal("10"),
    dimensions={"vendor_id": "V001", "item_id": "ITEM-001"},
)

invoice = MatchCandidate(
    document_type="INVOICE",
    document_id="INV-001",
    amount=Money.of("1005.00", "USD"),  # $5 price variance
    quantity=Decimal("10"),
    dimensions={"vendor_id": "V001", "item_id": "ITEM-001"},
)

# Create 3-way match
engine = MatchingEngine()
result = engine.create_match(
    documents=[po, receipt, invoice],
    match_type=MatchType.THREE_WAY,
    tolerance=MatchTolerance(amount_tolerance=Decimal("10.00")),
)

# Result includes any variances
if result.price_variance:
    print(f"Price variance: {result.price_variance.variance}")
```

### Example: Using the Aging Calculator

```python
from datetime import date
from finance_engines import AgingCalculator, STANDARD_BUCKETS
from finance_kernel.domain.values import Money

calculator = AgingCalculator()

# Age a single item
aged_item = calculator.age_item(
    document_id="INV-001",
    document_type="invoice",
    document_date=date(2024, 1, 15),
    amount=Money.of("500.00", "USD"),
    as_of_date=date(2024, 3, 1),
    due_date=date(2024, 2, 14),
    counterparty_id="CUST-001",
)

print(f"Age: {aged_item.age_days} days, Bucket: {aged_item.bucket.name}")
# Output: Age: 16 days, Bucket: 1-30
```

### Engine Rules

| Do | Don't |
|----|-------|
| Import from `finance_engines` | Recreate variance/matching/aging logic |
| Pass data as parameters | Add database access to engines |
| Use `Money` for amounts | Use raw `Decimal` for monetary values |
| Check for existing engine first | Build parallel implementations |

---

## Economic Profile Layer (Advanced)

For complex event interpretation, the kernel provides an **Economic Profile** layer. This is a declarative approach where profiles define policies for how events become journal entries.

### When to Use Profiles vs Strategies

| Use Case | Approach |
|----------|----------|
| Simple event → balanced entry | `BasePostingStrategy` (see above) |
| Complex policy rules, guards, multi-ledger | `EconomicProfile` |
| Effective date ranges for policies | `EconomicProfile` |
| Reject/block conditions | `EconomicProfile` |

### Profile Components

```python
from finance_kernel.domain.economic_profile import (
    EconomicProfile,
    ProfileTrigger,
    ProfileMeaning,
    LedgerEffect,
    GuardCondition,
    GuardType,
)

profile = EconomicProfile(
    name="inventory_receipt_standard",
    version=1,
    trigger=ProfileTrigger(
        event_type="inventory.receipt",
        schema_version=1,
    ),
    meaning=ProfileMeaning(
        economic_type="InventoryIncrease",
        quantity_field="payload.quantity",
        dimensions=("warehouse_id", "item_id"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="Inventory",      # Account role, not code
            credit_role="GoodsReceived",
        ),
        LedgerEffect(
            ledger="inventory_subledger",
            debit_role="ItemStock",
            credit_role="InTransit",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.quantity <= 0",
            reason_code="INVALID_QUANTITY",
            message="Quantity must be positive",
        ),
    ),
)
```

### Key Concepts

| Concept | Description |
|---------|-------------|
| **Trigger** | Defines which events this profile applies to |
| **Meaning** | Extracts economic meaning (type, quantity, dimensions) |
| **LedgerEffect** | Uses AccountRoles, not COA codes (resolved at posting) |
| **Guards** | REJECT (terminal) or BLOCK (resumable) conditions |
| **Precedence** | Resolves conflicts when multiple profiles match |

### Integration Flow

```
Event → ProfileRegistry → MeaningBuilder → AccountingIntent → InterpretationCoordinator → Journal
                ↓                                    ↓
        EconomicProfile                        JournalWriter
                ↓                                    ↓
           Guards                            OutcomeRecorder
```

### L5 Atomicity Invariant

The `InterpretationCoordinator` enforces **L5**: All journal entries and the `InterpretationOutcome=POSTED` must be committed in the same transaction. Partial writes are impossible.

```python
from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator

coordinator = InterpretationCoordinator(
    session=session,
    journal_writer=writer,
    outcome_recorder=recorder,
)

result = coordinator.interpret_and_post(
    meaning_result=meaning_result,
    accounting_intent=intent,
    actor_id=actor_id,
)

if result.success:
    session.commit()  # L5: All or nothing
else:
    session.rollback()
```

---

## Module Directory Structure

```
accounts_payable/
├── __init__.py              # Register strategies, export public API
├── events/
│   ├── __init__.py
│   └── schemas.py           # Event payload schemas/validation
├── strategies/
│   ├── __init__.py
│   ├── vendor_invoice.py    # VendorInvoiceStrategy
│   ├── vendor_payment.py    # VendorPaymentStrategy
│   └── payment_void.py      # PaymentVoidStrategy
├── services/
│   ├── __init__.py
│   ├── invoice_service.py   # Business logic, calls Bookkeeper
│   └── payment_service.py
├── models/                   # If you need module-specific entities
│   ├── __init__.py
│   └── vendor_invoice.py
└── exceptions.py             # Module-specific exceptions
```

---

## Creating Module-Specific Exceptions

Extend the kernel's exception hierarchy:

```python
from finance_kernel.exceptions import FinanceKernelError, ValidationError


class APError(FinanceKernelError):
    """Base exception for Accounts Payable module."""
    pass


class InvoiceNotFoundError(APError):
    code: str = "AP_INVOICE_NOT_FOUND"

    def __init__(self, invoice_id: str):
        self.invoice_id = invoice_id
        super().__init__(f"Invoice not found: {invoice_id}")


class DuplicateInvoiceError(APError):
    code: str = "AP_DUPLICATE_INVOICE"

    def __init__(self, vendor_id: str, invoice_number: str):
        self.vendor_id = vendor_id
        self.invoice_number = invoice_number
        super().__init__(
            f"Duplicate invoice {invoice_number} for vendor {vendor_id}"
        )
```

---

## Event Schema Design

Events should be self-contained and immutable:

```python
# accounts_payable/events/schemas.py
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class VendorInvoiceReceived:
    """Event: A vendor invoice has been received and validated."""

    invoice_id: str
    vendor_id: str
    invoice_number: str
    invoice_date: date
    due_date: date
    currency: str
    lines: tuple["InvoiceLine", ...]

    @property
    def total_amount(self) -> Decimal:
        return sum(line.amount for line in self.lines)


@dataclass(frozen=True)
class InvoiceLine:
    line_number: int
    description: str
    amount: Decimal
    expense_account: str
    cost_center: str | None = None
```

---

## Anti-Patterns to Avoid

### 1. Creating Custom Amount Types

```python
# WRONG - Creates parallel hierarchy
@dataclass
class InvoiceAmount:
    value: Decimal
    currency_code: str

# CORRECT - Use Money
from finance_kernel.domain.values import Money
invoice_amount: Money
```

### 2. Using Raw Decimals for Money

```python
# WRONG - Loses currency context
amount: Decimal = Decimal("100.00")
currency: str = "USD"

# CORRECT - Paired together
amount: Money = Money.of("100.00", "USD")
```

### 3. Not Extending BasePostingStrategy

```python
# WRONG - Bypasses validation
class MyStrategy:
    def post(self, event):
        return [{"account": "1000", "amount": 100}]

# CORRECT - Gets automatic validation
class MyStrategy(BasePostingStrategy):
    def _compute_line_specs(self, event, reference_data):
        return [LineSpec(...)]
```

### 4. Creating Parallel Exception Hierarchies

```python
# WRONG - Breaks error handling consistency
class APException(Exception):
    pass

# CORRECT - Extends kernel hierarchy
class APError(FinanceKernelError):
    pass
```

### 5. Accessing Database in Strategies

```python
# WRONG - Side effects break determinism
def _compute_line_specs(self, event, reference_data):
    account = self.session.query(Account).filter_by(code="1000").first()
    ...

# CORRECT - Use reference_data (pre-loaded, immutable)
def _compute_line_specs(self, event, reference_data):
    account_id = reference_data.get_account_id("1000")
    ...
```

### 6. Building SQL with String Formatting

```python
# WRONG - SQL injection vulnerability
def get_triggers(trigger_names: list[str]) -> list:
    names = ", ".join(f"'{n}'" for n in trigger_names)  # DANGEROUS!
    return conn.execute(f"SELECT * FROM pg_trigger WHERE tgname IN ({names})")

# CORRECT - Use parameterized queries
def get_triggers(trigger_names: list[str]) -> list:
    return conn.execute(
        text("SELECT * FROM pg_trigger WHERE tgname = ANY(:names)"),
        {"names": trigger_names}
    )
```

---

## Testing Your Module

### Strategy Tests

```python
import pytest
from decimal import Decimal
from finance_kernel.domain.values import Money, Currency
from finance_kernel.domain.dtos import EventEnvelope, LineSide
from finance_kernel.testing import make_reference_data  # Test helper

from accounts_payable.strategies import VendorInvoiceStrategy


class TestVendorInvoiceStrategy:

    def test_creates_balanced_entry(self):
        strategy = VendorInvoiceStrategy()

        event = EventEnvelope(
            event_id="inv-001",
            event_type="ap.invoice.received",
            occurred_at=datetime.now(),
            effective_date=date.today(),
            actor_id="user-1",
            producer="ap-service",
            payload={
                "amount": "1000.00",
                "currency": "USD",
                "expense_account": "5100",
                "cost_center": "SALES",
            }
        )

        reference_data = make_reference_data(
            accounts={"5100": "uuid-1", "2100": "uuid-2"},
            active_accounts={"5100", "2100"},
        )

        result = strategy.propose(event, reference_data)

        assert result.is_valid
        entry = result.entry
        assert len(entry.lines) == 2

        # Verify balanced
        debits = sum(l.money.amount for l in entry.lines if l.side == LineSide.DEBIT)
        credits = sum(l.money.amount for l in entry.lines if l.side == LineSide.CREDIT)
        assert debits == credits

    def test_rejects_inactive_account(self):
        strategy = VendorInvoiceStrategy()
        event = make_invoice_event(expense_account="5100")

        reference_data = make_reference_data(
            accounts={"5100": "uuid-1", "2100": "uuid-2"},
            active_accounts={"2100"},  # 5100 is inactive
        )

        result = strategy.propose(event, reference_data)

        assert not result.is_valid
        assert "inactive" in result.errors[0].message.lower()
```

### Architecture Compliance

The architecture tests in `tests/architecture/test_primitive_reuse.py` will automatically catch violations when your module:

- Defines custom `Money`, `Amount`, `Currency`, or `Quantity` classes
- Uses `amount: Decimal` instead of `Money`
- Creates strategies without extending `BasePostingStrategy`
- Creates exceptions without extending `FinanceKernelError`

### Security Testing

Security tests verify that the database-level protections work correctly:

```bash
# SQL injection prevention tests
pytest tests/security/test_sql_injection.py -v

# Database-level invariant tests (requires PostgreSQL)
pytest tests/database_security/test_database_invariants.py -v
```

These tests verify:
- **Transaction Isolation**: No dirty reads, lost updates prevented
- **Constraint Bypass Prevention**: Raw SQL cannot bypass balance/immutability rules
- **Rollback Safety**: Failed transactions don't leave orphaned records
- **Trigger Enforcement**: PostgreSQL triggers block unauthorized modifications

### Running Tests

See `TEST_COMMANDS.md` in the project root for comprehensive test commands organized by category.

```bash
# Quick smoke test before committing
pytest tests/unit/ tests/posting/test_balance.py tests/audit/test_immutability.py -v --tb=short

# Full security audit
pytest tests/security/ tests/database_security/ tests/adversarial/ -v
```

---

## Checklist for New Modules

### Before You Start
- [ ] **CHECK ENGINE READINESS** - Review the Engine Status table at the top of this document. Do NOT proceed if required engines are 🔴.
- [ ] Check `finance_engines/` for existing calculation engines (variance, matching, aging, allocation, tax)
- [ ] Check if `EconomicProfile` is more appropriate than `BasePostingStrategy`
- [ ] Review `docs/reusable_modules.md` for shared patterns
- [ ] Plan to use `EconomicLink` for all artifact relationships (not foreign keys)

### Core Requirements
- [ ] Import value objects from `finance_kernel.domain.values`
- [ ] Use `Money` for all monetary fields
- [ ] Extend `FinanceKernelError` for exceptions
- [ ] Keep strategies/profiles pure (no I/O, no database, no clock)

### Posting Configuration
- [ ] Either extend `BasePostingStrategy` OR define `EconomicProfile`
- [ ] Register strategies with `StrategyRegistry` (or profiles with `ProfileRegistry`)
- [ ] Document GL effect in strategy/profile docstrings

### Testing
- [ ] Write tests verifying balanced entries
- [ ] Run architecture tests: `pytest tests/architecture/`
- [ ] Run security tests: `pytest tests/security/ tests/database_security/`
- [ ] Run engine tests if using `finance_engines`: `pytest tests/engines/`
- [ ] Verify no SQL injection vulnerabilities (use parameterized queries only)

---

## Cross-Cutting Concerns

Before building complex module features, **use these existing engines** (see `docs/reusable_modules.md`):

| Need | Import |
|------|--------|
| Calculate variances (price, quantity, FX) | `from finance_engines import VarianceCalculator` |
| Match documents (PO ↔ Receipt ↔ Invoice) | `from finance_engines import MatchingEngine` |
| Allocate amounts across targets | `from finance_engines import AllocationEngine` |
| Calculate aging buckets | `from finance_engines import AgingCalculator` |
| Track open items and balances | `from finance_engines import SubledgerService` |
| Calculate taxes | `from finance_engines import TaxCalculator` |

**Do NOT duplicate this logic in your module.** All engines are pure functions with comprehensive tests.

---

## Quick Reference

```python
# Standard imports for any module
from decimal import Decimal
from finance_kernel.domain.values import Money, Currency, Quantity, ExchangeRate
from finance_kernel.domain.dtos import (
    EventEnvelope, LineSpec, LineSide, ReferenceData
)
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.exceptions import FinanceKernelError

# Creating a strategy
class MyStrategy(BasePostingStrategy):
    @property
    def event_type(self) -> str:
        return "my.event.type"

    @property
    def version(self) -> int:
        return 1

    def _compute_line_specs(self, event, reference_data) -> list[LineSpec]:
        amount = Money.of(event.payload["amount"], event.payload["currency"])
        return [
            LineSpec(account_code="1000", side=LineSide.DEBIT, money=amount),
            LineSpec(account_code="2000", side=LineSide.CREDIT, money=amount),
        ]

# Register it
StrategyRegistry.register(MyStrategy())
```
