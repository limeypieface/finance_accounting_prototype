# Scripts

Utility scripts for the finance kernel. All scripts connect to the local
PostgreSQL database (`finance_kernel_test`) and run from the project root.

## Prerequisites

- PostgreSQL 15+ running locally
- Database `finance_kernel_test` owned by user `finance` (password: `finance_test_pwd`)
- Python dependencies installed (`pip install -r requirements.txt`)

## Accounting Demo Scripts

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
in real time. Data **persists** between sessions.

```bash
python3 scripts/interactive.py
```

Pick events by number (1-10), then:
- `J` — view journal entries
- `R` — view all 5 financial statements
- `X` — reset database and start fresh
- `Q` — quit (data remains for next session)

### seed_data.py — Seed Database

Drops all tables, recreates the schema with immutability triggers, posts
8 business transactions, and **commits**. Data persists until the next reset.

```bash
python3 scripts/seed_data.py
```

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
