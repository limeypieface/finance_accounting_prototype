"""
finance_modules.cash.models
============================

Responsibility:
    Frozen dataclass value objects representing the vocabulary of cash
    management -- bank accounts, transactions, statements, reconciliation
    records, forecasts, and payment files.  No business logic; structure only.

Architecture:
    Module layer (finance_modules).  These are in-memory DTOs, NOT
    SQLAlchemy ORM models.  They are used by CashService and helpers
    for type-safe data passing.

Invariants enforced:
    - All monetary fields use ``Decimal`` -- never ``float``.
    - All DTOs are frozen (immutable after construction).

Failure modes:
    - Invalid construction arguments -> standard ``TypeError`` / ``ValueError``
      from ``dataclass(frozen=True)``.

Audit relevance:
    These DTOs carry the data that flows through the audit-logged posting
    pipeline.  Their immutability guarantees that values cannot be silently
    modified after creation.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.cash.models")


class TransactionType(Enum):
    """Types of bank transactions recognized by the cash module."""
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    FEE = "fee"
    INTEREST = "interest"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"


class ReconciliationStatus(Enum):
    """Reconciliation workflow states.  See ``workflows.RECONCILIATION_WORKFLOW``."""
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    PENDING_REVIEW = "pending_review"
    COMPLETED = "completed"


@dataclass(frozen=True)
class BankAccount:
    """
    A bank account managed by the organization.

    Contract:
        Frozen after construction; maps to a GL account for cash position
        tracking.  ``account_number_masked`` stores last 4 digits only
        for PCI compliance.

    Guarantees:
        - Immutable (frozen dataclass).
        - ``currency`` is an ISO 4217 string.

    Non-goals:
        - Does NOT enforce account-number format (bank-specific).
    """
    id: UUID
    code: str
    name: str
    institution: str
    account_number_masked: str  # last 4 digits only
    currency: str
    gl_account_code: str  # maps to chart of accounts
    is_active: bool = True


@dataclass(frozen=True)
class BankTransaction:
    """
    A single transaction from a bank statement or feed.

    Contract:
        Immutable record of a bank-side transaction.  Matched against
        book entries during reconciliation.

    Guarantees:
        - ``amount`` is ``Decimal`` (never float).
    """
    id: UUID
    bank_account_id: UUID
    transaction_date: date
    amount: Decimal
    transaction_type: TransactionType
    reference: str
    description: str = ""
    external_id: str | None = None  # from bank feed
    reconciled: bool = False
    matched_journal_line_id: UUID | None = None


@dataclass(frozen=True)
class Reconciliation:
    """
    A bank reconciliation for a specific account and statement period.

    Contract:
        Immutable snapshot of reconciliation state.
        Workflow: draft -> in_progress -> pending_review -> completed.

    Guarantees:
        - All monetary fields (``statement_balance``, ``book_balance``,
          ``adjusted_book_balance``, ``variance``) are ``Decimal``.
    """
    id: UUID
    bank_account_id: UUID
    statement_date: date
    statement_balance: Decimal
    book_balance: Decimal
    adjusted_book_balance: Decimal | None = None
    variance: Decimal | None = None
    status: ReconciliationStatus = ReconciliationStatus.DRAFT
    completed_by_id: UUID | None = None
    completed_at: date | None = None


@dataclass(frozen=True)
class BankStatement:
    """A parsed bank statement."""
    id: UUID
    bank_account_id: UUID
    statement_date: date
    opening_balance: Decimal
    closing_balance: Decimal
    line_count: int
    format: str = "MT940"
    currency: str = "USD"


@dataclass(frozen=True)
class BankStatementLine:
    """A single line from a parsed bank statement."""
    id: UUID
    statement_id: UUID
    transaction_date: date
    amount: Decimal
    reference: str
    description: str = ""
    transaction_type: str = "UNKNOWN"


@dataclass(frozen=True)
class ReconciliationMatch:
    """A match between a bank statement line and a book entry."""
    id: UUID
    statement_line_id: UUID
    journal_line_id: UUID | None = None
    match_confidence: Decimal = Decimal("1.0")
    match_method: str = "manual"


@dataclass(frozen=True)
class CashForecast:
    """A cash flow forecast for a future period."""
    period: str
    opening_balance: Decimal
    expected_inflows: Decimal
    expected_outflows: Decimal
    projected_closing: Decimal
    currency: str = "USD"


@dataclass(frozen=True)
class PaymentFile:
    """A generated payment file for bank submission."""
    id: UUID
    format: str
    payment_count: int
    total_amount: Decimal
    content: str
    currency: str = "USD"
