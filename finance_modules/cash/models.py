"""
Cash Module Domain Models.

These define the vocabulary of cash management - the nouns that exist
in this domain. No business logic here, just structure.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.cash.models")


class TransactionType(Enum):
    """Types of bank transactions."""
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    FEE = "fee"
    INTEREST = "interest"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"


class ReconciliationStatus(Enum):
    """Reconciliation workflow states."""
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    PENDING_REVIEW = "pending_review"
    COMPLETED = "completed"


@dataclass(frozen=True)
class BankAccount:
    """
    A bank account managed by the organization.

    Maps to a GL account for cash position tracking.
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

    These are matched against book entries during reconciliation.
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

    Workflow: draft → in_progress → pending_review → completed
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
