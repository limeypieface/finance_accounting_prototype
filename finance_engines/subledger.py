"""
finance_engines.subledger -- Pure domain types for subledger management.

Responsibility:
    Define immutable value objects for subledger entries, balances, and
    reconciliation results across AP, AR, Bank, Inventory, and Fixed Assets
    subledgers.  These are the data contracts exchanged between the engine
    layer and the service layer.

Architecture position:
    Engines -- pure calculation layer, zero I/O.
    May only import finance_kernel/domain/values and
    finance_kernel/domain/subledger_control (SL-G9 canonical enum).
    The stateful SubledgerService ABC lives in
    finance_services.subledger_service.

Invariants enforced:
    - SL-G1 (single-sided entries): SubledgerEntry.__post_init__ enforces
      exactly one of debit/credit is set; never both, never neither.
    - SL-G2 (GL linkage): gl_entry_id field on SubledgerEntry provides
      the mandatory GL-to-SL link for reconciliation.
    - R6 (replay safety): all value objects are frozen dataclasses;
      with_reconciliation() returns a new instance.
    - R16 (ISO 4217): currency derived from the Money amount.

Failure modes:
    - ValueError from SubledgerEntry.__post_init__ if both debit and credit
      are set, or if neither is set.
    - Potential currency mismatch if with_reconciliation is called with
      a Money in a different currency (caught by Money arithmetic).

Audit relevance:
    Subledger entries are the subsidiary detail behind GL control account
    balances.  Each entry carries source_document_type/id for traceability,
    gl_entry_id for GL linkage, and reconciliation state for period-end
    reconciliation (SL-G6).

Usage:
    from finance_engines.subledger import SubledgerEntry, SubledgerBalance
    from finance_kernel.domain.values import Money

    entry = SubledgerEntry(
        subledger_type="AP",
        entity_id="vendor-001",
        source_document_type="INVOICE",
        source_document_id="INV-001",
        debit=Money.of("1000.00", "USD"),
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from finance_kernel.domain.subledger_control import (
    SubledgerType,  # SL-G9: canonical enum from kernel domain
)
from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger

logger = get_logger("engines.subledger")


class EntryDirection(str, Enum):
    """Direction of subledger entry."""

    DEBIT = "debit"
    CREDIT = "credit"


class ReconciliationStatus(str, Enum):
    """Status of reconciliation for an entry."""

    OPEN = "open"  # Not reconciled
    PARTIAL = "partial"  # Partially reconciled
    RECONCILED = "reconciled"  # Fully reconciled
    WRITTEN_OFF = "written_off"  # Written off (closed)


@dataclass(frozen=True)
class SubledgerEntry:
    """
    Generic subledger entry.

    Contract:
        Frozen dataclass representing a single entry in a subledger.
        Links to the GL through gl_entry_id.
    Guarantees:
        - Exactly one of debit/credit is set (SL-G1); never both, never
          neither; enforced in __post_init__.
        - ``with_reconciliation`` returns a new immutable instance with
          updated reconciliation state (never mutates self).
        - ``signed_amount`` is positive for debits, negative for credits.
    Non-goals:
        - Does not enforce that gl_entry_id references a real journal entry;
          that linkage is the service layer's responsibility.
    """

    # Identity
    entry_id: str | UUID = field(default_factory=uuid4)
    subledger_type: str = ""
    entity_id: str | UUID = ""  # vendor_id, customer_id, bank_id, etc.

    # Source reference
    source_document_type: str = ""
    source_document_id: str | UUID = ""
    source_line_id: str | UUID | None = None

    # GL linkage
    gl_entry_id: str | UUID | None = None
    gl_line_id: str | UUID | None = None

    # Amounts (one should be None)
    debit: Money | None = None
    credit: Money | None = None

    # Dates
    effective_date: date | None = None
    posted_at: datetime | None = None

    # Reconciliation
    reconciliation_status: ReconciliationStatus = ReconciliationStatus.OPEN
    reconciled_amount: Money | None = None
    reconciled_to_ids: tuple[str | UUID, ...] = ()

    # Description
    memo: str = ""
    reference: str = ""

    # Dimensions for multi-dimensional tracking
    dimensions: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # INVARIANT [SL-G1]: single-sided entry -- exactly one of debit/credit must be set.
        if self.debit is not None and self.credit is not None:
            logger.error("subledger_entry_both_sides", extra={
                "entry_id": str(self.entry_id),
                "subledger_type": self.subledger_type,
            })
            raise ValueError("Entry cannot have both debit and credit")
        if self.debit is None and self.credit is None:
            logger.error("subledger_entry_no_amount", extra={
                "entry_id": str(self.entry_id),
                "subledger_type": self.subledger_type,
            })
            raise ValueError("Entry must have either debit or credit")

    @property
    def direction(self) -> EntryDirection:
        """Direction of this entry."""
        return EntryDirection.DEBIT if self.debit is not None else EntryDirection.CREDIT

    @property
    def amount(self) -> Money:
        """The amount (whether debit or credit)."""
        return self.debit if self.debit is not None else self.credit

    @property
    def signed_amount(self) -> Money:
        """Amount with sign (positive for debit, negative for credit)."""
        if self.debit is not None:
            return self.debit
        return self.credit * Decimal("-1")

    @property
    def currency(self) -> str:
        """Currency of this entry."""
        return self.amount.currency.code

    @property
    def is_open(self) -> bool:
        """True if entry is not fully reconciled."""
        return self.reconciliation_status in (
            ReconciliationStatus.OPEN,
            ReconciliationStatus.PARTIAL,
        )

    @property
    def is_reconciled(self) -> bool:
        """True if entry is fully reconciled or written off."""
        return self.reconciliation_status in (
            ReconciliationStatus.RECONCILED,
            ReconciliationStatus.WRITTEN_OFF,
        )

    @property
    def open_amount(self) -> Money:
        """Remaining unreconciled amount."""
        if self.reconciled_amount is None:
            return self.amount
        return self.amount - self.reconciled_amount

    def with_reconciliation(
        self,
        reconciled_amount: Money,
        reconciled_to_id: str | UUID,
    ) -> SubledgerEntry:
        """
        Create new entry with updated reconciliation.

        Returns new immutable entry with reconciliation applied.
        """
        new_reconciled = (
            self.reconciled_amount + reconciled_amount
            if self.reconciled_amount
            else reconciled_amount
        )
        new_reconciled_to = self.reconciled_to_ids + (reconciled_to_id,)

        # Determine new status
        if new_reconciled.amount >= self.amount.amount:
            new_status = ReconciliationStatus.RECONCILED
        elif new_reconciled.amount > Decimal("0"):
            new_status = ReconciliationStatus.PARTIAL
        else:
            new_status = ReconciliationStatus.OPEN

        # Create new frozen instance with updated fields
        return SubledgerEntry(
            entry_id=self.entry_id,
            subledger_type=self.subledger_type,
            entity_id=self.entity_id,
            source_document_type=self.source_document_type,
            source_document_id=self.source_document_id,
            source_line_id=self.source_line_id,
            gl_entry_id=self.gl_entry_id,
            gl_line_id=self.gl_line_id,
            debit=self.debit,
            credit=self.credit,
            effective_date=self.effective_date,
            posted_at=self.posted_at,
            reconciliation_status=new_status,
            reconciled_amount=new_reconciled,
            reconciled_to_ids=new_reconciled_to,
            memo=self.memo,
            reference=self.reference,
            dimensions=self.dimensions,
        )


@dataclass(frozen=True)
class SubledgerBalance:
    """
    Balance for a subledger entity.

    Immutable value object representing current balance state.
    """

    entity_id: str | UUID
    subledger_type: str
    as_of_date: date
    debit_total: Money
    credit_total: Money
    balance: Money  # Debit - Credit (or Credit - Debit for liability accounts)
    open_item_count: int
    currency: str

    @property
    def is_zero(self) -> bool:
        return self.balance.is_zero


@dataclass(frozen=True)
class ReconciliationResult:
    """
    Result of a reconciliation operation.

    Immutable value object with reconciliation details.
    """

    reconciliation_id: str | UUID
    debit_entry_id: str | UUID
    credit_entry_id: str | UUID
    reconciled_amount: Money
    reconciled_at: datetime
    is_full_match: bool
    notes: str = ""



# Convenience factory functions

def create_debit_entry(
    subledger_type: str,
    entity_id: str | UUID,
    amount: Money,
    source_document_type: str,
    source_document_id: str | UUID,
    effective_date: date,
    memo: str = "",
    reference: str = "",
    dimensions: dict[str, str] | None = None,
) -> SubledgerEntry:
    """Create a debit subledger entry."""
    return SubledgerEntry(
        subledger_type=subledger_type,
        entity_id=entity_id,
        source_document_type=source_document_type,
        source_document_id=source_document_id,
        debit=amount,
        credit=None,
        effective_date=effective_date,
        memo=memo,
        reference=reference,
        dimensions=dimensions or {},
    )


def create_credit_entry(
    subledger_type: str,
    entity_id: str | UUID,
    amount: Money,
    source_document_type: str,
    source_document_id: str | UUID,
    effective_date: date,
    memo: str = "",
    reference: str = "",
    dimensions: dict[str, str] | None = None,
) -> SubledgerEntry:
    """Create a credit subledger entry."""
    return SubledgerEntry(
        subledger_type=subledger_type,
        entity_id=entity_id,
        source_document_type=source_document_type,
        source_document_id=source_document_id,
        debit=None,
        credit=amount,
        effective_date=effective_date,
        memo=memo,
        reference=reference,
        dimensions=dimensions or {},
    )
