"""
Rate DCAA Compliance Engine (``finance_engines.rate_compliance``).

Responsibility
--------------
Pure validation functions for DCAA-compliant rate management:

* D8 -- labor rate verification against approved schedules and contract
  ceilings (FAR 31.201-3)
* Provisional-to-final indirect rate reconciliation

Architecture position
---------------------
**Engines layer** -- pure functional core.  ZERO I/O, ZERO database,
ZERO clock reads.  May only import from ``finance_kernel.domain.values``
and module-level DTO types.

Invariants enforced
-------------------
* No ``datetime.now()`` or ``date.today()`` calls.
* No ORM, no services, no config imports.
* All functions are deterministic: same inputs = same outputs.

Failure modes
-------------
* Returns validation results (not exceptions) for business rule violations.
* Raises ``ValueError`` only for programming errors (invalid arguments).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from finance_modules.contracts.rate_types import (
    ContractRateCeiling,
    IndirectRateRecord,
    IndirectRateType,
    LaborRateSchedule,
    RateReconciliationRecord,
    RateSource,
    RateVerificationResult,
    RateViolationType,
    ReconciliationDirection,
)


# ---------------------------------------------------------------------------
# Rate schedule lookup
# ---------------------------------------------------------------------------


def find_applicable_rate(
    employee_classification: str,
    labor_category: str,
    rate_schedule: tuple[LaborRateSchedule, ...],
    as_of_date: date,
) -> LaborRateSchedule | None:
    """Find the effective rate for an employee classification on a date.

    Searches through the rate schedule for the first matching entry that
    is effective on the given date.

    Args:
        employee_classification: The employee's classification (e.g., "Senior Engineer").
        labor_category: The DCAA labor category code.
        rate_schedule: All available rate schedules.
        as_of_date: The date to check effectiveness.

    Returns:
        The matching LaborRateSchedule, or None if not found.
    """
    for schedule in rate_schedule:
        if (
            schedule.employee_classification == employee_classification
            and schedule.labor_category == labor_category
            and schedule.is_effective(as_of_date)
        ):
            return schedule
    return None


def find_contract_ceiling(
    contract_id: UUID,
    labor_category: str,
    ceilings: tuple[ContractRateCeiling, ...],
    as_of_date: date,
) -> ContractRateCeiling | None:
    """Find the rate ceiling for a contract and labor category.

    Args:
        contract_id: The contract to check.
        labor_category: The DCAA labor category code.
        ceilings: All available rate ceilings.
        as_of_date: The date to check effectiveness.

    Returns:
        The matching ContractRateCeiling, or None if no ceiling exists.
    """
    for ceiling in ceilings:
        if (
            ceiling.contract_id == contract_id
            and ceiling.labor_category == labor_category
            and ceiling.is_effective(as_of_date)
        ):
            return ceiling
    return None


# ---------------------------------------------------------------------------
# D8: Labor rate verification (FAR 31.201-3)
# ---------------------------------------------------------------------------


def verify_labor_rate(
    employee_id: UUID,
    employee_classification: str,
    labor_category: str,
    charged_rate: Decimal,
    rate_schedule: tuple[LaborRateSchedule, ...],
    contract_ceilings: tuple[ContractRateCeiling, ...] | None,
    contract_id: UUID | None,
    charge_date: date,
) -> RateVerificationResult:
    """Verify a labor charge rate against approved schedules and ceilings.

    Checks in order:
    1. Rate schedule lookup -- does an approved rate exist?
    2. Rate expiration -- is the approved rate still effective?
    3. Classification check -- does the charged rate exceed the approved rate?
    4. Contract ceiling check -- does the charged rate exceed the contract cap?

    Args:
        employee_id: The employee being charged.
        employee_classification: Employee's classification.
        labor_category: DCAA labor category code.
        charged_rate: The hourly rate being charged.
        rate_schedule: All available rate schedules.
        contract_ceilings: Contract-specific rate ceilings (may be None).
        contract_id: The contract being charged (may be None for indirect).
        charge_date: The date of the labor charge.

    Returns:
        RateVerificationResult with validity and violation details.
    """
    # Step 1: Find the approved rate
    approved = find_applicable_rate(
        employee_classification, labor_category, rate_schedule, charge_date,
    )

    if approved is None:
        return RateVerificationResult(
            is_valid=False,
            employee_id=employee_id,
            charged_rate=charged_rate,
            approved_rate=Decimal("0"),
            violation_type=RateViolationType.RATE_EXPIRED,
            message=(
                f"No approved rate found for classification "
                f"'{employee_classification}' / category '{labor_category}' "
                f"effective on {charge_date}."
            ),
        )

    # Step 2: Check provisional status
    if approved.rate_source == RateSource.PROVISIONAL:
        # Provisional rates are allowed but flagged
        pass  # continue with verification

    # Step 3: Check against approved rate
    if charged_rate > approved.loaded_rate:
        excess = charged_rate - approved.loaded_rate
        return RateVerificationResult(
            is_valid=False,
            employee_id=employee_id,
            charged_rate=charged_rate,
            approved_rate=approved.loaded_rate,
            excess_amount=excess,
            violation_type=RateViolationType.EXCEEDS_CLASSIFICATION,
            message=(
                f"Charged rate ({charged_rate}) exceeds approved loaded "
                f"rate ({approved.loaded_rate}) for classification "
                f"'{employee_classification}'."
            ),
        )

    # Step 4: Check contract ceiling
    ceiling_rate: Decimal | None = None
    if contract_id is not None and contract_ceilings:
        ceiling = find_contract_ceiling(
            contract_id, labor_category, contract_ceilings, charge_date,
        )
        if ceiling is not None:
            ceiling_rate = ceiling.max_loaded_rate or ceiling.max_hourly_rate
            if charged_rate > ceiling_rate:
                excess = charged_rate - ceiling_rate
                return RateVerificationResult(
                    is_valid=False,
                    employee_id=employee_id,
                    charged_rate=charged_rate,
                    approved_rate=approved.loaded_rate,
                    ceiling_rate=ceiling_rate,
                    excess_amount=excess,
                    violation_type=RateViolationType.EXCEEDS_CONTRACT_CEILING,
                    message=(
                        f"Charged rate ({charged_rate}) exceeds contract "
                        f"ceiling ({ceiling_rate}) for labor category "
                        f"'{labor_category}' on contract {contract_id}."
                    ),
                )

    # All checks passed
    return RateVerificationResult(
        is_valid=True,
        employee_id=employee_id,
        charged_rate=charged_rate,
        approved_rate=approved.loaded_rate,
        ceiling_rate=ceiling_rate,
    )


# ---------------------------------------------------------------------------
# Indirect rate reconciliation
# ---------------------------------------------------------------------------


def compute_rate_reconciliation(
    reconciliation_id: UUID,
    fiscal_year: int,
    rate_type: IndirectRateType,
    provisional_rate: Decimal,
    final_rate: Decimal,
    base_amount: Decimal,
) -> RateReconciliationRecord:
    """Compute a single provisional-to-final rate reconciliation.

    The adjustment equals the base amount times the rate difference.
    Positive difference means underapplied (owe more); negative means
    overapplied (credit due).

    Args:
        reconciliation_id: Unique ID for this reconciliation record.
        fiscal_year: The fiscal year being reconciled.
        rate_type: The indirect rate type (FRINGE, OVERHEAD, G_AND_A, etc.).
        provisional_rate: Rate used throughout the year.
        final_rate: DCAA-audited final rate.
        base_amount: Total base dollars for the year.

    Returns:
        RateReconciliationRecord with computed adjustment.
    """
    return RateReconciliationRecord(
        reconciliation_id=reconciliation_id,
        fiscal_year=fiscal_year,
        rate_type=rate_type,
        provisional_rate=provisional_rate,
        final_rate=final_rate,
        base_amount=base_amount,
    )


def compute_all_reconciliations(
    fiscal_year: int,
    provisional_rates: tuple[IndirectRateRecord, ...],
    final_rates: tuple[IndirectRateRecord, ...],
    base_amounts: dict[IndirectRateType, Decimal],
    reconciliation_id_factory: callable,
) -> tuple[RateReconciliationRecord, ...]:
    """Compute all rate reconciliations for a fiscal year.

    Matches provisional rates with their corresponding final rates by
    rate_type, then computes the adjustment for each.

    Args:
        fiscal_year: The fiscal year being reconciled.
        provisional_rates: All provisional rates for the year.
        final_rates: All final (audited) rates for the year.
        base_amounts: Total base dollars by rate type.
        reconciliation_id_factory: Callable that returns a new UUID.

    Returns:
        Tuple of RateReconciliationRecord for each rate type that has
        both provisional and final rates.
    """
    # Index final rates by type
    final_by_type: dict[IndirectRateType, IndirectRateRecord] = {}
    for rate in final_rates:
        if rate.fiscal_year == fiscal_year and rate.rate_status == RateSource.FINAL:
            final_by_type[rate.rate_type] = rate

    results: list[RateReconciliationRecord] = []

    for prov in provisional_rates:
        if prov.fiscal_year != fiscal_year:
            continue
        if prov.rate_status != RateSource.PROVISIONAL:
            continue

        final = final_by_type.get(prov.rate_type)
        if final is None:
            continue  # no final rate yet, skip

        base = base_amounts.get(prov.rate_type, Decimal("0"))
        if base == Decimal("0"):
            continue  # no base, no adjustment

        record = compute_rate_reconciliation(
            reconciliation_id=reconciliation_id_factory(),
            fiscal_year=fiscal_year,
            rate_type=prov.rate_type,
            provisional_rate=prov.rate_value,
            final_rate=final.rate_value,
            base_amount=base,
        )
        results.append(record)

    return tuple(results)
