"""
Subledger Service - Stateful subledger service base class.

This module provides the abstract base class for subledger management services.
It defines the interface and shared logic for AP, AR, Bank, Inventory, and
Fixed Assets subledgers, with session injection for persistence.

The pure domain types (SubledgerEntry, SubledgerBalance, ReconciliationResult,
ReconciliationStatus, EntryDirection) live in finance_engines.subledger.
This module re-exports only the stateful SubledgerService ABC that concrete
implementations extend.

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
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Generic, TypeVar, Sequence
from uuid import UUID, uuid4

from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger

from finance_engines.subledger import (
    SubledgerEntry,
    SubledgerBalance,
    ReconciliationResult,
    ReconciliationStatus,
    EntryDirection,
)

logger = get_logger("services.subledger")


# Type variable for entity type (Vendor, Customer, Bank, etc.)
EntityT = TypeVar("EntityT")


class SubledgerService(ABC, Generic[EntityT]):
    """
    Base class for subledger management.

    Abstract class - each subledger type (AP, AR, Bank) extends this
    and implements specific business rules.

    Design:
        - Pure domain logic in this base class
        - Persistence handled by concrete implementations
        - Session injection for database access
    """

    # Override in subclass
    subledger_type: str = ""

    @abstractmethod
    def post(
        self,
        entry: SubledgerEntry,
        gl_entry_id: str | UUID,
    ) -> SubledgerEntry:
        """
        Post entry to subledger with GL link.

        Must be implemented by concrete subledger service.

        Args:
            entry: The subledger entry to post
            gl_entry_id: Link to the GL journal entry

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
            reconciled_at=datetime.now(),
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

        as_of = as_of_date or date.today()
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

        # Balance depends on subledger type
        # AP: Credit normal (liability), so balance = credit - debit
        # AR: Debit normal (asset), so balance = debit - credit
        if subledger_type in ("AP", "BANK"):
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
