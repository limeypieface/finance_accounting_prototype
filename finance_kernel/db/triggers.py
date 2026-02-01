"""
Module: finance_kernel.db.triggers
Responsibility: Loading, installing, and verifying PostgreSQL immutability
    triggers (R10 Compliance - Layer 2 of 2).  This is the database-level
    complement to the ORM-level listeners in db/immutability.py.
Architecture position: Kernel > DB.  May import from db/ only (pathlib for
    SQL file loading, sqlalchemy for execution).  MUST NOT import from
    models/, services/, selectors/, domain/, or outer layers.

Invariants enforced (via 26 PostgreSQL triggers across 10 SQL files):
    R10 -- Posted JournalEntry rows: no UPDATE, no DELETE.
    R10 -- JournalLine rows: no UPDATE/DELETE when parent entry is posted.
    R10 -- AuditEvent rows: always immutable (no UPDATE/DELETE ever).
    R10 -- Account structural fields (type, normal_balance, code) immutable
           once referenced by posted journal lines.
    R5  -- At most one is_rounding=True line per entry; rounding threshold.
    R10 -- Closed FiscalPeriod: no UPDATE/DELETE.
    R10 -- Dimension.code immutable when values exist.
    R10 -- DimensionValue structural fields always immutable.
    R10 -- ExchangeRate immutable when referenced by journal lines.
    R1  -- Event rows: always immutable (no UPDATE/DELETE).
    R4  -- Balance enforcement trigger on posted entries.

Failure modes:
    - PostgreSQL RAISE EXCEPTION on any trigger violation (caught as
      IntegrityError or OperationalError by SQLAlchemy).
    - FileNotFoundError if SQL files are missing from the sql/ directory.
    - OperationalError on deadlock during installation (caller retries).

Audit relevance:
    These triggers are the last line of defense against data tampering.
    Even if the ORM layer is bypassed (raw SQL, bulk operations, direct
    psql access, compromised migrations), the database triggers prevent
    modification of financial records.  Both layers must be bypassed
    simultaneously to tamper with data -- this is intentional redundancy.
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
    """
    Load SQL content from a file in the sql/ directory.

    Preconditions: filename exists in SQL_DIR.
    Postconditions: Returns the full text content of the file, UTF-8 decoded.

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
    Load and concatenate all trigger SQL files in numbered order.

    Postconditions: Returns a single SQL string containing all trigger
        definitions, separated by comment headers identifying each source file.
        File order matches TRIGGER_FILES (numerical prefix ordering).

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

    INVARIANT R10: This function installs 26 PostgreSQL triggers that
    enforce immutability at the database level, providing defense-in-depth
    complementing the ORM listeners in db/immutability.py.

    Preconditions: Tables must exist (call after create_tables()).
        Engine must be connected to PostgreSQL.
    Postconditions: All triggers in ALL_TRIGGER_NAMES are installed.
        Trigger functions are created with CREATE OR REPLACE (idempotent).

    Args:
        engine: SQLAlchemy engine connected to PostgreSQL.
    """
    sql_content = _load_all_trigger_sql()

    with engine.connect() as conn:
        conn.execute(text(sql_content))
        conn.commit()


def uninstall_immutability_triggers(engine: Engine) -> None:
    """
    Remove database-level immutability triggers.

    WARNING: Only use this for migrations that need to modify historical data.
    Re-install triggers IMMEDIATELY after the migration completes.  Leaving
    triggers uninstalled in production is a critical security vulnerability.

    Preconditions: Engine must be connected to PostgreSQL.
    Postconditions: All triggers and their backing functions are removed.

    Args:
        engine: SQLAlchemy engine connected to PostgreSQL.
    """
    sql_content = _load_drop_sql()

    with engine.connect() as conn:
        conn.execute(text(sql_content))
        conn.commit()


def triggers_installed(engine: Engine) -> bool:
    """
    Check if all immutability triggers are installed.

    Preconditions: Engine must be connected to PostgreSQL.
    Postconditions: Returns True iff the count of installed triggers in
        pg_trigger matches len(ALL_TRIGGER_NAMES).

    Args:
        engine: SQLAlchemy engine.

    Returns:
        True if all triggers are installed, False otherwise.
    """
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
