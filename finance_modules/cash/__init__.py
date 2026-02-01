"""
finance_modules.cash
====================

Responsibility:
    Thin ERP glue for cash management -- bank accounts, cash positions,
    inter-account transfers, wire transfers, reconciliation, and payment
    file generation.  All financial computation is delegated to shared
    engines; all journal posting is delegated to the kernel.

Architecture:
    Module layer (finance_modules).  May import from finance_kernel,
    finance_engines, and finance_services.  MUST NOT be imported by
    finance_kernel or finance_engines.

Invariants enforced:
    - R4  (DOUBLE_ENTRY_BALANCE): every CashService posting method delegates
          to ModulePostingService which enforces Dr = Cr per currency.
    - R7  (TRANSACTION_BOUNDARIES): CashService owns commit/rollback; kernel
          runs with auto_commit=False.
    - R16 (ISO_4217): currency codes validated at kernel boundary.

Failure modes:
    - Unsupported bank statement format -> ValueError from helpers.
    - Profile mismatch (event_type not registered) -> PostingError from kernel.
    - Session failure -> rollback in CashService, exception re-raised.

Audit relevance:
    Cash is the highest-risk ledger area.  Every public CashService method
    logs structured events for the audit trail and delegates to the kernel's
    posting pipeline, which records InterpretationOutcome and AuditEvent rows.
"""

from finance_modules.cash.config import CashConfig
from finance_modules.cash.models import (
    BankAccount,
    BankStatement,
    BankStatementLine,
    BankTransaction,
    CashForecast,
    PaymentFile,
    Reconciliation,
    ReconciliationMatch,
)
from finance_modules.cash.profiles import CASH_PROFILES
from finance_modules.cash.workflows import RECONCILIATION_WORKFLOW

__all__ = [
    "BankAccount",
    "BankStatement",
    "BankStatementLine",
    "BankTransaction",
    "CashForecast",
    "PaymentFile",
    "Reconciliation",
    "ReconciliationMatch",
    "CASH_PROFILES",
    "RECONCILIATION_WORKFLOW",
    "CashConfig",
]
