"""
ORM-Level Immutability Enforcement (R10 Compliance - Layer 1 of 2).

===============================================================================
WHY THIS EXISTS
===============================================================================

Financial records must be tamper-proof. Auditors and regulators require that
posted transactions cannot be modified - only reversed with new entries that
create a visible paper trail.

This module is the FIRST layer of "defense in depth" for data integrity:

  Layer 1: THIS FILE (ORM event listeners)
    - Catches modifications through Python/SQLAlchemy code
    - Fires BEFORE the SQL is sent to the database

  Layer 2: db/sql/*.sql (PostgreSQL triggers)
    - Catches raw SQL, bulk UPDATE statements, direct psql access
    - Fires AT the database level, independent of application code

Both layers enforce the SAME rules. An attacker must bypass BOTH to tamper
with data. This is intentional redundancy, not duplication.

See also:
  - db/triggers.py - Loads and installs PostgreSQL triggers
  - db/sql/*.sql - The actual trigger SQL
  - db/README.md - Full documentation of both layers

===============================================================================
HOW IT WORKS
===============================================================================

SQLAlchemy fires events before UPDATE/DELETE operations reach the database.
We register listeners that intercept these events and check our invariants:

    session.flush()
         |
         v
    [before_update event] --> _check_*_immutability() --> ImmutabilityViolationError
         |                                                        ^
         v                                                        |
    [before_delete event] --> _check_*_delete() -----------------+
         |
         v
    SQL sent to database (only if checks pass)

If a check fails, we raise ImmutabilityViolationError and the transaction
is aborted. The database is never modified.

===============================================================================
PROTECTED ENTITIES
===============================================================================

Entity                      | When Immutable                    | Why
----------------------------|-----------------------------------|----------------------------------
JournalEntry                | After status = POSTED             | Posted = finalized, auditable
JournalLine                 | When parent entry is POSTED       | Lines are part of the entry
AuditEvent                  | ALWAYS (from creation)            | Audit trail is sacred
Account                     | Structural fields when referenced | Changing type would corrupt reports
FiscalPeriod                | After status = CLOSED             | Closed = year-end finalized
Dimension                   | Code when values exist            | Code is FK target
DimensionValue              | Code/name always                  | Referenced in journal dimensions
ExchangeRate                | When referenced by journal lines  | Changing rate alters history
SubledgerEntry              | After posted_at is set            | Financial fields frozen; recon fields mutable
ReconciliationFailureReport | ALWAYS (from creation)            | Audit artifact (SL-G6) is sacred

===============================================================================
DESIGN DECISIONS
===============================================================================

1. WHY ALLOW updated_at/updated_by_id CHANGES?
   These are audit metadata fields, not financial data. They track WHO looked
   at a record, not the record's content. Blocking them would break audit logging.

2. WHY CHECK "WAS POSTED" NOT "IS POSTED"?
   The posting workflow itself must set status=POSTED. We allow the transition
   DRAFT->POSTED, but block any changes AFTER that transition completes.
   We detect this by checking SQLAlchemy's attribute history.

3. WHY INLINE IMPORTS?
   Avoids circular imports. Models import from db, db imports from models.
   Inline imports defer resolution until the function runs.

4. WHY BOTH ORM AND SQL ENFORCEMENT?
   - ORM catches application bugs and most attacks
   - SQL catches: raw SQL injection, bulk operations, rogue migrations,
     direct database access by compromised accounts
   - Defense in depth: assume any single layer can be bypassed

===============================================================================
USAGE
===============================================================================

Called automatically during application startup:

    from finance_kernel.db.immutability import register_immutability_listeners
    register_immutability_listeners()  # Called once at startup

To temporarily disable (TESTS ONLY - never in production):

    from finance_kernel.db.immutability import unregister_immutability_listeners
    unregister_immutability_listeners()
    # ... do forbidden operation ...
    register_immutability_listeners()

===============================================================================
"""

from sqlalchemy import event
from sqlalchemy.orm import Session, UOWTransaction

from finance_kernel.exceptions import ImmutabilityViolationError
from finance_kernel.logging_config import get_logger

logger = get_logger("db.immutability")


def _check_account_deletion_before_flush(session, flush_context, instances):
    """
    Check for account deletions and prevent if they have posted journal lines.

    This runs in SessionEvents.before_flush, BEFORE the flush plan is finalized.
    This is the correct place to prevent deletions because mapper-level events
    fire after the flush plan is already determined.

    R3 Compliance: Accounts referenced by posted journal entries cannot be deleted.
    """
    from finance_kernel.models.account import Account
    from finance_kernel.exceptions import AccountReferencedError
    from sqlalchemy import text

    # Check all objects marked for deletion
    for obj in list(session.deleted):  # Use list() to avoid set modification during iteration
        if not isinstance(obj, Account):
            continue

        # Disable autoflush during our query to prevent recursive flush
        with session.no_autoflush:
            result = session.execute(
                text("""
                    WITH RECURSIVE account_tree AS (
                        SELECT id FROM accounts WHERE id = :account_id
                        UNION ALL
                        SELECT a.id
                        FROM accounts a
                        JOIN account_tree t ON a.parent_id = t.id
                    )
                    SELECT EXISTS (
                        SELECT 1 FROM journal_lines jl
                        JOIN journal_entries je ON jl.journal_entry_id = je.id
                        WHERE jl.account_id IN (SELECT id FROM account_tree)
                        AND je.status = 'posted'
                    )
                """),
                {"account_id": str(obj.id)},
            )
            has_posted_refs = result.scalar()

        if has_posted_refs:
            logger.error(
                "immutability_violation_blocked",
                extra={
                    "entity_type": "Account",
                    "entity_id": str(obj.id),
                    "operation": "DELETE",
                    "reason": "account_has_posted_references",
                },
            )
            raise AccountReferencedError(account_id=str(obj.id))


def _check_journal_entry_immutability(mapper, connection, target):
    """
    Prevent updates to posted JournalEntry records.

    R10 Compliance: Posted journal entries are immutable.

    This check allows the posting workflow to complete (setting status to POSTED
    and posted_at), but prevents any subsequent modifications once the entry
    has been posted and flushed.

    Logic explained:
        1. If status is changing FROM posted -> anything: block (was already posted)
        2. If status is NOT changing AND is posted: block (already posted, other field changing)
        3. If status is changing TO posted (DRAFT->POSTED): allow (this IS the posting)
    """
    from finance_kernel.models.journal import JournalEntry, JournalEntryStatus

    if not isinstance(target, JournalEntry):
        return

    from sqlalchemy.orm.attributes import get_history

    # SQLAlchemy tracks attribute changes as "history":
    #   - history.deleted = old values (what was in DB before)
    #   - history.added = new values (what we're trying to set)
    #   - history.unchanged = values that didn't change
    status_history = get_history(target, "status")

    # Determine if this entry was ALREADY posted before this update began.
    # This is the key question: are we IN the posting workflow, or AFTER it?
    was_posted_before = False

    if status_history.deleted:
        # Status IS changing. Check what the OLD value was.
        # If old value was POSTED, someone is trying to modify a posted entry.
        old_status = status_history.deleted[0]
        if isinstance(old_status, str):
            was_posted_before = old_status == "posted"
        else:
            was_posted_before = old_status == JournalEntryStatus.POSTED
    elif not status_history.added:
        # Status is NOT changing (no deleted, no added = unchanged).
        # If current status is POSTED, the entry was already posted and
        # someone is trying to modify a different field.
        current_status = target.status
        if isinstance(current_status, str):
            was_posted_before = current_status == "posted"
        else:
            was_posted_before = current_status == JournalEntryStatus.POSTED

    # If the entry was already posted, block ALL field modifications
    # (except audit fields like updated_at)
    if was_posted_before:
        from sqlalchemy import inspect

        insp = inspect(target)
        for attr in insp.attrs:
            # Allow updated_at/updated_by_id to change (they're audit fields)
            if attr.key in ("updated_at", "updated_by_id"):
                continue
            hist = attr.history
            if hist.has_changes():
                logger.error(
                    "immutability_violation_blocked",
                    extra={
                        "invariant": "R10",
                        "entity_type": "JournalEntry",
                        "entity_id": str(target.id),
                        "operation": "UPDATE",
                        "field": attr.key,
                    },
                )
                raise ImmutabilityViolationError(
                    entity_type="JournalEntry",
                    entity_id=str(target.id),
                    reason=f"Cannot modify field '{attr.key}' on posted journal entry",
                )


def _check_journal_entry_delete(mapper, connection, target):
    """
    Prevent deletion of posted JournalEntry records.

    R10 Compliance: Posted journal entries cannot be deleted.
    """
    from finance_kernel.models.journal import JournalEntry, JournalEntryStatus

    if not isinstance(target, JournalEntry):
        return

    if target.status == JournalEntryStatus.POSTED:
        logger.error(
            "immutability_violation_blocked",
            extra={
                "invariant": "R10",
                "entity_type": "JournalEntry",
                "entity_id": str(target.id),
                "operation": "DELETE",
            },
        )
        raise ImmutabilityViolationError(
            entity_type="JournalEntry",
            entity_id=str(target.id),
            reason="Posted journal entries cannot be deleted",
        )


def _check_journal_line_immutability(mapper, connection, target):
    """
    Prevent updates to JournalLine when parent entry is posted.

    R10 Compliance: Journal lines are immutable once parent is posted.
    """
    from finance_kernel.models.journal import JournalLine, JournalEntryStatus

    if not isinstance(target, JournalLine):
        return

    # Check if parent entry is posted
    if target.entry and target.entry.status == JournalEntryStatus.POSTED:
        logger.error(
            "immutability_violation_blocked",
            extra={
                "invariant": "R10",
                "entity_type": "JournalLine",
                "entity_id": str(target.id),
                "operation": "UPDATE",
            },
        )
        raise ImmutabilityViolationError(
            entity_type="JournalLine",
            entity_id=str(target.id),
            reason="Journal lines cannot be modified after parent entry is posted",
        )


def _check_journal_line_delete(mapper, connection, target):
    """
    Prevent deletion of JournalLine when parent entry is posted.

    R10 Compliance: Journal lines cannot be deleted once parent is posted.
    """
    from finance_kernel.models.journal import JournalLine, JournalEntryStatus

    if not isinstance(target, JournalLine):
        return

    if target.entry and target.entry.status == JournalEntryStatus.POSTED:
        logger.error(
            "immutability_violation_blocked",
            extra={
                "invariant": "R10",
                "entity_type": "JournalLine",
                "entity_id": str(target.id),
                "operation": "DELETE",
            },
        )
        raise ImmutabilityViolationError(
            entity_type="JournalLine",
            entity_id=str(target.id),
            reason="Journal lines cannot be deleted after parent entry is posted",
        )


def _check_audit_event_immutability(mapper, connection, target):
    """
    Prevent any updates to AuditEvent records.

    R10 Compliance: Audit events are always immutable.
    """
    from finance_kernel.models.audit_event import AuditEvent

    if not isinstance(target, AuditEvent):
        return

    logger.error(
        "immutability_violation_blocked",
        extra={
            "invariant": "R10",
            "entity_type": "AuditEvent",
            "entity_id": str(target.id),
            "operation": "UPDATE",
        },
    )
    raise ImmutabilityViolationError(
        entity_type="AuditEvent",
        entity_id=str(target.id),
        reason="Audit events are immutable and cannot be modified",
    )


def _check_audit_event_delete(mapper, connection, target):
    """
    Prevent deletion of AuditEvent records.

    R10 Compliance: Audit events cannot be deleted.
    """
    from finance_kernel.models.audit_event import AuditEvent

    if not isinstance(target, AuditEvent):
        return

    logger.error(
        "immutability_violation_blocked",
        extra={
            "invariant": "R10",
            "entity_type": "AuditEvent",
            "entity_id": str(target.id),
            "operation": "DELETE",
        },
    )
    raise ImmutabilityViolationError(
        entity_type="AuditEvent",
        entity_id=str(target.id),
        reason="Audit events cannot be deleted",
    )


# =============================================================================
# Account Structural Immutability
# =============================================================================
#
# Accounts have two types of fields:
#   - Structural: account_type, normal_balance, code
#     These determine how the account behaves in financial reports.
#     Changing them after posting would corrupt historical reports.
#
#   - Non-structural: name, tags, is_active, parent_id
#     These are display/organizational. Changing them is safe.
#
# We only lock structural fields, and only after the account is referenced
# by a posted journal line. Before first use, accounts can be freely edited.
# =============================================================================

# Structural fields that become immutable once account is referenced by posted lines
ACCOUNT_STRUCTURAL_FIELDS = frozenset({"account_type", "normal_balance", "code"})


def _account_has_posted_references(connection, account_id: str) -> bool:
    """
    Check if an account OR ANY OF ITS DESCENDANTS has posted journal lines.

    This protects the financial integrity of the entire account hierarchy.
    If a parent account's structural fields change, it corrupts the meaning
    of all descendant balances in financial reports.

    Args:
        connection: SQLAlchemy connection
        account_id: The account ID to check

    Returns:
        True if account or any descendant has posted references, False otherwise
    """
    from sqlalchemy import text

    # Use a recursive CTE to find all descendants, then check for posted journal lines
    result = connection.execute(
        text("""
            WITH RECURSIVE account_tree AS (
                -- Base case: the account itself
                SELECT id FROM accounts WHERE id = :account_id
                UNION ALL
                -- Recursive case: all children
                SELECT a.id
                FROM accounts a
                JOIN account_tree t ON a.parent_id = t.id
            )
            SELECT EXISTS (
                SELECT 1 FROM journal_lines jl
                JOIN journal_entries je ON jl.journal_entry_id = je.id
                WHERE jl.account_id IN (SELECT id FROM account_tree)
                AND je.status = 'posted'
            )
        """),
        {"account_id": account_id},
    )
    return result.scalar()


def _check_account_structural_immutability(mapper, connection, target):
    """
    Prevent changes to structural fields on accounts referenced by posted journal lines.

    R10 Compliance: Account type, normal_balance, and code are immutable
    once the account is referenced by posted journal entries.

    Non-structural fields (name, tags, is_active, etc.) can still be modified.
    """
    from finance_kernel.models.account import Account
    from sqlalchemy.orm.attributes import get_history

    if not isinstance(target, Account):
        return

    # Check which structural fields have changed
    changed_structural_fields = []
    for field in ACCOUNT_STRUCTURAL_FIELDS:
        history = get_history(target, field)
        if history.has_changes():
            changed_structural_fields.append(field)

    # If no structural fields changed, allow the update
    if not changed_structural_fields:
        return

    # Structural fields changed - check if account has posted references
    if _account_has_posted_references(connection, str(target.id)):
        logger.error(
            "immutability_violation_blocked",
            extra={
                "invariant": "R10",
                "entity_type": "Account",
                "entity_id": str(target.id),
                "operation": "UPDATE",
                "fields": changed_structural_fields,
            },
        )
        raise ImmutabilityViolationError(
            entity_type="Account",
            entity_id=str(target.id),
            reason=(
                f"Cannot modify structural field(s) {changed_structural_fields} "
                "on account referenced by posted journal entries"
            ),
        )


def _is_last_rounding_account(connection, account_id: str, currency: str | None) -> bool:
    """
    Check if this is the last rounding account for the given currency.

    Args:
        connection: SQLAlchemy connection
        account_id: The account ID being deleted
        currency: The currency of the account (None for multi-currency)

    Returns:
        True if this is the last rounding account for the currency, False otherwise
    """
    from sqlalchemy import text

    if currency:
        # Check for currency-specific rounding accounts
        result = connection.execute(
            text("""
                SELECT COUNT(*) FROM accounts
                WHERE tags::text LIKE '%rounding%'
                AND currency = :currency
                AND id != :account_id
            """),
            {"currency": currency, "account_id": account_id},
        )
    else:
        # Check for global/multi-currency rounding accounts (currency IS NULL)
        result = connection.execute(
            text("""
                SELECT COUNT(*) FROM accounts
                WHERE tags::text LIKE '%rounding%'
                AND currency IS NULL
                AND id != :account_id
            """),
            {"account_id": account_id},
        )

    other_rounding_count = result.scalar()
    return other_rounding_count == 0


def _check_account_delete(mapper, connection, target):
    """
    Prevent deletion of accounts with posted journal lines or the last rounding account.

    R3 Compliance: Accounts referenced by posted journal entries cannot be deleted.
    This check runs BEFORE cascade delete, so we raise a clear AccountReferencedError
    rather than letting the cascade trigger ImmutabilityViolationError on journal lines.

    Invariants:
    1. Accounts with posted journal lines cannot be deleted
    2. At least one rounding account must exist per currency or ledger
    """
    from finance_kernel.models.account import Account, AccountTag
    from finance_kernel.exceptions import AccountReferencedError

    if not isinstance(target, Account):
        return

    # Rule 1: Accounts with posted journal lines cannot be deleted
    # This check must happen BEFORE cascade delete reaches journal lines
    if _account_has_posted_references(connection, str(target.id)):
        logger.error(
            "immutability_violation_blocked",
            extra={
                "entity_type": "Account",
                "entity_id": str(target.id),
                "operation": "DELETE",
                "reason": "account_has_posted_references",
            },
        )
        raise AccountReferencedError(account_id=str(target.id))

    # Rule 2: Check if this is a rounding account
    if target.tags is None or AccountTag.ROUNDING.value not in target.tags:
        return  # Not a rounding account, allow deletion

    # This is a rounding account - check if it's the last one for its currency
    if _is_last_rounding_account(connection, str(target.id), target.currency):
        currency_desc = target.currency if target.currency else "multi-currency/global"
        logger.error(
            "immutability_violation_blocked",
            extra={
                "entity_type": "Account",
                "entity_id": str(target.id),
                "operation": "DELETE",
                "reason": "last_rounding_account",
                "currency": currency_desc,
            },
        )
        raise ImmutabilityViolationError(
            entity_type="Account",
            entity_id=str(target.id),
            reason=(
                f"Cannot delete the last rounding account for {currency_desc}. "
                "At least one rounding account must exist per currency."
            ),
        )


# =============================================================================
# FiscalPeriod Closed Immutability
# =============================================================================


def _check_fiscal_period_immutability(mapper, connection, target):
    """
    Prevent modifications to closed/locked FiscalPeriod records.

    R10 Compliance: Closed fiscal periods are immutable (with controlled exceptions).

    Allowed status transitions (period close lifecycle):
        OPEN -> CLOSING      (begin_closing — R25 close lock)
        CLOSING -> OPEN      (cancel_closing — release lock)
        CLOSING -> CLOSED    (close_period — orchestrated close)
        OPEN -> CLOSED       (close_period — direct close without orchestrator)
        CLOSED -> LOCKED     (lock_period — year-end permanent seal)

    Blocked:
        CLOSED -> anything other than LOCKED
        LOCKED -> anything
        Any field change when status is CLOSED or LOCKED (except allowed transitions)
    """
    from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
    from sqlalchemy.orm.attributes import get_history

    if not isinstance(target, FiscalPeriod):
        return

    status_history = get_history(target, "status")

    # Check for allowed status transitions
    if status_history.deleted and status_history.added:
        old_status = status_history.deleted[0]
        new_status = status_history.added[0]

        # Normalize to enum values for comparison
        if isinstance(old_status, str):
            old_status = PeriodStatus(old_status)
        if isinstance(new_status, str):
            new_status = PeriodStatus(new_status)

        allowed_transitions = {
            (PeriodStatus.OPEN, PeriodStatus.CLOSING),
            (PeriodStatus.OPEN, PeriodStatus.CLOSED),
            (PeriodStatus.CLOSING, PeriodStatus.OPEN),
            (PeriodStatus.CLOSING, PeriodStatus.CLOSED),
            (PeriodStatus.CLOSED, PeriodStatus.LOCKED),
        }

        if (old_status, new_status) in allowed_transitions:
            return  # This transition is allowed

    # Check if the period WAS closed or locked before this update
    was_sealed_before = False
    if status_history.deleted:
        old_status = status_history.deleted[0]
        if isinstance(old_status, str):
            was_sealed_before = old_status in ("closed", "locked")
        else:
            was_sealed_before = old_status in (PeriodStatus.CLOSED, PeriodStatus.LOCKED)
    elif not status_history.added:
        # Status is not changing - check current value
        current_status = target.status
        if isinstance(current_status, str):
            was_sealed_before = current_status in ("closed", "locked")
        else:
            was_sealed_before = current_status in (PeriodStatus.CLOSED, PeriodStatus.LOCKED)

    # If period was already closed/locked, prevent any modifications
    if was_sealed_before:
        from sqlalchemy import inspect

        insp = inspect(target)
        for attr in insp.attrs:
            # Allow updated_at/updated_by_id to change (they're audit fields)
            if attr.key in ("updated_at", "updated_by_id"):
                continue
            hist = attr.history
            if hist.has_changes():
                logger.error(
                    "immutability_violation_blocked",
                    extra={
                        "invariant": "R10",
                        "entity_type": "FiscalPeriod",
                        "entity_id": str(target.id),
                        "operation": "UPDATE",
                        "field": attr.key,
                    },
                )
                raise ImmutabilityViolationError(
                    entity_type="FiscalPeriod",
                    entity_id=str(target.id),
                    reason=f"Cannot modify field '{attr.key}' on closed fiscal period",
                )


def _period_has_journal_entries(connection, start_date, end_date) -> bool:
    """
    Check if any journal entries have effective_date within the period's date range.

    Args:
        connection: SQLAlchemy connection
        start_date: Period start date
        end_date: Period end date

    Returns:
        True if period has journal entries, False otherwise
    """
    from sqlalchemy import text

    result = connection.execute(
        text("""
            SELECT EXISTS (
                SELECT 1 FROM journal_entries
                WHERE effective_date >= :start_date
                AND effective_date <= :end_date
            )
        """),
        {"start_date": start_date, "end_date": end_date},
    )
    return result.scalar()


def _check_fiscal_period_delete(mapper, connection, target):
    """
    Prevent deletion of FiscalPeriod records that are closed or have journal entries.

    R10 Compliance:
    - Closed fiscal periods cannot be deleted
    - Periods with journal entries cannot be deleted (data integrity)
    """
    from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus

    if not isinstance(target, FiscalPeriod):
        return

    # Rule 1: Closed or locked periods cannot be deleted
    if target.status in (PeriodStatus.CLOSED, PeriodStatus.LOCKED):
        logger.error(
            "immutability_violation_blocked",
            extra={
                "invariant": "R10",
                "entity_type": "FiscalPeriod",
                "entity_id": str(target.id),
                "operation": "DELETE",
                "reason": "period_closed",
            },
        )
        raise ImmutabilityViolationError(
            entity_type="FiscalPeriod",
            entity_id=str(target.id),
            reason="Closed fiscal periods cannot be deleted",
        )

    # Rule 2: Periods with journal entries cannot be deleted (even if open)
    if _period_has_journal_entries(connection, target.start_date, target.end_date):
        logger.error(
            "immutability_violation_blocked",
            extra={
                "invariant": "R10",
                "entity_type": "FiscalPeriod",
                "entity_id": str(target.id),
                "operation": "DELETE",
                "reason": "period_has_journal_entries",
            },
        )
        raise ImmutabilityViolationError(
            entity_type="FiscalPeriod",
            entity_id=str(target.id),
            reason="Cannot delete fiscal period with journal entries",
        )


# =============================================================================
# Dimension and DimensionValue Immutability
# =============================================================================

# Immutable fields on DimensionValue after creation
DIMENSION_VALUE_IMMUTABLE_FIELDS = frozenset({"code", "name", "dimension_code"})


def _check_dimension_value_immutability(mapper, connection, target):
    """
    Prevent changes to immutable fields on DimensionValue.

    Invariant: code, name, and dimension_code are immutable once created.
    This ensures audit trail integrity - dimension values referenced in
    historical journal lines must remain stable.
    """
    from finance_kernel.models.dimensions import DimensionValue
    from sqlalchemy.orm.attributes import get_history

    if not isinstance(target, DimensionValue):
        return

    # Check which immutable fields have changed
    changed_fields = []
    for field in DIMENSION_VALUE_IMMUTABLE_FIELDS:
        history = get_history(target, field)
        if history.has_changes():
            changed_fields.append(field)

    if changed_fields:
        logger.error(
            "immutability_violation_blocked",
            extra={
                "entity_type": "DimensionValue",
                "entity_id": str(target.id),
                "operation": "UPDATE",
                "fields": changed_fields,
            },
        )
        raise ImmutabilityViolationError(
            entity_type="DimensionValue",
            entity_id=str(target.id),
            reason=f"Cannot modify immutable field(s) {changed_fields} on DimensionValue",
        )


def _check_dimension_code_immutability(mapper, connection, target):
    """
    Prevent changes to Dimension.code once the dimension has values.

    The dimension code is the FK target for DimensionValue, so changing it
    would orphan existing values.
    """
    from finance_kernel.models.dimensions import Dimension
    from sqlalchemy.orm.attributes import get_history
    from sqlalchemy import text

    if not isinstance(target, Dimension):
        return

    # Check if code has changed
    code_history = get_history(target, "code")
    if not code_history.has_changes():
        return

    old_code = code_history.deleted[0] if code_history.deleted else None
    if old_code is None:
        return

    # Check if dimension has any values
    result = connection.execute(
        text("SELECT EXISTS (SELECT 1 FROM dimension_values WHERE dimension_code = :code)"),
        {"code": old_code},
    )
    has_values = result.scalar()

    if has_values:
        logger.error(
            "immutability_violation_blocked",
            extra={
                "entity_type": "Dimension",
                "entity_id": str(target.id),
                "operation": "UPDATE",
                "field": "code",
            },
        )
        raise ImmutabilityViolationError(
            entity_type="Dimension",
            entity_id=str(target.id),
            reason=f"Cannot change code on dimension with existing values (old code: {old_code})",
        )


# =============================================================================
# ExchangeRate Immutability
# =============================================================================
#
# Exchange rates convert between currencies. If you change a rate after it's
# been used in a journal entry, you silently change the value of that entry.
#
# Example of the attack this prevents:
#   1. Post entry: 100 USD -> 85 EUR at rate 0.85
#   2. Later, change rate to 0.90
#   3. Historical reports now show 90 EUR instead of 85 EUR
#   4. 5 EUR has "disappeared" with no audit trail
#
# Once an exchange rate is referenced by ANY journal line, its rate value
# is frozen forever. Create a new rate record for new transactions.
# =============================================================================


def _exchange_rate_is_referenced(connection, rate_id: str) -> int:
    """
    Check if an ExchangeRate is referenced by any JournalLine.

    Args:
        connection: SQLAlchemy connection
        rate_id: The exchange rate ID to check

    Returns:
        Count of JournalLines referencing this rate
    """
    from sqlalchemy import text

    result = connection.execute(
        text("""
            SELECT COUNT(*) FROM journal_lines
            WHERE exchange_rate_id = :rate_id
        """),
        {"rate_id": rate_id},
    )
    return result.scalar() or 0


def _validate_exchange_rate_value(rate_value, rate_id: str = None):
    """
    Validate that an exchange rate value is positive and non-zero.

    Args:
        rate_value: The rate value to validate
        rate_id: Optional rate ID for error messages

    Raises:
        InvalidExchangeRateError: If rate is zero, negative, or invalid
    """
    from decimal import Decimal
    from finance_kernel.exceptions import InvalidExchangeRateError

    if rate_value is None:
        raise InvalidExchangeRateError(
            rate_value="None",
            reason="Exchange rate cannot be null"
        )

    # Convert to Decimal if needed
    if not isinstance(rate_value, Decimal):
        try:
            rate_value = Decimal(str(rate_value))
        except Exception:
            raise InvalidExchangeRateError(
                rate_value=str(rate_value),
                reason="Exchange rate must be a valid number"
            )

    if rate_value <= Decimal("0"):
        raise InvalidExchangeRateError(
            rate_value=str(rate_value),
            reason="Exchange rate must be positive (greater than zero)"
        )

    # Check for unreasonably large rates (potential data entry error)
    if rate_value > Decimal("1000000"):
        raise InvalidExchangeRateError(
            rate_value=str(rate_value),
            reason="Exchange rate exceeds maximum allowed value (1,000,000)"
        )


def _check_exchange_rate_insert(mapper, connection, target):
    """
    Validate exchange rate value on insert.

    Ensures new exchange rates have valid positive values.
    """
    from finance_kernel.models.exchange_rate import ExchangeRate

    if not isinstance(target, ExchangeRate):
        return

    _validate_exchange_rate_value(target.rate, str(target.id) if target.id else "new")


def _check_exchange_rate_immutability(mapper, connection, target):
    """
    Prevent updates to ExchangeRate records that have been used in journal lines.

    Once an ExchangeRate is referenced by any JournalLine (via exchange_rate_id),
    its rate value becomes immutable. This prevents retroactive manipulation
    of historical multi-currency transactions.

    Also validates that rate values are always positive.
    """
    from finance_kernel.models.exchange_rate import ExchangeRate
    from finance_kernel.exceptions import ExchangeRateImmutableError
    from sqlalchemy.orm.attributes import get_history

    if not isinstance(target, ExchangeRate):
        return

    # Always validate that rate is positive
    _validate_exchange_rate_value(target.rate, str(target.id))

    # Check if the rate value is changing
    rate_history = get_history(target, "rate")
    if not rate_history.has_changes():
        return  # Rate not changing, allow other updates

    # Rate is changing - check if this rate has been used
    reference_count = _exchange_rate_is_referenced(connection, str(target.id))

    if reference_count > 0:
        logger.error(
            "immutability_violation_blocked",
            extra={
                "entity_type": "ExchangeRate",
                "entity_id": str(target.id),
                "operation": "UPDATE",
                "reference_count": reference_count,
                "from_currency": target.from_currency,
                "to_currency": target.to_currency,
            },
        )
        raise ExchangeRateImmutableError(
            rate_id=str(target.id),
            from_currency=target.from_currency,
            to_currency=target.to_currency,
        )


def _check_exchange_rate_delete(mapper, connection, target):
    """
    Prevent deletion of ExchangeRate records that are referenced by journal lines.

    Exchange rates cannot be deleted once used, as this would break the audit
    trail and make historical entries uninterpretable.
    """
    from finance_kernel.models.exchange_rate import ExchangeRate
    from finance_kernel.exceptions import ExchangeRateReferencedError

    if not isinstance(target, ExchangeRate):
        return

    reference_count = _exchange_rate_is_referenced(connection, str(target.id))

    if reference_count > 0:
        logger.error(
            "immutability_violation_blocked",
            extra={
                "entity_type": "ExchangeRate",
                "entity_id": str(target.id),
                "operation": "DELETE",
                "reference_count": reference_count,
            },
        )
        raise ExchangeRateReferencedError(
            rate_id=str(target.id),
            reference_count=reference_count,
        )


# =============================================================================
# Subledger Entry Immutability
# =============================================================================
#
# Once a subledger entry is posted (posted_at is set), its financial fields
# are immutable. This mirrors the JournalEntry pattern: financial truth is
# frozen, corrections happen via reversal entries.
#
# Reconciliation fields (reconciliation_status, reconciled_amount) are
# ALLOWED to change even after posting — reconciliation is a post-posting
# lifecycle event, not a financial mutation.
#
# ReconciliationFailureReportModel is always immutable (append-only audit
# artifact, like AuditEvent).
# =============================================================================

# Fields that may be updated on posted subledger entries (reconciliation lifecycle)
SUBLEDGER_ENTRY_MUTABLE_AFTER_POST = frozenset({
    "reconciliation_status",
    "reconciled_amount",
    "updated_at",
    "updated_by_id",
})


def _check_subledger_entry_immutability(mapper, connection, target):
    """
    Prevent updates to financial fields on posted SubledgerEntryModel records.

    R10 Compliance: Posted subledger entries are immutable except for
    reconciliation lifecycle fields.

    A subledger entry is considered "posted" when posted_at is not None.
    After posting, only reconciliation_status and reconciled_amount may change.
    """
    from finance_kernel.models.subledger import SubledgerEntryModel
    from sqlalchemy.orm.attributes import get_history

    if not isinstance(target, SubledgerEntryModel):
        return

    # Check if the entry was already posted before this update
    posted_at_history = get_history(target, "posted_at")

    was_posted_before = False
    if posted_at_history.deleted:
        # posted_at is changing — if old value was not None, it was already posted
        was_posted_before = posted_at_history.deleted[0] is not None
    elif not posted_at_history.added:
        # posted_at is NOT changing — check current value
        was_posted_before = target.posted_at is not None

    if not was_posted_before:
        return  # Entry not yet posted, all changes allowed

    # Entry was already posted — only reconciliation fields may change
    from sqlalchemy import inspect

    insp = inspect(target)
    for attr in insp.attrs:
        if attr.key in SUBLEDGER_ENTRY_MUTABLE_AFTER_POST:
            continue
        hist = attr.history
        if hist.has_changes():
            logger.error(
                "immutability_violation_blocked",
                extra={
                    "invariant": "R10",
                    "entity_type": "SubledgerEntry",
                    "entity_id": str(target.id),
                    "operation": "UPDATE",
                    "field": attr.key,
                },
            )
            raise ImmutabilityViolationError(
                entity_type="SubledgerEntry",
                entity_id=str(target.id),
                reason=f"Cannot modify field '{attr.key}' on posted subledger entry",
            )


def _check_subledger_entry_delete(mapper, connection, target):
    """
    Prevent deletion of posted SubledgerEntryModel records.

    R10 Compliance: Posted subledger entries cannot be deleted.
    """
    from finance_kernel.models.subledger import SubledgerEntryModel

    if not isinstance(target, SubledgerEntryModel):
        return

    if target.posted_at is not None:
        logger.error(
            "immutability_violation_blocked",
            extra={
                "invariant": "R10",
                "entity_type": "SubledgerEntry",
                "entity_id": str(target.id),
                "operation": "DELETE",
            },
        )
        raise ImmutabilityViolationError(
            entity_type="SubledgerEntry",
            entity_id=str(target.id),
            reason="Posted subledger entries cannot be deleted",
        )


def _check_reconciliation_failure_report_immutability(mapper, connection, target):
    """
    Prevent any updates to ReconciliationFailureReportModel records.

    These are append-only audit artifacts (SL-G6, F11). Once created,
    they must never be modified.
    """
    from finance_kernel.models.subledger import ReconciliationFailureReportModel

    if not isinstance(target, ReconciliationFailureReportModel):
        return

    logger.error(
        "immutability_violation_blocked",
        extra={
            "invariant": "R10",
            "entity_type": "ReconciliationFailureReport",
            "entity_id": str(target.id),
            "operation": "UPDATE",
        },
    )
    raise ImmutabilityViolationError(
        entity_type="ReconciliationFailureReport",
        entity_id=str(target.id),
        reason="Reconciliation failure reports are immutable audit artifacts",
    )


def _check_reconciliation_failure_report_delete(mapper, connection, target):
    """
    Prevent deletion of ReconciliationFailureReportModel records.

    These are append-only audit artifacts and cannot be deleted.
    """
    from finance_kernel.models.subledger import ReconciliationFailureReportModel

    if not isinstance(target, ReconciliationFailureReportModel):
        return

    logger.error(
        "immutability_violation_blocked",
        extra={
            "invariant": "R10",
            "entity_type": "ReconciliationFailureReport",
            "entity_id": str(target.id),
            "operation": "DELETE",
        },
    )
    raise ImmutabilityViolationError(
        entity_type="ReconciliationFailureReport",
        entity_id=str(target.id),
        reason="Reconciliation failure reports cannot be deleted",
    )


def register_immutability_listeners():
    """
    Register all immutability enforcement event listeners.

    R10 Compliance: This function must be called during application
    initialization to enforce append-only persistence rules.

    Call this after all models are imported but before any database
    operations begin.
    """
    from finance_kernel.models.journal import JournalEntry, JournalLine
    from finance_kernel.models.audit_event import AuditEvent
    from finance_kernel.models.account import Account
    from finance_kernel.models.fiscal_period import FiscalPeriod
    from finance_kernel.models.dimensions import Dimension, DimensionValue
    from finance_kernel.models.exchange_rate import ExchangeRate
    from finance_kernel.models.subledger import (
        SubledgerEntryModel,
        ReconciliationFailureReportModel,
    )

    # Session-level before_flush event for checking deletions before flush plan
    # This is necessary for Account deletion checks because mapper-level events
    # fire after the flush plan is finalized (too late to prevent the deletion)
    event.listen(Session, "before_flush", _check_account_deletion_before_flush)

    # JournalEntry listeners
    event.listen(JournalEntry, "before_update", _check_journal_entry_immutability)
    event.listen(JournalEntry, "before_delete", _check_journal_entry_delete)

    # JournalLine listeners
    event.listen(JournalLine, "before_update", _check_journal_line_immutability)
    event.listen(JournalLine, "before_delete", _check_journal_line_delete)

    # AuditEvent listeners
    event.listen(AuditEvent, "before_update", _check_audit_event_immutability)
    event.listen(AuditEvent, "before_delete", _check_audit_event_delete)

    # Account listeners
    event.listen(Account, "before_update", _check_account_structural_immutability)
    # Note: Account deletion is now handled in before_flush, not before_delete

    # FiscalPeriod listeners
    event.listen(FiscalPeriod, "before_update", _check_fiscal_period_immutability)
    event.listen(FiscalPeriod, "before_delete", _check_fiscal_period_delete)

    # Dimension listeners
    event.listen(Dimension, "before_update", _check_dimension_code_immutability)

    # DimensionValue listeners
    event.listen(DimensionValue, "before_update", _check_dimension_value_immutability)

    # ExchangeRate listeners
    event.listen(ExchangeRate, "before_insert", _check_exchange_rate_insert)
    event.listen(ExchangeRate, "before_update", _check_exchange_rate_immutability)
    event.listen(ExchangeRate, "before_delete", _check_exchange_rate_delete)

    # SubledgerEntry listeners
    event.listen(SubledgerEntryModel, "before_update", _check_subledger_entry_immutability)
    event.listen(SubledgerEntryModel, "before_delete", _check_subledger_entry_delete)

    # ReconciliationFailureReport listeners (always immutable)
    event.listen(
        ReconciliationFailureReportModel,
        "before_update",
        _check_reconciliation_failure_report_immutability,
    )
    event.listen(
        ReconciliationFailureReportModel,
        "before_delete",
        _check_reconciliation_failure_report_delete,
    )


def _safe_remove_listener(target, event_name, listener_fn):
    """
    Safely remove an event listener, ignoring if not registered.

    This prevents errors when unregistering listeners that may not have been
    registered (e.g., in test scenarios with custom setup/teardown).
    """
    if event.contains(target, event_name, listener_fn):
        event.remove(target, event_name, listener_fn)


def unregister_immutability_listeners():
    """
    Remove immutability enforcement event listeners.

    WARNING: Only use this in tests where you need to intentionally
    violate immutability rules to verify detection.
    """
    from finance_kernel.models.journal import JournalEntry, JournalLine
    from finance_kernel.models.audit_event import AuditEvent
    from finance_kernel.models.account import Account
    from finance_kernel.models.fiscal_period import FiscalPeriod
    from finance_kernel.models.dimensions import Dimension, DimensionValue
    from finance_kernel.models.exchange_rate import ExchangeRate
    from finance_kernel.models.subledger import (
        SubledgerEntryModel,
        ReconciliationFailureReportModel,
    )

    # Session-level before_flush event
    _safe_remove_listener(Session, "before_flush", _check_account_deletion_before_flush)

    # JournalEntry listeners
    _safe_remove_listener(JournalEntry, "before_update", _check_journal_entry_immutability)
    _safe_remove_listener(JournalEntry, "before_delete", _check_journal_entry_delete)

    # JournalLine listeners
    _safe_remove_listener(JournalLine, "before_update", _check_journal_line_immutability)
    _safe_remove_listener(JournalLine, "before_delete", _check_journal_line_delete)

    # AuditEvent listeners
    _safe_remove_listener(AuditEvent, "before_update", _check_audit_event_immutability)
    _safe_remove_listener(AuditEvent, "before_delete", _check_audit_event_delete)

    # Account listeners
    _safe_remove_listener(Account, "before_update", _check_account_structural_immutability)
    # Note: Account deletion is now handled in before_flush, not before_delete

    # FiscalPeriod listeners
    _safe_remove_listener(FiscalPeriod, "before_update", _check_fiscal_period_immutability)
    _safe_remove_listener(FiscalPeriod, "before_delete", _check_fiscal_period_delete)

    # Dimension listeners
    _safe_remove_listener(Dimension, "before_update", _check_dimension_code_immutability)

    # DimensionValue listeners
    _safe_remove_listener(DimensionValue, "before_update", _check_dimension_value_immutability)

    # ExchangeRate listeners
    _safe_remove_listener(ExchangeRate, "before_insert", _check_exchange_rate_insert)
    _safe_remove_listener(ExchangeRate, "before_update", _check_exchange_rate_immutability)
    _safe_remove_listener(ExchangeRate, "before_delete", _check_exchange_rate_delete)

    # SubledgerEntry listeners
    _safe_remove_listener(SubledgerEntryModel, "before_update", _check_subledger_entry_immutability)
    _safe_remove_listener(SubledgerEntryModel, "before_delete", _check_subledger_entry_delete)

    # ReconciliationFailureReport listeners
    _safe_remove_listener(
        ReconciliationFailureReportModel,
        "before_update",
        _check_reconciliation_failure_report_immutability,
    )
    _safe_remove_listener(
        ReconciliationFailureReportModel,
        "before_delete",
        _check_reconciliation_failure_report_delete,
    )
