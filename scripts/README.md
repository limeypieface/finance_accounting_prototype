# Scripts

Utility scripts for the finance kernel. All scripts connect to the local
PostgreSQL database (`finance_kernel_test`) and run from the project root.

## Script index by category

| Category | Script | Purpose |
|----------|--------|---------|
| **CLI** | `interactive.py` | Main menu-driven CLI (post events, reports, import, trace). Implementation: `cli/`. |
| **Database** | `seed_data.py` | Drop/recreate schema, post 8 demo transactions, commit. |
| **Database** | `reset_db_ironflow.py` | Drop/recreate, bootstrap party + fiscal periods from config `import_bootstrap.yaml`. |
| **Database** | `pg_check_connections.py` | List active PostgreSQL connections. |
| **Database** | `pg_kill_connections.py` | Terminate connections (all, idle, or by PID). |
| **Import** | `run_import.py` | Load/validate/promote from CSV/JSON/XLSX using config import mappings. |
| **Import** | `run_ironflow_import.py` | Ironflow import pipeline (accounts, customers, vendors, journal). |
| **Import** | `run_ironflow_full_import.py` | Full import sequence: optional reset → accounts → customers → vendors → journal. |
| **Import** | `import_bootstrap.py` | Library: load `import_bootstrap.yaml`, ensure fiscal periods (used by reset/import scripts). |
| **View** | `view_reports.py` | Print all 5 financial statements (read-only). |
| **View** | `view_journal.py` | Print all journal entries and lines (read-only). |
| **View** | `trace.py` | Trace a journal entry or event (CLI: `--event-id`, `--entry-id`, `--list`, `--json`). |
| **View** | `trace_render.py` | Library: same trace output as CLI/interactive (used by `cli/views/trace.py`, tests). |
| **Config** | `approve_config.py` | Compute and write canonical fingerprint for a config set. |
| **Config** | `generate_config_fragments.py` | One-time: write YAML fragments from registered AccountingPolicy. |
| **Config** | `generate_ironflow_config.py` | Generate a config set (CoA, mapping) from QBO accounts JSON. |
| **Config** | `generate_tier_configs.py` | Generate STARTUP/MIDMARKET/ENTERPRISE tier config sets from v1. |
| **QBO** | `qbo/` (package) | Convert QBO XLSX/CSV → JSON; CoA extract/recommend/map. Run: `python3 -m scripts.qbo.run`. |
| **QBO** | `run_qbo_convert.py` | Thin wrapper: convert folder of QBO exports to JSON (same as `-m scripts.qbo.run`). |
| **QBO** | `recommend_coa.py` | Recommend config CoA from QBO accounts JSON. |
| **QBO** | `map_coa.py` | Map QBO accounts to config CoA; output YAML mapping. |
| **Diagnostics** | `balance_sheet_from_qbo_journal.py` | Build balance sheet from a QBO journal file (no DB). |
| **Diagnostics** | `compare_balance_sheet_line_by_line.py` | Compare two balance sheets line-by-line. |
| **Diagnostics** | `compare_bs_to_qbo.py` | Compare DB balance sheet to QBO export. |
| **Diagnostics** | `diagnose_journal_count.py` | Diagnose journal entry counts (e.g. after import). |
| **Diagnostics** | `find_missing_journal_rows.py` | Find rows in QBO journal file not yet in DB. |
| **Diagnostics** | `run_mutation_audit.py` | Run mutation testing audit on tests. |
| **Demo** | `demo_reports.py` | Post transactions, print reports, roll back (no persist). |
| **Demo** | `demo_trace.py` | Seed + trace every posted entry (full audit trail). |
| **Demo** | `demo_engines.py` | Post 15 engine scenarios (variance, tax, matching, allocation, billing). |
| **Dev** | `count_loc.py` | Count lines of code in `finance_*` packages only. |
| **Data** | `fixtures/` | Test CSVs/JSON for QBO and Ironflow (see `fixtures/README.md`). |

Scripts not listed above are either packages (`cli/`, `qbo/`) or the same entry point (e.g. `run_qbo_convert.py` ↔ `python3 -m scripts.qbo.run`). No scripts are currently marked as obsolete; all are in use or referenced in docs/tests. To remove one, search the repo for its path and update references.

## Prerequisites

- PostgreSQL 15+ running locally
- Database `finance_kernel_test` owned by user `finance` (password: `finance_test_pwd`)
- Python dependencies installed (`pip install -r requirements.txt`)

## CLI and accounting demos

These scripts post real business events through the **InterpretationCoordinator
pipeline** — the same code path used in production. Events flow through:

```
Event -> MeaningBuilder -> AccountingIntent -> JournalWriter -> JournalEntry + Lines
```

Every journal entry is created via double-entry posting with full audit trail,
idempotency enforcement, and immutability protection (ORM listeners + PostgreSQL
triggers). No data is inserted directly into journal tables.

### interactive.py — Interactive Accounting CLI

Menu-driven interface for posting events and viewing financial statements
in real time. Data **persists** between sessions. This is the **global CLI application**
for operating the system (not just a demo). Implementation lives in **`scripts/cli/`**
as small reusable modules (config, data, setup, menu, posting, close, views, main).
See **`scripts/cli/README.md`** for the package layout.

**Architecture:** No workarounds or mocks. The script uses the same pipelines
as production:

- **Config:** `get_active_config(legal_entity, as_of_date)` — real YAML load and compile.
- **Orchestrator:** `PostingOrchestrator(session, compiled_pack=config, role_resolver, clock)` — same constructor as production; builds `policy_source` (PackPolicySource), `control_rules`, approval policies, and `workflow_executor` from the pack.
- **Pipeline scenarios:** `ModulePostingService.from_orchestrator(orchestrator, ...)` — same posting service as production; uses the orchestrator’s interpretation coordinator, policy_source, and control_rules.
- **Simple events (1–10):** Use the orchestrator’s `interpretation_coordinator` directly (same kernel path).
- **Subledger scenarios:** Use `orchestrator.subledger_services` (AP, AR, Bank, Inventory, Contract).
- **AR Invoice (workflow trace):** Uses `ARService(session, orchestrator.role_resolver, orchestrator.workflow_executor, clock)` — real AR module with real workflow executor; posting goes through the kernel pipeline (ARService builds its own ModulePostingService by design).

```bash
python3 scripts/interactive.py
```

Pick events by number (1-10 simple, 11+ pipeline/subledger), then:
- `J` — view journal entries
- `R` — view all 5 financial statements
- `T` — trace a journal entry (full audit trace including workflow + interpretation)
- `F` — list and trace failed/rejected events
- `X` — reset database and start fresh
- `Q` — quit (data remains for next session)

**Ironflow / import data:** Reports use the fiscal year from `scripts/cli/config.py` (default 2026). After an Ironflow reset, the DB has **FY2025** only. To see imported data in reports, run the CLI with the same year, e.g. `FINANCE_CLI_FY_YEAR=2025 python3 scripts/interactive.py`. Reports will be empty until journal entries are successfully promoted (account import adds CoA; journal import must complete without CONCURRENT_INSERT for entries to appear).

**Workflow trace:** Post "AR Invoice (workflow trace)" from the pipeline menu, then use `T` to see the decision journal with WORKFLOW TRANSITION (workflow outcome) followed by interpretation/posting entries.

### seed_data.py — Seed Database

Drops all tables, recreates the schema with immutability triggers, posts
8 business transactions, and **commits**. Data persists until the next reset.

```bash
python3 scripts/seed_data.py
```

### reset_db_ironflow.py — Configuration-Driven Reset for Import

Drops all tables, recreates the schema, and creates a **bootstrap party** and **fiscal periods** from the config set’s optional `import_bootstrap.yaml`. No chart of accounts or demo transactions. Works for any company: define `import_bootstrap.yaml` in your config set (e.g. under `finance_config/sets/<config_id>/`) with `fiscal_periods` (and optional `bootstrap_party_code`). If the file is absent, only the SYSTEM party is created.

```bash
python3 scripts/reset_db_ironflow.py [--config-id US-GAAP-2026-IRONFLOW-AI]
```

Default config-id is `FINANCE_IMPORT_CONFIG_ID` env or `US-GAAP-2026-IRONFLOW-AI`. Then run account import and journal import with the same `--config-id`.

### run_ironflow_full_import.py — Single-command full Ironflow import

Runs the full import in the correct sequence: optional DB reset → CoA (accounts) → customers → vendors → journal. Discovers QBO JSON files in a directory by pattern (`qbo_accounts_*.json`, `qbo_customers_*.json`, `qbo_vendors_*.json`, `qbo_journal_*.json`), runs `run_ironflow_import.py` for each in order, then prints load confirmation (DB counts and a note on trace/logs).

```bash
# Full import from upload/ with DB reset first
python3 scripts/run_ironflow_full_import.py --reset

# Full import from a specific directory (no reset)
python3 scripts/run_ironflow_full_import.py --dir path/to/json

# Config and DB URL
python3 scripts/run_ironflow_full_import.py --config-id US-GAAP-2026-IRONFLOW-AI --dir upload --db-url "postgresql://..."
```

Confirmation shows: account count, customer count, vendor count, journal entry count, posted outcomes count, and points to `scripts/trace_render.py` or CLI (T) for decision_log and audit trail.

### view_reports.py — View Financial Statements

Read-only. Generates all 5 financial statements from whatever data is in
the database:

- Trial Balance
- Balance Sheet (classified, current/non-current)
- Income Statement (multi-step with gross profit)
- Statement of Changes in Equity
- Cash Flow Statement (indirect method, ASC 230)

```bash
python3 scripts/view_reports.py
```

### view_journal.py — View Journal Entries

Read-only. Prints every journal entry with its debit/credit lines.

```bash
python3 scripts/view_journal.py
```

### demo_reports.py — Self-Contained Demo

Posts transactions, prints all reports, then **rolls back** — leaves the
database untouched. Useful for a quick demo without persisting data.

```bash
python3 scripts/demo_reports.py
```

## Data Transition (Import) Pipeline

Import mappings are defined in the config set (e.g. `import_mappings.yaml` or
`import_mappings/*.yaml`). Staging tables (`import_batches`, `import_records`)
are created by `create_all_tables()` (e.g. when you run `seed_data.py` or
call `finance_modules._orm_registry.create_all_tables()`).

### run_import.py — Load, Validate, Promote

Runs the ERP ingestion pipeline using the active config’s import mappings:
load source file → stage → validate → optionally promote to live tables.

```bash
# List available mappings (defined in config set import_mappings)
# Then run with a mapping name and source file:
python3 scripts/run_import.py --mapping <name> --file <path>

# Load and validate only (no promotion)
python3 scripts/run_import.py --mapping qb_vendors --file vendors.csv --no-promote

# Probe source file (row count, columns, sample) without DB writes
python3 scripts/run_import.py --mapping qb_vendors --file vendors.csv --probe-only
```

Options: `--legal-entity`, `--as-of-date`, `--dry-run`, `--no-promote`, `--probe-only`, `--actor-id`, `--db-url`.

**QuickBooks Online:** Predefined mappings and test CSVs exist for QBO. Use `--legal-entity ENTERPRISE` and mapping names `qbo_chart_of_accounts`, `qbo_vendors`, `qbo_customers`. Test files: `scripts/fixtures/qbo_*.csv`. See `scripts/fixtures/README.md`.

### interactive.py — Import & Staging (I)

**Upload folder:** Put CSV, JSON, or **XLSX** files in the project **`upload/`** folder. The CLI lists only files that are **not** already staged (so you cannot select a file that was already uploaded). **XLSX** (Excel) files use an intelligent reader that auto-detects the header row (looks for journal-like column names: date, account, debit, credit, amount, description, etc.) so different sheet layouts are supported.

1. **Upload (stage)** — Press **U**, pick a file from the list, pick a mapping; the file is loaded and validated into staging.
2. **Review** — Staged batches table shows mapping, entity type, file, status, valid/invalid, ready vs blocked.
3. **View issues** — **V #** shows validation errors for batch `#`.
4. **Remove from staging** — **D #** removes batch `#` from staging (so you can re-upload that file with **U**).
5. **Promote** — When ready, **P** promotes all ready batches, or type a batch number to promote that batch only.

Actions: **U** = Upload (stage) a file from upload folder, **P** = Promote all ready, **V #** = View issues, **#** = Promote batch #, **D #** = Remove batch # from staging, **R** = Refresh, **Enter** = Back.

### interactive.py — Define import mapping (M)

If your CSV columns don’t match the built-in QuickBooks mappings, use **M** to define a new mapping from your file’s columns:

1. Put a **sample file** (CSV or XLSX) in the **`upload/`** folder (same column layout you’ll use for future imports).
2. In the CLI, press **M** (Define import mapping).
3. Select the file, then choose **entity type** (account, vendor, or customer).
4. For each target field (e.g. code, name, account_type), choose which **source column** in your CSV maps to it (or skip optional fields).
5. Enter a **mapping name** (e.g. `my_qbo_accounts`). The mapping is saved to the active config set’s `import_mappings/custom.yaml`.
6. Next time you use **I** (Import & Staging), your new mapping appears in the list. Use it when uploading files with the same column layout.

This creates or updates YAML under `finance_config/sets/<config_id>/import_mappings/custom.yaml` so future uploads can use your column names without changing QuickBooks export headers.

### QBO Toolset — XLSX/CSV to structured JSON

The **scripts/qbo** package reads standard QuickBooks Online exports (XLSX or CSV) and writes **structured JSON** into the upload directory. Use it when you have a folder of QBO report exports (Account List, Customer List, Vendor List, Journal, General Ledger, Trial Balance, etc.) and want a single, consistent format for the import pipeline.

```bash
# Convert all QBO files in ./upload and write JSON there (default)
python3 -m scripts.qbo.run

# Convert from a dedicated exports folder
python3 -m scripts.qbo.run /path/to/qbo_exports

# Merge all files of each type into one JSON per type (qbo_accounts.json, qbo_journal.json, ...)
python3 -m scripts.qbo.run /path/to/qbo_exports --one-file-per-type -o upload
```

Output files are named `qbo_<type>_<basename>.json` (or `qbo_<type>.json` with `--one-file-per-type`). Each JSON has `source`, `report`, `row_count`, and `rows` (array of record dicts). Use **Import & Staging (I)** with a mapping that uses `source_format: json` and `source_options: { "json_path": "rows" }` to import these files. See **scripts/qbo/README.md** for supported report types and column mapping.

## Configuration Scripts

### generate_config_fragments.py

One-time migration tool. Reads registered `AccountingPolicy` definitions
from Python modules and writes YAML configuration fragments.

```bash
python3 scripts/generate_config_fragments.py
```

### approve_config.py

Approves a configuration set by computing and writing its canonical
fingerprint.

```bash
python3 scripts/approve_config.py [config_set_directory]
```

## Database Utilities

### pg_check_connections.py

Shows all active PostgreSQL connections to `finance_kernel_test`.

```bash
python3 scripts/pg_check_connections.py
```

### pg_kill_connections.py

Terminates orphaned connections that may be holding locks.

```bash
python3 scripts/pg_kill_connections.py            # kill all
python3 scripts/pg_kill_connections.py --idle      # kill only idle
python3 scripts/pg_kill_connections.py --pid 123   # kill specific PID
```

## View and trace

### trace.py — Trace journal entry or event

Standalone CLI to print the full audit trace for a journal entry or source event (same kind of output as the interactive **T** menu).

```bash
python3 scripts/trace.py --list
python3 scripts/trace.py --event-id <uuid>
python3 scripts/trace.py --entry-id <uuid>
python3 scripts/trace.py --event-id <uuid> --json
```

### trace_render.py (library)

Shared trace renderer used by `interactive.py` (T menu), `cli/views/trace.py`, and `tests/trace/show_trace.py`. Not run directly.

Full documentation: **docs/TRACE.md** (trace bundle, TraceSelector, LogQueryPort, decision journal).

## Diagnostics and audit

These scripts help compare DB state to QBO or debug import/journal counts.

| Script | Purpose |
|--------|---------|
| `balance_sheet_from_qbo_journal.py` | Build a balance sheet from a QBO journal file (no DB). |
| `compare_balance_sheet_line_by_line.py` | Compare two balance sheets line-by-line (e.g. `--as-of`). |
| `compare_bs_to_qbo.py` | Compare DB balance sheet to QBO export. |
| `diagnose_journal_count.py` | Diagnose journal entry counts (e.g. after QBO journal import). |
| `find_missing_journal_rows.py` | Find rows in a QBO journal file that are not in the DB. |
| `run_mutation_audit.py` | Run mutation testing on tests (see `tests/mutation/README.md`). |

Run from project root; most accept optional `--db-url` or `--as-of`. See each script’s docstring for usage.

## QBO standalone tools

Besides the **scripts/qbo** package (`python3 -m scripts.qbo.run`), these entry points are useful for one-off CoA and mapping:

- **run_qbo_convert.py** — Convert a folder of QBO XLSX/CSV to JSON (same as `-m scripts.qbo.run`).  
  `python3 scripts/run_qbo_convert.py [source_dir] [-o output_dir] [--one-file-per-type]`
- **recommend_coa.py** — Recommend which config set’s CoA best matches a QBO accounts JSON.  
  `python3 scripts/recommend_coa.py --input path/to/qbo_accounts_*.json`
- **map_coa.py** — Produce a CoA mapping YAML from QBO accounts and a config set.  
  `python3 scripts/map_coa.py --input path/to/qbo_accounts_*.json --config US-GAAP-2026-v1 [--output qbo_coa_mapping.yaml]`
- **generate_ironflow_config.py** — Generate a full config set (including CoA mapping) from QBO accounts JSON.  
  `python3 scripts/generate_ironflow_config.py --input path/to/qbo_accounts_*.json [--output-dir ...]`

See **scripts/qbo/README.md** for the full QBO workflow.

## Demo variants

- **demo_reports.py** — Post transactions, print all reports, **roll back** (database unchanged).
- **demo_trace.py** — Seed 8 transactions, then trace every posted entry (full audit trail). Options: `--trace-only`, `--json`.
- **demo_engines.py** — Post 15 engine-driven scenarios (variance, tax, matching, allocation, billing). Options: `--trace`, `--trace-only`.

## Dev

- **count_loc.py** — Count lines of code in `finance_config`, `finance_modules`, `finance_kernel`, `finance_engines`, `finance_services` only (excludes tests, docs, scripts).
- **generate_tier_configs.py** — Regenerate STARTUP / MIDMARKET / ENTERPRISE config sets from US-GAAP-2026-v1. Used by benchmark tier configs. `python3 scripts/generate_tier_configs.py [--validate]`

## Organization

Scripts are kept at **top level** (plus packages `cli/`, `qbo/`, `fixtures/`) so that existing references in docs, plans, and tests remain valid. The **Script index by category** at the top of this README is the logical grouping. If you later move scripts into subdirs (e.g. `scripts/db/`, `scripts/import/`), update all references to the new paths.
