# Database Layer - Persistence & Defense in Depth

## Overview

The database layer provides **persistent storage** with **defense-in-depth immutability enforcement**. This layer ensures that financial data cannot be tampered with, even by actors with direct database access.

**Key principle**: Immutability is enforced at TWO levels:
1. **ORM Level** (`immutability.py`): Catches modifications through Python/SQLAlchemy
2. **Database Level** (`triggers.py`): Catches raw SQL, bulk updates, migrations

Both layers must be bypassed to modify protected data, making tampering significantly harder and always detectable.

---

## Components

### Engine (`engine.py`)

Database connection management and table creation.

```python
from finance_kernel.db.engine import get_engine, get_session, create_tables

# Get SQLAlchemy engine
engine = get_engine()

# Get session for operations
with get_session() as session:
    # ... do work ...

# Create all tables (including triggers)
create_tables()
```

**Environment configuration:**
```bash
DATABASE_URL=postgresql://user:pass@localhost/finance_kernel
```

---

### Models (`../models/`)

SQLAlchemy ORM models for all persistent entities.

#### JournalEntry & JournalLine (`journal.py`)

The core financial records.

```python
class JournalEntry(Base):
    id: UUID                    # Primary key
    source_event_id: UUID       # Links to Event
    idempotency_key: str        # UNIQUE - ensures exactly-once posting
    effective_date: date        # Accounting date
    posted_at: datetime         # When posted
    status: JournalEntryStatus   # draft | posted | reversed
    seq: int                    # Global sequence number
    posting_rule_version: int   # For replay compatibility

class JournalLine(Base):
    id: UUID
    journal_entry_id: UUID      # FK to JournalEntry
    account_id: UUID            # FK to Account
    side: LineSide              # debit | credit
    amount: Decimal             # Always positive
    currency: str               # ISO 4217
    dimensions: dict            # JSONB dimension values
    is_rounding: bool           # True if auto-generated rounding line
```

#### Event (`event.py`)

The event store - source of all financial facts.

```python
class Event(Base):
    id: UUID                    # Row primary key (auto-generated)
    event_id: UUID              # External event ID from producer (UNIQUE)
    event_type: str
    occurred_at: datetime
    effective_date: date
    actor_id: UUID
    producer: str
    payload: dict               # JSONB
    payload_hash: str           # For tampering detection
    schema_version: int
```

#### AuditEvent (`audit_event.py`)

The cryptographic audit trail.

```python
class AuditEvent(Base):
    id: UUID                    # Primary key (inherited from Base)
    seq: int                    # Monotonic sequence (UNIQUE, for ordering)
    entity_type: str            # 'event', 'journal_entry', etc.
    entity_id: UUID
    action: str                 # 'event_ingested', 'journal_posted', etc.
    actor_id: UUID
    occurred_at: datetime
    payload_hash: str           # Hash of event data
    prev_hash: str | None       # Hash of previous AuditEvent (NULL for first)
    hash: str                   # Hash of (payload_hash + prev_hash)
```

#### Account (`account.py`)

Chart of accounts with hierarchy support.

```python
class Account(Base):
    id: UUID
    code: str                   # UNIQUE account code
    name: str
    account_type: AccountType   # asset|liability|equity|revenue|expense
    normal_balance: NormalBalance  # debit|credit
    parent_id: UUID | None      # For hierarchy
    is_active: bool
    currency: str | None        # For currency-specific accounts
    tags: list[str]             # e.g., ['rounding', 'system']
```

#### FiscalPeriod (`fiscal_period.py`)

Period management with status control.

```python
class FiscalPeriod(Base):
    id: UUID
    period_code: str            # UNIQUE, e.g., "2024-Q1"
    name: str
    start_date: date
    end_date: date
    status: PeriodStatus        # open | closed
    allows_adjustments: bool    # R13 control
```

#### Dimension & DimensionValue (`dimensions.py`)

Flexible dimension system for cost centers, projects, etc.

```python
class Dimension(Base):
    code: str                   # PK, e.g., "cost_center"
    name: str
    is_required: bool
    is_active: bool

class DimensionValue(Base):
    id: UUID
    dimension_code: str         # FK to Dimension
    code: str                   # e.g., "CC-001"
    name: str                   # e.g., "Engineering"
    is_active: bool
```

---

### Immutability - ORM Level (`immutability.py`)

SQLAlchemy event listeners that **prevent modifications through Python code**.

**How it works:**
```python
@event.listens_for(JournalEntry, "before_update")
def _check_journal_entry_immutability(mapper, connection, target):
    # Get the original state
    state = inspect(target)

    # Check if status was 'posted' before this update
    if state.attrs.status.history.deleted:
        old_status = state.attrs.status.history.deleted[0]
        if old_status == EntryStatus.POSTED:
            raise ImmutabilityError("Cannot modify posted journal entry")
```

**Protected entities:**

| Entity | Protection |
|--------|------------|
| JournalEntry | Immutable when status='posted' |
| JournalLine | Immutable when parent entry is posted |
| AuditEvent | Always immutable (no updates or deletes ever) |
| Event | Immutable after creation (payload_hash enforced) |
| FiscalPeriod | Immutable when status='closed' |
| Account | Structural fields immutable when referenced by posted lines |
| Dimension | Code immutable when values exist |
| DimensionValue | Code, name, dimension_code always immutable |

**Example - blocked modification:**
```python
entry = session.get(JournalEntry, entry_id)
entry.description = "Hacked!"  # This sets the attribute
session.flush()  # RAISES ImmutabilityError - blocked by listener
```

---

### Immutability - Database Level (`triggers.py` + `sql/`)

PostgreSQL triggers that **prevent modifications via raw SQL**.

**Why database triggers?**
- ORM listeners can be bypassed with raw SQL
- Bulk UPDATE statements bypass ORM entirely
- Direct database access (psql, migrations) bypasses Python
- Defense in depth - both layers must be compromised

**File organization:**

Trigger SQL is stored in separate `.sql` files for better maintainability:

```
db/
├── __init__.py          # Package init
├── base.py              # Base, TrackedBase, UUIDString
├── engine.py            # Connection management, table creation
├── types.py             # Custom SQLAlchemy column types
├── triggers.py          # Python loader and API
├── immutability.py      # ORM-level event listeners
└── sql/
    ├── README.md        # SQL file documentation
    ├── 01_journal_entry.sql          # 2 triggers
    ├── 02_journal_line.sql           # 2 triggers
    ├── 03_audit_event.sql            # 2 triggers
    ├── 04_account.sql                # 2 triggers
    ├── 05_fiscal_period.sql          # 2 triggers
    ├── 06_rounding.sql               # 2 triggers
    ├── 07_dimension.sql              # 4 triggers
    ├── 08_exchange_rate.sql          # 4 triggers
    ├── 09_event_immutability.sql     # 2 triggers
    ├── 10_balance_enforcement.sql    # 2 triggers
    ├── 11_economic_link_immutability.sql  # 2 triggers
    └── 99_drop_all.sql               # Drops all triggers
```

**Total: 26 PostgreSQL triggers across 11 SQL files.**

**Benefits of SQL files:**
- Syntax highlighting in editors
- IDE support for SQL (linting, formatting)
- Easier code review
- Independent testing (`psql -f file.sql`)
- Clear separation of concerns

**Trigger installation:**
```python
from finance_kernel.db.triggers import install_immutability_triggers

# Called automatically by create_tables()
install_immutability_triggers(engine)

# Check which triggers are installed
from finance_kernel.db.triggers import get_installed_triggers, get_missing_triggers
installed = get_installed_triggers(engine)
missing = get_missing_triggers(engine)

# Install a single trigger file (for testing)
from finance_kernel.db.triggers import install_trigger_file
install_trigger_file(engine, "01_journal_entry.sql")
```

**All 26 triggers implemented:**

| Trigger | Table | Protection |
|---------|-------|------------|
| `trg_journal_entry_immutability_update` | journal_entries | Blocks modification of posted entries |
| `trg_journal_entry_immutability_delete` | journal_entries | Blocks deletion of posted entries |
| `trg_journal_line_immutability_update` | journal_lines | Blocks modification when parent posted |
| `trg_journal_line_immutability_delete` | journal_lines | Blocks deletion when parent posted |
| `trg_audit_event_immutability_update` | audit_events | Always blocks (audit is sacred) |
| `trg_audit_event_immutability_delete` | audit_events | Always blocks |
| `trg_account_structural_immutability_update` | accounts | Blocks structural field changes when referenced |
| `trg_account_last_rounding_delete` | accounts | Protects last rounding account |
| `trg_fiscal_period_immutability_update` | fiscal_periods | Blocks modification of closed periods |
| `trg_fiscal_period_immutability_delete` | fiscal_periods | Blocks deletion of closed/used periods |
| `trg_journal_line_single_rounding` | journal_lines | Only one rounding line per entry |
| `trg_journal_line_rounding_threshold` | journal_lines | Rounding amount must be small |
| `trg_dimension_code_immutability` | dimensions | Blocks code change when values exist |
| `trg_dimension_deletion_protection` | dimensions | Blocks deletion when values exist |
| `trg_dimension_value_structural_immutability` | dimension_values | Blocks code/name/dimension_code changes |
| `trg_dimension_value_deletion_protection` | dimension_values | Blocks deletion when referenced |
| `trg_exchange_rate_validate` | exchange_rates | Rate must be positive and non-zero |
| `trg_exchange_rate_immutability` | exchange_rates | Blocks modification when referenced |
| `trg_exchange_rate_delete` | exchange_rates | Blocks deletion when referenced |
| `trg_exchange_rate_arbitrage` | exchange_rates | Detects inconsistent inverse rates |
| `trg_event_immutability_update` | events | Blocks event record modification |
| `trg_event_immutability_delete` | events | Blocks event record deletion |
| `trg_journal_entry_balance_check` | journal_entries | Enforces balanced entries (R12) |
| `trg_journal_line_no_insert_posted` | journal_lines | Blocks adding lines to posted entries |
| `trg_economic_link_immutability_update` | economic_links | Blocks link record modification |
| `trg_economic_link_immutability_delete` | economic_links | Blocks link record deletion |

**Example - blocked raw SQL:**
```sql
-- This will be blocked by the trigger
UPDATE journal_entries SET description = 'Hacked' WHERE id = '...';
-- ERROR: R10 Violation: Cannot modify posted journal entry ...

-- Even this will be blocked
DELETE FROM audit_events WHERE seq = 1;
-- ERROR: R10 Violation: Audit events are immutable ...
```

---

### Custom Types (`types.py` and `base.py`)

Annotated type aliases for domain-specific columns. No floats anywhere in the finance kernel -- all monetary values use `Decimal` with explicit precision.

```python
# Monetary amount: Numeric(38, 9) -- 38 digits total, 9 decimal places
Money = Annotated[Decimal, Numeric(38, 9)]

# Exchange rate: Numeric(38, 18) -- 38 digits total, 18 decimal places
Rate = Annotated[Decimal, Numeric(38, 18)]

# Currency: String(3) -- ISO 4217 code
Currency = Annotated[str, String(3)]

# Sequence: BigInteger
Sequence = Annotated[int, BigInteger]

# Hash: String(64) -- SHA-256 hex
PayloadHash = Annotated[str, String(64)]
```

**Constants:**
- `MONEY_DECIMAL_PLACES = 9`
- `RATE_DECIMAL_PLACES = 18`
- `DEFAULT_ROUNDING = ROUND_HALF_UP`

UUIDs are stored as `String(36)` via the `UUIDString(TypeDecorator)` defined in `base.py`.

---

## Constraints & Indexes

### Key Constraints

```sql
-- Idempotency: exactly one entry per event
UNIQUE (idempotency_key) ON journal_entries

-- Event identity: no duplicate events
UNIQUE (id) ON events

-- Period uniqueness: no overlapping periods
-- (enforced via application logic + CHECK constraints)

-- Dimension values: unique within dimension
UNIQUE (dimension_code, code) ON dimension_values
```

### Performance Indexes

```sql
-- Event queries
CREATE INDEX idx_event_type_date ON events (event_type, effective_date);
CREATE INDEX idx_event_effective_date ON events (effective_date, occurred_at);

-- Journal queries
CREATE INDEX idx_journal_entry_effective_date ON journal_entries (effective_date);
CREATE INDEX idx_journal_entry_seq ON journal_entries (seq);
CREATE INDEX idx_journal_line_account ON journal_lines (account_id);

-- Period lookups
CREATE INDEX idx_fiscal_period_dates ON fiscal_periods (start_date, end_date);
```

---

## Migration Safety

When running migrations that need to modify protected data:

```python
from finance_kernel.db.triggers import (
    uninstall_immutability_triggers,
    install_immutability_triggers,
)

# Temporarily disable triggers
uninstall_immutability_triggers(engine)

try:
    # Perform migration
    # ... modify data ...
finally:
    # Re-enable triggers immediately
    install_immutability_triggers(engine)
```

**WARNING**: This should only be done for legitimate migrations, with appropriate audit trail and approval process. The absence of triggers is itself detectable.

---

## Database Support

| Database | Support Level | Notes |
|----------|---------------|-------|
| PostgreSQL 15+ | Full | Required. ORM + trigger immutability. |

PostgreSQL is the **only supported backend**. It provides:
- Robust ACID compliance
- Database-level triggers for defense in depth
- Proper concurrency handling
- JSONB support for dimensions

---

## Troubleshooting

### "Cannot modify posted journal entry"

**Cause**: Attempting to modify a posted entry (as designed).

**Solution**: Create a reversal entry instead:
```python
# Don't modify - reverse and re-post
reversal = create_reversal_entry(original_entry)
corrected = create_new_entry(corrected_data)
```

### "R10 Violation" from PostgreSQL

**Cause**: Raw SQL attempted to modify protected data.

**Solution**: This is working as intended. If you need to modify data:
1. For corrections: Use reversal entries
2. For migrations: Temporarily disable triggers (with approval)
3. For bugs: Fix the code, don't modify the data

### "Audit chain broken"

**Cause**: An AuditEvent was modified or deleted.

**Solution**: This indicates a serious integrity issue:
1. Restore from backup if available
2. Investigate how the modification occurred
3. Review access controls and audit logs

### Trigger not firing

**Cause**: Triggers may not be installed.

**Solution**: Verify triggers are installed:
```python
from finance_kernel.db.triggers import triggers_installed

if not triggers_installed(engine):
    raise RuntimeError("Triggers not installed. Call create_tables() first.")
```
