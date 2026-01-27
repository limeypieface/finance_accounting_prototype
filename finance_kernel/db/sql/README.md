# SQL Trigger Files

This directory contains PostgreSQL trigger definitions for database-level immutability enforcement (R10 Compliance - Defense in Depth).

## File Organization

| File | Entity | Invariants Enforced |
|------|--------|---------------------|
| `01_journal_entry.sql` | JournalEntry | Posted entries immutable |
| `02_journal_line.sql` | JournalLine | Lines immutable when parent posted |
| `03_audit_event.sql` | AuditEvent | Always immutable (no exceptions) |
| `04_account.sql` | Account | Structural fields immutable when referenced |
| `05_fiscal_period.sql` | FiscalPeriod | Closed periods immutable |
| `06_rounding.sql` | JournalLine | Rounding fraud prevention |
| `07_dimension.sql` | Dimension/DimensionValue | Reference data immutability |
| `08_exchange_rate.sql` | ExchangeRate | Rate immutability and validation |
| `99_drop_all.sql` | All | Drops all triggers (for migrations) |

## Numbered Prefixes

Files are numbered to ensure predictable installation order. This matters because:
- Some triggers may depend on functions defined in earlier files
- Consistent ordering makes debugging easier

## Each File Contains

1. **Header comment** explaining the invariants enforced
2. **Function definitions** (CREATE OR REPLACE FUNCTION)
3. **Trigger drop statements** (for idempotent installation)
4. **Trigger creation statements**

## Usage

These files are loaded by `triggers.py`:

```python
from finance_kernel.db.triggers import install_immutability_triggers

# Install all triggers
install_immutability_triggers(engine)

# Check if installed
triggers_installed(engine)  # Returns True/False

# Uninstall for migration (use with caution!)
uninstall_immutability_triggers(engine)
```

## Editing Guidelines

1. **Keep functions idempotent** - Use `CREATE OR REPLACE FUNCTION`
2. **Keep trigger creation idempotent** - Drop before create
3. **Document the invariant** - Clear header comment explaining "why"
4. **Test in isolation** - Each file should be independently executable
5. **Use consistent error codes**:
   - `restrict_violation` for immutability violations
   - `check_violation` for validation failures

## Testing Individual Files

You can test a single file directly in psql:

```bash
psql -d finance_kernel -f 01_journal_entry.sql
```

Or run them all:

```bash
for f in 0*.sql; do psql -d finance_kernel -f "$f"; done
```
