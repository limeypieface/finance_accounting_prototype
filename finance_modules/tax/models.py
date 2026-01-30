"""
Tax Domain Models.

The nouns of tax: jurisdictions, rates, exemptions, transactions, returns.
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
