"""
Module: finance_engines
Responsibility:
    Package entrypoint that re-exports all public symbols from the pure
    calculation engine sub-modules.  This is the canonical import surface
    for higher layers (finance_services, finance_modules).

Architecture position:
    Engines -- pure calculation layer, zero I/O.
    May only import finance_kernel/domain/values (and sibling engine modules).
    MUST NOT import finance_services or finance_modules.

Invariants enforced:
    - Purity: engines NEVER call ``datetime.now()`` or ``date.today()`` (R6).
      Timestamps and dates must be passed in as explicit parameters.
      Callers (services) are responsible for providing the current time.
    - Decimal-only arithmetic: all monetary amounts use ``Decimal``; floats
      are forbidden (R16, R17).
    - Determinism: identical inputs always produce identical outputs.

Failure modes:
    - ImportError if a sub-module is missing or has unresolved dependencies.
    - ValueError propagated from individual engines on invalid input.

Audit relevance:
    Every engine invocation is traced via the ``@traced_engine`` decorator
    (see ``finance_engines.tracer``), emitting FINANCE_ENGINE_TRACE log
    records that include engine name, version, input fingerprint, and
    duration.

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

from finance_engines.aging import (
    AgeBucket,
    AgedItem,
    AgingCalculator,
    AgingReport,
)
from finance_engines.allocation import (
    AllocationEngine,
    AllocationLine,
    AllocationMethod,
    AllocationResult,
    AllocationTarget,
)
from finance_engines.allocation_cascade import (
    AllocationBase,
    AllocationStep,
    AllocationStepResult,
    build_dcaa_cascade,
    calculate_contract_total,
    execute_cascade,
)
from finance_engines.billing import (
    BillingContractType,
    BillingInput,
    BillingLineItem,
    BillingLineType,
    BillingResult,
    CostBreakdown,
    IndirectRates,
    LaborRateEntry,
    MilestoneEntry,
    RateAdjustmentInput,
    RateAdjustmentResult,
    apply_funding_limit,
    apply_withholding,
    calculate_billing,
    calculate_fee,
    calculate_indirect_costs,
    calculate_rate_adjustment,
)
from finance_engines.correction import (
    AffectedArtifact,
    CompensatingEntry,
    CompensatingLine,
    CorrectionResult,
    CorrectionType,
    UnwindPlan,
    UnwindStrategy,
)
from finance_engines.ice import (
    AllowabilityStatus,
    ContractCeilingInput,
    ContractCostInput,
    CostElement,
    ICEInput,
    ICEScheduleType,
    ICESubmission,
    ICEValidationFinding,
    IndirectPoolInput,
    LaborDetailInput,
    OtherDirectCostInput,
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
    compile_ice_submission,
    compile_schedule_a,
    compile_schedule_b,
    compile_schedule_c,
    compile_schedule_g,
    compile_schedule_h,
    compile_schedule_i,
    compile_schedule_j,
)
from finance_engines.matching import (
    MatchCandidate,
    MatchingEngine,
    MatchResult,
    MatchStatus,
    MatchTolerance,
    MatchType,
    ToleranceType,
)
from finance_engines.reconciliation import (
    BankReconciliationLine,
    BankReconciliationStatus,
    DocumentMatch,
    PaymentApplication,
    ReconciliationState,
    ReconciliationStatus,
    ThreeWayMatchResult,
)
from finance_engines.subledger import (
    SubledgerEntry,
)
from finance_engines.tax import (
    TaxCalculationResult,
    TaxCalculator,
    TaxLine,
    TaxRate,
    TaxType,
)

# Pure domain objects from composite engine subpackages
from finance_engines.valuation import (
    ConsumptionResult,
    CostLayer,
    CostLayerConsumption,
    CostLot,
    CostMethod,
    StandardCostResult,
)
from finance_engines.variance import (
    VarianceCalculator,
    VarianceDisposition,
    VarianceResult,
    VarianceType,
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
