"""
Finance Engines - Pure Calculation Engines

Pure function engines that operate on finance_kernel primitives.
No I/O, no database access, fully deterministic.

Purity rule: engines NEVER call ``datetime.now()`` or ``date.today()``.
Timestamps and dates must be passed in as explicit parameters.
Callers (services) are responsible for providing the current time.

Usage:
    from finance_engines.variance import VarianceCalculator
    from finance_engines.allocation import AllocationEngine
    from finance_engines.matching import MatchingEngine
    from finance_engines.aging import AgingCalculator
    from finance_engines.subledger import SubledgerEntry
    from finance_engines.tax import TaxCalculator

    # Pure domain objects from valuation/reconciliation/correction
    from finance_engines.valuation import CostLot, CostMethod
    from finance_engines.reconciliation import ReconciliationState
    from finance_engines.correction import UnwindPlan
"""

from finance_kernel.logging_config import get_logger

logger = get_logger("engines")

from finance_engines.variance import (
    VarianceCalculator,
    VarianceResult,
    VarianceType,
    VarianceDisposition,
)
from finance_engines.allocation import (
    AllocationEngine,
    AllocationTarget,
    AllocationLine,
    AllocationResult,
    AllocationMethod,
)
from finance_engines.matching import (
    MatchingEngine,
    MatchCandidate,
    MatchResult,
    MatchTolerance,
    MatchStatus,
    MatchType,
    ToleranceType,
)
from finance_engines.aging import (
    AgingCalculator,
    AgeBucket,
    AgedItem,
    AgingReport,
)
from finance_engines.subledger import (
    SubledgerEntry,
)
from finance_engines.tax import (
    TaxCalculator,
    TaxRate,
    TaxLine,
    TaxCalculationResult,
    TaxType,
)

# Pure domain objects from composite engine subpackages
from finance_engines.valuation import (
    CostLot,
    CostLayer,
    CostLayerConsumption,
    ConsumptionResult,
    StandardCostResult,
    CostMethod,
)
from finance_engines.reconciliation import (
    ReconciliationState,
    ReconciliationStatus,
    DocumentMatch,
    PaymentApplication,
    ThreeWayMatchResult,
    BankReconciliationLine,
    BankReconciliationStatus,
)
from finance_engines.correction import (
    UnwindPlan,
    AffectedArtifact,
    CompensatingEntry,
    CompensatingLine,
    CorrectionResult,
    CorrectionType,
    UnwindStrategy,
)
from finance_engines.allocation_cascade import (
    AllocationBase,
    AllocationStep,
    AllocationStepResult,
    execute_cascade,
    build_dcaa_cascade,
    calculate_contract_total,
)
from finance_engines.billing import (
    BillingContractType,
    BillingLineType,
    BillingInput,
    BillingLineItem,
    BillingResult,
    CostBreakdown,
    IndirectRates,
    LaborRateEntry,
    MilestoneEntry,
    RateAdjustmentInput,
    RateAdjustmentResult,
    calculate_billing,
    calculate_indirect_costs,
    calculate_fee,
    calculate_rate_adjustment,
    apply_withholding,
    apply_funding_limit,
)
from finance_engines.ice import (
    ICEScheduleType,
    CostElement,
    AllowabilityStatus,
    LaborDetailInput,
    OtherDirectCostInput,
    ContractCostInput,
    IndirectPoolInput,
    ContractCeilingInput,
    ICEInput,
    ScheduleA,
    ScheduleALine,
    ScheduleB,
    ScheduleBLine,
    ScheduleC,
    ScheduleCLine,
    ScheduleG,
    ScheduleGLine,
    ScheduleH,
    ScheduleHLine,
    ScheduleI,
    ScheduleILine,
    ScheduleJ,
    ScheduleJLine,
    ICEValidationFinding,
    ICESubmission,
    compile_ice_submission,
    compile_schedule_a,
    compile_schedule_b,
    compile_schedule_c,
    compile_schedule_g,
    compile_schedule_h,
    compile_schedule_i,
    compile_schedule_j,
)

__all__ = [
    # Variance
    "VarianceCalculator",
    "VarianceResult",
    "VarianceType",
    "VarianceDisposition",
    # Allocation
    "AllocationEngine",
    "AllocationTarget",
    "AllocationLine",
    "AllocationResult",
    "AllocationMethod",
    # Matching
    "MatchingEngine",
    "MatchCandidate",
    "MatchResult",
    "MatchTolerance",
    "MatchStatus",
    "MatchType",
    "ToleranceType",
    # Aging
    "AgingCalculator",
    "AgeBucket",
    "AgedItem",
    "AgingReport",
    # Subledger (pure domain types only)
    "SubledgerEntry",
    # Tax
    "TaxCalculator",
    "TaxRate",
    "TaxLine",
    "TaxCalculationResult",
    "TaxType",
    # Valuation (pure domain objects)
    "CostLot",
    "CostLayer",
    "CostLayerConsumption",
    "ConsumptionResult",
    "StandardCostResult",
    "CostMethod",
    # Reconciliation (pure domain objects)
    "ReconciliationState",
    "ReconciliationStatus",
    "DocumentMatch",
    "PaymentApplication",
    "ThreeWayMatchResult",
    "BankReconciliationLine",
    "BankReconciliationStatus",
    # Correction (pure domain objects)
    "UnwindPlan",
    "AffectedArtifact",
    "CompensatingEntry",
    "CompensatingLine",
    "CorrectionResult",
    "CorrectionType",
    "UnwindStrategy",
    # Allocation Cascade (DCAA)
    "AllocationBase",
    "AllocationStep",
    "AllocationStepResult",
    "execute_cascade",
    "build_dcaa_cascade",
    "calculate_contract_total",
    # Billing Engine (pure)
    "BillingContractType",
    "BillingLineType",
    "BillingInput",
    "BillingLineItem",
    "BillingResult",
    "CostBreakdown",
    "IndirectRates",
    "LaborRateEntry",
    "MilestoneEntry",
    "RateAdjustmentInput",
    "RateAdjustmentResult",
    "calculate_billing",
    "calculate_indirect_costs",
    "calculate_fee",
    "calculate_rate_adjustment",
    "apply_withholding",
    "apply_funding_limit",
    # ICE Engine (pure, DCAA Incurred Cost Electronically)
    "ICEScheduleType",
    "CostElement",
    "AllowabilityStatus",
    "LaborDetailInput",
    "OtherDirectCostInput",
    "ContractCostInput",
    "IndirectPoolInput",
    "ContractCeilingInput",
    "ICEInput",
    "ScheduleA",
    "ScheduleALine",
    "ScheduleB",
    "ScheduleBLine",
    "ScheduleC",
    "ScheduleCLine",
    "ScheduleG",
    "ScheduleGLine",
    "ScheduleH",
    "ScheduleHLine",
    "ScheduleI",
    "ScheduleILine",
    "ScheduleJ",
    "ScheduleJLine",
    "ICEValidationFinding",
    "ICESubmission",
    "compile_ice_submission",
    "compile_schedule_a",
    "compile_schedule_b",
    "compile_schedule_c",
    "compile_schedule_g",
    "compile_schedule_h",
    "compile_schedule_i",
    "compile_schedule_j",
]

logger.debug("engines_package_loaded", extra={
    "module_count": 10,
    "modules": [
        "variance", "allocation", "allocation_cascade", "matching",
        "aging", "subledger", "tax", "valuation", "reconciliation",
        "correction", "billing", "ice",
    ],
})
