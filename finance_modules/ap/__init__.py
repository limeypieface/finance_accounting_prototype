"""
Accounts Payable Module (``finance_modules.ap``).

Responsibility
--------------
Thin ERP glue for the purchase-to-pay cycle: vendor invoices, three-way
PO/receipt/invoice matching, payments (single and batch), aging analysis,
accruals, prepayments, and vendor hold management.

Architecture position
---------------------
**Modules layer** -- declarative profiles, workflows, config schemas, and a
service facade that delegates all computation to ``finance_engines`` and all
journal posting to ``finance_kernel`` via ``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``APService``; commit/rollback is
          explicit per operation.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New AP scenarios require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.  Caller inspects ``result.message``.
* Database exceptions propagate after session rollback.

Audit relevance
---------------
Every posting method emits structured log events (``logger.info``) with
operation-specific extras for the audit trail.  Journal entries carry full
provenance through the kernel audit chain (R11).

Total: ~150 lines of module-specific code.
Matching, aging, and allocation come from shared engines.
"""

from finance_modules.ap.models import (
    Vendor,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentBatch,
    PaymentRun,
    PaymentRunLine,
    VendorHold,
)
from finance_modules.ap.profiles import AP_PROFILES
from finance_modules.ap.workflows import INVOICE_WORKFLOW, PAYMENT_WORKFLOW
from finance_modules.ap.config import APConfig

__all__ = [
    "Vendor",
    "Invoice",
    "InvoiceLine",
    "Payment",
    "PaymentBatch",
    "PaymentRun",
    "PaymentRunLine",
    "VendorHold",
    "AP_PROFILES",
    "INVOICE_WORKFLOW",
    "PAYMENT_WORKFLOW",
    "APConfig",
]
