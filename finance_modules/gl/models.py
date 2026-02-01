"""
General Ledger Domain Models (``finance_modules.gl.models``).

Responsibility
--------------
Frozen dataclass value objects representing the nouns of the general ledger:
account reconciliation records, period close tasks, recurring entries,
revaluation results, and translation results.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``GeneralLedgerService`` and returned to callers.  No dependency on kernel
services, database, or engines.

Invariants enforced
-------------------
* All models are ``frozen=True`` (immutable after construction).
* All monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Construction with invalid enum values raises ``ValueError``.

Audit relevance
---------------
* ``AccountReconciliation`` records track reconciliation status per period.
* ``PeriodCloseTask`` records support period-close checklist compliance.
* ``RevaluationResult`` and ``TranslationResult`` records support
  multi-currency disclosure requirements.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.gl.models")


class AccountType(Enum):
    """Account types."""
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


class AccountSubtype(Enum):
    """Account subtypes for reporting."""
    CURRENT_ASSET = "current_asset"
    FIXED_ASSET = "fixed_asset"
    OTHER_ASSET = "other_asset"
    CURRENT_LIABILITY = "current_liability"
    LONG_TERM_LIABILITY = "long_term_liability"
    RETAINED_EARNINGS = "retained_earnings"
    COMMON_STOCK = "common_stock"
    OPERATING_REVENUE = "operating_revenue"
    OTHER_REVENUE = "other_revenue"
    OPERATING_EXPENSE = "operating_expense"
    OTHER_EXPENSE = "other_expense"


class PeriodStatus(Enum):
    """Fiscal period states."""
    FUTURE = "future"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    LOCKED = "locked"  # year-end locked


class BatchStatus(Enum):
    """Journal batch states."""
    OPEN = "open"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    POSTED = "posted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Account:
    """A GL account."""
    id: UUID
    account_code: str
    name: str
    account_type: AccountType
    account_subtype: AccountSubtype | None = None
    is_control_account: bool = False  # subledger control
    subledger_type: str | None = None  # ar, ap, inventory, etc.
    is_active: bool = True
    allow_manual_entry: bool = True
    normal_balance: str = "debit"  # or "credit"
    currency: str | None = None  # for foreign currency accounts


@dataclass(frozen=True)
class AccountHierarchy:
    """Parent-child relationship for account rollups."""
    id: UUID
    parent_id: UUID
    child_id: UUID
    hierarchy_name: str  # "reporting", "statutory", "management"


@dataclass(frozen=True)
class FiscalPeriod:
    """A fiscal period."""
    id: UUID
    period_number: int  # 1-12 (or 13 for adjusting)
    fiscal_year: int
    start_date: date
    end_date: date
    status: PeriodStatus = PeriodStatus.FUTURE
    is_adjustment_period: bool = False

    def __post_init__(self):
        # Validate period_number is valid (1-13)
        if not 1 <= self.period_number <= 13:
            logger.warning(
                "fiscal_period_invalid_number",
                extra={
                    "period_id": str(self.id),
                    "period_number": self.period_number,
                },
            )
            raise ValueError(
                f"period_number must be between 1 and 13, got {self.period_number}"
            )

        # Validate end_date is after start_date
        if self.end_date <= self.start_date:
            raise ValueError(
                f"end_date ({self.end_date}) must be after start_date ({self.start_date})"
            )

        logger.debug(
            "fiscal_period_created",
            extra={
                "period_id": str(self.id),
                "period_number": self.period_number,
                "fiscal_year": self.fiscal_year,
                "start_date": str(self.start_date),
                "end_date": str(self.end_date),
                "status": self.status.value,
                "is_adjustment_period": self.is_adjustment_period,
            },
        )


@dataclass(frozen=True)
class JournalBatch:
    """A batch of journal entries for approval/posting."""
    id: UUID
    batch_number: str
    batch_date: date
    description: str
    source: str  # "manual", "ar", "ap", "payroll", etc.
    entry_count: int = 0
    total_debits: Decimal = Decimal("0")
    total_credits: Decimal = Decimal("0")
    status: BatchStatus = BatchStatus.OPEN
    created_by: UUID | None = None
    approved_by: UUID | None = None


class ReconciliationStatus(Enum):
    """Account reconciliation status."""
    PENDING = "pending"
    RECONCILED = "reconciled"
    EXCEPTION = "exception"


class CloseTaskStatus(Enum):
    """Period close task status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class RecurringEntry:
    """A recurring journal entry template."""
    id: UUID
    name: str
    description: str
    frequency: str  # "monthly", "quarterly", "annually"
    start_date: date
    end_date: date | None = None
    last_generated_date: date | None = None
    is_active: bool = True
    # Lines stored separately, linked by recurring_entry_id


@dataclass(frozen=True)
class AccountReconciliation:
    """Period-end account reconciliation sign-off."""
    id: UUID
    account_id: UUID
    period: str  # e.g., "2025-12"
    reconciled_date: date
    reconciled_by: UUID
    status: ReconciliationStatus = ReconciliationStatus.PENDING
    notes: str | None = None
    balance_confirmed: Decimal = Decimal("0")


@dataclass(frozen=True)
class PeriodCloseTask:
    """Tracks individual period-close checklist items."""
    id: UUID
    period: str  # e.g., "2025-12"
    task_name: str  # e.g., "reconcile_bank", "post_depreciation"
    module: str  # e.g., "gl", "ap", "ar"
    status: CloseTaskStatus = CloseTaskStatus.PENDING
    completed_by: UUID | None = None
    completed_date: date | None = None


class TranslationMethod(Enum):
    """Currency translation methods per ASC 830."""
    CURRENT_RATE = "current_rate"
    TEMPORAL = "temporal"


@dataclass(frozen=True)
class TranslationResult:
    """Result of a currency translation calculation."""
    id: UUID
    entity_id: str
    period: str
    source_currency: str
    target_currency: str
    method: TranslationMethod
    translated_amount: Decimal
    cta_amount: Decimal  # cumulative translation adjustment
    exchange_rate: Decimal


@dataclass(frozen=True)
class RevaluationResult:
    """Result of a period-end FX revaluation run."""
    id: UUID
    period: str
    revaluation_date: date
    currencies_processed: int = 0
    total_gain: Decimal = Decimal("0")
    total_loss: Decimal = Decimal("0")
    entries_posted: int = 0
