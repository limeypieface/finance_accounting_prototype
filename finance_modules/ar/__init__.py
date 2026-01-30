"""
Accounts Receivable Module.

Handles customer invoices, receipts, credit memos, and collections.

Total: ~150 lines of module-specific code.
Aging, allocation, and matching come from shared engines.
"""

from finance_modules.ar.models import Customer, Invoice, InvoiceLine, Receipt, CreditMemo
from finance_modules.ar.profiles import AR_PROFILES
from finance_modules.ar.workflows import INVOICE_WORKFLOW, RECEIPT_WORKFLOW
from finance_modules.ar.config import ARConfig

__all__ = [
    "Customer",
    "Invoice",
    "InvoiceLine",
    "Receipt",
    "CreditMemo",
    "AR_PROFILES",
    "INVOICE_WORKFLOW",
    "RECEIPT_WORKFLOW",
    "ARConfig",
]
