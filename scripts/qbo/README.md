# QuickBooks Online (QBO) Toolset

Read standard QuickBooks Online XLSX/CSV exports and write **structured JSON** into the upload directory for use with the import pipeline.

## Supported export types

| Type | Filename pattern | Output key | JSON shape |
|------|------------------|------------|------------|
| Account List | Account List, Chart of Accounts | `accounts` | `rows[].code, name, account_type, description, total_balance` |
| Customer Contact List | Customer Contact List, Customer List | `customers` | `rows[].code, name, company, email, phone, billing_address` |
| Vendor Contact List | Vendor Contact List, Vendor List | `vendors` | `rows[].code, name, email, phone, billing_address` |
| Journal | Journal, Transaction List | `journal` | `rows[].date, num, account, memo, debit, credit, balance` |
| General Ledger | General Ledger, GL | `general_ledger` | same as journal |
| Trial Balance | Trial Balance | `trial_balance` | `rows[].account, debit, credit, balance` |

Detection uses **filename** first (e.g. `Company_Account List.xlsx`), then **header row** for XLSX to distinguish Journal vs GL.

## Usage

### Convert a folder of QBO exports to JSON

```bash
# From project root (use files in ./upload by default, write JSON to ./upload)
python3 scripts/run_qbo_convert.py
# or
python3 -m scripts.qbo.run

# Use a dedicated folder of exports and write JSON to upload/
python3 scripts/run_qbo_convert.py /path/to/qbo_exports
python3 -m scripts.qbo.run /path/to/qbo_exports

# Write JSON to a specific directory
python -m scripts.qbo.run /path/to/qbo_exports -o ./upload

# Merge all files of each type into one JSON per type (qbo_accounts.json, qbo_journal.json, ...)
python -m scripts.qbo.run /path/to/qbo_exports --one-file-per-type
```

### Output structure

Each JSON file looks like:

```json
{
  "source": "qbo",
  "report": "accounts",
  "source_file": "Ironflow AI INC_Account List.xlsx",
  "row_count": 53,
  "rows": [
    { "code": "1000", "name": "Cash", "account_type": "bank", "total_balance": 100132.51 },
    ...
  ]
}
```

With `--one-file-per-type`, `source_file` is omitted and `rows` from all files of that type are combined.

### From Python

```python
from pathlib import Path
from scripts.qbo import run_qbo_convert, read_qbo_file, detect_qbo_type

# Convert entire folder
results = run_qbo_convert(Path("qbo_exports"), output_dir=Path("upload"))
for inp, out_path, count in results:
    print(out_path, count)

# Read a single file
rows = read_qbo_file(Path("upload/Journal.xlsx"))
report_type = detect_qbo_type(Path("upload/Journal.xlsx"))
```

## Column mapping

QBO export column names vary. The readers normalize common variants into a single structure:

- **Account List**: `Full name` / `Account Name` → `name`; `Account Number` / `Account` → `code`; `Type` → `account_type`; `Total balance` → `total_balance`.
- **Vendor/Customer**: `Vendor` / `Name` → `code` and `name`; `Phone numbers` → `phone`; `Email`, `Billing address` preserved.
- **Journal/GL**: `Transaction Date` / `Date` → `date`; `Account` / `Account Name` → `account`; `Debit` / `Credits` / `Credit` → numeric; `Memo` / `Description` → `memo`.

After conversion, use **Import & Staging (I)** with a mapping that expects these JSON keys, or use the mapping editor (**M**) to point at the generated JSON and map fields to the pipeline.

### Recommend which config COA matches your QBO accounts

```bash
python3 scripts/recommend_coa.py --input "upload/qbo_accounts_Ironflow AI INC_Account List _2_.json"
```

Prints ranked config sets (e.g. US-GAAP-2026-v1) by coverage of your QBO account types. Use the top `config_id` for mapping.

### Map QBO accounts to system COA (with recommendations)

```bash
# Print table: each QBO account → recommended role/code → target_code
python3 scripts/map_coa.py --input "upload/qbo_accounts_Ironflow AI INC_Account List _2_.json" --config US-GAAP-2026-v1

# Save editable YAML; edit target_code and target_name to map to existing or create new
python3 scripts/map_coa.py --input "upload/qbo_accounts_Ironflow AI INC_Account List _2_.json" --config US-GAAP-2026-v1 --output qbo_coa_mapping.yaml
```

The mapping file lists each QBO account with a **recommended** target (map to existing) or a **suggested new code** in a logical numbering scheme (Bank→10xx, Expense→60xx, etc.). Edit `target_code` and `target_name` to map to an existing account or create a new one. **Upload flow:** first create all relevant accounts (new codes + names from the mapping), then upload journals once those accounts exist.

### Ironflow AI config (1:1 map to QBO)

To use a config that **matches your QBO accounts by name** so every account maps 1:1:

```bash
# Generate US-GAAP-2026-IRONFLOW-AI from your QBO accounts (assigns codes, writes accounts_ironflow.yaml)
python3 scripts/generate_ironflow_config.py --input "upload/qbo_accounts_Ironflow AI INC_Account List _2_.json"

# Map with that config — every QBO account maps to the same-named account in the config
python3 scripts/map_coa.py --input "upload/qbo_accounts_Ironflow AI INC_Account List _2_.json" --config US-GAAP-2026-IRONFLOW-AI
```

The generated set lives in `finance_config/sets/US-GAAP-2026-IRONFLOW-AI/`: `root.yaml`, `chart_of_accounts.yaml` (one role per account), `accounts_ironflow.yaml` (code, name, type for reference), `ledgers.yaml`, and **`import_mappings/qbo_coa_mapping.yaml`** — the canonical QBO → system mapping (one row per QBO account, target_code/target_name = our named accounts) so you can verify accounts map to original and use it for journal import. When you use `--config US-GAAP-2026-IRONFLOW-AI`, map_coa loads `accounts_ironflow.yaml` and matches by account name so the accounts map 1:1 to the original.
