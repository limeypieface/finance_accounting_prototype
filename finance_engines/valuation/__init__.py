"""
Valuation - Pure cost lot domain objects for FIFO/LIFO/Standard costing.

Pure domain types only. The stateful ValuationLayer service has moved
to finance_services.valuation_service.
"""

from finance_kernel.logging_config import get_logger

logger = get_logger("engines.valuation")

from finance_engines.valuation.cost_lot import (
    ConsumptionResult,
    CostLayer,
    CostLayerConsumption,
    CostLot,
    CostMethod,
    StandardCostResult,
)

__all__ = [
    "CostLot",
    "CostLayer",
    "CostLayerConsumption",
    "ConsumptionResult",
    "StandardCostResult",
    "CostMethod",
]
