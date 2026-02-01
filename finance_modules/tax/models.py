"""
Tax Domain Models.

Responsibility:
    Frozen dataclass DTOs representing the nouns of taxation: jurisdictions,
    rates, exemptions, transactions, returns, and ASC 740 deferred-tax
    constructs.

Architecture:
    finance_modules -- Thin ERP glue (this layer).
    These models are pure data containers with no I/O, no ORM coupling, and
    no business logic beyond basic enum classification.

Invariants:
    - All models are ``frozen=True`` (immutable after construction).
    - All monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes:
    - Construction with invalid enum values raises ``ValueError`` from
      the ``Enum`` constructor.

Audit relevance:
    - ``TaxTransaction.is_reported`` and ``TaxReturn.status`` track
      reporting lifecycle; transitions are governed by ``workflows.py``.
    - ``TemporaryDifference``, ``DeferredTaxAsset``, ``DeferredTaxLiability``,
      and ``TaxProvision`` support ASC 740 disclosure.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.tax.models")


class TaxType(Enum):
    """Types of tax."""
    SALES = "sales"
    USE = "use"
    VAT = "vat"
    GST = "gst"
    EXCISE = "excise"
    WITHHOLDING = "withholding"


class TaxReturnStatus(Enum):
    """Tax return lifecycle states."""
    DRAFT = "draft"
    CALCULATED = "calculated"
    REVIEWED = "reviewed"
    FILED = "filed"
    PAID = "paid"
    AMENDED = "amended"


class ExemptionType(Enum):
    """Types of tax exemption."""
    RESALE = "resale"
    MANUFACTURING = "manufacturing"
    NONPROFIT = "nonprofit"
    GOVERNMENT = "government"
    EXPORT = "export"
    DIRECT_PAY = "direct_pay"


@dataclass(frozen=True)
class TaxJurisdiction:
    """A tax jurisdiction (state, county, city, etc.)."""
    id: UUID
    code: str
    name: str
    jurisdiction_type: str  # country, state, county, city, district
    parent_id: UUID | None = None
    tax_type: TaxType = TaxType.SALES
    is_active: bool = True


@dataclass(frozen=True)
class TaxRate:
    """A tax rate for a jurisdiction and category."""
    id: UUID
    jurisdiction_id: UUID
    tax_category: str  # taxable, reduced, exempt, etc.
    rate: Decimal
    effective_date: date
    end_date: date | None = None


@dataclass(frozen=True)
class TaxExemption:
    """A tax exemption certificate."""
    id: UUID
    exemption_type: ExemptionType
    jurisdiction_id: UUID
    certificate_number: str
    effective_date: date
    customer_id: UUID | None = None
    vendor_id: UUID | None = None
    expiration_date: date | None = None
    is_verified: bool = False


@dataclass(frozen=True)
class TaxTransaction:
    """A single taxable transaction for reporting."""
    id: UUID
    source_type: str  # ar_invoice, ap_invoice, pos_sale
    source_id: UUID
    transaction_date: date
    jurisdiction_id: UUID
    tax_type: TaxType
    taxable_amount: Decimal
    exempt_amount: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")
    is_reported: bool = False
    tax_return_id: UUID | None = None


@dataclass(frozen=True)
class TaxReturn:
    """A tax return for a jurisdiction and period."""
    id: UUID
    jurisdiction_id: UUID
    tax_type: TaxType
    period_start: date
    period_end: date
    filing_due_date: date
    gross_sales: Decimal = Decimal("0")
    taxable_sales: Decimal = Decimal("0")
    exempt_sales: Decimal = Decimal("0")
    tax_collected: Decimal = Decimal("0")
    tax_due: Decimal = Decimal("0")
    status: TaxReturnStatus = TaxReturnStatus.DRAFT
    filed_date: date | None = None
    confirmation_number: str | None = None


@dataclass(frozen=True)
class TemporaryDifference:
    """A temporary difference between book and tax basis (ASC 740)."""
    id: UUID
    description: str
    book_basis: Decimal
    tax_basis: Decimal
    difference_amount: Decimal
    difference_type: str  # "taxable" or "deductible"
    tax_rate: Decimal = Decimal("0.21")
    deferred_amount: Decimal = Decimal("0")
    period: str = ""


@dataclass(frozen=True)
class DeferredTaxAsset:
    """A deferred tax asset (ASC 740)."""
    id: UUID
    source: str  # e.g. "bad_debt_allowance", "warranty_reserve"
    amount: Decimal
    valuation_allowance: Decimal = Decimal("0")
    net_amount: Decimal = Decimal("0")
    period: str = ""


@dataclass(frozen=True)
class DeferredTaxLiability:
    """A deferred tax liability (ASC 740)."""
    id: UUID
    source: str  # e.g. "depreciation", "prepaid_expense"
    amount: Decimal
    period: str = ""


@dataclass(frozen=True)
class TaxProvision:
    """Tax provision summary for a period (ASC 740)."""
    period: str
    current_tax_expense: Decimal
    deferred_tax_expense: Decimal
    total_tax_expense: Decimal
    effective_rate: Decimal
    pre_tax_income: Decimal = Decimal("0")


@dataclass(frozen=True)
class Jurisdiction:
    """A tax jurisdiction for multi-jurisdiction calculations."""
    code: str
    name: str
    tax_rate: Decimal
    jurisdiction_type: str = "state"  # federal, state, local, foreign
    is_active: bool = True
