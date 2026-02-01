"""
finance_services.subledger_service -- Stateful subledger service base class.

Responsibility:
    Provide the abstract base class (ABC) for subledger management services.
    Define the interface (post, get_balance, get_open_items) and shared
    logic (reconcile, calculate_balance, validate_entry) for AP, AR, Bank,
    Inventory, WIP, and Fixed Assets subledgers.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    Concrete implementations (APSubledgerService, ARSubledgerService, etc.)
    receive a Session via constructor injection and delegate to
    SubledgerSelector for persistence.  Pure domain types (SubledgerEntry,
    SubledgerBalance, ReconciliationResult) live in finance_engines.subledger.

Invariants enforced:
    - SL-G1 (single-sided entries): validate_entry delegates to
      SubledgerEntry.__post_init__ which enforces exactly one of debit/credit.
    - SL-G2 (GL linkage): post() requires gl_entry_id; the concrete
      implementations persist this link.
    - SL-G5 (immutable entries): SubledgerEntry is a frozen dataclass;
      reconcile() returns a new ReconciliationResult rather than mutating.
    - R16 (ISO 4217): reconcile() validates currency match between entries.

Failure modes:
    - ValueError from reconcile() if entries are from different subledgers,
      different entities, different currencies, or are not open.
    - ValueError from calculate_balance() if entries list is empty or
      as_of_date is None (clock injection enforcement).

Audit relevance:
    Every post() call is logged with entry_id, subledger_type, entity_id,
    and journal_entry_id.  Reconciliation events are logged with amounts
    and timing.

Usage:
    from finance_services.subledger_service import SubledgerService
    from finance_engines.subledger import SubledgerEntry

    class APSubledgerService(SubledgerService):
        subledger_type = "AP"

        def post(self, entry, gl_entry_id):
            ...

        def get_balance(self, entity_id, as_of_date=None, currency=None):
            ...

        def get_open_items(self, entity_id, currency=None):
            ...
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Generic, TypeVar
from uuid import UUID, uuid4

from finance_engines.subledger import (
    EntryDirection,
    ReconciliationResult,
    ReconciliationStatus,
    SubledgerBalance,
    SubledgerEntry,
)
from finance_kernel.domain.subledger_control import SubledgerType
from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger

logger = get_logger("services.subledger")


# Type variable for entity type (Vendor, Customer, Bank, etc.)
EntityT = TypeVar("EntityT")


class SubledgerService(ABC, Generic[EntityT]):
    """
    Base class for subledger management.

    Contract:
        Abstract class -- each subledger type (AP, AR, Bank, Inventory,
        WIP) extends this and implements specific business rules.
        Concrete implementations receive Session via constructor injection.
    Guarantees:
        - ``reconcile`` validates subledger type match, entity match,
          currency match, and open status before producing a
          ReconciliationResult.
        - ``calculate_balance`` respects the normal-balance side
          convention (credit-normal for AP/PAYROLL, debit-normal for
          AR/BANK/INVENTORY/WIP).
        - ``validate_entry`` checks required fields and non-zero amount.
    Non-goals:
        - Does not persist entries directly; persistence is the concrete
          implementation's responsibility.
        - Does not manage transactions; session lifecycle is the caller's
          responsibility.
    """

    # Override in subclass with canonical SubledgerType enum value
    subledger_type: SubledgerType

    @abstractmethod
    def post(
        self,
        entry: SubledgerEntry,
        gl_entry_id: str | UUID,
        actor_id: UUID,
    ) -> SubledgerEntry:
        """
        Post entry to subledger with GL link.

        Must be implemented by concrete subledger service.

        Args:
            entry: The subledger entry to post
            gl_entry_id: Link to the GL journal entry
            actor_id: UUID of the actor creating this entry (required for audit trail)

        Returns:
            The posted entry (may have updated fields like posted_at)
        """
        ...

    @abstractmethod
    def get_balance(
        self,
        entity_id: str | UUID,
        as_of_date: date | None = None,
        currency: str | None = None,
    ) -> SubledgerBalance:
        """
        Get entity balance.

        Args:
            entity_id: The entity (vendor, customer, etc.)
            as_of_date: Optional date for point-in-time balance
            currency: Optional currency filter

        Returns:
            SubledgerBalance with totals and balance
        """
        ...

    @abstractmethod
    def get_open_items(
        self,
        entity_id: str | UUID,
        currency: str | None = None,
    ) -> list[SubledgerEntry]:
        """
        Get unreconciled/open items for entity.

        Args:
            entity_id: The entity to get open items for
            currency: Optional currency filter

        Returns:
            List of open SubledgerEntry items
        """
        ...

    def reconcile(
        self,
        debit_entry: SubledgerEntry,
        credit_entry: SubledgerEntry,
        amount: Money | None = None,
        reconciled_at: datetime | None = None,
    ) -> ReconciliationResult:
        """
        Reconcile matching debit and credit entries.

        Pure domain logic - validates and creates reconciliation.
        Persistence of updated entries handled by caller.

        Args:
            debit_entry: The debit entry to reconcile
            credit_entry: The credit entry to reconcile
            amount: Amount to reconcile (defaults to min of open amounts)

        Returns:
            ReconciliationResult with reconciliation details

        Raises:
            ValueError: If entries cannot be reconciled
        """
        t0 = time.monotonic()
        logger.info("subledger_reconciliation_started", extra={
            "debit_entry_id": str(debit_entry.entry_id),
            "credit_entry_id": str(credit_entry.entry_id),
            "subledger_type": debit_entry.subledger_type,
        })

        # Validate entries can be reconciled
        if debit_entry.subledger_type != credit_entry.subledger_type:
            logger.error("subledger_reconciliation_type_mismatch", extra={
                "debit_subledger": debit_entry.subledger_type,
                "credit_subledger": credit_entry.subledger_type,
            })
            raise ValueError("Cannot reconcile entries from different subledgers")

        if debit_entry.entity_id != credit_entry.entity_id:
            logger.error("subledger_reconciliation_entity_mismatch", extra={
                "debit_entity": str(debit_entry.entity_id),
                "credit_entity": str(credit_entry.entity_id),
            })
            raise ValueError("Cannot reconcile entries for different entities")

        if debit_entry.direction != EntryDirection.DEBIT:
            raise ValueError("First entry must be a debit")

        if credit_entry.direction != EntryDirection.CREDIT:
            raise ValueError("Second entry must be a credit")

        if debit_entry.currency != credit_entry.currency:
            raise ValueError("Cannot reconcile entries in different currencies")

        if not debit_entry.is_open or not credit_entry.is_open:
            raise ValueError("Both entries must be open for reconciliation")

        # Determine reconciliation amount
        if amount is None:
            amount = min(
                debit_entry.open_amount,
                credit_entry.open_amount,
                key=lambda m: m.amount,
            )

        if amount.amount <= Decimal("0"):
            raise ValueError("Reconciliation amount must be positive")

        if amount.amount > debit_entry.open_amount.amount:
            raise ValueError("Amount exceeds debit entry open amount")

        if amount.amount > credit_entry.open_amount.amount:
            raise ValueError("Amount exceeds credit entry open amount")

        # Determine if full match
        is_full = (
            amount.amount == debit_entry.open_amount.amount
            and amount.amount == credit_entry.open_amount.amount
        )

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("subledger_reconciliation_completed", extra={
            "debit_entry_id": str(debit_entry.entry_id),
            "credit_entry_id": str(credit_entry.entry_id),
            "reconciled_amount": str(amount.amount),
            "is_full_match": is_full,
            "duration_ms": duration_ms,
        })

        return ReconciliationResult(
            reconciliation_id=uuid4(),
            debit_entry_id=debit_entry.entry_id,
            credit_entry_id=credit_entry.entry_id,
            reconciled_amount=amount,
            reconciled_at=reconciled_at if reconciled_at is not None else datetime.min,
            is_full_match=is_full,
        )

    def calculate_balance(
        self,
        entries: Sequence[SubledgerEntry],
        as_of_date: date | None = None,
    ) -> SubledgerBalance:
        """
        Calculate balance from a sequence of entries.

        Pure function - no database access.

        Args:
            entries: Entries to calculate balance from
            as_of_date: Date for the balance (defaults to today)

        Returns:
            SubledgerBalance with calculated totals
        """
        logger.debug("subledger_balance_calculation_started", extra={
            "entry_count": len(entries),
            "as_of_date": as_of_date.isoformat() if as_of_date else None,
        })

        if not entries:
            logger.warning("subledger_balance_empty_entries", extra={})
            raise ValueError("Cannot calculate balance from empty entries")

        if as_of_date is None:
            raise ValueError("as_of_date is required (clock injection: never use date.today())")
        as_of = as_of_date
        currency = entries[0].currency
        entity_id = entries[0].entity_id
        subledger_type = entries[0].subledger_type

        # Filter entries by date if needed
        if as_of_date:
            entries = [
                e for e in entries
                if e.effective_date is None or e.effective_date <= as_of_date
            ]

        debit_total = Decimal("0")
        credit_total = Decimal("0")
        open_count = 0

        for entry in entries:
            if entry.debit:
                debit_total += entry.debit.amount
            if entry.credit:
                credit_total += entry.credit.amount
            if entry.is_open:
                open_count += 1

        # Balance depends on subledger type normal balance side.
        # Credit-normal (liabilities): balance = credit - debit
        # Debit-normal (assets): balance = debit - credit
        # Only AP and PAYROLL are credit-normal; BANK, AR, INVENTORY etc. are debit-normal.
        _credit_normal = (SubledgerType.AP.value, SubledgerType.PAYROLL.value)
        if subledger_type in _credit_normal:
            balance_amount = credit_total - debit_total
        else:
            balance_amount = debit_total - credit_total

        logger.info("subledger_balance_calculated", extra={
            "entity_id": str(entity_id),
            "subledger_type": subledger_type,
            "debit_total": str(debit_total),
            "credit_total": str(credit_total),
            "balance": str(balance_amount),
            "open_item_count": open_count,
        })

        return SubledgerBalance(
            entity_id=entity_id,
            subledger_type=subledger_type,
            as_of_date=as_of,
            debit_total=Money.of(debit_total, currency),
            credit_total=Money.of(credit_total, currency),
            balance=Money.of(balance_amount, currency),
            open_item_count=open_count,
            currency=currency,
        )

    def validate_entry(self, entry: SubledgerEntry) -> list[str]:
        """
        Validate a subledger entry.

        Override in subclass for additional validation.

        Args:
            entry: Entry to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        errors: list[str] = []

        if not entry.subledger_type:
            errors.append("Subledger type is required")

        if not entry.entity_id:
            errors.append("Entity ID is required")

        if not entry.source_document_type:
            errors.append("Source document type is required")

        if not entry.source_document_id:
            errors.append("Source document ID is required")

        if entry.amount.is_zero:
            errors.append("Amount cannot be zero")

        if errors:
            logger.warning("subledger_entry_validation_failed", extra={
                "entry_id": str(entry.entry_id),
                "subledger_type": entry.subledger_type,
                "error_count": len(errors),
                "errors": errors,
            })

        return errors
