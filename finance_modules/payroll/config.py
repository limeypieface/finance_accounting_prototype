"""
Payroll Configuration Schema.

Defines the structure and sensible defaults for payroll settings.
Actual values are loaded from company configuration at runtime.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger
from finance_modules.payroll.profiles import AccountRole

logger = get_logger("modules.payroll.config")

VALID_PAY_FREQUENCIES = {"weekly", "biweekly", "semimonthly", "monthly"}
VALID_WORK_DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
VALID_DEPOSIT_SCHEDULES = {"monthly", "semiweekly", "next_day"}
VALID_OVERTIME_PERIODS = {"daily", "weekly"}
VALID_LABOR_DISTRIBUTION_METHODS = {"timecard", "percentage"}


@dataclass
class OvertimeRule:
    """Overtime calculation rule."""
    threshold_hours: Decimal
    multiplier: Decimal
    period: str  # "daily", "weekly"

    def __post_init__(self):
        if self.threshold_hours <= 0:
            raise ValueError("threshold_hours must be positive")
        if self.multiplier <= 0:
            raise ValueError("multiplier must be positive")
        if self.period not in VALID_OVERTIME_PERIODS:
            raise ValueError(
                f"period must be one of {VALID_OVERTIME_PERIODS}, got '{self.period}'"
            )
        logger.debug(
            "overtime_rule_initialized",
            extra={
                "threshold_hours": str(self.threshold_hours),
                "multiplier": str(self.multiplier),
                "period": self.period,
            },
        )


@dataclass
class PayrollConfig:
    """
    Configuration schema for payroll module.

    Field defaults represent common US practices.
    Override at instantiation with company-specific values:

        config = PayrollConfig(
            california_overtime=True,
            default_pay_frequency="weekly",
            **load_from_database("payroll_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Pay frequency
    default_pay_frequency: str = "biweekly"
    salaried_frequency: str = "semimonthly"

    # Work week
    standard_hours_per_week: Decimal = Decimal("40")
    work_week_start: str = "monday"

    # Overtime
    overtime_rules: tuple[OvertimeRule, ...] = field(default_factory=tuple)
    california_overtime: bool = False

    # Tax deposits
    federal_deposit_schedule: str = "semiweekly"  # "monthly", "semiweekly"
    state_deposit_schedule: str = "monthly"

    # Direct deposit
    require_direct_deposit: bool = False
    direct_deposit_prenote_days: int = 10

    # Time entry
    require_timecard_approval: bool = True
    allow_negative_time: bool = False
    round_time_to_minutes: int = 15

    # Labor distribution
    distribute_labor_to_wip: bool = True
    labor_distribution_method: str = "timecard"  # "timecard", "percentage"

    # Accrual
    accrue_vacation: bool = True
    accrue_sick: bool = True
    vacation_accrual_rate: Decimal = Decimal("0.0385")
    sick_accrual_rate: Decimal = Decimal("0.0231")

    # Compliance
    w2_generation: bool = True
    aca_tracking: bool = True
    new_hire_reporting: bool = True

    def __post_init__(self):
        # Validate pay frequencies
        if self.default_pay_frequency not in VALID_PAY_FREQUENCIES:
            raise ValueError(
                f"default_pay_frequency must be one of {VALID_PAY_FREQUENCIES}, "
                f"got '{self.default_pay_frequency}'"
            )
        if self.salaried_frequency not in VALID_PAY_FREQUENCIES:
            raise ValueError(
                f"salaried_frequency must be one of {VALID_PAY_FREQUENCIES}, "
                f"got '{self.salaried_frequency}'"
            )

        # Validate work week
        if self.standard_hours_per_week <= 0:
            raise ValueError("standard_hours_per_week must be positive")
        if self.work_week_start not in VALID_WORK_DAYS:
            raise ValueError(
                f"work_week_start must be one of {VALID_WORK_DAYS}, "
                f"got '{self.work_week_start}'"
            )

        # Validate deposit schedules
        if self.federal_deposit_schedule not in VALID_DEPOSIT_SCHEDULES:
            raise ValueError(
                f"federal_deposit_schedule must be one of {VALID_DEPOSIT_SCHEDULES}, "
                f"got '{self.federal_deposit_schedule}'"
            )
        if self.state_deposit_schedule not in VALID_DEPOSIT_SCHEDULES:
            raise ValueError(
                f"state_deposit_schedule must be one of {VALID_DEPOSIT_SCHEDULES}, "
                f"got '{self.state_deposit_schedule}'"
            )

        # Validate direct deposit settings
        if self.direct_deposit_prenote_days < 0:
            raise ValueError("direct_deposit_prenote_days cannot be negative")

        # Validate time rounding
        if self.round_time_to_minutes <= 0:
            raise ValueError("round_time_to_minutes must be positive")
        if self.round_time_to_minutes > 60:
            raise ValueError("round_time_to_minutes cannot exceed 60")

        # Validate labor distribution method
        if self.labor_distribution_method not in VALID_LABOR_DISTRIBUTION_METHODS:
            raise ValueError(
                f"labor_distribution_method must be one of {VALID_LABOR_DISTRIBUTION_METHODS}, "
                f"got '{self.labor_distribution_method}'"
            )

        # Validate accrual rates
        if self.vacation_accrual_rate < 0:
            raise ValueError("vacation_accrual_rate cannot be negative")
        if self.vacation_accrual_rate > 1:
            raise ValueError("vacation_accrual_rate cannot exceed 1 (100%)")
        if self.sick_accrual_rate < 0:
            raise ValueError("sick_accrual_rate cannot be negative")
        if self.sick_accrual_rate > 1:
            raise ValueError("sick_accrual_rate cannot exceed 1 (100%)")

        # Validate overtime rules are sorted by threshold
        if self.overtime_rules:
            thresholds = [rule.threshold_hours for rule in self.overtime_rules]
            if thresholds != sorted(thresholds):
                raise ValueError("overtime_rules must be sorted by threshold_hours ascending")

        logger.info(
            "payroll_config_initialized",
            extra={
                "default_pay_frequency": self.default_pay_frequency,
                "standard_hours_per_week": str(self.standard_hours_per_week),
                "work_week_start": self.work_week_start,
                "california_overtime": self.california_overtime,
                "federal_deposit_schedule": self.federal_deposit_schedule,
                "labor_distribution_method": self.labor_distribution_method,
                "distribute_labor_to_wip": self.distribute_labor_to_wip,
                "overtime_rules_count": len(self.overtime_rules),
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with industry-standard defaults."""
        logger.info("payroll_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file)."""
        logger.info(
            "payroll_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        if "overtime_rules" in data:
            data["overtime_rules"] = tuple(
                OvertimeRule(**rule) if isinstance(rule, dict) else rule
                for rule in data["overtime_rules"]
            )
        return cls(**data)
