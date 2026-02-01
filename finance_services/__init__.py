"""
finance_services -- Package init and public API.

Responsibility:
    Stateful orchestration services that compose pure calculation engines
    (finance_engines/) with database sessions, LinkGraphService, and other
    I/O-dependent infrastructure.  This is the **only** layer that may hold
    database sessions, call LinkGraphService, or use wall-clock time.

Architecture position:
    Services -- stateful orchestration over engines + kernel.

    Dependency direction (enforced by tests/architecture/test_kernel_boundary.py):
        finance_services/ -> finance_engines/  (allowed)
        finance_services/ -> finance_kernel/   (allowed)
        finance_engines/  -> finance_services/ (FORBIDDEN)
        finance_kernel/   -> finance_services/ (FORBIDDEN)

Invariants enforced:
    - Layer isolation: finance_kernel and finance_engines must never import
      from this package.
    - DI transparency: All kernel service wiring is centralised in
      PostingOrchestrator; no service self-constructs dependencies.

Failure modes:
    - ImportError at startup if a service's dependency graph is broken.

Audit relevance:
    - This package is the canonical import surface for external consumers.
      Changes to __all__ must be reviewed for backwards-compatibility.
"""

from finance_kernel.logging_config import get_logger

logger = get_logger("services")

from finance_services.correction_service import CorrectionEngine
from finance_services.engine_dispatcher import EngineDispatcher
from finance_services.invokers import register_standard_engines
from finance_services.posting_orchestrator import PostingOrchestrator
from finance_services.reconciliation_service import ReconciliationManager
from finance_services.subledger_ap import APSubledgerService
from finance_services.subledger_ar import ARSubledgerService
from finance_services.subledger_bank import BankSubledgerService
from finance_services.subledger_contract import ContractSubledgerService
from finance_services.subledger_inventory import InventorySubledgerService
from finance_services.subledger_period_service import SubledgerPeriodService
from finance_services.subledger_service import SubledgerService
from finance_services.valuation_service import ValuationLayer

__all__ = [
    "APSubledgerService",
    "ARSubledgerService",
    "BankSubledgerService",
    "ContractSubledgerService",
    "CorrectionEngine",
    "EngineDispatcher",
    "InventorySubledgerService",
    "PostingOrchestrator",
    "ReconciliationManager",
    "SubledgerPeriodService",
    "SubledgerService",
    "ValuationLayer",
    "register_standard_engines",
]
