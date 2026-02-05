# Finance Ingestion

Configuration-driven ERP data ingestion for **migrations** and **bulk master-data loading**. Load from CSV, JSON, or XLSX into staging, validate and map fields using config-defined templates, then promote to live kernel and module tables (accounts, parties, AP/AR, journal). This package is separate from the kernel’s **event ingestion** (IngestorService), which handles business events for the posting pipeline.

---

## How it works

1. **Load** — A source adapter (CSV, JSON, or XLSX) reads a file and streams one record dict per row. No database or kernel dependency in adapters.
2. **Stage** — Records are written to staging tables (`ImportBatchModel`, `ImportRecordModel`). Each batch is tied to a **mapping name** and has status; each record stores raw payload, mapped payload, and validation status.
3. **Map and validate** — The mapping engine applies the chosen **ImportMapping** (field mappings, transforms, types from config). Validators run for required fields, types, currency codes, decimals, uniqueness, and date ranges. Invalid records stay in staging with errors; valid ones are marked ready.
4. **Promote** — The promotion service runs **EntityPromoters** (account, party, ap, ar, inventory, journal, etc.) over valid records. Each record is promoted inside a SAVEPOINT (per-record rollback on failure). A preflight step can report how many records are ready vs blocked.

All mapping and validation rules come from **finance_config**: each config set can define **import_mappings** in `import_mappings/` or `import_mappings.yaml`. The ingestion service compiles them into a **mapping registry** (name → ImportMapping) and uses it for load, map, and validate.

---

## Configuration

### Import mappings (templates)

Each config set can define **multiple named import mappings** so you don’t start from scratch per source system.

- **Named templates** — Each mapping has a `name` (e.g. `qbo_chart_of_accounts`, `qbo_vendors`, `xero_accounts`). At run time you choose a mapping by name; the batch stores `mapping_name` for audit. Add or change mappings in YAML only — no code change.
- **Organization** — Use one YAML file per system (e.g. `import_mappings/quickbooks_online.yaml`, `import_mappings/xero.yaml`) or one file with several mappings. The assembler merges them; names must be unique within the set.
- **No inheritance** — There is no `extends` or template inheritance. To vary a base mapping, copy it in YAML and give it a new name.

### Run order (dependency tier)

Each mapping has a **dependency_tier** (integer, default 0): lower tier = run earlier. Typical convention: **0** = COA/accounts, **1** = reference data, **2** = parties (vendors, customers), **3** = transactions (journal, invoices).

- **Within a batch** — Promotion runs in **source row order** (file order). Tier does not affect order inside a single batch.
- **Across batches** — The pipeline does **not** run multiple mappings in tier order automatically. You (or a runbook) run imports in the right order (e.g. COA, then vendors, then customers, then journal). A future enhancement could add “run all mappings in tier order.”
- **Preflight** — The service can report ready vs blocked counts. In v1, referential blocking is not implemented: all valid records are treated as ready. Enforce dependencies by **running tiers in order**.

---

## Package layout

| Area | Role |
|------|------|
| **adapters/** | SourceAdapter implementations (CSV, JSON, XLSX). `read()` streams records; `probe()` returns row count and sample. |
| **domain/** | Types (ImportBatch, ImportRecord, ImportMapping, FieldMapping) and validators (required, types, currency, decimals, uniqueness, dates). |
| **mapping/** | Pure mapping engine: apply field mappings and transforms; uses kernel `validate_field_type`. No I/O. |
| **models/** | Staging ORM: ImportBatchModel, ImportRecordModel. |
| **promoters/** | EntityPromoter implementations (account, party, ap, ar, inventory, journal). One record → one live entity. |
| **services/** | ImportService (load → stage → validate) and PromotionService (stage → live, SAVEPOINT per record, preflight). |

---

## Using ingestion

### Programmatic

Get the active config pack, build a mapping registry with `build_mapping_registry_from_defs(pack.import_mappings)`, and pass the registry to ImportService. For each run, pass the **mapping name** (e.g. `"qbo_chart_of_accounts"`) and the source file; the service stages and validates. Use PromotionService with a `batch_id` to promote; call `compute_preflight_graph(batch_id)` for ready/blocked counts. See **scripts/run_import.py** and **scripts/cli/views/import_staging.py** for full wiring.

### Command line: run_import.py

One mapping and one file per run. Steps: load (stage) → validate → optionally promote. Options: `--probe-only` (row count, columns, sample; no DB write), `--no-promote` (stop after validate), `--dry-run` (preflight only, no promote). Output: batch ID, valid/invalid counts, first N validation or promotion errors. Run tier-0 (e.g. COA), then tier-2 (vendors, customers), then tier-3 (journal) in separate invocations. See **scripts/README.md** for examples.

### Interactive: Import & Staging menu

Place files in the configured upload directory. The menu shows files ready to stage and existing batches. For each batch: mapping name, entity type, filename, status, total/valid/invalid, and Ready (Yes / Blocked / Done). Actions: **U** = upload (stage) a file, **P** = promote all ready batches, **V #** = view validation issues for batch #, **O # N** = open import row N (raw + mapped + errors), **E # N** = edit row and re-validate, **#** = promote single batch, **D #** = remove batch from staging, **R** = refresh. Workflow: upload in tier order (or all), fix issues with V/O/E, then promote; duplicates are skipped at promote time.

### run_ironflow_import.py

Same as run_import with config-set selection and optional **chunked** journal import (large file in chunks, commit per chunk).

---

## Boundaries

- **Kernel** — Ingestion reads kernel models (Account, Party, etc.) and uses kernel types (ValidationError, clock). It does not post events through ModulePostingService; journal promotion writes to journal/event tables for historical load. The kernel does not import from finance_ingestion.
- **Config** — Import mappings are defined in finance_config (ImportMappingDef). Ingestion compiles them via `compile_mapping_from_def()` and uses the result; it does not read YAML directly at run time.
- **Modules** — Promoters may create module ORM rows (e.g. AP, AR) for bulk load. Live posting of new business events remains via ModulePostingService.

---

## See also

- **finance_config/README.md** — Config entrypoint; import_mappings live in config sets.
- **finance_kernel/README.md** — Event ingestion (IngestorService) vs this package.
- **scripts/README.md** — run_import.py, interactive Import & Staging, run_ironflow_import.
- **docs/MODULE_DEVELOPMENT_GUIDE.md** — Module and ORM context.
