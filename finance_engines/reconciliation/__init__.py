"""
Reconciliation - Pure domain objects for document reconciliation.

Pure domain types only. The stateful ReconciliationManager service has moved
to finance_services.reconciliation_service.
"""

from finance_kernel.logging_config import get_logger

logger = get_logger("engines.reconciliation")

from finance_engines.reconciliation.domain import (
    BankReconciliationLine,
    BankReconciliationStatus,
    DocumentMatch,
    MatchType,
    PaymentApplication,
    ReconciliationState,
    ReconciliationStatus,
    ThreeWayMatchResult,
)

from finance_engines.reconciliation.lifecycle_types import (
    CheckSeverity,
    CheckStatus,
    LifecycleChain,
    LifecycleCheckResult,
    LifecycleEdge,
    LifecycleNode,
    ReconciliationFinding,
)

from finance_engines.reconciliation.checker import (
    LifecycleReconciliationChecker,
)

from finance_engines.reconciliation.bank_recon_types import (
    BankReconCheckResult,
    BankReconContext,
    BankReconLine,
    BankReconStatement,
)

from finance_engines.reconciliation.bank_checker import (
    BankReconciliationChecker,
)

__all__ = [
    # Existing reconciliation domain
    "ReconciliationState",
    "ReconciliationStatus",
    "DocumentMatch",
    "MatchType",
    "PaymentApplication",
    "ThreeWayMatchResult",
    "BankReconciliationLine",
    "BankReconciliationStatus",
    # Lifecycle reconciliation (GAP-REC)
    "CheckSeverity",
    "CheckStatus",
    "LifecycleChain",
    "LifecycleCheckResult",
    "LifecycleEdge",
    "LifecycleNode",
    "ReconciliationFinding",
    "LifecycleReconciliationChecker",
    # Bank reconciliation checks (GAP-BRC)
    "BankReconLine",
    "BankReconStatement",
    "BankReconContext",
    "BankReconCheckResult",
    "BankReconciliationChecker",
]
