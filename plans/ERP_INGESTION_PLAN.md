# ERP Data Ingestion System

**Objective:** Design and implement a configuration-driven ERP data ingestion system with staging, per-record validation, and granular visibility into processing status.

**Last Update:** 2026-02-01 (v3 -- added intra-batch dependency resolution, SAVEPOINT atomicity, skip_blocked mode)
**Status:** NOT STARTED -- plan approved, awaiting implementation

**User Requirements:**
- Configuration-driven (YAML mapping definitions)
- Pre-packaged validation methods for format/type/integrity
- Staging area with clean visibility per record (not all-or-nothing)
- Easy to plan and test migrations
- Complete visibility into processing status and issues
- Every element must have a story -- full logging and audit trace integration

---

## Current State

### What Exists
- **IngestorService** (`finance_kernel/services/ingestor_service.py`): Sole boundary guard for event ingestion. Validates, hashes, deduplicates. Returns `IngestResult(ACCEPTED|DUPLICATE|REJECTED)`.
- **EventEnvelope** (`finance_kernel/domain/dtos.py`): Deep-frozen DTO for events. Payload is `MappingProxyType` (R2.5).
- **EventSchema system** (`finance_kernel/domain/schemas/`): Typed field definitions (`EventFieldType`: STRING, INTEGER, DECIMAL, BOOLEAN, DATE, DATETIME, UUID, CURRENCY, OBJECT, ARRAY) with constraints (min/max, length, pattern, allowed_values).
- **event_validator.py**: Pure validation functions: `validate_event()`, `validate_payload_against_schema()`, `validate_field_type()`, `validate_field_constraints()`, `validate_currencies_in_payload()`.
- **Config pipeline**: YAML -> `loader.py` -> `assembler.py` -> `compiler.py` -> `CompiledPolicyPack`. All schema types are frozen dataclasses in `finance_config/schema.py`.
- **ORM pattern**: `TrackedBase` provides UUID PK + `created_at/updated_at/created_by_id`. Every ORM model has `from_dto()` + `to_dto()` methods.
- **~80-100 frozen dataclass DTOs** across 18 modules (AP, AR, Inventory, Payroll, etc.).
- **~50+ ORM tables** with FK relationships. Party is the identity anchor for all external entities.

### The Gap
- **No ETL/migration/import infrastructure exists.** All data entry is event-driven via `ModulePostingService.post_event()`.
- No CSV/JSON/Excel file readers.
- No staging tables.
- No mapping configuration.
- No bulk data loading path for master data (vendors, customers, items, accounts).

---

## Architecture

### Package Placement

```
finance_ingestion/                NEW top-level package
    domain/
        types.py                  Import batch/record domain types (frozen)
        validators.py             Pre-packaged validation functions (pure)
    adapters/
        base.py                   SourceAdapter protocol
        csv_adapter.py            CSV source adapter
        json_adapter.py           JSON source adapter
    mapping/
        engine.py                 Field mapping + type coercion (pure)
    models/
        staging.py                Staging ORM models
    services/
        import_service.py         Load -> stage -> validate orchestrator
        promotion_service.py      Stage -> live promotion orchestrator
    promoters/
        base.py                   EntityPromoter protocol
        party.py                  Party promotion logic
        account.py                Account/COA promotion logic
        ap.py                     AP entity promoters (vendor, invoice, etc.)
        ar.py                     AR entity promoters
        inventory.py              Inventory entity promoters
        journal.py                Opening balance journal entry promoter

finance_config/
    schema.py                     Add ImportMappingDef, ImportFieldDef
    loader.py                     Add parse_import_mapping()
    assembler.py                  Add import_mappings fragment loading
```

### Dependency Rules
- `finance_ingestion/domain/` -- ZERO I/O, imports only from `finance_kernel/domain/`
- `finance_ingestion/mapping/` -- Pure computation, no I/O
- `finance_ingestion/adapters/` -- File I/O only, no DB
- `finance_ingestion/models/` -- Imports from `finance_kernel/db/base.py`
- `finance_ingestion/services/` -- May import from all ingestion subpackages + kernel services + module ORM
- `finance_ingestion/promoters/` -- May import module ORM models for entity creation
- Nothing in `finance_kernel/`, `finance_engines/`, `finance_modules/` imports from `finance_ingestion/`

### Data Flow

```
Source File (CSV/JSON)
    |
    v
SourceAdapter.read()          --> Iterator[dict[str, Any]]  (raw rows)
    |
    v
ImportService.load_batch()    --> ImportBatch + staged ImportRecords
    |
    v
ImportService.validate_batch()  --> each record validated individually
    |                               results stored on record
    v
[Human Review]                --> dashboard shows per-record status
    |
    v
PromotionService.promote_batch()  --> valid records -> live tables
    |                                  each record promoted individually
    v
Live ORM tables               --> Party, VendorProfile, Invoice, etc.
```

### Observability & Audit Trail

Every element of the ingestion lifecycle has a story. The system integrates with the existing `LogContext`, `AuditorService`, and structured logging infrastructure.

#### Structured Logging

All ingestion operations emit structured JSON log events via `get_logger("ingestion.*")`. The existing `LogContext` context vars propagate through every log line.

```
LogContext.bind(
    correlation_id = str(batch_id),       # Batch is the correlation scope
    producer       = "ingestion",         # Fixed producer for all import ops
    actor_id       = str(actor_id),       # Who initiated the import
    trace_id       = str(trace_id),       # Optional cross-system trace
)
```

**Log events emitted at each lifecycle point:**

| Event | Level | Extra Fields | Trigger |
|-------|-------|-------------|---------|
| `batch_created` | INFO | mapping_name, entity_type, source_filename | `load_batch()` start |
| `batch_staged` | INFO | total_records | All records staged |
| `record_staged` | DEBUG | source_row, record_id | Each record written to staging |
| `batch_validation_started` | INFO | total_records | `validate_batch()` start |
| `record_validated` | DEBUG | record_id, source_row, status, error_count | Each record validated |
| `batch_validated` | INFO | valid_records, invalid_records | All records validated |
| `preflight_computed` | INFO | blocked_count, unresolved_refs | Preflight graph computed |
| `batch_promotion_started` | INFO | valid_records | `promote_batch()` start |
| `record_promoted` | INFO | record_id, source_row, entity_type, promoted_entity_id | Each successful promotion |
| `record_promotion_failed` | WARNING | record_id, source_row, error_code, error_msg | Each failed promotion |
| `record_skipped` | INFO | record_id, source_row, reason | Duplicate/skip |
| `batch_completed` | INFO | promoted, failed, skipped, duration_ms | Batch done |
| `record_retried` | INFO | record_id, source_row, new_status | `retry_record()` |
| `mapping_test_executed` | INFO | mapping_name, sample_count, error_count | `test_mapping()` |

#### Audit Events (Hash-Chained via AuditorService)

New `AuditAction` members for ingestion lifecycle. Every significant state change gets an immutable, hash-chained audit event through the existing `AuditorService`.

```python
# New AuditAction members (add to finance_kernel/models/audit_event.py)
IMPORT_BATCH_CREATED = "import_batch_created"
IMPORT_BATCH_VALIDATED = "import_batch_validated"
IMPORT_RECORD_PROMOTED = "import_record_promoted"
IMPORT_RECORD_REJECTED = "import_record_rejected"
IMPORT_BATCH_COMPLETED = "import_batch_completed"
```

**Audit event payloads:**

| Action | entity_type | entity_id | Payload |
|--------|------------|-----------|---------|
| `import_batch_created` | `ImportBatch` | batch_id | `{mapping_name, mapping_version, mapping_hash, source_filename, total_records}` |
| `import_batch_validated` | `ImportBatch` | batch_id | `{valid_records, invalid_records, validation_duration_ms}` |
| `import_record_promoted` | `ImportRecord` | record_id | `{batch_id, source_row, entity_type, promoted_entity_id, promoted_entity_type}` |
| `import_record_rejected` | `ImportRecord` | record_id | `{batch_id, source_row, error_count, first_error_code}` |
| `import_batch_completed` | `ImportBatch` | batch_id | `{promoted, failed, skipped, total_duration_ms}` |

This means: the hash chain links ingestion events to journal postings, period closes, and every other auditable action in the system. An auditor can trace from a journal entry back through the import record, to the source file, to the raw row.

#### Canonical Import Event Stream (IM-12)

Every successful promotion emits an immutable `import.record.promoted` event through the existing `IngestorService`. This aligns ingestion with the event-sourced architecture -- every state change in the system, including data migration, has an event.

```python
# Emitted by PromotionService after each successful promotion
event_type = "import.record.promoted"
payload = {
    "batch_id": str(batch_id),
    "record_id": str(record_id),
    "source_row": source_row,
    "entity_type": entity_type,
    "promoted_entity_id": str(promoted_entity_id),
    "mapping_name": mapping_name,
    "mapping_version": mapping_version,
    "mapping_hash": mapping_hash,
}
```

This enables:
- Unified audit trail across ALL system state changes (events, journals, imports)
- Event replay for import verification
- Downstream reactions to imports (e.g., trigger approval workflows on imported invoices)

#### Trace Continuity

The full provenance chain for any imported entity:

```
Source file row N
    → ImportRecord (raw_data preserved, IM-9)
        → ImportRecord (mapped_data + validation results, IM-6)
            → AuditEvent (import_record_promoted, hash-chained)
                → Event (import.record.promoted, immutable, payload-hashed)
                    → Live entity (promoted_entity_id links back)
```

Any entity created by ingestion can be traced backwards to the exact source row in the exact source file, with every intermediate transformation preserved and hash-linked.

---

## Data Categories

### 1. Master Data (direct ORM insertion)
Entity types that are loaded directly into ORM tables because they represent reference/setup data, not economic events.

| Entity Type | Target Tables | Dependencies |
|------------|--------------|-------------|
| `account` | `accounts` | None |
| `fiscal_period` | `fiscal_periods` | None |
| `party` | `parties` | None |
| `vendor` | `parties` + `ap_vendor_profiles` | Party |
| `customer` | `parties` + `ar_customer_profiles` | Party |
| `employee` | `parties` + `payroll_employees` | Party |
| `item` | `inventory_items` | None |
| `location` | `inventory_locations` | None |

### 2. Transactional Data (direct ORM insertion for in-flight)
In-flight transactions at cutover (e.g., unpaid invoices) are loaded directly into module tables. They haven't completed their lifecycle so they don't go through the event pipeline.

| Entity Type | Target Tables | Dependencies |
|------------|--------------|-------------|
| `ap_invoice` | `ap_invoices` + `ap_invoice_lines` | Vendor, Account |
| `ar_invoice` | `ar_invoices` + `ar_invoice_lines` | Customer, Account |
| `inventory_balance` | `inventory_stock_levels` | Item, Location |

### 3. Opening Balances (via posting pipeline)
Aggregate balances at cutover date that become journal entries.

| Entity Type | Target | Dependencies |
|------------|--------|-------------|
| `opening_balance` | Event pipeline -> JournalEntry | Account, FiscalPeriod |

### Dependency Ordering (enforced by PromotionService)
```
Tier 0: account, fiscal_period
Tier 1: party
Tier 2: vendor, customer, employee, item, location
Tier 3: ap_invoice, ar_invoice, inventory_balance
Tier 4: opening_balance
```

---

## Phase 0: Domain Types (`finance_ingestion/domain/types.py`)

Pure frozen dataclasses for the import system.

### Types

```python
class ImportRecordStatus(str, Enum):
    STAGED = "staged"              # Raw data loaded
    VALIDATING = "validating"      # Validation in progress
    VALID = "valid"                # All validations passed
    INVALID = "invalid"            # One or more validations failed
    PROMOTING = "promoting"        # Promotion in progress
    PROMOTED = "promoted"          # Successfully promoted to live tables
    PROMOTION_FAILED = "promotion_failed"  # Promotion error
    SKIPPED = "skipped"            # Intentionally skipped (e.g., duplicate)

class ImportBatchStatus(str, Enum):
    LOADING = "loading"            # Source file being read
    STAGED = "staged"              # All records loaded to staging
    VALIDATING = "validating"      # Validation running
    VALIDATED = "validated"        # All records validated (some may be invalid)
    PROMOTING = "promoting"        # Promotion running
    COMPLETED = "completed"        # All promotable records promoted
    FAILED = "failed"              # Batch-level failure (e.g., file read error)

@dataclass(frozen=True)
class ImportBatch:
    batch_id: UUID
    mapping_name: str              # References ImportMappingDef.name
    entity_type: str               # Target entity type
    source_filename: str
    status: ImportBatchStatus
    total_records: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    promoted_records: int = 0
    skipped_records: int = 0
    created_at: datetime | None = None
    completed_at: datetime | None = None

@dataclass(frozen=True)
class ImportRecord:
    record_id: UUID
    batch_id: UUID
    source_row: int                # Row number in source file (1-indexed)
    entity_type: str
    status: ImportRecordStatus
    raw_data: dict[str, Any]       # Original source data
    mapped_data: dict[str, Any] | None = None   # After field mapping
    validation_errors: tuple[ValidationError, ...] = ()
    promoted_entity_id: UUID | None = None  # ID in live table after promotion
    promoted_at: datetime | None = None

    # IM-11: Mapping version snapshot (deterministic replay)
    mapping_version: int | None = None
    mapping_hash: str | None = None        # SHA-256 of mapping config at import time

@dataclass(frozen=True)
class FieldMapping:
    source: str                    # Source field name
    target: str                    # Target field name
    field_type: str                # string, integer, decimal, date, etc.
    required: bool = False
    default: Any = None
    format: str | None = None      # e.g., date format "MM/DD/YYYY"
    transform: str | None = None   # e.g., "upper", "strip", "to_decimal"

@dataclass(frozen=True)
class ImportMapping:
    name: str
    version: int
    entity_type: str               # Target entity type
    entity_subtype: str | None = None
    source_format: str             # "csv", "json"
    source_options: dict[str, Any] = field(default_factory=dict)
    field_mappings: tuple[FieldMapping, ...] = ()
    validations: tuple[ValidationRuleDef, ...] = ()
    dependency_tier: int = 0       # For promotion ordering

@dataclass(frozen=True)
class ValidationRuleDef:
    rule_type: str                 # "unique", "exists", "expression", "cross_field"
    fields: tuple[str, ...] = ()   # Fields involved
    scope: str = "batch"           # "batch", "system", "record"
    reference_entity: str | None = None  # For "exists" rules
    expression: str | None = None  # For "expression" rules
    message: str = ""
```

### Files Created
- `finance_ingestion/__init__.py`
- `finance_ingestion/domain/__init__.py`
- `finance_ingestion/domain/types.py`

---

## Phase 1: Staging ORM Models (`finance_ingestion/models/staging.py`)

### Models

```python
class ImportBatchModel(TrackedBase):
    __tablename__ = "import_batches"

    mapping_name: Mapped[str] = mapped_column(String(200))
    mapping_version: Mapped[int] = mapped_column(nullable=False)       # IM-11
    mapping_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # IM-11
    entity_type: Mapped[str] = mapped_column(String(100))
    source_filename: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(50))  # ImportBatchStatus
    total_records: Mapped[int] = mapped_column(default=0)
    valid_records: Mapped[int] = mapped_column(default=0)
    invalid_records: Mapped[int] = mapped_column(default=0)
    promoted_records: Mapped[int] = mapped_column(default=0)
    skipped_records: Mapped[int] = mapped_column(default=0)
    completed_at: Mapped[datetime | None]
    error_message: Mapped[str | None] = mapped_column(Text)

    records: Mapped[list["ImportRecordModel"]] = relationship(...)

class ImportRecordModel(TrackedBase):
    __tablename__ = "import_records"

    batch_id: Mapped[UUID] = mapped_column(ForeignKey("import_batches.id"))
    source_row: Mapped[int]
    entity_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50))  # ImportRecordStatus
    raw_data: Mapped[dict] = mapped_column(JSON)        # Original source
    mapped_data: Mapped[dict | None] = mapped_column(JSON)  # After mapping
    validation_errors: Mapped[list | None] = mapped_column(JSON)
    promoted_entity_id: Mapped[UUID | None]
    promoted_at: Mapped[datetime | None]

    # IM-11: Mapping version snapshot for deterministic replay
    mapping_version: Mapped[int | None] = mapped_column(nullable=True)
    mapping_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_import_records_batch_status", "batch_id", "status"),
        Index("ix_import_records_entity_type", "entity_type"),
    )
```

### Design Decisions
- Raw data stored as JSON (schema-agnostic staging)
- Validation errors stored as JSON array on the record (fully self-contained)
- Mapped data stored separately from raw data (both preserved for auditability)
- Batch-level counters for quick status queries without counting records
- mapping_version + mapping_hash frozen at batch creation for replay determinism (IM-11)

### Files Created
- `finance_ingestion/models/__init__.py`
- `finance_ingestion/models/staging.py`

### Files Modified
- `finance_kernel/models/audit_event.py` (add IMPORT_* AuditAction members)

---

## Phase 2: Source Adapters

### SourceAdapter Protocol

```python
class SourceAdapter(Protocol):
    def read(self, source_path: Path, options: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Yield one dict per source record."""
        ...

    def probe(self, source_path: Path, options: dict[str, Any]) -> SourceProbe:
        """Quick probe: row count, detected columns, sample rows."""
        ...

@dataclass(frozen=True)
class SourceProbe:
    row_count: int
    columns: tuple[str, ...]
    sample_rows: tuple[dict[str, Any], ...]  # First 5 rows
    encoding: str | None = None
    detected_delimiter: str | None = None
```

### CSV Adapter
- Uses Python `csv.DictReader`
- Configurable: delimiter, encoding, has_header, quoting, skip_rows
- Handles BOM, encoding detection
- Streams rows (does not load entire file into memory)

### JSON Adapter
- Handles JSON array (file is `[{...}, {...}, ...]`)
- Handles JSON Lines (one JSON object per line)
- Configurable: `json_path` for nested arrays (e.g., `"data.records"`)

### Files Created
- `finance_ingestion/adapters/__init__.py`
- `finance_ingestion/adapters/base.py`
- `finance_ingestion/adapters/csv_adapter.py`
- `finance_ingestion/adapters/json_adapter.py`

---

## Phase 3: Import Mapping Config

### New Config Types (`finance_config/schema.py`)

```python
@dataclass(frozen=True)
class ImportFieldDef:
    source: str
    target: str
    field_type: str = "string"     # string, integer, decimal, boolean, date, datetime, uuid, currency
    required: bool = False
    default: Any = None
    format: str | None = None      # Date/time format string
    transform: str | None = None   # "upper", "lower", "strip", "trim", "to_decimal"

@dataclass(frozen=True)
class ImportValidationDef:
    rule_type: str                 # "unique", "exists", "expression", "cross_field"
    fields: tuple[str, ...] = ()
    scope: str = "batch"
    reference_entity: str | None = None
    expression: str | None = None
    message: str = ""

@dataclass(frozen=True)
class ImportSourceOptionsDef:
    delimiter: str = ","
    encoding: str = "utf-8"
    has_header: bool = True
    skip_rows: int = 0
    json_path: str | None = None   # For JSON: path to array

@dataclass(frozen=True)
class ImportMappingDef:
    name: str
    version: int = 1
    entity_type: str = ""
    entity_subtype: str | None = None
    source_format: str = "csv"
    source_options: ImportSourceOptionsDef = field(default_factory=ImportSourceOptionsDef)
    field_mappings: tuple[ImportFieldDef, ...] = ()
    validations: tuple[ImportValidationDef, ...] = ()
    dependency_tier: int = 0
```

### AccountingConfigurationSet Addition
```python
@dataclass(frozen=True)
class AccountingConfigurationSet:
    ...
    import_mappings: tuple[ImportMappingDef, ...] = ()  # NEW
```

### Loader Addition
New `parse_import_mapping()` function + `import_mappings` section in assembler.

### YAML Example

```yaml
# config/import_mappings/sap_vendors.yaml
import_mappings:
  - name: "sap_vendors"
    version: 1
    entity_type: "vendor"
    source_format: "csv"
    source_options:
      delimiter: ","
      encoding: "utf-8"
      has_header: true
    dependency_tier: 2
    field_mappings:
      - source: "LIFNR"
        target: "code"
        field_type: "string"
        required: true
        transform: "strip"
      - source: "NAME1"
        target: "name"
        field_type: "string"
        required: true
      - source: "STCD1"
        target: "tax_id"
        field_type: "string"
      - source: "ZTERM"
        target: "payment_terms_days"
        field_type: "integer"
        default: 30
      - source: "WAERS"
        target: "currency"
        field_type: "currency"
        default: "USD"
    validations:
      - rule_type: "unique"
        fields: ["code"]
        scope: "batch"
        message: "Duplicate vendor code within batch"
      - rule_type: "unique"
        fields: ["code"]
        scope: "system"
        message: "Vendor code already exists in system"
```

### Files Modified
- `finance_config/schema.py` (add `ImportFieldDef`, `ImportValidationDef`, `ImportSourceOptionsDef`, `ImportMappingDef`, field on `AccountingConfigurationSet`)
- `finance_config/loader.py` (add `parse_import_mapping()`)
- `finance_config/assembler.py` (add `import_mappings/` fragment directory)

---

## Phase 4: Mapping Engine (`finance_ingestion/mapping/engine.py`)

Pure transformation: source dict -> target dict using field mappings.

### Functions

```python
def apply_mapping(
    raw_data: dict[str, Any],
    field_mappings: tuple[FieldMapping, ...],
) -> MappingResult:
    """Apply field mappings to a raw source record. Pure function."""

def coerce_type(
    value: Any,
    field_type: str,
    format: str | None = None,
) -> CoercionResult:
    """Coerce a value to the target type. Pure function."""

def apply_transform(
    value: Any,
    transform: str,
) -> Any:
    """Apply a named transform (upper, lower, strip, to_decimal). Pure function."""

@dataclass(frozen=True)
class MappingResult:
    success: bool
    mapped_data: dict[str, Any] = field(default_factory=dict)
    errors: tuple[ValidationError, ...] = ()

@dataclass(frozen=True)
class CoercionResult:
    success: bool
    value: Any = None
    error: ValidationError | None = None
```

### Supported Types
- `string` -- strip, encode
- `integer` -- int() with error handling
- `decimal` -- Decimal() (never float)
- `boolean` -- true/false/yes/no/1/0
- `date` -- configurable format (default ISO), auto-detect common formats
- `datetime` -- ISO 8601
- `uuid` -- UUID parse
- `currency` -- ISO 4217 validation via CurrencyRegistry

### Supported Transforms
- `strip` / `trim` -- whitespace removal
- `upper` / `lower` -- case conversion
- `to_decimal` -- explicit Decimal conversion
- `normalize_date` -- standardize to ISO format

### Files Created
- `finance_ingestion/mapping/__init__.py`
- `finance_ingestion/mapping/engine.py`

---

## Phase 5: Validation Pipeline (`finance_ingestion/domain/validators.py`)

### Pre-packaged Validators

```python
# Record-level validators (one record at a time)
def validate_required_fields(record: dict, mappings: tuple[FieldMapping, ...]) -> list[ValidationError]
def validate_field_types(record: dict, mappings: tuple[FieldMapping, ...]) -> list[ValidationError]
def validate_field_constraints(record: dict, constraints: tuple[FieldConstraint, ...]) -> list[ValidationError]
def validate_currency_codes(record: dict, currency_fields: tuple[str, ...]) -> list[ValidationError]
def validate_decimal_precision(record: dict, decimal_fields: tuple[str, ...]) -> list[ValidationError]
def validate_date_ranges(record: dict, date_fields: tuple[str, ...]) -> list[ValidationError]

# Cross-record validators (need batch context)
def validate_batch_uniqueness(records: Sequence[dict], fields: tuple[str, ...]) -> dict[int, list[ValidationError]]

# Referential integrity validators (need DB session + staging context)
def validate_entity_exists(
    session: Session,
    entity_type: str,
    field: str,
    value: Any,
    staged_records: Sequence[dict] | None = None,  # Valid staged records in same batch
) -> ValidationError | None:
    """
    Check that a referenced entity exists.

    Resolution order (IM-15):
    1. Check live tables first (already promoted / pre-existing)
    2. If not found, check valid staged records in the same batch
       (for intra-batch dependencies, e.g., vendor referencing party in same import)
    3. If still not found, return ValidationError

    This handles the common migration scenario where master data (parties)
    and dependent data (vendors) arrive in the same import batch.
    """

def validate_system_uniqueness(session: Session, entity_type: str, fields: dict[str, Any]) -> ValidationError | None
```

### Intra-Batch Dependency Resolution (IM-15)

When a batch contains entities across multiple dependency tiers (e.g., parties at tier 1 and vendors at tier 2), the "exists" validator must resolve references against **both** live tables and valid staged records within the same batch. Without this, a vendor referencing a party in the same import file would fail validation because the party hasn't been promoted yet.

**Resolution strategy:**
1. Validation runs in dependency-tier order within a batch (tier 0 records validated first, then tier 1, etc.)
2. `validate_entity_exists()` receives the set of valid staged records from lower tiers as context
3. A staged record satisfies an "exists" check only if it has status VALID (already passed its own validation)
4. The preflight graph (Phase 7) uses the same resolution logic -- a blocker is only reported if the missing entity is absent from **both** live tables and valid staged records

**Constraints:**
- Circular intra-batch dependencies are impossible by construction (dependency tiers are a DAG)
- An invalid staged record cannot satisfy a dependency -- only VALID records count
- This does NOT relax IM-1 (staging isolation) -- no FKs are created; resolution is query-based

### Entity-Specific Validators

Pre-packaged validation profiles for common entity types. These know the business rules for each entity.

```python
ENTITY_VALIDATORS: dict[str, tuple[Callable, ...]] = {
    "party": (validate_party_code, validate_party_type),
    "vendor": (validate_vendor_code, validate_payment_terms, validate_vendor_party_exists),
    "customer": (validate_customer_code, validate_credit_limit, validate_customer_party_exists),
    "account": (validate_account_code_format, validate_account_type, validate_normal_balance),
    "ap_invoice": (validate_invoice_total, validate_invoice_dates, validate_invoice_vendor_exists),
    "item": (validate_item_code, validate_unit_of_measure),
    "opening_balance": (validate_balance_accounts, validate_balance_amounts, validate_debits_credits),
}
```

### Files Created
- `finance_ingestion/domain/validators.py`

---

## Phase 6: Import Service (`finance_ingestion/services/import_service.py`)

Orchestrates the load -> stage -> validate flow.

```python
class ImportService:
    def __init__(
        self,
        session: Session,
        clock: Clock | None = None,
        adapters: dict[str, SourceAdapter] | None = None,
    ):
        ...

    def probe_source(
        self,
        source_path: Path,
        mapping: ImportMapping,
    ) -> SourceProbe:
        """Preview source file: row count, columns, sample data."""

    def load_batch(
        self,
        source_path: Path,
        mapping: ImportMapping,
        actor_id: UUID,
    ) -> ImportBatch:
        """
        Load source file into staging.

        Flow:
        1. Create ImportBatch (LOADING)
        2. Read source via adapter -> raw dicts
        3. Apply field mappings -> mapped dicts
        4. Create ImportRecord per row (STAGED)
        5. Update batch status (STAGED)
        """

    def validate_batch(
        self,
        batch_id: UUID,
    ) -> ImportBatch:
        """
        Validate all staged records in a batch.

        Flow:
        1. Load batch + records
        2. Group records by dependency tier
        3. For each tier (ascending order -- IM-15):
           a. Run record-level validators (type, required, format)
           b. Run cross-record validators (batch uniqueness)
           c. Run referential integrity validators (system uniqueness, FK exists)
              - "exists" checks resolve against live tables + valid staged records
                from lower tiers in the same batch (IM-15)
           d. Update each record status (VALID/INVALID)
        4. Update batch counters
        """

    def get_batch_summary(self, batch_id: UUID) -> ImportBatch:
        """Get batch with summary counts."""

    def get_batch_errors(self, batch_id: UUID) -> list[ImportRecord]:
        """Get all invalid records with their errors."""

    def get_record_detail(self, record_id: UUID) -> ImportRecord:
        """Get full record with raw data, mapped data, and errors."""

    def retry_record(
        self,
        record_id: UUID,
        corrected_data: dict[str, Any],
    ) -> ImportRecord:
        """
        Re-validate a single record with corrected data.
        Updates raw_data, re-runs mapping and validation.
        """
```

### Files Created
- `finance_ingestion/services/__init__.py`
- `finance_ingestion/services/import_service.py`

---

## Phase 7: Promotion Service (`finance_ingestion/services/promotion_service.py`)

Promotes valid staged records to live ORM tables.

```python
class PromotionService:
    def __init__(
        self,
        session: Session,
        promoters: dict[str, EntityPromoter],
        clock: Clock | None = None,
    ):
        ...

    def promote_batch(
        self,
        batch_id: UUID,
        actor_id: UUID,
        dry_run: bool = False,
        skip_blocked: bool = False,
    ) -> PromotionResult:
        """
        Promote all valid records in a batch.

        Args:
            skip_blocked: If True, skip records whose dependencies are unresolved
                          (status -> SKIPPED) and promote only ready records.
                          If False (default), unresolved dependencies cause
                          PROMOTION_FAILED on the blocked records.

        Flow:
        1. Load batch + valid records
        2. Compute preflight graph (identify ready vs. blocked)
        3. If skip_blocked: filter to ready records only; mark blocked as SKIPPED
        4. Sort ready records by dependency tier
        5. For each record (within a SAVEPOINT -- IM-16):
           a. BEGIN SAVEPOINT
           b. Look up EntityPromoter for entity_type
           c. Call promoter.promote(mapped_data)
           d. On success: RELEASE SAVEPOINT, PROMOTED + record promoted_entity_id
           e. On failure: ROLLBACK TO SAVEPOINT, PROMOTION_FAILED + error stored
        6. Update batch counters
        """

    def promote_record(
        self,
        record_id: UUID,
        actor_id: UUID,
        dry_run: bool = False,
    ) -> ImportRecord:
        """Promote a single record (within its own SAVEPOINT)."""

@dataclass(frozen=True)
class PromotionResult:
    batch_id: UUID
    total_attempted: int
    promoted: int
    failed: int
    skipped: int
    errors: tuple[PromotionError, ...] = ()

@dataclass(frozen=True)
class PromotionError:
    record_id: UUID
    source_row: int
    error_code: str
    message: str
```

### Referential Preflight Graph

Before promotion, `PromotionService` computes a dependency DAG of unresolved references and surfaces it as a navigable structure. This turns debugging from "hunt through records" into "navigate a graph."

```python
def compute_preflight_graph(
    self,
    batch_id: UUID,
) -> PreflightGraph:
    """
    Compute dependency graph of unresolved references.

    Returns a graph showing:
    - Which records are ready to promote (all deps resolved)
    - Which records are blocked and WHY (missing vendor, missing account, etc.)
    - Aggregated blockers (e.g., "1 missing vendor blocks 12 invoices")
    """

@dataclass(frozen=True)
class PreflightGraph:
    batch_id: UUID
    ready_count: int
    blocked_count: int
    blockers: tuple[PreflightBlocker, ...] = ()

@dataclass(frozen=True)
class PreflightBlocker:
    missing_entity_type: str           # "vendor", "account", etc.
    missing_key: str                   # "VENDOR-001", "4100"
    blocked_records: tuple[UUID, ...]  # Record IDs blocked by this missing entity
    blocked_count: int
```

**Example output:**
```
PreflightGraph:
  ready: 87 records
  blocked: 13 records
  blockers:
    - missing vendor "V-9001" blocks 8 invoices (rows 12, 15, 23, 31, 44, 56, 78, 91)
    - missing vendor "V-9002" blocks 3 invoices (rows 19, 33, 67)
    - missing account "6500" blocks 2 opening balances (rows 101, 102)
```

### SAVEPOINT Atomicity (IM-16)

Each record promotion is wrapped in a database SAVEPOINT. This is critical for **compound promoters** (VendorPromoter, CustomerPromoter, APInvoicePromoter) that touch multiple tables in a single promotion:

```python
# Inside promote_batch() loop:
for record in ready_records:
    savepoint = session.begin_nested()  # SAVEPOINT
    try:
        result = promoter.promote(record.mapped_data, session, actor_id, clock)
        if result.success:
            savepoint.commit()          # RELEASE SAVEPOINT
            record.status = PROMOTED
        else:
            savepoint.rollback()        # ROLLBACK TO SAVEPOINT
            record.status = PROMOTION_FAILED
    except Exception as exc:
        savepoint.rollback()            # ROLLBACK TO SAVEPOINT
        record.status = PROMOTION_FAILED
        record.error = str(exc)
```

**Why this matters:** If VendorPromoter creates a Party row (step 1) but fails on VendorProfileModel creation (step 2), the SAVEPOINT rollback undoes the Party for that record only. Other records' promotions are unaffected. The outer transaction continues.

This is the mechanism behind IM-8 (rollback safety). Without SAVEPOINTs, a compound promoter failure would leave orphaned Party rows or require complex manual cleanup.

### Files Created
- `finance_ingestion/services/promotion_service.py`

---

## Phase 7b: Mapping Test Harness

A pure function that validates mappings against sample data without touching staging tables. This lets users verify their YAML mapping configuration works correctly before running an actual import.

```python
# finance_ingestion/mapping/test_harness.py

def test_mapping(
    mapping: ImportMapping,
    sample_rows: list[dict[str, Any]],
) -> MappingTestReport:
    """
    Test a mapping configuration against sample data. Pure function.

    Runs the full mapping + validation pipeline on sample rows
    without writing to staging tables. Returns a detailed report.
    """

@dataclass(frozen=True)
class MappingTestReport:
    mapping_name: str
    mapping_version: int
    sample_count: int
    success_count: int
    error_count: int
    rows: tuple[MappingTestRow, ...] = ()
    summary_errors: tuple[str, ...] = ()  # Aggregated error messages

@dataclass(frozen=True)
class MappingTestRow:
    source_row: int
    success: bool
    raw_data: dict[str, Any]
    mapped_data: dict[str, Any] | None = None
    errors: tuple[ValidationError, ...] = ()
```

**Usage pattern:**
```python
# 1. Probe the source file
probe = import_service.probe_source(path, mapping)
# Returns: SourceProbe(row_count=1500, columns=("LIFNR","NAME1",...), sample_rows=[...])

# 2. Test the mapping against sample rows (no DB, no staging)
report = test_mapping(mapping, probe.sample_rows)
# Returns: MappingTestReport(success_count=4, error_count=1, rows=[...])

# 3. If satisfied, run the real import
batch = import_service.load_batch(path, mapping, actor_id)
```

This becomes a sales feature: customers can validate their migration mappings in minutes, not days.

### Files Created
- `finance_ingestion/mapping/test_harness.py`

---

## Phase 8: Entity Promoters

### EntityPromoter Protocol

```python
class EntityPromoter(Protocol):
    entity_type: str

    def promote(
        self,
        mapped_data: dict[str, Any],
        session: Session,
        actor_id: UUID,
        clock: Clock,
    ) -> PromoteResult:
        """Create the live entity from mapped data."""
        ...

    def check_duplicate(
        self,
        mapped_data: dict[str, Any],
        session: Session,
    ) -> bool:
        """Check if this entity already exists."""
        ...

@dataclass(frozen=True)
class PromoteResult:
    success: bool
    entity_id: UUID | None = None
    error: str | None = None
```

### Promoters (one per entity type)

| Promoter | Entity Type | Creates |
|----------|------------|---------|
| `PartyPromoter` | `party` | `Party` row |
| `AccountPromoter` | `account` | `Account` row |
| `VendorPromoter` | `vendor` | `Party` + `VendorProfileModel` rows |
| `CustomerPromoter` | `customer` | `Party` + customer profile rows |
| `EmployeePromoter` | `employee` | `Party` + employee profile rows |
| `ItemPromoter` | `item` | `InventoryItemModel` row |
| `LocationPromoter` | `location` | `InventoryLocationModel` row |
| `APInvoicePromoter` | `ap_invoice` | `APInvoiceModel` + lines |
| `ARInvoicePromoter` | `ar_invoice` | `ARInvoiceModel` + lines |
| `OpeningBalancePromoter` | `opening_balance` | Via `ModulePostingService.post_event()` |

### Key Design: Vendor/Customer Promoters Handle Compound Creation

The `VendorPromoter` knows that creating a vendor requires:
1. Check if Party already exists (by code or external ID)
2. Create Party row if needed
3. Create VendorProfileModel row linked to Party

This keeps the mapping YAML simple -- the user maps vendor fields and the promoter handles the multi-table dance.

**SAVEPOINT contract:** Compound promoters do NOT manage their own transactions. They execute within the SAVEPOINT provided by `PromotionService` (IM-16). If any step fails, the promoter raises an exception and `PromotionService` handles the SAVEPOINT rollback. Promoters are responsible only for the ORM operations, not the transaction boundary.

### Files Created
- `finance_ingestion/promoters/__init__.py`
- `finance_ingestion/promoters/base.py`
- `finance_ingestion/promoters/party.py`
- `finance_ingestion/promoters/account.py`
- `finance_ingestion/promoters/ap.py`
- `finance_ingestion/promoters/ar.py`
- `finance_ingestion/promoters/inventory.py`
- `finance_ingestion/promoters/journal.py`

---

## Phase 9: Tests

### Test Files

| File | Scope | Tests |
|------|-------|-------|
| `tests/ingestion/test_domain_types.py` | Domain type construction, immutability, enum values | ~15 |
| `tests/ingestion/test_mapping_engine.py` | Field mapping, type coercion, transforms | ~25 |
| `tests/ingestion/test_mapping_harness.py` | Mapping test harness, report generation | ~15 |
| `tests/ingestion/test_validators.py` | Pre-packaged validators, intra-batch dependency resolution | ~35 |
| `tests/ingestion/test_csv_adapter.py` | CSV reading, encoding, edge cases | ~15 |
| `tests/ingestion/test_json_adapter.py` | JSON/JSONL reading, nested paths | ~10 |
| `tests/ingestion/test_staging_orm.py` | ORM round-trip, mapping snapshot columns | ~15 |
| `tests/ingestion/test_import_service.py` | Load, validate, retry flows, logging | ~30 |
| `tests/ingestion/test_promotion_service.py` | Promotion flow, SAVEPOINT atomicity, preflight graph, skip_blocked, event stream, audit events | ~40 |
| `tests/ingestion/test_promoters.py` | Per-entity-type promotion | ~25 |
| `tests/ingestion/test_config_loading.py` | YAML mapping loading, validation | ~10 |
| `tests/ingestion/test_audit_trail.py` | Audit event creation, hash chain, trace continuity | ~15 |
| `tests/architecture/test_ingestion_boundary.py` | Import boundary enforcement | ~5 |

### Key Test Scenarios

**Core Pipeline:**
1. CSV with missing required fields -> record marked INVALID with clear error
2. CSV with type coercion failures (bad date format) -> per-field error
3. Batch with 100 records, 3 invalid -> 97 VALID, 3 INVALID, batch proceeds
4. Duplicate vendor code within batch -> cross-record validation catches it
5. Vendor referencing non-existent party -> referential integrity error
6. Retry invalid record with corrected data -> re-validates to VALID
7. Promote batch with dependency ordering -> tier 0 before tier 1
8. Promote vendor -> creates Party + VendorProfile in single transaction
9. Promote duplicate vendor -> SKIPPED (idempotent)
10. Promotion failure rolls back individual record, not entire batch
11. Dry-run promotion returns what would happen without writing
12. Opening balance promotion -> flows through event pipeline -> journal entry created
13. probe_source returns column list + sample rows for mapping verification
14. JSON Lines format reads correctly
15. Empty file -> batch with 0 records, COMPLETED status

**Mapping Snapshots (IM-11):**
16. mapping_version + mapping_hash stored on batch at creation
17. mapping_version + mapping_hash propagated to every record
18. Replay test: same source data + same mapping version = identical mapped output
19. Different mapping version on same source data = different mapped output flagged

**Audit & Event Stream (IM-12, IM-13):**
20. Batch creation emits IMPORT_BATCH_CREATED audit event with mapping snapshot
21. Batch validation emits IMPORT_BATCH_VALIDATED audit event with counts
22. Each promotion emits IMPORT_RECORD_PROMOTED audit event with entity link
23. Each promotion emits import.record.promoted event via IngestorService
24. Batch completion emits IMPORT_BATCH_COMPLETED audit event with summary
25. Audit events are hash-chained (prev_hash links to prior audit event)
26. Promotion events appear in unified event stream alongside journal postings

**Trace Continuity (IM-14):**
27. Full provenance trace: live entity -> ImportRecord -> ImportBatch -> source file
28. LogContext.correlation_id = batch_id propagated through all structured log events
29. Structured log events emitted at every lifecycle transition (see table above)

**Preflight Graph:**
30. Preflight identifies "1 missing vendor blocks 12 invoices"
31. Preflight returns ready_count + blocked_count before promotion starts
32. After resolving missing reference and re-running, blocked records become ready

**Mapping Test Harness:**
33. test_mapping() with valid sample rows -> all success, mapped data returned
34. test_mapping() with invalid rows -> per-row errors with field-level detail
35. test_mapping() does not write to database (pure function)
36. test_mapping() + probe_source() compose naturally (probe provides sample_rows)

**Intra-Batch Dependencies (IM-15):**
37. Batch with parties (tier 1) + vendors (tier 2) -> vendors pass "exists" check against staged parties
38. Batch with vendors only (no parties) -> vendors fail "exists" check (party not in live or staged)
39. Vendor references party that is INVALID in staging -> fails (only VALID staged records satisfy deps)
40. Validation runs in tier order: tier 0 validated first, then tier 1 sees tier 0 results

**SAVEPOINT Atomicity (IM-16):**
41. VendorPromoter fails on VendorProfile after creating Party -> SAVEPOINT rollback undoes Party, no orphan
42. APInvoicePromoter fails on line 3 of 5 -> entire invoice + all lines rolled back, record PROMOTION_FAILED
43. Record N promotion failure does not affect record N-1 (already committed) or N+1 (still pending)

**skip_blocked Mode:**
44. promote_batch(skip_blocked=True) with 2 blocked records -> 2 SKIPPED, rest PROMOTED
45. promote_batch(skip_blocked=False) with 2 blocked records -> 2 PROMOTION_FAILED with dependency error
46. After creating missing reference, re-promote skipped records -> PROMOTED

---

## Invariants

| Rule | Name | Summary |
|------|------|---------|
| IM-1 | Staging isolation | Staging tables are completely separate from live tables. No FK from staging to live. |
| IM-2 | Record independence | Each record validates and promotes independently. One failure does not block others. |
| IM-3 | Batch traceability | Every promoted record is traceable to source batch + source row number. |
| IM-4 | Mapping determinism | Same source data + same mapping config = same mapped output. Mapping engine is pure. |
| IM-5 | Dependency ordering | Entity types promoted in dependency tier order. Tier N fully promoted before tier N+1. |
| IM-6 | Validation completeness | All validation results stored on the record. Never discarded or summarized. |
| IM-7 | Promotion idempotency | Promoting same record twice produces same result or SKIPPED. No duplicates. |
| IM-8 | Rollback safety | Failed promotion rolls back individual record transaction, not entire batch. |
| IM-9 | Raw data preservation | Original source data preserved unmodified alongside mapped data. |
| IM-10 | Decimal fidelity | All monetary amounts use Decimal (never float). Type coercion to Decimal is explicit. |
| IM-11 | Mapping version snapshot | `mapping_version` + `mapping_hash` snapshotted on every batch and record at import time. Enables deterministic replay. Mirrors R21 for journal entries and AL-2 for approval requests. |
| IM-12 | Canonical event stream | Every successful promotion emits an immutable `import.record.promoted` event via IngestorService. Unifies ingestion with the event-sourced audit trail. |
| IM-13 | Audit chain integration | All ingestion lifecycle transitions produce hash-chained AuditEvents via AuditorService. Import provenance is tamper-evident and linked to the same chain as journal postings. |
| IM-14 | Trace continuity | Every imported entity is traceable from live table → promoted_entity_id → ImportRecord → ImportBatch → source file + row. LogContext propagates batch_id as correlation_id through all structured log events. |
| IM-15 | Intra-batch dependency resolution | "Exists" validators resolve references against both live tables AND valid staged records in the same batch (tier-ordered). A vendor can reference a party in the same import file without failing validation. |
| IM-16 | SAVEPOINT atomicity | Each record promotion is wrapped in a database SAVEPOINT. Compound promoter failures roll back only that record's writes (e.g., orphaned Party from failed VendorProfile creation is impossible). Outer transaction continues for remaining records. |

---

## Key Decisions

1. **New `finance_ingestion/` top-level package** -- Ingestion is cross-cutting, too large for a single service, has its own domain types and adapters.
2. **JSON staging** -- Raw and mapped data stored as JSON columns. Schema-agnostic staging avoids needing per-entity staging tables.
3. **Per-record status** -- Each record tracks its own lifecycle independently. Dashboard queries filter by status.
4. **EntityPromoter protocol** -- Pluggable per-entity-type promotion logic. Adding a new entity type = new promoter class only.
5. **Compound promoters** -- VendorPromoter handles Party + VendorProfile creation. User maps flat fields; promoter handles multi-table.
6. **Opening balances via event pipeline** -- Maintains all posting invariants (R1-R24). Treated as economic events, not raw inserts.
7. **Mapping config in YAML** -- Follows existing `finance_config/` patterns. Import mappings are configuration, not code.
8. **Probe before import** -- `probe_source()` lets users verify column detection and preview data before committing to a full import.
9. **Retry individual records** -- Failed records can be corrected and re-validated without re-importing the entire batch.
10. **Dry-run promotion** -- `dry_run=True` parameter validates everything would work without writing to live tables.
11. **Mapping version snapshotting** -- `mapping_version` + `mapping_hash` frozen on batch/record at import time. Same pattern as R21 for journals and AL-2 for approvals. Enables deterministic replay of imports.
12. **Canonical event stream** -- Every promotion emits `import.record.promoted` via IngestorService. Ingestion joins the unified event model rather than being a side-channel.
13. **Referential preflight graph** -- Before promotion, compute and surface a dependency DAG. "1 missing vendor blocks 12 invoices" instead of 12 individual error messages.
14. **Mapping test harness** -- Pure `test_mapping()` function for pre-import validation. No DB, no staging tables. Customers validate mappings in minutes.
15. **Full audit chain integration** -- Ingestion lifecycle events enter the same hash-chained AuditEvent table as journal postings and period closes. One chain, one truth.
16. **Structured logging with LogContext** -- `batch_id` propagated as `correlation_id` through all log events. Every log line in an import operation carries batch context automatically.
17. **Intra-batch dependency resolution** -- "Exists" validators check valid staged records in the same batch, not just live tables. Validation runs in tier order within a batch so lower-tier records are validated first. Eliminates the false-negative problem where a vendor and its party arrive in the same file.
18. **SAVEPOINT per record promotion** -- Each record promotion gets a `session.begin_nested()` SAVEPOINT. Compound promoters (VendorPromoter, APInvoicePromoter) rely on the SAVEPOINT for atomicity rather than managing their own transactions. Promoters are pure ORM operations; PromotionService owns the transaction boundary.
19. **skip_blocked promotion mode** -- `promote_batch(skip_blocked=True)` promotes only ready records and marks blocked records as SKIPPED. This avoids the false choice between "promote everything or nothing" when some references are unresolvable. Blocked records can be retried after the missing references are created.

---

## Implementation Order

| Phase | Description | Depends On |
|-------|------------|------------|
| 0 | Domain types | -- |
| 1 | Staging ORM models + AuditAction additions | Phase 0 |
| 2 | Source adapters (CSV, JSON) | -- |
| 3 | Import mapping config (YAML schema) | -- |
| 4 | Mapping engine + test harness (pure) | Phases 0, 3 |
| 5 | Validation pipeline | Phases 0, 4 |
| 6 | Import service (with structured logging) | Phases 1, 2, 4, 5 |
| 7 | Promotion service (with preflight graph, event stream, audit events) | Phases 1, 6 |
| 8 | Entity promoters | Phase 7 |
| 9 | Tests | All phases |

Phases 2 and 3 can be parallelized. Phases 4 and 5 can be parallelized after Phase 0.

---

## YAML Configuration Examples

### Vendor Import (SAP)
```yaml
import_mappings:
  - name: "sap_vendors"
    version: 1
    entity_type: "vendor"
    source_format: "csv"
    dependency_tier: 2
    source_options:
      delimiter: ","
      encoding: "utf-8"
      has_header: true
    field_mappings:
      - source: "LIFNR"
        target: "code"
        field_type: "string"
        required: true
        transform: "strip"
      - source: "NAME1"
        target: "name"
        field_type: "string"
        required: true
      - source: "STCD1"
        target: "tax_id"
        field_type: "string"
      - source: "ZTERM"
        target: "payment_terms_days"
        field_type: "integer"
        default: 30
      - source: "WAERS"
        target: "currency"
        field_type: "currency"
        default: "USD"
    validations:
      - rule_type: "unique"
        fields: ["code"]
        scope: "batch"
      - rule_type: "unique"
        fields: ["code"]
        scope: "system"
```

### Chart of Accounts Import
```yaml
import_mappings:
  - name: "legacy_coa"
    version: 1
    entity_type: "account"
    source_format: "csv"
    dependency_tier: 0
    field_mappings:
      - source: "Account_Number"
        target: "code"
        field_type: "string"
        required: true
      - source: "Account_Name"
        target: "name"
        field_type: "string"
        required: true
      - source: "Type"
        target: "account_type"
        field_type: "string"
        required: true
      - source: "Normal_Balance"
        target: "normal_balance"
        field_type: "string"
        required: true
      - source: "Parent_Account"
        target: "parent_code"
        field_type: "string"
    validations:
      - rule_type: "unique"
        fields: ["code"]
        scope: "batch"
      - rule_type: "expression"
        expression: "account_type in ('asset','liability','equity','revenue','expense')"
        message: "Invalid account type"
```

### Opening Balances Import
```yaml
import_mappings:
  - name: "opening_balances"
    version: 1
    entity_type: "opening_balance"
    source_format: "csv"
    dependency_tier: 4
    field_mappings:
      - source: "Account_Code"
        target: "account_code"
        field_type: "string"
        required: true
      - source: "Debit_Amount"
        target: "debit_amount"
        field_type: "decimal"
        default: "0"
      - source: "Credit_Amount"
        target: "credit_amount"
        field_type: "decimal"
        default: "0"
      - source: "Currency"
        target: "currency"
        field_type: "currency"
        default: "USD"
      - source: "Description"
        target: "description"
        field_type: "string"
    validations:
      - rule_type: "exists"
        fields: ["account_code"]
        reference_entity: "account"
        message: "Account does not exist"
      - rule_type: "expression"
        expression: "debit_amount > 0 or credit_amount > 0"
        message: "At least one of debit or credit must be positive"
```

---

## Verification

1. **Unit tests pass**: `python3 -m pytest tests/ingestion/ -v --tb=short`
2. **Architecture tests pass**: `python3 -m pytest tests/architecture/ -v --tb=short`
3. **Round-trip test**: Load CSV -> stage -> validate -> promote -> verify ORM records
4. **Partial failure test**: Batch with mix of valid/invalid records -> valid promoted, invalid preserved with errors
5. **Dependency ordering test**: Import vendors + accounts in same batch -> accounts (tier 0) promoted before vendors (tier 2)
6. **Retry test**: Invalid record -> correct data -> re-validate -> VALID -> promote
7. **Probe test**: probe_source() returns accurate column list and row count
8. **Idempotency test**: Promote same batch twice -> second time SKIPs already-promoted records
9. **Opening balance test**: Promote opening_balance records -> journal entries created via posting pipeline -> trial balance correct
10. **Audit chain test**: Verify AuditEvents created for batch_created -> batch_validated -> record_promoted -> batch_completed, all hash-chained
11. **Event stream test**: Verify `import.record.promoted` events emitted, payload contains batch/record/mapping references
12. **Provenance trace test**: Given a promoted entity, trace back to source file row via record_id -> batch_id -> source_filename + source_row
13. **Mapping snapshot test**: Verify mapping_version + mapping_hash frozen on batch/record, verified on replay
14. **Preflight test**: Compute preflight graph with missing references, verify blocked count and blocker detail
15. **Test harness test**: Run test_mapping() on sample data, verify report accuracy without database side effects
16. **Structured logging test**: Verify LogContext.correlation_id = batch_id propagated through all log events in a batch operation
17. **Intra-batch dependency test**: Batch with parties + vendors in same file -> vendors validated successfully against staged parties
18. **SAVEPOINT rollback test**: Compound promoter failure -> only that record's writes rolled back, no orphaned rows
19. **skip_blocked test**: promote_batch(skip_blocked=True) skips blocked records, promotes ready ones, re-promote after fix succeeds
