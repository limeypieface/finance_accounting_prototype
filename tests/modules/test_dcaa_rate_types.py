"""
Tests for DCAA Rate Control Domain Types.

Validates frozen dataclass invariants:
- LaborRateSchedule: non-negative rates, loaded >= base
- ContractRateCeiling: non-negative rates
- RateVerificationResult: value correctness
- IndirectRateRecord: non-negative rate
- RateReconciliationRecord: derived field computation
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

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


class TestLaborRateSchedule:
    """LaborRateSchedule frozen dataclass invariants."""

    def test_valid_schedule(self):
        s = LaborRateSchedule(
            schedule_id=uuid4(),
            employee_classification="Senior Engineer",
            labor_category="ENG-03",
            base_rate=Decimal("75.00"),
            loaded_rate=Decimal("125.00"),
            effective_from=date(2025, 1, 1),
        )
        assert s.base_rate == Decimal("75.00")

    def test_negative_base_rate_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            LaborRateSchedule(
                schedule_id=uuid4(),
                employee_classification="SE",
                labor_category="ENG",
                base_rate=Decimal("-10"),
                loaded_rate=Decimal("100"),
                effective_from=date(2025, 1, 1),
            )

    def test_negative_loaded_rate_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            LaborRateSchedule(
                schedule_id=uuid4(),
                employee_classification="SE",
                labor_category="ENG",
                base_rate=Decimal("50"),
                loaded_rate=Decimal("-10"),
                effective_from=date(2025, 1, 1),
            )

    def test_loaded_less_than_base_rejected(self):
        with pytest.raises(ValueError, match="less than"):
            LaborRateSchedule(
                schedule_id=uuid4(),
                employee_classification="SE",
                labor_category="ENG",
                base_rate=Decimal("100"),
                loaded_rate=Decimal("80"),
                effective_from=date(2025, 1, 1),
            )

    def test_is_effective_within_range(self):
        s = LaborRateSchedule(
            schedule_id=uuid4(),
            employee_classification="SE",
            labor_category="ENG",
            base_rate=Decimal("75"),
            loaded_rate=Decimal("125"),
            effective_from=date(2025, 1, 1),
            effective_to=date(2025, 12, 31),
        )
        assert s.is_effective(date(2025, 6, 15))
        assert not s.is_effective(date(2026, 1, 1))
        assert not s.is_effective(date(2024, 12, 31))

    def test_is_effective_no_end_date(self):
        s = LaborRateSchedule(
            schedule_id=uuid4(),
            employee_classification="SE",
            labor_category="ENG",
            base_rate=Decimal("75"),
            loaded_rate=Decimal("125"),
            effective_from=date(2025, 1, 1),
        )
        assert s.is_effective(date(2030, 1, 1))


class TestContractRateCeiling:
    """ContractRateCeiling frozen dataclass invariants."""

    def test_valid_ceiling(self):
        c = ContractRateCeiling(
            contract_id=uuid4(),
            labor_category="ENG-03",
            max_hourly_rate=Decimal("130.00"),
        )
        assert c.max_hourly_rate == Decimal("130.00")

    def test_negative_max_hourly_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            ContractRateCeiling(
                contract_id=uuid4(),
                labor_category="ENG",
                max_hourly_rate=Decimal("-10"),
            )

    def test_negative_max_loaded_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            ContractRateCeiling(
                contract_id=uuid4(),
                labor_category="ENG",
                max_hourly_rate=Decimal("100"),
                max_loaded_rate=Decimal("-10"),
            )

    def test_is_effective(self):
        c = ContractRateCeiling(
            contract_id=uuid4(),
            labor_category="ENG",
            max_hourly_rate=Decimal("130"),
            effective_from=date(2025, 1, 1),
            effective_to=date(2025, 12, 31),
        )
        assert c.is_effective(date(2025, 6, 15))
        assert not c.is_effective(date(2026, 1, 1))


class TestRateVerificationResult:
    """RateVerificationResult value correctness."""

    def test_valid_result(self):
        r = RateVerificationResult(
            is_valid=True,
            employee_id=uuid4(),
            charged_rate=Decimal("120"),
            approved_rate=Decimal("125"),
        )
        assert r.is_valid
        assert r.excess_amount == Decimal("0")

    def test_violation_result(self):
        r = RateVerificationResult(
            is_valid=False,
            employee_id=uuid4(),
            charged_rate=Decimal("150"),
            approved_rate=Decimal("125"),
            excess_amount=Decimal("25"),
            violation_type=RateViolationType.EXCEEDS_CLASSIFICATION,
            message="Rate exceeds approved",
        )
        assert not r.is_valid
        assert r.violation_type == RateViolationType.EXCEEDS_CLASSIFICATION

    def test_all_violation_types(self):
        for vt in RateViolationType:
            r = RateVerificationResult(
                is_valid=False,
                employee_id=uuid4(),
                charged_rate=Decimal("100"),
                approved_rate=Decimal("90"),
                violation_type=vt,
            )
            assert r.violation_type == vt


class TestIndirectRateRecord:
    """IndirectRateRecord frozen dataclass invariants."""

    def test_valid_record(self):
        r = IndirectRateRecord(
            rate_id=uuid4(),
            rate_type=IndirectRateType.FRINGE,
            rate_value=Decimal("0.35"),
            base_description="direct_labor_dollars",
            fiscal_year=2025,
        )
        assert r.rate_value == Decimal("0.35")

    def test_negative_rate_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            IndirectRateRecord(
                rate_id=uuid4(),
                rate_type=IndirectRateType.FRINGE,
                rate_value=Decimal("-0.10"),
                base_description="direct_labor_dollars",
                fiscal_year=2025,
            )

    def test_all_rate_types(self):
        for rt in IndirectRateType:
            r = IndirectRateRecord(
                rate_id=uuid4(),
                rate_type=rt,
                rate_value=Decimal("0.25"),
                base_description="test",
                fiscal_year=2025,
            )
            assert r.rate_type == rt

    def test_all_rate_sources(self):
        for rs in RateSource:
            r = IndirectRateRecord(
                rate_id=uuid4(),
                rate_type=IndirectRateType.OVERHEAD,
                rate_value=Decimal("0.40"),
                base_description="test",
                fiscal_year=2025,
                rate_status=rs,
            )
            assert r.rate_status == rs


class TestRateReconciliationRecord:
    """RateReconciliationRecord derived field computation."""

    def test_underapplied_direction(self):
        r = RateReconciliationRecord(
            reconciliation_id=uuid4(),
            fiscal_year=2025,
            rate_type=IndirectRateType.FRINGE,
            provisional_rate=Decimal("0.30"),
            final_rate=Decimal("0.35"),
            base_amount=Decimal("1000000"),
        )
        assert r.direction == ReconciliationDirection.UNDERAPPLIED
        assert r.rate_difference == Decimal("0.05")
        assert r.adjustment_amount == Decimal("50000")

    def test_overapplied_direction(self):
        r = RateReconciliationRecord(
            reconciliation_id=uuid4(),
            fiscal_year=2025,
            rate_type=IndirectRateType.OVERHEAD,
            provisional_rate=Decimal("0.40"),
            final_rate=Decimal("0.35"),
            base_amount=Decimal("1000000"),
        )
        assert r.direction == ReconciliationDirection.OVERAPPLIED
        assert r.rate_difference == Decimal("-0.05")
        assert r.adjustment_amount == Decimal("-50000")

    def test_exact_direction(self):
        r = RateReconciliationRecord(
            reconciliation_id=uuid4(),
            fiscal_year=2025,
            rate_type=IndirectRateType.G_AND_A,
            provisional_rate=Decimal("0.10"),
            final_rate=Decimal("0.10"),
            base_amount=Decimal("1000000"),
        )
        assert r.direction == ReconciliationDirection.EXACT
        assert r.adjustment_amount == Decimal("0")

    def test_zero_base_no_adjustment(self):
        r = RateReconciliationRecord(
            reconciliation_id=uuid4(),
            fiscal_year=2025,
            rate_type=IndirectRateType.FRINGE,
            provisional_rate=Decimal("0.30"),
            final_rate=Decimal("0.35"),
            base_amount=Decimal("0"),
        )
        assert r.adjustment_amount == Decimal("0")
        # Direction is still UNDERAPPLIED since final > provisional
        assert r.direction == ReconciliationDirection.UNDERAPPLIED
