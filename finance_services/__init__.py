"""
Finance Services - Stateful orchestration services.

Services compose pure engines with database sessions, LinkGraphService,
and other I/O-dependent infrastructure. They are the only layer that
may hold sessions, call LinkGraphService, or use wall-clock time.

Dependency direction:
    finance_services/ -> finance_engines/  (allowed)
    finance_services/ -> finance_kernel/   (allowed)
    finance_engines/  -> finance_services/ (FORBIDDEN)
    finance_kernel/   -> finance_services/ (FORBIDDEN)
"""

from finance_kernel.logging_config import get_logger

logger = get_logger("services")

from finance_services.valuation_service import ValuationLayer
from finance_services.reconciliation_service import ReconciliationManager
from finance_services.correction_service import CorrectionEngine
from finance_services.subledger_service import SubledgerService
from finance_services.subledger_ap import APSubledgerService
from finance_services.subledger_ar import ARSubledgerService
from finance_services.subledger_bank import BankSubledgerService
from finance_services.subledger_contract import ContractSubledgerService
from finance_services.subledger_inventory import InventorySubledgerService
from finance_services.engine_dispatcher import EngineDispatcher
from finance_services.invokers import register_standard_engines
from finance_services.posting_orchestrator import PostingOrchestrator
from finance_services.subledger_period_service import SubledgerPeriodService

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
