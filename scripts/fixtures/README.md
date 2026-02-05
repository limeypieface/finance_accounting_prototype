# Import test fixtures

Sample CSV files for testing the data transition pipeline.

## QuickBooks Online (QBO) test CSVs

Column headers match **QuickBooks Online** export formats so the `qbo_*` import mappings (in `finance_config/sets/US-GAAP-2026-ENTERPRISE/import_mappings/quickbooks_online.yaml`) work as-is.

| File | Mapping name | Source | Rows |
|------|--------------|--------|------|
| `qbo_chart_of_accounts_test.csv` | `qbo_chart_of_accounts` | Account List (Reports) | 8 |
| `qbo_vendors_test.csv` | `qbo_vendors` | Vendor list export | 3 |
| `qbo_customers_test.csv` | `qbo_customers` | Customer list export | 3 |

**Run imports** (use ENTERPRISE config; ensure DB has staging tables via `create_all_tables()`):

```bash
python3 scripts/run_import.py --legal-entity ENTERPRISE --mapping qbo_chart_of_accounts --file scripts/fixtures/qbo_chart_of_accounts_test.csv
python3 scripts/run_import.py --legal-entity ENTERPRISE --mapping qbo_vendors --file scripts/fixtures/qbo_vendors_test.csv
python3 scripts/run_import.py --legal-entity ENTERPRISE --mapping qbo_customers --file scripts/fixtures/qbo_customers_test.csv
```

**Chart of accounts:** The `Type` column uses QBO type names (Bank, Accounts Receivable, Income, Expense, etc.). The account promoter maps these to our `AccountType` and derives normal balance automatically.

**Vendors/Customers:** Optional columns (e.g. Tax Identification Number, Payment Terms) can be empty; validation skips optional missing fields.
