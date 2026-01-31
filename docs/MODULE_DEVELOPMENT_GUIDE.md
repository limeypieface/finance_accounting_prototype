# Module Development Guide

This guide explains how to build new modules (AP, AR, Inventory, WIP) on top of `finance_kernel`. Following these patterns ensures consistency, prevents code duplication, and maintains the invariants required for audit compliance.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│            Your Module (AP, AR, Inventory, etc.)                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │  Event Schemas  │  │ AccountingPolicy│  │  Module Service  │  │
│  │  (your events)  │  │  Definitions    │  │  (orchestrator)  │  │
│  └────────┬────────┘  └────────┬────────┘  └───────┬─────────┘  │
└───────────┼────────────────────┼───────────────────┼────────────┘
            │                    │                   │
            ▼                    ▼                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                       finance_config                             │
│  AccountingConfigurationSet → CompiledPolicyPack                │
│  get_active_config() — the ONLY configuration entrypoint         │
└─────────────────────────────────┬───────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────┬──────────────────────────────┐
│      finance_engines             │     finance_services          │
│  (Pure — no I/O, no session)    │  (Stateful — session, I/O)   │
│  VarianceCalculator              │  ValuationService             │
│  AllocationEngine                │  ReconciliationService        │
│  AllocationCascade               │  CorrectionService            │
│  MatchingEngine                  │  SubledgerService             │
│  AgingCalculator                 │                               │
│  TaxCalculator                   │                               │
│  BillingEngine                   │                               │
│  ICEEngine                       │                               │
└──────────────────────────────────┴──────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                       finance_kernel                             │
│  Value Objects │ AccountingPolicy │ InterpretationCoordinator    │
│  Bookkeeper    │ MeaningBuilder   │ JournalWriter                │
│  (pure)        │   (pure)         │ OutcomeRecorder              │
└─────────────────────────────────────────────────────────────────┘
```

**Your module provides**: Event schemas, accounting policy definitions, module service (orchestrator)
**finance_config provides**: `AccountingConfigurationSet` compiled into `CompiledPolicyPack` via `get_active_config()`
**finance_engines provides**: Pure calculation engines (variance, matching, aging, allocation, tax)
**finance_services provides**: Stateful services (valuation, reconciliation, correction, subledger)
**finance_kernel provides**: Value objects, validation, posting, audit trail, accounting policies

---

## Engine Readiness Status

All engines and services are now complete. Business modules can be built using the full suite.

### Pure Engines (finance_engines)

| Engine | Status | Location | What It Provides |
|--------|--------|----------|------------------|
| **VarianceCalculator** | Complete | `finance_engines.variance` | PPV, quantity, FX variance analysis. |
| **AllocationEngine** | Complete | `finance_engines.allocation` | Payment application, cost allocation. |
| **AllocationCascade** | Complete | `finance_engines.allocation_cascade` | DCAA indirect cost allocation (fringe, overhead, G&A). |
| **MatchingEngine** | Complete | `finance_engines.matching` | 3-way match, 2-way match, bank reconciliation. |
| **AgingCalculator** | Complete | `finance_engines.aging` | AP/AR aging buckets, slow-moving inventory. |
| **TaxCalculator** | Complete | `finance_engines.tax` | VAT, GST, WHT, compound tax calculations. |

### Stateful Services (finance_services)

| Service | Status | Location | What It Provides |
|---------|--------|----------|------------------|
| **ValuationLayer** | Complete | `finance_services.valuation_service` | FIFO/LIFO/Weighted Average/Standard costing, cost lot tracking, layer consumption. |
| **ReconciliationManager** | Complete | `finance_services.reconciliation_service` | Open/Matched state tracking, partial payment application, document matching, settlement. |
| **CorrectionEngine** | Complete | `finance_services.correction_service` | Recursive reversals using EconomicLink graph, cascade unwind logic. |
| **SubledgerService** | Complete | `finance_services.subledger_service` | Open item tracking, reconciliation. |

> **Note:** BillingEngine and ICEEngine are pure engines in `finance_engines/`, not stateful services.

### Kernel Primitives

| Primitive | Status | Location | What It Provides |
|-----------|--------|----------|------------------|
| **EconomicLink** | Complete | `finance_kernel.domain.economic_link` | First-class artifact relationships (PO->Receipt->Invoice). Knowledge graph replacing brittle foreign keys. |
| **LinkGraphService** | Complete | `finance_kernel.services.link_graph_service` | Graph traversal, cycle detection (L3), unconsumed value calculation, reversal detection. |

### Module Dependencies (All Engines Ready)

| Module | Required Engines / Services | Can Start? |
|--------|----------------------------|------------|
| **AP (Accounts Payable)** | EconomicLink, ReconciliationManager, CorrectionEngine | Yes |
| **AR (Accounts Receivable)** | EconomicLink, ReconciliationManager, CorrectionEngine | Yes |
| **Inventory** | EconomicLink, ValuationLayer, CorrectionEngine | Yes |
| **WIP** | EconomicLink, ValuationLayer, AllocationEngine | Yes |
| **T&E (Travel & Expense)** | EconomicLink, ReconciliationManager | Yes |
| **Cash** | EconomicLink, ReconciliationManager | Yes |
| **Assets** | EconomicLink | Yes |
| **Payroll** | EconomicLink, AllocationEngine | Yes |
| **Tax** | TaxCalculator | Yes |
| **Procurement** | EconomicLink, MatchingEngine | Yes |
| **GL** | All kernel services | Yes |

### ERP Modules (All Implemented)

All 12 ERP modules are implemented in `finance_modules/`:

| Module | Directory | Contents |
|--------|-----------|----------|
| AP | `finance_modules/ap/` | `config.py`, `models.py`, `profiles.py`, `service.py`, `workflows.py` |
| AR | `finance_modules/ar/` | `config.py`, `models.py`, `profiles.py`, `service.py`, `workflows.py` |
| Assets | `finance_modules/assets/` | `config.py`, `models.py`, `profiles.py`, `service.py`, `workflows.py` |
| Cash | `finance_modules/cash/` | `config.py`, `models.py`, `profiles.py`, `service.py`, `workflows.py` |
| Contracts | `finance_modules/contracts/` | `config.py`, `profiles.py`, `service.py`, `workflows.py` |
| Expense | `finance_modules/expense/` | `config.py`, `models.py`, `profiles.py`, `service.py`, `workflows.py` |
| GL | `finance_modules/gl/` | `config.py`, `models.py`, `profiles.py`, `service.py`, `workflows.py` |
| Inventory | `finance_modules/inventory/` | `config.py`, `models.py`, `profiles.py`, `service.py`, `workflows.py` |
| Payroll | `finance_modules/payroll/` | `config.py`, `models.py`, `profiles.py`, `service.py`, `workflows.py` |
| Procurement | `finance_modules/procurement/` | `config.py`, `models.py`, `profiles.py`, `service.py`, `workflows.py` |
| Tax | `finance_modules/tax/` | `config.py`, `models.py`, `profiles.py`, `service.py`, `workflows.py` |
| WIP | `finance_modules/wip/` | `config.py`, `models.py`, `profiles.py`, `service.py`, `workflows.py` |

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
inverse = rate.inverse()  # USD -> EUR
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
- Account code -> ID resolution
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

Use `EconomicLink` to connect artifacts (PO->Receipt->Invoice, Invoice->Payment, etc.). This replaces foreign keys with a traversable knowledge graph.

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
receipt_ref = ArtifactRef.receipt(receipt_id)
invoice_ref = ArtifactRef.invoice(invoice_id)

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

# Core kernel artifacts
ArtifactType.EVENT
ArtifactType.JOURNAL_ENTRY
ArtifactType.JOURNAL_LINE

# Document artifacts (subledgers)
ArtifactType.PURCHASE_ORDER
ArtifactType.RECEIPT
ArtifactType.INVOICE
ArtifactType.PAYMENT
ArtifactType.CREDIT_MEMO
ArtifactType.DEBIT_MEMO

# Inventory artifacts
ArtifactType.COST_LOT
ArtifactType.SHIPMENT
ArtifactType.INVENTORY_ADJUSTMENT

# Fixed asset artifacts
ArtifactType.ASSET
ArtifactType.DEPRECIATION
ArtifactType.DISPOSAL

# Banking artifacts
ArtifactType.BANK_STATEMENT
ArtifactType.BANK_TRANSACTION

# Intercompany
ArtifactType.INTERCOMPANY_TRANSACTION
```

### Available LinkTypes

```python
from finance_kernel.domain.economic_link import LinkType

LinkType.FULFILLED_BY    # PO->Receipt, SO->Shipment
LinkType.PAID_BY         # Invoice->Payment
LinkType.APPLIED_TO      # Payment->Invoice application
LinkType.REVERSED_BY     # Original->Reversal (max 1 child)
LinkType.CORRECTED_BY    # Original->Correction (max 1 child)
LinkType.CONSUMED_BY     # CostLot->Consumption (NOT CONSUMED_FROM)
LinkType.SOURCED_FROM    # Derived from source
LinkType.ALLOCATED_TO    # Cost allocation target
LinkType.ALLOCATED_FROM  # Cost allocation source
LinkType.DERIVED_FROM    # JournalEntry->Event
LinkType.MATCHED_WITH    # Three-way match
LinkType.ADJUSTED_BY     # Adjustments
```

---

## Using Finance Engines and Services

Before building calculation logic, check if a shared engine or service exists. Pure engines live in `finance_engines/` and stateful services live in `finance_services/`.

### Pure Engines (from `finance_engines`)

These are pure-function engines with no I/O or session dependencies. They operate on kernel primitives and return results directly.

| Engine | Import | Use Case |
|--------|--------|----------|
| `VarianceCalculator` | `from finance_engines import VarianceCalculator` | PPV, quantity variance, FX variance |
| `MatchingEngine` | `from finance_engines import MatchingEngine` | 3-way match, 2-way match, bank recon |
| `AgingCalculator` | `from finance_engines import AgingCalculator` | AP/AR aging, slow-moving inventory |
| `AllocationEngine` | `from finance_engines import AllocationEngine` | Payment application, cost allocation |
| `AllocationCascade` | `from finance_engines import AllocationCascade` | DCAA indirect cost allocation cascade |
| `TaxCalculator` | `from finance_engines import TaxCalculator` | Tax calculations |

### Stateful Services (from `finance_services`)

These services require a database session and/or interact with the kernel's link graph. They manage state and I/O.

| Service | Import | Use Case |
|---------|--------|----------|
| `ValuationLayer` | `from finance_services.valuation_service import ValuationLayer` | FIFO/LIFO/weighted avg costing |
| `ReconciliationManager` | `from finance_services.reconciliation_service import ReconciliationManager` | Document matching, settlement |
| `CorrectionEngine` | `from finance_services.correction_service import CorrectionEngine` | Reversals, compensating entries |
| `SubledgerService` | `from finance_services.subledger_service import SubledgerService` | Open item tracking, reconciliation |

> **Note:** BillingEngine and ICEEngine are pure engines in `finance_engines/`, not stateful services. Import them from `finance_engines`.

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

### Engine and Service Rules

| Do | Don't |
|----|-------|
| Import pure engines from `finance_engines` | Recreate variance/matching/aging logic |
| Import stateful services from `finance_services` | Add database access to pure engines |
| Pass data as parameters to pure engines | Build parallel implementations |
| Use `Money` for amounts | Use raw `Decimal` for monetary values |
| Share the session with stateful services | Create separate sessions per service |

---

## Accounting Policy Layer (Advanced)

For complex event interpretation, the kernel provides the **Accounting Policy** infrastructure (`AccountingPolicy`, `PolicySelector`, `MeaningBuilder`, `PolicyBridge`). Your module defines the actual policies in its `profiles.py` file, and the `PolicyAuthority` governs which policies are active. The `ModulePostingService` orchestrates the full posting flow: event -> policy lookup -> meaning -> intent -> atomic post.

### When to Use Policies vs Strategies

| Use Case | Approach |
|----------|----------|
| Simple event -> balanced entry | `BasePostingStrategy` (see above) |
| Complex policy rules, guards, multi-ledger | `AccountingPolicy` |
| Effective date ranges for policies | `AccountingPolicy` |
| Reject/block conditions | `AccountingPolicy` |

### Policy Components

```python
from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    PolicyTrigger,
    PolicyMeaning,
    LedgerEffect,
    GuardCondition,
    GuardType,
)

policy = AccountingPolicy(
    name="inventory_receipt_standard",
    version=1,
    trigger=PolicyTrigger(
        event_type="inventory.receipt",
        schema_version=1,
    ),
    meaning=PolicyMeaning(
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
| **Trigger** | Defines which events this policy applies to |
| **Meaning** | Extracts economic meaning (type, quantity, dimensions) |
| **LedgerEffect** | Uses AccountRoles, not COA codes (resolved at posting) |
| **Guards** | REJECT (terminal) or BLOCK (resumable) conditions |
| **Precedence** | Resolves conflicts when multiple policies match |

### Integration Flow

```
Event → PolicySelector → MeaningBuilder → AccountingIntent → InterpretationCoordinator → Journal
               ↓                                    ↓
       AccountingPolicy                       JournalWriter
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

## Building a Module Service

Each module should have a `service.py` that provides a thin orchestration layer. The module service owns the transaction boundary (R7 compliance), composes pure engines and stateful services, and delegates posting to `ModulePostingService`.

### Standard Pattern

```python
from sqlalchemy.orm import Session
from finance_kernel.services.module_posting_service import ModulePostingService
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_engines import VarianceCalculator
from finance_services.reconciliation_service import ReconciliationManager


class APService:
    """Accounts Payable module service -- thin orchestration layer."""

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()
        self._poster = ModulePostingService(
            session=session,
            role_resolver=role_resolver,
            clock=self._clock,
            auto_commit=False,  # R7: service owns transaction
        )
        # Pure engines (no session needed)
        self._variance = VarianceCalculator()
        # Stateful services (share session)
        self._reconciliation = ReconciliationManager(session, LinkGraphService(session))

    def receive_invoice(self, invoice_data: dict) -> PostingResult:
        """Process a vendor invoice through the full posting pipeline."""
        try:
            # 1. Build event envelope from invoice data
            event = self._build_invoice_event(invoice_data)

            # 2. Post via ModulePostingService
            result = self._poster.post(event)

            # 3. Establish economic links
            self._link_to_purchase_order(invoice_data, result)

            # 4. Commit on success (R7)
            self._session.commit()
            return result

        except Exception:
            self._session.rollback()
            raise
```

### Design Principles

- **Services are thin orchestration layers.** They coordinate engines and kernel primitives but contain minimal business logic themselves.
- **Services own the transaction boundary (R7).** Create `ModulePostingService` with `auto_commit=False`, then explicitly commit on success and rollback on failure.
- **Pure engines are instantiated without arguments.** They need no session or configuration.
- **Stateful services share the module service's session.** This ensures all database operations participate in the same transaction.
- **One service per module.** The service is the single entry point for all module operations.

---

## Configuration-Driven Architecture

The system uses a configuration-driven approach where `finance_config` is the single source of truth for all policy and role binding decisions.

### How It Works

1. **`finance_config.get_active_config()`** returns an `AccountingConfigurationSet` that contains all policy definitions, role bindings, and engine settings.
2. The configuration set is compiled into a **`CompiledPolicyPack`** which is an optimized, immutable snapshot used at runtime.
3. **Role bindings come from configuration, not code.** Your module's `profiles.py` defines the policy structure (triggers, meanings, ledger effects), but the actual account role -> COA code mappings are resolved through configuration.

### Usage

```python
from finance_config import get_active_config

config = get_active_config()
# config.compiled_pack contains all active policies, role bindings, etc.
# Pass to ModulePostingService or PolicySelector as needed
```

This separation means you can change which accounts are debited/credited for a given event type without modifying code -- only configuration changes.

---

## Runtime Tracing

The system provides four structured trace types for diagnosing behavior at each layer of the architecture. Enable them via environment variables or logging configuration.

| Trace Type | Layer | What It Captures |
|------------|-------|------------------|
| `FINANCE_CONFIG_TRACE` | finance_config | Configuration loading, policy pack compilation, role binding resolution |
| `FINANCE_POLICY_TRACE` | Accounting policies | Policy selection, guard evaluation, meaning extraction, trigger matching |
| `FINANCE_ENGINE_TRACE` | finance_engines / finance_services | Engine inputs/outputs, allocation steps, matching decisions, valuation layer consumption |
| `FINANCE_KERNEL_TRACE` | finance_kernel | Posting pipeline, journal writes, balance checks, immutability enforcement |

Use these traces to follow an event from ingestion through configuration lookup, policy evaluation, engine processing, and final journal posting.

---

## Module Directory Structure

```
finance_modules/ap/
├── __init__.py
├── config.py
├── models.py
├── profiles.py          # AccountingPolicy definitions
├── service.py           # APService — orchestrates engines + kernel
└── workflows.py
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

### 7. Importing Stateful Services from finance_engines

```python
# WRONG - These moved to finance_services
from finance_engines.valuation import ValuationLayer
from finance_engines.reconciliation import ReconciliationManager

# CORRECT - Import from finance_services
from finance_services.valuation_service import ValuationLayer
from finance_services.reconciliation_service import ReconciliationManager
```

---

## Testing Your Module

### Strategy Tests

```python
import pytest
from decimal import Decimal
from finance_kernel.domain.values import Money, Currency
from finance_kernel.domain.dtos import EventEnvelope, LineSide
# Test helpers are in tests/conftest.py, not a kernel testing module
# Use fixtures: standard_accounts, current_period, posting_orchestrator

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
- [ ] Check `finance_engines/` for existing pure calculation engines (variance, matching, aging, allocation, tax)
- [ ] Check `finance_services/` for existing stateful services (valuation, reconciliation, correction, subledger)
- [ ] Check if `AccountingPolicy` is more appropriate than `BasePostingStrategy`
- [ ] Check `finance_modules/` for existing ERP modules that may already cover your use case
- [ ] Plan to use `EconomicLink` for all artifact relationships (not foreign keys)

### Core Requirements
- [ ] Import value objects from `finance_kernel.domain.values`
- [ ] Use `Money` for all monetary fields
- [ ] Extend `FinanceKernelError` for exceptions
- [ ] Keep strategies/policies pure (no I/O, no database, no clock)

### Module Service
- [ ] Create a `service.py` in your module following the standard pattern
- [ ] Service constructor takes (session, role_resolver, clock)
- [ ] Service creates `ModulePostingService` with `auto_commit=False`
- [ ] Import pure engines from `finance_engines/`
- [ ] Import stateful services from `finance_services/`

### Posting Configuration
- [ ] Either extend `BasePostingStrategy` OR define `AccountingPolicy`
- [ ] If using policies: define in your module's `profiles.py`
- [ ] Policy definitions go in configuration YAML, not code
- [ ] Register strategies with `StrategyRegistry` (or policies via `PolicyBridge`)
- [ ] Document GL effect in strategy/policy docstrings

### Testing
- [ ] Write tests verifying balanced entries
- [ ] Run architecture tests: `pytest tests/architecture/`
- [ ] Run security tests: `pytest tests/security/ tests/database_security/`
- [ ] Run engine tests if using `finance_engines`: `pytest tests/engines/`
- [ ] Run `pytest tests/modules/test_cross_module_flow.py` to verify structural compliance
- [ ] Verify no SQL injection vulnerabilities (use parameterized queries only)

---

## Cross-Cutting Concerns

Before building complex module features, **use these existing engines and services**:

### Pure Engines

| Need | Import |
|------|--------|
| Calculate variances (price, quantity, FX) | `from finance_engines import VarianceCalculator` |
| Match documents (PO <-> Receipt <-> Invoice) | `from finance_engines import MatchingEngine` |
| Allocate amounts across targets | `from finance_engines import AllocationEngine` |
| DCAA indirect cost allocation cascade | `from finance_engines import AllocationCascade` |
| Calculate aging buckets | `from finance_engines import AgingCalculator` |
| Calculate taxes | `from finance_engines import TaxCalculator` |

### Stateful Services

| Need | Import |
|------|--------|
| FIFO/LIFO/weighted average costing | `from finance_services.valuation_service import ValuationLayer` |
| Document reconciliation and settlement | `from finance_services.reconciliation_service import ReconciliationManager` |
| Reversals and compensating entries | `from finance_services.correction_service import CorrectionEngine` |
| Open item tracking and reconciliation | `from finance_services.subledger_service import SubledgerService` |

> **Note:** BillingEngine and ICEEngine are pure engines in `finance_engines/`, not stateful services.

**Do NOT duplicate this logic in your module.** All engines are pure functions with comprehensive tests. All services are tested against the kernel invariants.

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

---

## Reconciliation Manager Reference

### Definition and Role

The Reconciliation Manager is a stateful service that sits on top of the LinkGraphService. While the Graph Service knows that "A is linked to B," the Reconciliation Manager knows "A is 40% settled by B."

**Import:** `from finance_services.reconciliation_service import ReconciliationManager`

### Core Responsibilities

- **Balance Tracking**: Maintaining the "Open Amount" of any artifact (Invoices, Receipts, Payments).
- **Tolerance Enforcement**: Deciding if a $1.00 difference is a "Price Variance" (Acceptable) or a "Dispute" (Blocked).
- **Match Lifecycle**: Moving links through states: PROPOSED -> PENDING_APPROVAL -> MATCHED.
- **Subledger Integrity**: Ensuring that the sum of all matches to an invoice exactly equals the amount posted to the GL.

### Technical Specification

**Inputs (The "Match Proposal")**

To create a match, the module provides a MatchProposal:

- **LeftRef & RightRef**: The two ArtifactRefs being linked (e.g., Invoice and Payment).
- **MatchAmount**: The Money value being applied.
- **TolerancePolicy**: (Optional) The rules for handling over/under-payments.
- **LinkType**: Usually PAID_BY or FULFILLED_BY.

**Outputs (The "Match Result")**

- **LinkID**: The UUID of the newly established EconomicLink.
- **RemainingBalance**: The UnconsumedValue for both sides.
- **VarianceEvent**: If the amounts don't match perfectly but are within tolerance, the manager emits a variance fact for the kernel to post to a "Price Variance" or "FX Gain/Loss" account.

### Key Invariants

| ID | Invariant | Description |
|----|-----------|-------------|
| R1 | No Over-Settlement | Sum(ChildLinks) <= Parent.TotalAmount. You cannot pay $1,100 on a $1,000 invoice without a specific "Overpayment" event. |
| R2 | Currency Consistency | Matches must occur in the same currency OR provide an ExchangeRate that results in zero "unexplained" dust. |
| R3 | Transaction Atomicity | The match state update and the EconomicLink creation must occur in a single DB transaction. |

### How Modules Use the Manager

The Manager transforms how you write module logic. Instead of writing math, you write orchestration:

**AP Module Example (Invoice Payment)**

1. Module: Receives a "Payment applied to Invoice" command.
2. Module: Calls `ReconciliationManager.propose_match(invoice_ref, payment_ref, amount)`.
3. Manager: Checks `LinkGraphService.get_unconsumed_value(invoice_ref)`.
4. Manager: If valid, creates the PAID_BY link.
5. Manager: Returns the new "Open Balance" of the invoice.
6. Module: Updates the UI to show the invoice as "Partially Paid."
