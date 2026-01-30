"""
Accounts Payable Module.

Handles vendor invoices, three-way matching, payments, and aging.

Total: ~150 lines of module-specific code.
Matching, aging, and allocation come from shared engines.
"""

from finance_modules.ap.models import Vendor, Invoice, InvoiceLine, Payment, PaymentBatch
from finance_modules.ap.profiles import AP_PROFILES
from finance_modules.ap.workflows import INVOICE_WORKFLOW, PAYMENT_WORKFLOW
from finance_modules.ap.config import APConfig

__all__ = [
    "Vendor",
    "Invoice",
    "InvoiceLine",
    "Payment",
    "PaymentBatch",
    "AP_PROFILES",
    "INVOICE_WORKFLOW",
    "PAYMENT_WORKFLOW",
    "APConfig",
]
