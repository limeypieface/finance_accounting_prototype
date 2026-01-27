"""
Ledger query selector.

Provides read-only access to the ledger with support for:
- As-of-date queries
- Trial balance computation
- Account balance queries
- Dimension filtering
- R24: Canonical ledger hash computation
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from finance_kernel.models.account import Account
from finance_kernel.models.journal import (
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    LineSide,
)
from finance_kernel.selectors.base import BaseSelector


@dataclass
class TrialBalanceRow:
    """A single row in a trial balance report."""

    account_id: UUID
    account_code: str
    account_name: str
    currency: str
    debit_total: Decimal
    credit_total: Decimal

    @property
    def balance(self) -> Decimal:
        """Net balance (debits - credits)."""
        return self.debit_total - self.credit_total


@dataclass
class AccountBalance:
    """Balance for a single account."""

    account_id: UUID
    currency: str
    debit_total: Decimal
    credit_total: Decimal
    line_count: int

    @property
    def balance(self) -> Decimal:
        """Net balance (debits - credits)."""
        return self.debit_total - self.credit_total


@dataclass
class LedgerLine:
    """A single line from the ledger view."""

    journal_entry_id: UUID
    journal_line_id: UUID
    seq: int
    effective_date: date
    account_id: UUID
    account_code: str
    side: LineSide
    amount: Decimal
    currency: str
    dimensions: dict | None


class LedgerSelector(BaseSelector[JournalLine]):
    """
    Selector for ledger queries.

    The ledger is a derived view over posted JournalLines where:
    - effective_date <= as_of_effective_date
    - status = posted
    - ordered by seq ASC

    IMPORTANT: No stored balances. All balances are computed from JournalLines.
    """

    def __init__(self, session: Session):
        super().__init__(session)

    def _base_query(self, as_of_date: date | None = None):
        """
        Build the base query for ledger lines.

        Args:
            as_of_date: Optional cutoff date for ledger view.

        Returns:
            SQLAlchemy query.
        """
        query = (
            select(JournalLine)
            .join(JournalEntry)
            .where(JournalEntry.status == JournalEntryStatus.POSTED)
        )

        if as_of_date is not None:
            query = query.where(JournalEntry.effective_date <= as_of_date)

        return query.order_by(JournalEntry.seq)

    def query(
        self,
        as_of_date: date | None = None,
        account_id: UUID | None = None,
        currency: str | None = None,
        dimensions: dict | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[LedgerLine]:
        """
        Query the ledger with optional filters.

        Args:
            as_of_date: Cutoff date for ledger view.
            account_id: Filter by account.
            currency: Filter by currency.
            dimensions: Filter by dimension values.
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            List of LedgerLine DTOs.
        """
        query = self._base_query(as_of_date)

        if account_id is not None:
            query = query.where(JournalLine.account_id == account_id)

        if currency is not None:
            query = query.where(JournalLine.currency == currency)

        # Dimension filtering would require JSON query support
        # For SQLite, this is limited - we filter in Python if needed

        if limit is not None:
            query = query.limit(limit)

        if offset is not None:
            query = query.offset(offset)

        # Execute query and join with accounts
        query = query.add_columns(Account.code).join(
            Account, JournalLine.account_id == Account.id
        )

        results = self.session.execute(query).all()

        ledger_lines = []
        for line, account_code in results:
            # Filter by dimensions in Python if needed
            if dimensions is not None and line.dimensions:
                match = all(
                    line.dimensions.get(k) == v for k, v in dimensions.items()
                )
                if not match:
                    continue

            ledger_lines.append(
                LedgerLine(
                    journal_entry_id=line.journal_entry_id,
                    journal_line_id=line.id,
                    seq=line.entry.seq,
                    effective_date=line.entry.effective_date,
                    account_id=line.account_id,
                    account_code=account_code,
                    side=line.side,
                    amount=line.amount,
                    currency=line.currency,
                    dimensions=line.dimensions,
                )
            )

        return ledger_lines

    def trial_balance(
        self,
        as_of_date: date | None = None,
        currency: str | None = None,
    ) -> list[TrialBalanceRow]:
        """
        Compute trial balance as of a specific date.

        The trial balance shows the sum of debits and credits for each account.
        Computed from JournalLines - never stored.

        Args:
            as_of_date: Cutoff date for trial balance.
            currency: Optional currency filter.

        Returns:
            List of TrialBalanceRow DTOs.
        """
        # Build aggregation query
        debit_sum = func.sum(
            case(
                (JournalLine.side == LineSide.DEBIT, JournalLine.amount),
                else_=Decimal("0"),
            )
        ).label("debit_total")

        credit_sum = func.sum(
            case(
                (JournalLine.side == LineSide.CREDIT, JournalLine.amount),
                else_=Decimal("0"),
            )
        ).label("credit_total")

        query = (
            select(
                JournalLine.account_id,
                Account.code.label("account_code"),
                Account.name.label("account_name"),
                JournalLine.currency,
                debit_sum,
                credit_sum,
            )
            .join(JournalEntry)
            .join(Account, JournalLine.account_id == Account.id)
            .where(JournalEntry.status == JournalEntryStatus.POSTED)
            .group_by(
                JournalLine.account_id,
                Account.code,
                Account.name,
                JournalLine.currency,
            )
            .order_by(Account.code, JournalLine.currency)
        )

        if as_of_date is not None:
            query = query.where(JournalEntry.effective_date <= as_of_date)

        if currency is not None:
            query = query.where(JournalLine.currency == currency)

        results = self.session.execute(query).all()

        return [
            TrialBalanceRow(
                account_id=row.account_id,
                account_code=row.account_code,
                account_name=row.account_name,
                currency=row.currency,
                debit_total=row.debit_total or Decimal("0"),
                credit_total=row.credit_total or Decimal("0"),
            )
            for row in results
        ]

    def account_balance(
        self,
        account_id: UUID,
        as_of_date: date | None = None,
        currency: str | None = None,
    ) -> list[AccountBalance]:
        """
        Get the balance for a specific account.

        Args:
            account_id: Account to query.
            as_of_date: Cutoff date.
            currency: Optional currency filter.

        Returns:
            List of AccountBalance DTOs (one per currency).
        """
        debit_sum = func.sum(
            case(
                (JournalLine.side == LineSide.DEBIT, JournalLine.amount),
                else_=Decimal("0"),
            )
        ).label("debit_total")

        credit_sum = func.sum(
            case(
                (JournalLine.side == LineSide.CREDIT, JournalLine.amount),
                else_=Decimal("0"),
            )
        ).label("credit_total")

        line_count = func.count(JournalLine.id).label("line_count")

        query = (
            select(
                JournalLine.account_id,
                JournalLine.currency,
                debit_sum,
                credit_sum,
                line_count,
            )
            .join(JournalEntry)
            .where(
                and_(
                    JournalEntry.status == JournalEntryStatus.POSTED,
                    JournalLine.account_id == account_id,
                )
            )
            .group_by(JournalLine.account_id, JournalLine.currency)
        )

        if as_of_date is not None:
            query = query.where(JournalEntry.effective_date <= as_of_date)

        if currency is not None:
            query = query.where(JournalLine.currency == currency)

        results = self.session.execute(query).all()

        return [
            AccountBalance(
                account_id=row.account_id,
                currency=row.currency,
                debit_total=row.debit_total or Decimal("0"),
                credit_total=row.credit_total or Decimal("0"),
                line_count=row.line_count,
            )
            for row in results
        ]

    def total_debits_credits(
        self,
        as_of_date: date | None = None,
        currency: str | None = None,
    ) -> tuple[Decimal, Decimal]:
        """
        Get total debits and credits across all accounts.

        Useful for verifying double-entry integrity.

        Args:
            as_of_date: Cutoff date.
            currency: Optional currency filter.

        Returns:
            Tuple of (total_debits, total_credits).
        """
        debit_sum = func.sum(
            case(
                (JournalLine.side == LineSide.DEBIT, JournalLine.amount),
                else_=Decimal("0"),
            )
        ).label("debit_total")

        credit_sum = func.sum(
            case(
                (JournalLine.side == LineSide.CREDIT, JournalLine.amount),
                else_=Decimal("0"),
            )
        ).label("credit_total")

        query = (
            select(debit_sum, credit_sum)
            .select_from(JournalLine)
            .join(JournalEntry)
            .where(JournalEntry.status == JournalEntryStatus.POSTED)
        )

        if as_of_date is not None:
            query = query.where(JournalEntry.effective_date <= as_of_date)

        if currency is not None:
            query = query.where(JournalLine.currency == currency)

        result = self.session.execute(query).one()

        return (
            result.debit_total or Decimal("0"),
            result.credit_total or Decimal("0"),
        )

    # =========================================================================
    # R24: Canonical Ledger Hash
    # =========================================================================

    def canonical_hash(
        self,
        as_of_date: date | None = None,
        currency: str | None = None,
    ) -> str:
        """
        Compute a deterministic, canonical hash of the ledger (R24).

        The hash is computed over sorted (account_id, currency, dimensions, seq)
        and is stable across rebuilds. This enables:
        - Verifying ledger consistency after replay
        - Detecting tampering or corruption
        - Validating distributed ledger agreement

        Args:
            as_of_date: Optional cutoff date for the hash.
            currency: Optional currency filter.

        Returns:
            SHA-256 hash of the canonical ledger representation.
        """
        canonical_lines = self._get_canonical_lines(as_of_date, currency)
        return self._compute_hash(canonical_lines)

    def _get_canonical_lines(
        self,
        as_of_date: date | None = None,
        currency: str | None = None,
    ) -> list[dict]:
        """
        Get lines in canonical order for hashing (R24).

        Canonical order: sorted by (account_id, currency, dimensions_json, seq)
        This ensures the same ledger state always produces the same hash.

        Args:
            as_of_date: Optional cutoff date.
            currency: Optional currency filter.

        Returns:
            List of line dictionaries in canonical order.
        """
        # Query all posted lines
        query = (
            select(
                JournalLine.id,
                JournalLine.journal_entry_id,
                JournalLine.account_id,
                JournalLine.side,
                JournalLine.amount,
                JournalLine.currency,
                JournalLine.dimensions,
                JournalLine.is_rounding,
                JournalLine.line_seq,
                JournalEntry.seq.label("entry_seq"),
            )
            .join(JournalEntry)
            .where(JournalEntry.status == JournalEntryStatus.POSTED)
        )

        if as_of_date is not None:
            query = query.where(JournalEntry.effective_date <= as_of_date)

        if currency is not None:
            query = query.where(JournalLine.currency == currency)

        # Order by entry seq first (global ordering), then line seq within entry
        query = query.order_by(JournalEntry.seq, JournalLine.line_seq)

        results = self.session.execute(query).all()

        # Build canonical representations
        canonical_lines = []
        for row in results:
            # Canonicalize dimensions: sorted keys, JSON serialized
            dims_canonical = self._canonicalize_dimensions(row.dimensions)

            canonical_lines.append({
                "account_id": str(row.account_id),
                "currency": row.currency,
                "dimensions": dims_canonical,
                "entry_seq": row.entry_seq,
                "line_seq": row.line_seq,
                "side": row.side.value,
                "amount": str(row.amount),
                "is_rounding": row.is_rounding,
            })

        # Sort by (account_id, currency, dimensions, entry_seq, line_seq)
        canonical_lines.sort(key=lambda x: (
            x["account_id"],
            x["currency"],
            x["dimensions"],
            x["entry_seq"],
            x["line_seq"],
        ))

        return canonical_lines

    def _canonicalize_dimensions(self, dimensions: dict | None) -> str:
        """
        Canonicalize dimensions to a deterministic string (R24).

        Ensures the same dimension set always produces the same string
        regardless of dict key ordering in Python.

        Args:
            dimensions: The dimensions dict or None.

        Returns:
            JSON string with sorted keys, or empty string if None.
        """
        if dimensions is None or not dimensions:
            return ""
        # Sort keys and serialize to JSON
        return json.dumps(dimensions, sort_keys=True, separators=(",", ":"))

    def _compute_hash(self, canonical_lines: list[dict]) -> str:
        """
        Compute SHA-256 hash of canonical lines (R24).

        Args:
            canonical_lines: Lines in canonical order.

        Returns:
            Hex-encoded SHA-256 hash.
        """
        hasher = hashlib.sha256()

        for line in canonical_lines:
            # Create deterministic string representation
            line_str = json.dumps(line, sort_keys=True, separators=(",", ":"))
            hasher.update(line_str.encode("utf-8"))
            hasher.update(b"\n")  # Line separator

        return hasher.hexdigest()

    def verify_canonical_hash(
        self,
        expected_hash: str,
        as_of_date: date | None = None,
        currency: str | None = None,
    ) -> bool:
        """
        Verify ledger matches an expected canonical hash (R24).

        Useful for:
        - Post-replay verification
        - Distributed consistency checks
        - Audit verification

        Args:
            expected_hash: The expected hash value.
            as_of_date: Optional cutoff date.
            currency: Optional currency filter.

        Returns:
            True if hashes match, False otherwise.
        """
        actual_hash = self.canonical_hash(as_of_date, currency)
        return actual_hash == expected_hash

    def get_canonical_representation(
        self,
        as_of_date: date | None = None,
        currency: str | None = None,
    ) -> tuple[str, list[dict]]:
        """
        Get both the hash and the canonical representation (R24).

        Useful for debugging and audit purposes.

        Args:
            as_of_date: Optional cutoff date.
            currency: Optional currency filter.

        Returns:
            Tuple of (hash, canonical_lines).
        """
        canonical_lines = self._get_canonical_lines(as_of_date, currency)
        hash_value = self._compute_hash(canonical_lines)
        return hash_value, canonical_lines
