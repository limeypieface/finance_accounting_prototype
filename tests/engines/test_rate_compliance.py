"""
Tests for Rate DCAA Compliance Engine.

Covers:
- D8: Labor rate verification (FAR 31.201-3)
- Rate schedule lookup
- Contract ceiling lookup
- Indirect rate reconciliation
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_engines.rate_compliance import (
    compute_all_reconciliations,
    compute_rate_reconciliation,
    find_applicable_rate,
    find_contract_ceiling,
    verify_labor_rate,
)
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
# Helpers
# ---------------------------------------------------------------------------


def _schedule(
    classification: str = "Senior Engineer",
    category: str = "ENG-03",
    base_rate: str = "75.00",
    loaded_rate: str = "125.00",
    effective_from: date = date(2025, 1, 1),
    effective_to: date | None = None,
    rate_source: RateSource = RateSource.NEGOTIATED,
) -> LaborRateSchedule:
    return LaborRateSchedule(
        schedule_id=uuid4(),
        employee_classification=classification,
        labor_category=category,
        base_rate=Decimal(base_rate),
        loaded_rate=Decimal(loaded_rate),
        effective_from=effective_from,
        effective_to=effective_to,
        rate_source=rate_source,
    )


def _ceiling(
    contract_id: "uuid4" = None,
    category: str = "ENG-03",
    max_hourly: str = "130.00",
    max_loaded: str | None = None,
    effective_from: date = date(2025, 1, 1),
) -> ContractRateCeiling:
    return ContractRateCeiling(
        contract_id=contract_id or uuid4(),
        labor_category=category,
        max_hourly_rate=Decimal(max_hourly),
        max_loaded_rate=Decimal(max_loaded) if max_loaded else None,
        effective_from=effective_from,
    )


# ===========================================================================
# Rate Schedule Lookup
# ===========================================================================


class TestFindApplicableRate:
    """Rate schedule lookup by classification, category, date."""

    def test_exact_match(self):
        schedule = (_schedule(),)
        result = find_applicable_rate(
            "Senior Engineer", "ENG-03", schedule, date(2026, 1, 15),
        )
        assert result is not None
        assert result.loaded_rate == Decimal("125.00")

    def test_no_match_wrong_classification(self):
        schedule = (_schedule(classification="Junior Engineer"),)
        result = find_applicable_rate(
            "Senior Engineer", "ENG-03", schedule, date(2026, 1, 15),
        )
        assert result is None

    def test_no_match_expired(self):
        schedule = (_schedule(effective_to=date(2025, 12, 31)),)
        result = find_applicable_rate(
            "Senior Engineer", "ENG-03", schedule, date(2026, 1, 15),
        )
        assert result is None

    def test_no_match_not_yet_effective(self):
        schedule = (_schedule(effective_from=date(2026, 6, 1)),)
        result = find_applicable_rate(
            "Senior Engineer", "ENG-03", schedule, date(2026, 1, 15),
        )
        assert result is None

    def test_multiple_schedules_picks_first_match(self):
        schedules = (
            _schedule(loaded_rate="120.00", effective_from=date(2025, 1, 1)),
            _schedule(loaded_rate="130.00", effective_from=date(2025, 1, 1)),
        )
        result = find_applicable_rate(
            "Senior Engineer", "ENG-03", schedules, date(2026, 1, 15),
        )
        assert result.loaded_rate == Decimal("120.00")


# ===========================================================================
# Contract Ceiling Lookup
# ===========================================================================


class TestFindContractCeiling:
    """Contract rate ceiling lookup."""

    def test_ceiling_found(self):
        contract_id = uuid4()
        ceilings = (_ceiling(contract_id=contract_id),)
        result = find_contract_ceiling(
            contract_id, "ENG-03", ceilings, date(2026, 1, 15),
        )
        assert result is not None
        assert result.max_hourly_rate == Decimal("130.00")

    def test_no_ceiling_wrong_contract(self):
        ceilings = (_ceiling(contract_id=uuid4()),)
        result = find_contract_ceiling(
            uuid4(), "ENG-03", ceilings, date(2026, 1, 15),
        )
        assert result is None

    def test_no_ceiling_wrong_category(self):
        contract_id = uuid4()
        ceilings = (_ceiling(contract_id=contract_id, category="MGR-01"),)
        result = find_contract_ceiling(
            contract_id, "ENG-03", ceilings, date(2026, 1, 15),
        )
        assert result is None


# ===========================================================================
# D8: Labor Rate Verification
# ===========================================================================


class TestVerifyLaborRate:
    """D8: Rate verification against schedules and ceilings."""

    def test_valid_rate(self):
        schedule = (_schedule(loaded_rate="125.00"),)
        result = verify_labor_rate(
            employee_id=uuid4(),
            employee_classification="Senior Engineer",
            labor_category="ENG-03",
            charged_rate=Decimal("120.00"),
            rate_schedule=schedule,
            contract_ceilings=None,
            contract_id=None,
            charge_date=date(2026, 1, 15),
        )
        assert result.is_valid
        assert result.approved_rate == Decimal("125.00")

    def test_exceeds_classification_rate(self):
        schedule = (_schedule(loaded_rate="125.00"),)
        result = verify_labor_rate(
            employee_id=uuid4(),
            employee_classification="Senior Engineer",
            labor_category="ENG-03",
            charged_rate=Decimal("150.00"),
            rate_schedule=schedule,
            contract_ceilings=None,
            contract_id=None,
            charge_date=date(2026, 1, 15),
        )
        assert not result.is_valid
        assert result.violation_type == RateViolationType.EXCEEDS_CLASSIFICATION
        assert result.excess_amount == Decimal("25.00")

    def test_exceeds_contract_ceiling(self):
        contract_id = uuid4()
        schedule = (_schedule(loaded_rate="150.00"),)
        ceilings = (
            _ceiling(contract_id=contract_id, max_hourly="120.00"),
        )
        result = verify_labor_rate(
            employee_id=uuid4(),
            employee_classification="Senior Engineer",
            labor_category="ENG-03",
            charged_rate=Decimal("125.00"),
            rate_schedule=schedule,
            contract_ceilings=ceilings,
            contract_id=contract_id,
            charge_date=date(2026, 1, 15),
        )
        assert not result.is_valid
        assert result.violation_type == RateViolationType.EXCEEDS_CONTRACT_CEILING

    def test_no_approved_rate_found(self):
        result = verify_labor_rate(
            employee_id=uuid4(),
            employee_classification="Senior Engineer",
            labor_category="ENG-03",
            charged_rate=Decimal("100.00"),
            rate_schedule=(),  # empty
            contract_ceilings=None,
            contract_id=None,
            charge_date=date(2026, 1, 15),
        )
        assert not result.is_valid
        assert result.violation_type == RateViolationType.RATE_EXPIRED
        assert "No approved rate found" in result.message

    def test_exactly_at_approved_rate(self):
        schedule = (_schedule(loaded_rate="125.00"),)
        result = verify_labor_rate(
            employee_id=uuid4(),
            employee_classification="Senior Engineer",
            labor_category="ENG-03",
            charged_rate=Decimal("125.00"),
            rate_schedule=schedule,
            contract_ceilings=None,
            contract_id=None,
            charge_date=date(2026, 1, 15),
        )
        assert result.is_valid

    def test_exactly_at_ceiling(self):
        contract_id = uuid4()
        schedule = (_schedule(loaded_rate="150.00"),)
        ceilings = (
            _ceiling(contract_id=contract_id, max_hourly="125.00"),
        )
        result = verify_labor_rate(
            employee_id=uuid4(),
            employee_classification="Senior Engineer",
            labor_category="ENG-03",
            charged_rate=Decimal("125.00"),
            rate_schedule=schedule,
            contract_ceilings=ceilings,
            contract_id=contract_id,
            charge_date=date(2026, 1, 15),
        )
        assert result.is_valid

    def test_ceiling_with_loaded_rate(self):
        contract_id = uuid4()
        schedule = (_schedule(loaded_rate="150.00"),)
        ceilings = (
            _ceiling(
                contract_id=contract_id,
                max_hourly="130.00",
                max_loaded="120.00",
            ),
        )
        result = verify_labor_rate(
            employee_id=uuid4(),
            employee_classification="Senior Engineer",
            labor_category="ENG-03",
            charged_rate=Decimal("125.00"),
            rate_schedule=schedule,
            contract_ceilings=ceilings,
            contract_id=contract_id,
            charge_date=date(2026, 1, 15),
        )
        assert not result.is_valid
        assert result.ceiling_rate == Decimal("120.00")


# ===========================================================================
# Indirect Rate Reconciliation
# ===========================================================================


class TestComputeReconciliation:
    """Provisional-to-final rate reconciliation."""

    def test_underapplied(self):
        record = compute_rate_reconciliation(
            reconciliation_id=uuid4(),
            fiscal_year=2025,
            rate_type=IndirectRateType.FRINGE,
            provisional_rate=Decimal("0.30"),
            final_rate=Decimal("0.35"),
            base_amount=Decimal("1000000"),
        )
        assert record.direction == ReconciliationDirection.UNDERAPPLIED
        assert record.rate_difference == Decimal("0.05")
        assert record.adjustment_amount == Decimal("50000")

    def test_overapplied(self):
        record = compute_rate_reconciliation(
            reconciliation_id=uuid4(),
            fiscal_year=2025,
            rate_type=IndirectRateType.OVERHEAD,
            provisional_rate=Decimal("0.40"),
            final_rate=Decimal("0.35"),
            base_amount=Decimal("1000000"),
        )
        assert record.direction == ReconciliationDirection.OVERAPPLIED
        assert record.rate_difference == Decimal("-0.05")
        assert record.adjustment_amount == Decimal("-50000")

    def test_exact_match(self):
        record = compute_rate_reconciliation(
            reconciliation_id=uuid4(),
            fiscal_year=2025,
            rate_type=IndirectRateType.G_AND_A,
            provisional_rate=Decimal("0.10"),
            final_rate=Decimal("0.10"),
            base_amount=Decimal("1000000"),
        )
        assert record.direction == ReconciliationDirection.EXACT
        assert record.adjustment_amount == Decimal("0")


class TestComputeAllReconciliations:
    """Batch reconciliation for all rate types in a fiscal year."""

    def _prov(self, rate_type: IndirectRateType, value: str) -> IndirectRateRecord:
        return IndirectRateRecord(
            rate_id=uuid4(),
            rate_type=rate_type,
            rate_value=Decimal(value),
            base_description="direct_labor_dollars",
            fiscal_year=2025,
            rate_status=RateSource.PROVISIONAL,
            effective_from=date(2025, 1, 1),
        )

    def _final(self, rate_type: IndirectRateType, value: str) -> IndirectRateRecord:
        return IndirectRateRecord(
            rate_id=uuid4(),
            rate_type=rate_type,
            rate_value=Decimal(value),
            base_description="direct_labor_dollars",
            fiscal_year=2025,
            rate_status=RateSource.FINAL,
            effective_from=date(2025, 1, 1),
        )

    def test_multiple_rate_types(self):
        provisional = (
            self._prov(IndirectRateType.FRINGE, "0.30"),
            self._prov(IndirectRateType.OVERHEAD, "0.40"),
        )
        final = (
            self._final(IndirectRateType.FRINGE, "0.35"),
            self._final(IndirectRateType.OVERHEAD, "0.38"),
        )
        base_amounts = {
            IndirectRateType.FRINGE: Decimal("1000000"),
            IndirectRateType.OVERHEAD: Decimal("1000000"),
        }

        results = compute_all_reconciliations(
            fiscal_year=2025,
            provisional_rates=provisional,
            final_rates=final,
            base_amounts=base_amounts,
            reconciliation_id_factory=uuid4,
        )

        assert len(results) == 2
        fringe = next(r for r in results if r.rate_type == IndirectRateType.FRINGE)
        overhead = next(r for r in results if r.rate_type == IndirectRateType.OVERHEAD)

        assert fringe.direction == ReconciliationDirection.UNDERAPPLIED
        assert overhead.direction == ReconciliationDirection.OVERAPPLIED

    def test_skips_missing_final(self):
        provisional = (self._prov(IndirectRateType.FRINGE, "0.30"),)
        final = ()  # no final rates
        results = compute_all_reconciliations(
            fiscal_year=2025,
            provisional_rates=provisional,
            final_rates=final,
            base_amounts={IndirectRateType.FRINGE: Decimal("1000000")},
            reconciliation_id_factory=uuid4,
        )
        assert len(results) == 0

    def test_skips_zero_base(self):
        provisional = (self._prov(IndirectRateType.FRINGE, "0.30"),)
        final = (self._final(IndirectRateType.FRINGE, "0.35"),)
        results = compute_all_reconciliations(
            fiscal_year=2025,
            provisional_rates=provisional,
            final_rates=final,
            base_amounts={IndirectRateType.FRINGE: Decimal("0")},
            reconciliation_id_factory=uuid4,
        )
        assert len(results) == 0

    def test_wrong_fiscal_year_skipped(self):
        provisional = (self._prov(IndirectRateType.FRINGE, "0.30"),)
        final = (self._final(IndirectRateType.FRINGE, "0.35"),)
        results = compute_all_reconciliations(
            fiscal_year=2024,  # different year
            provisional_rates=provisional,
            final_rates=final,
            base_amounts={IndirectRateType.FRINGE: Decimal("1000000")},
            reconciliation_id_factory=uuid4,
        )
        assert len(results) == 0
