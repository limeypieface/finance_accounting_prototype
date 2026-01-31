"""
Subledger query selector.

Provides read-only access to subledger entries and balances.

Key design decisions:
- Returns DTOs (frozen dataclasses), not ORM models
- Uses the caller's Session (SL-G4: snapshot isolation for G9)
- All balance methods accept currency filter (SL-G3: per-currency reconciliation)
- get_aggregate_balance() is the primary method for G9 control account comparison

Invariants:
- SL-G3: Per-currency reconciliation — all balance queries are per-currency
- SL-G4: Snapshot isolation — uses caller's session, never creates its own
- SL-G10: Currency codes are uppercase ISO 4217 (assumed normalized at ingestion)
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from finance_kernel.domain.subledger_control import SubledgerType
from finance_kernel.domain.values import Money
from finance_kernel.models.subledger import (
    ReconciliationFailureReportModel,
    ReconciliationStatus,
    SubledgerEntryModel,
    SubledgerReconciliationModel,
)
from finance_kernel.selectors.base import BaseSelector


@dataclass(frozen=True)
class SubledgerEntryDTO:
    """Data transfer object for a subledger entry."""

    id: UUID
    subledger_type: str
    entity_id: str
    journal_entry_id: UUID
    journal_line_id: UUID | None
    source_document_type: str
    source_document_id: str
    source_line_id: str | None
    debit_amount: Decimal | None
    credit_amount: Decimal | None
    currency: str
    effective_date: date
    posted_at: datetime | None
    reconciliation_status: str
    reconciled_amount: Decimal | None
    memo: str | None
    reference: str | None
    dimensions: dict | None

    @property
    def amount(self) -> Decimal:
        """The entry amount (debit or credit)."""
        if self.debit_amount is not None:
            return self.debit_amount
        return self.credit_amount or Decimal("0")

    @property
    def is_debit(self) -> bool:
        """True if this is a debit entry."""
        return self.debit_amount is not None

    @property
    def is_open(self) -> bool:
        """True if entry is not fully reconciled."""
        return self.reconciliation_status in (
            ReconciliationStatus.OPEN.value,
            ReconciliationStatus.PARTIAL.value,
        )


@dataclass(frozen=True)
class SubledgerBalanceDTO:
    """Balance for a single entity in a subledger."""

    entity_id: str
    subledger_type: str
    currency: str
    as_of_date: date
    debit_total: Decimal
    credit_total: Decimal
    balance: Decimal
    open_item_count: int
    entry_count: int

    @property
    def is_zero(self) -> bool:
        return self.balance == Decimal("0")


@dataclass(frozen=True)
class ReconciliationDTO:
    """Data transfer object for a reconciliation record."""

    id: UUID
    debit_entry_id: UUID
    credit_entry_id: UUID
    reconciled_amount: Decimal
    reconciled_at: datetime
    is_full_match: bool
    reconciled_by: UUID | None
    notes: str | None


class SubledgerSelector(BaseSelector[SubledgerEntryModel]):
    """
    Selector for subledger queries.

    Returns DTOs rather than ORM models for clean separation.
    Uses the caller's Session for snapshot isolation (SL-G4).
    """

    def __init__(self, session: Session):
        super().__init__(session)

    def _to_entry_dto(self, entry: SubledgerEntryModel) -> SubledgerEntryDTO:
        """Convert ORM model to DTO."""
        return SubledgerEntryDTO(
            id=entry.id,
            subledger_type=entry.subledger_type,
            entity_id=entry.entity_id,
            journal_entry_id=entry.journal_entry_id,
            journal_line_id=entry.journal_line_id,
            source_document_type=entry.source_document_type,
            source_document_id=entry.source_document_id,
            source_line_id=entry.source_line_id,
            debit_amount=entry.debit_amount,
            credit_amount=entry.credit_amount,
            currency=entry.currency,
            effective_date=entry.effective_date,
            posted_at=entry.posted_at,
            reconciliation_status=entry.reconciliation_status,
            reconciled_amount=entry.reconciled_amount,
            memo=entry.memo,
            reference=entry.reference,
            dimensions=entry.dimensions,
        )

    def _to_reconciliation_dto(
        self, recon: SubledgerReconciliationModel
    ) -> ReconciliationDTO:
        """Convert reconciliation ORM model to DTO."""
        return ReconciliationDTO(
            id=recon.id,
            debit_entry_id=recon.debit_entry_id,
            credit_entry_id=recon.credit_entry_id,
            reconciled_amount=recon.reconciled_amount,
            reconciled_at=recon.reconciled_at,
            is_full_match=recon.is_full_match,
            reconciled_by=recon.reconciled_by,
            notes=recon.notes,
        )

    # =========================================================================
    # Entry Queries
    # =========================================================================

    def get_entry(self, entry_id: UUID) -> SubledgerEntryDTO | None:
        """
        Get a subledger entry by ID.

        Args:
            entry_id: Entry ID.

        Returns:
            SubledgerEntryDTO if found, None otherwise.
        """
        entry = self.session.execute(
            select(SubledgerEntryModel).where(SubledgerEntryModel.id == entry_id)
        ).scalar_one_or_none()

        if entry is None:
            return None

        return self._to_entry_dto(entry)

    def get_entries_by_entity(
        self,
        entity_id: str,
        subledger_type: SubledgerType,
        as_of_date: date | None = None,
        currency: str | None = None,
    ) -> list[SubledgerEntryDTO]:
        """
        Get all entries for an entity in a subledger.

        Args:
            entity_id: Entity (vendor, customer, etc.).
            subledger_type: Subledger type.
            as_of_date: Optional cutoff date.
            currency: Optional currency filter (SL-G3).

        Returns:
            List of SubledgerEntryDTO ordered by effective_date.
        """
        query = (
            select(SubledgerEntryModel)
            .where(
                SubledgerEntryModel.entity_id == entity_id,
                SubledgerEntryModel.subledger_type == subledger_type.value,
            )
            .order_by(SubledgerEntryModel.effective_date)
        )

        if as_of_date is not None:
            query = query.where(SubledgerEntryModel.effective_date <= as_of_date)

        if currency is not None:
            query = query.where(SubledgerEntryModel.currency == currency)

        entries = self.session.execute(query).scalars().all()
        return [self._to_entry_dto(e) for e in entries]

    def get_entries_by_journal_entry(
        self,
        journal_entry_id: UUID,
    ) -> list[SubledgerEntryDTO]:
        """
        Get all subledger entries linked to a journal entry.

        Args:
            journal_entry_id: Journal entry ID.

        Returns:
            List of SubledgerEntryDTO.
        """
        entries = self.session.execute(
            select(SubledgerEntryModel)
            .where(SubledgerEntryModel.journal_entry_id == journal_entry_id)
            .order_by(SubledgerEntryModel.effective_date)
        ).scalars().all()

        return [self._to_entry_dto(e) for e in entries]

    def get_open_items(
        self,
        entity_id: str,
        subledger_type: SubledgerType,
        currency: str | None = None,
    ) -> list[SubledgerEntryDTO]:
        """
        Get unreconciled/open items for an entity.

        Args:
            entity_id: Entity to get open items for.
            subledger_type: Subledger type.
            currency: Optional currency filter (SL-G3).

        Returns:
            List of open SubledgerEntryDTO ordered by effective_date.
        """
        query = (
            select(SubledgerEntryModel)
            .where(
                SubledgerEntryModel.entity_id == entity_id,
                SubledgerEntryModel.subledger_type == subledger_type.value,
                SubledgerEntryModel.reconciliation_status.in_([
                    ReconciliationStatus.OPEN.value,
                    ReconciliationStatus.PARTIAL.value,
                ]),
            )
            .order_by(SubledgerEntryModel.effective_date)
        )

        if currency is not None:
            query = query.where(SubledgerEntryModel.currency == currency)

        entries = self.session.execute(query).scalars().all()
        return [self._to_entry_dto(e) for e in entries]

    # =========================================================================
    # Balance Queries
    # =========================================================================

    def get_balance(
        self,
        entity_id: str,
        subledger_type: SubledgerType,
        as_of_date: date,
        currency: str,
    ) -> SubledgerBalanceDTO:
        """
        Get balance for a single entity in a subledger.

        Args:
            entity_id: Entity (vendor, customer, etc.).
            subledger_type: Subledger type.
            as_of_date: Cutoff date (required — no clock access).
            currency: Currency filter (required — SL-G3 per-currency).

        Returns:
            SubledgerBalanceDTO with computed totals.
        """
        debit_sum = func.coalesce(
            func.sum(SubledgerEntryModel.debit_amount), Decimal("0")
        ).label("debit_total")

        credit_sum = func.coalesce(
            func.sum(SubledgerEntryModel.credit_amount), Decimal("0")
        ).label("credit_total")

        open_count = func.sum(
            case(
                (
                    SubledgerEntryModel.reconciliation_status.in_([
                        ReconciliationStatus.OPEN.value,
                        ReconciliationStatus.PARTIAL.value,
                    ]),
                    1,
                ),
                else_=0,
            )
        ).label("open_count")

        entry_count = func.count(SubledgerEntryModel.id).label("entry_count")

        query = (
            select(debit_sum, credit_sum, open_count, entry_count)
            .where(
                SubledgerEntryModel.entity_id == entity_id,
                SubledgerEntryModel.subledger_type == subledger_type.value,
                SubledgerEntryModel.currency == currency,
                SubledgerEntryModel.effective_date <= as_of_date,
            )
        )

        result = self.session.execute(query).one()

        debit_total = result.debit_total or Decimal("0")
        credit_total = result.credit_total or Decimal("0")

        # Balance sign depends on subledger type normal balance side
        # Credit-normal (liabilities): balance = credit - debit
        # Debit-normal (assets): balance = debit - credit
        _credit_normal = (SubledgerType.AP.value, SubledgerType.PAYROLL.value)
        if subledger_type.value in _credit_normal:
            balance = credit_total - debit_total
        else:
            balance = debit_total - credit_total

        return SubledgerBalanceDTO(
            entity_id=entity_id,
            subledger_type=subledger_type.value,
            currency=currency,
            as_of_date=as_of_date,
            debit_total=debit_total,
            credit_total=credit_total,
            balance=balance,
            open_item_count=result.open_count or 0,
            entry_count=result.entry_count or 0,
        )

    def get_aggregate_balance(
        self,
        subledger_type: SubledgerType,
        as_of_date: date,
        currency: str,
    ) -> Money:
        """
        Get total balance across all entities for a subledger type.

        This is the primary method for G9 control account comparison.
        Returns the aggregate subledger balance that should match the
        GL control account balance.

        Args:
            subledger_type: Subledger type.
            as_of_date: Cutoff date (required).
            currency: Currency (required — SL-G3 per-currency).

        Returns:
            Money representing the aggregate balance.
        """
        debit_sum = func.coalesce(
            func.sum(SubledgerEntryModel.debit_amount), Decimal("0")
        ).label("debit_total")

        credit_sum = func.coalesce(
            func.sum(SubledgerEntryModel.credit_amount), Decimal("0")
        ).label("credit_total")

        query = (
            select(debit_sum, credit_sum)
            .where(
                SubledgerEntryModel.subledger_type == subledger_type.value,
                SubledgerEntryModel.currency == currency,
                SubledgerEntryModel.effective_date <= as_of_date,
            )
        )

        result = self.session.execute(query).one()

        debit_total = result.debit_total or Decimal("0")
        credit_total = result.credit_total or Decimal("0")

        # Balance sign depends on subledger type
        _credit_normal = (SubledgerType.AP.value, SubledgerType.PAYROLL.value)
        if subledger_type.value in _credit_normal:
            balance = credit_total - debit_total
        else:
            balance = debit_total - credit_total

        return Money.of(balance, currency)

    # =========================================================================
    # Reconciliation Queries
    # =========================================================================

    def get_reconciliation_history(
        self,
        entry_id: UUID,
    ) -> list[ReconciliationDTO]:
        """
        Get reconciliation history for a subledger entry.

        Returns all reconciliation records where the entry appears
        as either the debit or credit side.

        Args:
            entry_id: Subledger entry ID.

        Returns:
            List of ReconciliationDTO ordered by reconciled_at.
        """
        query = (
            select(SubledgerReconciliationModel)
            .where(
                (SubledgerReconciliationModel.debit_entry_id == entry_id)
                | (SubledgerReconciliationModel.credit_entry_id == entry_id)
            )
            .order_by(SubledgerReconciliationModel.reconciled_at)
        )

        recons = self.session.execute(query).scalars().all()
        return [self._to_reconciliation_dto(r) for r in recons]

    # =========================================================================
    # Counting / Existence
    # =========================================================================

    def count_entries(
        self,
        subledger_type: SubledgerType,
        as_of_date: date | None = None,
        currency: str | None = None,
    ) -> int:
        """
        Count subledger entries.

        Args:
            subledger_type: Subledger type.
            as_of_date: Optional cutoff date.
            currency: Optional currency filter.

        Returns:
            Number of entries.
        """
        query = (
            select(func.count(SubledgerEntryModel.id))
            .where(SubledgerEntryModel.subledger_type == subledger_type.value)
        )

        if as_of_date is not None:
            query = query.where(SubledgerEntryModel.effective_date <= as_of_date)

        if currency is not None:
            query = query.where(SubledgerEntryModel.currency == currency)

        return self.session.execute(query).scalar_one()

    def get_entities(
        self,
        subledger_type: SubledgerType,
    ) -> list[str]:
        """
        Get all distinct entity IDs for a subledger type.

        Useful for period-close reconciliation iteration.

        Args:
            subledger_type: Subledger type.

        Returns:
            List of entity ID strings.
        """
        query = (
            select(SubledgerEntryModel.entity_id)
            .where(SubledgerEntryModel.subledger_type == subledger_type.value)
            .distinct()
            .order_by(SubledgerEntryModel.entity_id)
        )

        return list(self.session.execute(query).scalars().all())
