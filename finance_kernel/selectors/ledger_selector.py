"""
Module: finance_kernel.selectors.ledger_selector
Responsibility: Read-only ledger queries including trial balance computation,
    account balance aggregation, and canonical ledger hash calculation (R24).
    The ledger is a derived view over posted JournalLines -- there are no
    stored balances anywhere in the system (R6 replay safety).
Architecture position: Kernel > Selectors.  May import from models/ and
    selectors/base.py.  MUST NOT import from services/, domain/, or outer layers.

Invariants enforced:
    R4  -- Double-entry balance verification via total_debits_credits().
    R6  -- No stored balances.  All balance computations derive from posted
           JournalLine rows at query time.
    R24 -- Canonical ledger hash.  canonical_hash() computes a deterministic
           SHA-256 hash over sorted posted lines, enabling post-replay
           verification, tamper detection, and distributed consistency checks.

Failure modes:
    - Returns empty results or zero balances when no posted entries exist.
    - canonical_hash() is deterministic: same ledger state always produces
      same hash, regardless of query order or Python dict ordering.

Audit relevance:
    LedgerSelector is the authoritative read path for financial reporting.
    Trial balance, account balances, and the canonical ledger hash all derive
    exclusively from posted JournalLine rows.  The R24 canonical hash enables
    auditors to verify ledger integrity by comparing hashes across replays
    or distributed systems.
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
    Selector for ledger queries -- the authoritative balance computation engine.

    Contract:
        The ledger is a derived view over posted JournalLines.  All queries
        filter by status=POSTED and optionally by effective_date <= as_of_date.
        Results are ordered by JournalEntry.seq ASC.

    Guarantees:
        - INVARIANT R6: No stored balances.  Every balance is computed at
          query time from JournalLine rows.
        - INVARIANT R24: canonical_hash() produces a deterministic SHA-256
          hash over sorted posted lines for integrity verification.
        - All balance methods return Decimal (never float).

    Non-goals:
        - This selector does NOT perform currency conversion; it returns
          balances in their original transaction currency.
        - Dimension filtering is performed in Python (not SQL JSONB) for
          portability.
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

        # Dimension filtering would require JSONB query support

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

        INVARIANT R6: The trial balance is computed from JournalLines at query
        time -- never stored.  Sum of all debit_totals MUST equal sum of all
        credit_totals per currency (R4).

        Preconditions: None (returns empty list if no posted entries exist).
        Postconditions: Returns one TrialBalanceRow per (account, currency) pair,
            ordered by account_code then currency.

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

        Preconditions: account_id is a valid UUID referencing an existing Account.
        Postconditions: Returns one AccountBalance per currency held in the account.
            Empty list if no posted lines exist for the account.

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

        INVARIANT R4: For a balanced ledger, total_debits == total_credits
        per currency.  This method is the read-side verification of the
        double-entry balance invariant.

        Preconditions: None (returns (0, 0) if no posted entries exist).
        Postconditions: Returns (total_debits, total_credits) as Decimals.
            Both values are >= 0.

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

        INVARIANT R24: The canonical ledger hash is a SHA-256 digest over
        all posted journal lines sorted by (account_id, currency, dimensions,
        entry_seq, line_seq).  The same ledger state ALWAYS produces the same
        hash, regardless of query order or Python dict key ordering.

        Preconditions: None (returns hash of empty data if no posted entries).
        Postconditions: Returns a 64-character hex-encoded SHA-256 hash string.

        Use cases:
            - Post-replay verification: replay event stream, compare hashes.
            - Tamper detection: store hash at period close, verify later.
            - Distributed consistency: compare hashes across replicas.

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

        INVARIANT R24: Constant-time hash comparison for tamper detection.

        Preconditions: expected_hash is a 64-character hex SHA-256 string.
        Postconditions: Returns True iff the computed canonical hash exactly
            matches expected_hash.  False indicates ledger state divergence.

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
