"""PostgreSQL immutability triggers (R10 compliance -- layer 2 of 2)."""

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

# Directory containing SQL trigger files
SQL_DIR = Path(__file__).parent / "sql"

# Ordered list of trigger files to install (numbered for predictable order)
TRIGGER_FILES = [
    "01_journal_entry.sql",
    "02_journal_line.sql",
    "03_audit_event.sql",
    "04_account.sql",
    "05_fiscal_period.sql",
    "06_rounding.sql",
    "07_dimension.sql",
    "08_exchange_rate.sql",
    "09_event_immutability.sql",
    "10_balance_enforcement.sql",
]

# File containing drop statements for all triggers
DROP_FILE = "99_drop_all.sql"

# All trigger names (for verification)
ALL_TRIGGER_NAMES = [
    # Journal Entry (01)
    "trg_journal_entry_immutability_update",
    "trg_journal_entry_immutability_delete",
    # Journal Line (02)
    "trg_journal_line_immutability_update",
    "trg_journal_line_immutability_delete",
    # Audit Event (03)
    "trg_audit_event_immutability_update",
    "trg_audit_event_immutability_delete",
    # Account (04)
    "trg_account_structural_immutability_update",
    "trg_account_last_rounding_delete",
    # Fiscal Period (05)
    "trg_fiscal_period_immutability_update",
    "trg_fiscal_period_immutability_delete",
    # Rounding (06)
    "trg_journal_line_single_rounding",
    "trg_journal_line_rounding_threshold",
    # Dimension (07)
    "trg_dimension_code_immutability",
    "trg_dimension_deletion_protection",
    "trg_dimension_value_structural_immutability",
    "trg_dimension_value_deletion_protection",
    # Exchange Rate (08)
    "trg_exchange_rate_validate",
    "trg_exchange_rate_immutability",
    "trg_exchange_rate_delete",
    "trg_exchange_rate_arbitrage",
    # Event (09)
    "trg_event_immutability_update",
    "trg_event_immutability_delete",
    # Balance Enforcement (10)
    "trg_journal_entry_balance_check",
    "trg_journal_line_no_insert_posted",
]


def _load_sql_file(filename: str) -> str:
    """Load SQL content from a file in the sql/ directory."""
    filepath = SQL_DIR / filename
    return filepath.read_text(encoding="utf-8")


def _load_all_trigger_sql() -> str:
    """Load and concatenate all trigger SQL files in numbered order."""
    sql_parts = []
    for filename in TRIGGER_FILES:
        sql_content = _load_sql_file(filename)
        sql_parts.append(f"-- Loading: {filename}")
        sql_parts.append(sql_content)
        sql_parts.append("")  # Blank line between files
    return "\n".join(sql_parts)


def _load_drop_sql() -> str:
    """Load the SQL to drop all triggers."""
    return _load_sql_file(DROP_FILE)


# =============================================================================
# Public API
# =============================================================================


def install_immutability_triggers(engine: Engine) -> None:
    """Install database-level immutability triggers (R10)."""
    sql_content = _load_all_trigger_sql()

    with engine.connect() as conn:
        conn.execute(text(sql_content))
        conn.commit()


def uninstall_immutability_triggers(engine: Engine) -> None:
    """Remove database-level immutability triggers. Re-install immediately after migration."""
    sql_content = _load_drop_sql()

    with engine.connect() as conn:
        conn.execute(text(sql_content))
        conn.commit()


def triggers_installed(engine: Engine) -> bool:
    """Check if all immutability triggers are installed."""
    trigger_list = ", ".join(f"'{name}'" for name in ALL_TRIGGER_NAMES)
    check_sql = f"""
    SELECT COUNT(*) FROM pg_trigger
    WHERE tgname IN ({trigger_list});
    """

    with engine.connect() as conn:
        result = conn.execute(text(check_sql)).scalar()
        return result == len(ALL_TRIGGER_NAMES)


def get_installed_triggers(engine: Engine) -> list[str]:
    """Get list of installed immutability trigger names."""
    trigger_list = ", ".join(f"'{name}'" for name in ALL_TRIGGER_NAMES)
    check_sql = f"""
    SELECT tgname FROM pg_trigger
    WHERE tgname IN ({trigger_list})
    ORDER BY tgname;
    """

    with engine.connect() as conn:
        result = conn.execute(text(check_sql))
        return [row[0] for row in result]


def get_missing_triggers(engine: Engine) -> list[str]:
    """Get list of immutability triggers that should be installed but are not."""
    installed = set(get_installed_triggers(engine))
    expected = set(ALL_TRIGGER_NAMES)
    return sorted(expected - installed)


# =============================================================================
# Individual Trigger Installation (for selective deployment)
# =============================================================================


def install_trigger_file(engine: Engine, filename: str) -> None:
    """Install triggers from a specific SQL file."""
    sql_content = _load_sql_file(filename)

    with engine.connect() as conn:
        conn.execute(text(sql_content))
        conn.commit()


def list_trigger_files() -> list[str]:
    """Get list of available trigger SQL files in installation order."""
    return TRIGGER_FILES.copy()
