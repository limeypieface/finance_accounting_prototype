"""
Travel & Expense Domain Models.

The nouns of expense management: reports, lines, cards, transactions.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.expense.models")


class ExpenseCategory(Enum):
    """Expense categories."""
    TRAVEL = "travel"
    MEALS = "meals"
    LODGING = "lodging"
    TRANSPORTATION = "transportation"
    OFFICE_SUPPLIES = "office_supplies"
    PROFESSIONAL_SERVICES = "professional_services"
    ENTERTAINMENT = "entertainment"
    COMMUNICATION = "communication"
    OTHER = "other"


class ReportStatus(Enum):
    """Expense report lifecycle states."""
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROCESSING = "processing"
    PAID = "paid"


class PaymentMethod(Enum):
    """How expense was paid."""
    CASH = "cash"
    PERSONAL_CARD = "personal_card"
    CORPORATE_CARD = "corporate_card"
    DIRECT_BILL = "direct_bill"


@dataclass(frozen=True)
class ExpenseReport:
    """An employee expense report."""
    id: UUID
    report_number: str
    employee_id: UUID
    report_date: date
    purpose: str
    total_amount: Decimal = Decimal("0")
    currency: str = "USD"
    status: ReportStatus = ReportStatus.DRAFT
    submitted_date: date | None = None
    approved_date: date | None = None
    approved_by: UUID | None = None
    paid_date: date | None = None
    project_id: UUID | None = None
    department_id: UUID | None = None


@dataclass(frozen=True)
class ExpenseLine:
    """A single expense item on a report."""
    id: UUID
    report_id: UUID
    line_number: int
    expense_date: date
    category: ExpenseCategory
    description: str
    amount: Decimal
    currency: str
    payment_method: PaymentMethod
    receipt_attached: bool = False
    billable: bool = False
    gl_account_code: str | None = None
    project_id: UUID | None = None
    card_transaction_id: UUID | None = None  # if corporate card
    violation_notes: str | None = None  # policy violations


@dataclass(frozen=True)
class ExpensePolicy:
    """Category-level expense policy rules."""
    category: str
    daily_limit: Decimal | None = None
    per_transaction_limit: Decimal | None = None
    requires_receipt_above: Decimal | None = None
    requires_justification: bool = False


@dataclass(frozen=True)
class PolicyViolation:
    """A detected policy violation on an expense line."""
    line_id: UUID
    violation_type: str  # OVER_LIMIT, MISSING_RECEIPT, MISSING_JUSTIFICATION
    category: str
    amount: Decimal
    limit: Decimal | None
    message: str


@dataclass(frozen=True)
class MileageRate:
    """IRS-style mileage reimbursement rate."""
    effective_date: date
    rate_per_mile: Decimal
    currency: str = "USD"


@dataclass(frozen=True)
class PerDiemRate:
    """GSA-style per diem rates for a location."""
    location: str
    meals_rate: Decimal
    lodging_rate: Decimal
    incidentals_rate: Decimal
    currency: str = "USD"
    effective_date: date = date(2024, 1, 1)


@dataclass(frozen=True)
class CorporateCard:
    """A corporate credit card assigned to an employee."""
    id: UUID
    card_number_masked: str  # last 4 digits
    employee_id: UUID
    credit_limit: Decimal
    is_active: bool = True
    single_transaction_limit: Decimal | None = None


@dataclass(frozen=True)
class CardTransaction:
    """A corporate card transaction."""
    id: UUID
    card_id: UUID
    transaction_date: date
    posting_date: date
    merchant_name: str
    amount: Decimal
    currency: str
    merchant_category_code: str | None = None
    is_reconciled: bool = False
    expense_line_id: UUID | None = None  # linked expense
