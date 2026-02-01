"""ORM-level immutability enforcement (R10 compliance -- layer 1 of 2)."""

from sqlalchemy import event
from sqlalchemy.orm import Session, UOWTransaction

from finance_kernel.exceptions import ImmutabilityViolationError
from finance_kernel.logging_config import get_logger

logger = get_logger("db.immutability")


def _check_account_deletion_before_flush(session, flush_context, instances):
    """Prevent account deletions when posted journal lines exist (R3)."""
    from sqlalchemy import text

    from finance_kernel.exceptions import AccountReferencedError
    from finance_kernel.models.account import Account

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
    """Prevent updates to posted JournalEntry records (R10)."""
    from finance_kernel.models.journal import JournalEntry, JournalEntryStatus

    if not isinstance(target, JournalEntry):
        return

    from sqlalchemy.orm.attributes import get_history

    status_history = get_history(target, "status")

    # Determine if this entry was ALREADY posted before this update began.
    was_posted_before = False

    if status_history.deleted:
        # Status IS changing. Check what the OLD value was.
        old_status = status_history.deleted[0]
        if isinstance(old_status, str):
            was_posted_before = old_status == "posted"
        else:
            was_posted_before = old_status == JournalEntryStatus.POSTED
    elif not status_history.added:
        # Status is NOT changing -- if current is POSTED, block modifications.
        current_status = target.status
        if isinstance(current_status, str):
            was_posted_before = current_status == "posted"
        else:
            was_posted_before = current_status == JournalEntryStatus.POSTED

    if was_posted_before:
        from sqlalchemy import inspect

        insp = inspect(target)
        for attr in insp.attrs:
            # Allow updated_at/updated_by_id to change (audit fields)
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
    """Prevent deletion of posted JournalEntry records (R10)."""
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
    """Prevent updates to JournalLine when parent entry is posted (R10)."""
    from finance_kernel.models.journal import JournalEntryStatus, JournalLine

    if not isinstance(target, JournalLine):
        return

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
    """Prevent deletion of JournalLine when parent entry is posted (R10)."""
    from finance_kernel.models.journal import JournalEntryStatus, JournalLine

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
    """Prevent any updates to AuditEvent records (R10)."""
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
    """Prevent deletion of AuditEvent records (R10)."""
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


# Structural fields that become immutable once account is referenced by posted lines
ACCOUNT_STRUCTURAL_FIELDS = frozenset({"account_type", "normal_balance", "code"})


def _account_has_posted_references(connection, account_id: str) -> bool:
    """Check if an account or any descendant has posted journal lines."""
    from sqlalchemy import text

    result = connection.execute(
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
        {"account_id": account_id},
    )
    return result.scalar()


def _check_account_structural_immutability(mapper, connection, target):
    """Prevent changes to structural fields on accounts referenced by posted lines (R10)."""
    from sqlalchemy.orm.attributes import get_history

    from finance_kernel.models.account import Account

    if not isinstance(target, Account):
        return

    changed_structural_fields = []
    for field in ACCOUNT_STRUCTURAL_FIELDS:
        history = get_history(target, field)
        if history.has_changes():
            changed_structural_fields.append(field)

    if not changed_structural_fields:
        return

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
    """Check if this is the last rounding account for the given currency."""
    from sqlalchemy import text

    if currency:
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
    """Prevent deletion of accounts with posted lines or last rounding account (R3)."""
    from finance_kernel.exceptions import AccountReferencedError
    from finance_kernel.models.account import Account, AccountTag

    if not isinstance(target, Account):
        return

    # Rule 1: Accounts with posted journal lines cannot be deleted
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


def _check_fiscal_period_immutability(mapper, connection, target):
    """Prevent modifications to closed/locked FiscalPeriod records (R10)."""
    from sqlalchemy.orm.attributes import get_history

    from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus

    if not isinstance(target, FiscalPeriod):
        return

    status_history = get_history(target, "status")

    # Check for allowed status transitions
    if status_history.deleted and status_history.added:
        old_status = status_history.deleted[0]
        new_status = status_history.added[0]

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
        current_status = target.status
        if isinstance(current_status, str):
            was_sealed_before = current_status in ("closed", "locked")
        else:
            was_sealed_before = current_status in (PeriodStatus.CLOSED, PeriodStatus.LOCKED)

    if was_sealed_before:
        from sqlalchemy import inspect

        insp = inspect(target)
        for attr in insp.attrs:
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
    """Check if any journal entries fall within the period's date range."""
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
    """Prevent deletion of closed periods or periods with journal entries (R10)."""
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


# Immutable fields on DimensionValue after creation
DIMENSION_VALUE_IMMUTABLE_FIELDS = frozenset({"code", "name", "dimension_code"})


def _check_dimension_value_immutability(mapper, connection, target):
    """Prevent changes to immutable fields on DimensionValue."""
    from sqlalchemy.orm.attributes import get_history

    from finance_kernel.models.dimensions import DimensionValue

    if not isinstance(target, DimensionValue):
        return

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
    """Prevent changes to Dimension.code once the dimension has values."""
    from sqlalchemy import text
    from sqlalchemy.orm.attributes import get_history

    from finance_kernel.models.dimensions import Dimension

    if not isinstance(target, Dimension):
        return

    code_history = get_history(target, "code")
    if not code_history.has_changes():
        return

    old_code = code_history.deleted[0] if code_history.deleted else None
    if old_code is None:
        return

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


def _exchange_rate_is_referenced(connection, rate_id: str) -> int:
    """Check if an ExchangeRate is referenced by any JournalLine."""
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
    """Validate that an exchange rate value is positive and non-zero."""
    from decimal import Decimal

    from finance_kernel.exceptions import InvalidExchangeRateError

    if rate_value is None:
        raise InvalidExchangeRateError(
            rate_value="None",
            reason="Exchange rate cannot be null"
        )

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

    if rate_value > Decimal("1000000"):
        raise InvalidExchangeRateError(
            rate_value=str(rate_value),
            reason="Exchange rate exceeds maximum allowed value (1,000,000)"
        )


def _check_exchange_rate_insert(mapper, connection, target):
    """Validate exchange rate value on insert."""
    from finance_kernel.models.exchange_rate import ExchangeRate

    if not isinstance(target, ExchangeRate):
        return

    _validate_exchange_rate_value(target.rate, str(target.id) if target.id else "new")


def _check_exchange_rate_immutability(mapper, connection, target):
    """Prevent updates to referenced ExchangeRate records."""
    from sqlalchemy.orm.attributes import get_history

    from finance_kernel.exceptions import ExchangeRateImmutableError
    from finance_kernel.models.exchange_rate import ExchangeRate

    if not isinstance(target, ExchangeRate):
        return

    # Always validate that rate is positive
    _validate_exchange_rate_value(target.rate, str(target.id))

    rate_history = get_history(target, "rate")
    if not rate_history.has_changes():
        return  # Rate not changing, allow other updates

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
    """Prevent deletion of referenced ExchangeRate records."""
    from finance_kernel.exceptions import ExchangeRateReferencedError
    from finance_kernel.models.exchange_rate import ExchangeRate

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


# Fields that may be updated on posted subledger entries (reconciliation lifecycle)
SUBLEDGER_ENTRY_MUTABLE_AFTER_POST = frozenset({
    "reconciliation_status",
    "reconciled_amount",
    "updated_at",
    "updated_by_id",
})


def _check_subledger_entry_immutability(mapper, connection, target):
    """Prevent updates to financial fields on posted SubledgerEntryModel (R10)."""
    from sqlalchemy.orm.attributes import get_history

    from finance_kernel.models.subledger import SubledgerEntryModel

    if not isinstance(target, SubledgerEntryModel):
        return

    posted_at_history = get_history(target, "posted_at")

    was_posted_before = False
    if posted_at_history.deleted:
        was_posted_before = posted_at_history.deleted[0] is not None
    elif not posted_at_history.added:
        was_posted_before = target.posted_at is not None

    if not was_posted_before:
        return  # Entry not yet posted, all changes allowed

    # Entry was already posted -- only reconciliation fields may change
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
    """Prevent deletion of posted SubledgerEntryModel records (R10)."""
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
    """Prevent any updates to ReconciliationFailureReportModel (SL-G6, F11)."""
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
    """Prevent deletion of ReconciliationFailureReportModel records."""
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
    """Register all immutability enforcement event listeners (R10)."""
    from finance_kernel.models.account import Account
    from finance_kernel.models.audit_event import AuditEvent
    from finance_kernel.models.dimensions import Dimension, DimensionValue
    from finance_kernel.models.exchange_rate import ExchangeRate
    from finance_kernel.models.fiscal_period import FiscalPeriod
    from finance_kernel.models.journal import JournalEntry, JournalLine
    from finance_kernel.models.subledger import (
        ReconciliationFailureReportModel,
        SubledgerEntryModel,
    )

    # Session-level before_flush for account deletion checks
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
    """Safely remove an event listener, ignoring if not registered."""
    if event.contains(target, event_name, listener_fn):
        event.remove(target, event_name, listener_fn)


def unregister_immutability_listeners():
    """Remove immutability enforcement listeners. WARNING: tests only."""
    from finance_kernel.models.account import Account
    from finance_kernel.models.audit_event import AuditEvent
    from finance_kernel.models.dimensions import Dimension, DimensionValue
    from finance_kernel.models.exchange_rate import ExchangeRate
    from finance_kernel.models.fiscal_period import FiscalPeriod
    from finance_kernel.models.journal import JournalEntry, JournalLine
    from finance_kernel.models.subledger import (
        ReconciliationFailureReportModel,
        SubledgerEntryModel,
    )

    _safe_remove_listener(Session, "before_flush", _check_account_deletion_before_flush)

    _safe_remove_listener(JournalEntry, "before_update", _check_journal_entry_immutability)
    _safe_remove_listener(JournalEntry, "before_delete", _check_journal_entry_delete)

    _safe_remove_listener(JournalLine, "before_update", _check_journal_line_immutability)
    _safe_remove_listener(JournalLine, "before_delete", _check_journal_line_delete)

    _safe_remove_listener(AuditEvent, "before_update", _check_audit_event_immutability)
    _safe_remove_listener(AuditEvent, "before_delete", _check_audit_event_delete)

    _safe_remove_listener(Account, "before_update", _check_account_structural_immutability)

    _safe_remove_listener(FiscalPeriod, "before_update", _check_fiscal_period_immutability)
    _safe_remove_listener(FiscalPeriod, "before_delete", _check_fiscal_period_delete)

    _safe_remove_listener(Dimension, "before_update", _check_dimension_code_immutability)

    _safe_remove_listener(DimensionValue, "before_update", _check_dimension_value_immutability)

    _safe_remove_listener(ExchangeRate, "before_insert", _check_exchange_rate_insert)
    _safe_remove_listener(ExchangeRate, "before_update", _check_exchange_rate_immutability)
    _safe_remove_listener(ExchangeRate, "before_delete", _check_exchange_rate_delete)

    _safe_remove_listener(SubledgerEntryModel, "before_update", _check_subledger_entry_immutability)
    _safe_remove_listener(SubledgerEntryModel, "before_delete", _check_subledger_entry_delete)

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
