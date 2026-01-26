"""
Append-only persistence enforcement (R10 Compliance).

This module provides SQLAlchemy ORM event listeners that enforce
immutability rules on critical financial records:

- JournalEntry: Immutable after status becomes POSTED
- JournalLine: Immutable when parent entry is POSTED
- AuditEvent: Always immutable after insert

Updates and deletes are forbidden at the ORM layer.

Hard invariants:
- Posted JournalEntry records cannot be modified
- JournalLine records cannot be modified once parent is posted
- AuditEvent records are append-only - no modifications ever
- Violations raise ImmutabilityViolationError
"""

from sqlalchemy import event
from sqlalchemy.orm import Session, UOWTransaction

from finance_kernel.exceptions import ImmutabilityViolationError


def _check_journal_entry_immutability(mapper, connection, target):
    """
    Prevent updates to posted JournalEntry records.

    R10 Compliance: Posted journal entries are immutable.

    This check allows the posting workflow to complete (setting status to POSTED
    and posted_at), but prevents any subsequent modifications once the entry
    has been posted and flushed.
    """
    from finance_kernel.models.journal import JournalEntry, JournalEntryStatus

    if not isinstance(target, JournalEntry):
        return

    # Get the history of the status attribute
    from sqlalchemy.orm.attributes import get_history

    status_history = get_history(target, "status")

    # Check if the entry WAS posted before this update (status was POSTED in DB)
    was_posted_before = False
    if status_history.deleted:
        # Status is changing - check what it was before
        old_status = status_history.deleted[0]
        if isinstance(old_status, str):
            was_posted_before = old_status == "posted"
        else:
            was_posted_before = old_status == JournalEntryStatus.POSTED
    elif not status_history.added:
        # Status is not changing - check current value
        # If unchanged and POSTED, entry was already posted
        current_status = target.status
        if isinstance(current_status, str):
            was_posted_before = current_status == "posted"
        else:
            was_posted_before = current_status == JournalEntryStatus.POSTED

    # If entry was already posted, prevent any modifications
    if was_posted_before:
        from sqlalchemy import inspect

        insp = inspect(target)
        for attr in insp.attrs:
            # Allow updated_at/updated_by_id to change (they're audit fields)
            if attr.key in ("updated_at", "updated_by_id"):
                continue
            hist = attr.history
            if hist.has_changes():
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

    raise ImmutabilityViolationError(
        entity_type="AuditEvent",
        entity_id=str(target.id),
        reason="Audit events cannot be deleted",
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

    # JournalEntry listeners
    event.listen(JournalEntry, "before_update", _check_journal_entry_immutability)
    event.listen(JournalEntry, "before_delete", _check_journal_entry_delete)

    # JournalLine listeners
    event.listen(JournalLine, "before_update", _check_journal_line_immutability)
    event.listen(JournalLine, "before_delete", _check_journal_line_delete)

    # AuditEvent listeners
    event.listen(AuditEvent, "before_update", _check_audit_event_immutability)
    event.listen(AuditEvent, "before_delete", _check_audit_event_delete)


def unregister_immutability_listeners():
    """
    Remove immutability enforcement event listeners.

    WARNING: Only use this in tests where you need to intentionally
    violate immutability rules to verify detection.
    """
    from finance_kernel.models.journal import JournalEntry, JournalLine
    from finance_kernel.models.audit_event import AuditEvent

    # JournalEntry listeners
    event.remove(JournalEntry, "before_update", _check_journal_entry_immutability)
    event.remove(JournalEntry, "before_delete", _check_journal_entry_delete)

    # JournalLine listeners
    event.remove(JournalLine, "before_update", _check_journal_line_immutability)
    event.remove(JournalLine, "before_delete", _check_journal_line_delete)

    # AuditEvent listeners
    event.remove(AuditEvent, "before_update", _check_audit_event_immutability)
    event.remove(AuditEvent, "before_delete", _check_audit_event_delete)
