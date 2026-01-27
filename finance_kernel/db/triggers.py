"""
Database-level immutability triggers (R10 Compliance - Defense in Depth).

This module provides PostgreSQL triggers that enforce immutability rules
at the database level, protecting against:
- Bulk UPDATE statements
- Raw SQL modifications
- Direct database access
- Migration scripts (unless explicitly disabled)

These triggers complement the ORM-level listeners in immutability.py,
providing defense-in-depth for critical financial records.

Trigger SQL files are stored in the `sql/` subdirectory for:
- Syntax highlighting in editors
- IDE support for SQL
- Easier maintenance and review
- Independent testing

Hard invariants enforced:
- Posted JournalEntry records cannot be modified or deleted
- JournalLine records cannot be modified or deleted when parent is posted
- AuditEvent records are always immutable (no updates or deletes ever)
- Account structural fields (type, normal_balance, code) immutable once
  referenced by posted journal lines
- Last rounding account per currency cannot be deleted
- Closed FiscalPeriod records cannot be modified or deleted (one-way OPEN->CLOSED)
- Dimension.code immutable when dimension values exist
- DimensionValue structural fields (code, name, dimension_code) always immutable
- DimensionValue cannot be deleted when referenced by posted journal lines
"""

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine


# =============================================================================
# SQL File Loading
# =============================================================================

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
]


def _load_sql_file(filename: str) -> str:
    """
    Load SQL content from a file in the sql/ directory.

    Args:
        filename: Name of the SQL file (e.g., "01_journal_entry.sql")

    Returns:
        SQL content as a string.

    Raises:
        FileNotFoundError: If the file doesn't exist.
    """
    filepath = SQL_DIR / filename
    return filepath.read_text(encoding="utf-8")


def _load_all_trigger_sql() -> str:
    """
    Load and concatenate all trigger SQL files in order.

    Returns:
        Combined SQL content for all triggers.
    """
    sql_parts = []
    for filename in TRIGGER_FILES:
        sql_content = _load_sql_file(filename)
        sql_parts.append(f"-- Loading: {filename}")
        sql_parts.append(sql_content)
        sql_parts.append("")  # Blank line between files
    return "\n".join(sql_parts)


def _load_drop_sql() -> str:
    """
    Load the SQL to drop all triggers.

    Returns:
        SQL content to drop all triggers and functions.
    """
    return _load_sql_file(DROP_FILE)


# =============================================================================
# Public API
# =============================================================================


def install_immutability_triggers(engine: Engine) -> None:
    """
    Install database-level immutability triggers.

    This should be called after create_tables() to add PostgreSQL triggers
    that enforce R10 compliance at the database level.

    The triggers are loaded from SQL files in the sql/ subdirectory,
    making them easier to maintain, review, and test independently.

    Args:
        engine: SQLAlchemy engine connected to PostgreSQL.

    Note:
        Triggers are PostgreSQL-specific and will be skipped for SQLite.
        SQLite-only deployments rely on ORM-level immutability (immutability.py).
    """
    if engine.dialect.name != "postgresql":
        # Triggers are PostgreSQL-specific; skip for SQLite
        return

    sql_content = _load_all_trigger_sql()

    with engine.connect() as conn:
        conn.execute(text(sql_content))
        conn.commit()


def uninstall_immutability_triggers(engine: Engine) -> None:
    """
    Remove database-level immutability triggers.

    WARNING: Only use this for migrations that need to modify historical data.
    Re-install triggers immediately after the migration completes.

    Args:
        engine: SQLAlchemy engine connected to PostgreSQL.
    """
    if engine.dialect.name != "postgresql":
        return

    sql_content = _load_drop_sql()

    with engine.connect() as conn:
        conn.execute(text(sql_content))
        conn.commit()


def triggers_installed(engine: Engine) -> bool:
    """
    Check if immutability triggers are installed.

    Args:
        engine: SQLAlchemy engine.

    Returns:
        True if all triggers are installed, False otherwise.
    """
    if engine.dialect.name != "postgresql":
        return False

    # Build the check query dynamically from ALL_TRIGGER_NAMES
    trigger_list = ", ".join(f"'{name}'" for name in ALL_TRIGGER_NAMES)
    check_sql = f"""
    SELECT COUNT(*) FROM pg_trigger
    WHERE tgname IN ({trigger_list});
    """

    with engine.connect() as conn:
        result = conn.execute(text(check_sql)).scalar()
        return result == len(ALL_TRIGGER_NAMES)


def get_installed_triggers(engine: Engine) -> list[str]:
    """
    Get list of installed immutability triggers.

    Useful for debugging and verification.

    Args:
        engine: SQLAlchemy engine.

    Returns:
        List of installed trigger names.
    """
    if engine.dialect.name != "postgresql":
        return []

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
    """
    Get list of immutability triggers that should be installed but aren't.

    Useful for debugging and verification.

    Args:
        engine: SQLAlchemy engine.

    Returns:
        List of missing trigger names.
    """
    installed = set(get_installed_triggers(engine))
    expected = set(ALL_TRIGGER_NAMES)
    return sorted(expected - installed)


# =============================================================================
# Individual Trigger Installation (for selective deployment)
# =============================================================================


def install_trigger_file(engine: Engine, filename: str) -> None:
    """
    Install triggers from a specific SQL file.

    Useful for selective deployment or testing individual trigger sets.

    Args:
        engine: SQLAlchemy engine connected to PostgreSQL.
        filename: Name of the SQL file (e.g., "01_journal_entry.sql")

    Raises:
        FileNotFoundError: If the file doesn't exist.
    """
    if engine.dialect.name != "postgresql":
        return

    sql_content = _load_sql_file(filename)

    with engine.connect() as conn:
        conn.execute(text(sql_content))
        conn.commit()


def list_trigger_files() -> list[str]:
    """
    Get list of available trigger SQL files.

    Returns:
        List of filenames in installation order.
    """
    return TRIGGER_FILES.copy()
