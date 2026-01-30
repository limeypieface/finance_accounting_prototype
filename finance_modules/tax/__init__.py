"""
Tax Module.

Handles sales/use tax, VAT, and tax reporting.

Total: ~180 lines of module-specific code.
Tax calculation engines (jurisdiction lookup, rate determination) come from shared engines.
"""

from finance_modules.tax.models import (
    TaxJurisdiction,
    TaxRate,
    TaxExemption,
    TaxTransaction,
    TaxReturn,
)
from finance_modules.tax.profiles import TAX_PROFILES
from finance_modules.tax.workflows import TAX_RETURN_WORKFLOW
from finance_modules.tax.config import TaxConfig

__all__ = [
    "TaxJurisdiction",
    "TaxRate",
    "TaxExemption",
    "TaxTransaction",
    "TaxReturn",
    "TAX_PROFILES",
    "TAX_RETURN_WORKFLOW",
    "TaxConfig",
]
