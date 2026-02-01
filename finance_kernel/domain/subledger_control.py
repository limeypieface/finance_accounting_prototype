"""SubledgerControl -- Subledger/GL reconciliation invariant enforcement."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger

logger = get_logger("domain.subledger_control")


class SubledgerType(str, Enum):
    """Canonical subledger type identifiers (SL-Phase 1)."""

    AP = "AP"  # Accounts Payable
    AR = "AR"  # Accounts Receivable
    INVENTORY = "INVENTORY"  # Inventory subledger
    FIXED_ASSETS = "FIXED_ASSETS"  # Fixed asset register
    BANK = "BANK"  # Bank transactions
    PAYROLL = "PAYROLL"  # Payroll liabilities
    WIP = "WIP"  # Work in progress
    INTERCOMPANY = "INTERCOMPANY"  # Intercompany transactions


class ReconciliationTiming(str, Enum):
    """When reconciliation must occur."""

    REAL_TIME = "real_time"  # Every posting must balance
    DAILY = "daily"  # End of day reconciliation
    PERIOD_END = "period_end"  # Only at period close


class ToleranceType(str, Enum):
    """How tolerance is calculated."""

    ABSOLUTE = "absolute"  # Fixed amount (e.g., $0.01)
    PERCENTAGE = "percentage"  # Percentage of balance
    NONE = "none"  # Zero tolerance


@dataclass(frozen=True, slots=True)
class ControlAccountBinding:
    """Binds a subledger type to its GL control account."""

    subledger_type: SubledgerType
    control_account_role: str  # Role code in COA (e.g., "AP_CONTROL")
    control_account_code: str  # Actual account code (e.g., "2100")
    is_debit_normal: bool  # True if debit increases balance
    currency: str  # Primary currency for this subledger

    def expected_sign(self) -> int:
        """Get expected sign for positive balance."""
        return 1 if self.is_debit_normal else -1


@dataclass(frozen=True, slots=True)
class ReconciliationTolerance:
    """Tolerance rules for reconciliation variance."""

    tolerance_type: ToleranceType
    absolute_amount: Decimal = Decimal("0")  # For ABSOLUTE
    percentage: Decimal = Decimal("0")  # For PERCENTAGE
    max_absolute_cap: Decimal | None = None  # Cap for PERCENTAGE

    @classmethod
    def zero(cls) -> ReconciliationTolerance:
        """No tolerance - must be exact."""
        return cls(tolerance_type=ToleranceType.NONE)

    @classmethod
    def pennies(cls, amount: Decimal = Decimal("0.01")) -> ReconciliationTolerance:
        """Allow small rounding tolerance."""
        return cls(tolerance_type=ToleranceType.ABSOLUTE, absolute_amount=amount)

    @classmethod
    def percent(
        cls,
        pct: Decimal,
        max_cap: Decimal | None = None,
    ) -> ReconciliationTolerance:
        """Percentage-based tolerance."""
        return cls(
            tolerance_type=ToleranceType.PERCENTAGE,
            percentage=pct,
            max_absolute_cap=max_cap,
        )

    def is_within_tolerance(
        self,
        variance: Decimal,
        balance: Decimal,
    ) -> bool:
        """Check if variance is within tolerance."""
        abs_variance = abs(variance)

        if self.tolerance_type == ToleranceType.NONE:
            return abs_variance == Decimal("0")

        if self.tolerance_type == ToleranceType.ABSOLUTE:
            return abs_variance <= self.absolute_amount

        if self.tolerance_type == ToleranceType.PERCENTAGE:
            threshold = abs(balance) * self.percentage / Decimal("100")
            if self.max_absolute_cap is not None:
                threshold = min(threshold, self.max_absolute_cap)
            return abs_variance <= threshold

        return False


@dataclass(frozen=True, slots=True)
class SubledgerControlContract:
    """Complete control contract for a subledger."""

    binding: ControlAccountBinding
    timing: ReconciliationTiming
    tolerance: ReconciliationTolerance
    enforce_on_post: bool = True  # Check balance after every post
    enforce_on_close: bool = True  # Must reconcile to close period
    auto_create_adjustments: bool = False  # Auto-create adjustment entries

    @property
    def subledger_type(self) -> SubledgerType:
        return self.binding.subledger_type

    @property
    def control_account_role(self) -> str:
        return self.binding.control_account_role


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Result of a reconciliation check."""

    subledger_type: SubledgerType
    as_of_date: date
    subledger_balance: Money
    control_account_balance: Money
    variance: Money
    is_reconciled: bool
    is_within_tolerance: bool
    tolerance_used: ReconciliationTolerance
    checked_at: datetime
    entries_checked: int = 0

    @property
    def variance_amount(self) -> Decimal:
        return self.variance.amount

    @property
    def is_balanced(self) -> bool:
        """True if perfectly balanced (no variance)."""
        return self.variance.is_zero


@dataclass(frozen=True, slots=True)
class ReconciliationViolation:
    """A violation of the subledger control contract."""

    subledger_type: SubledgerType
    contract: SubledgerControlContract
    result: ReconciliationResult
    violation_type: str  # "out_of_balance", "exceeds_tolerance", "missing_entries"
    message: str
    severity: str = "error"  # "error" or "warning"
    blocking: bool = True  # If True, prevents close/post


class SubledgerControlRegistry:
    """Registry of all subledger control contracts."""

    def __init__(self) -> None:
        self._contracts: dict[SubledgerType, SubledgerControlContract] = {}

    def register(self, contract: SubledgerControlContract) -> None:
        """Register a subledger control contract."""
        self._contracts[contract.subledger_type] = contract
        logger.info(
            "subledger_contract_registered",
            extra={
                "subledger_type": contract.subledger_type.value,
                "control_account_role": contract.control_account_role,
                "timing": contract.timing.value,
                "enforce_on_post": contract.enforce_on_post,
                "enforce_on_close": contract.enforce_on_close,
            },
        )

    def get(self, subledger_type: SubledgerType) -> SubledgerControlContract | None:
        """Get contract for a subledger type."""
        return self._contracts.get(subledger_type)

    def get_all(self) -> Sequence[SubledgerControlContract]:
        """Get all registered contracts."""
        return tuple(self._contracts.values())

    def get_by_control_account(
        self, control_account_role: str
    ) -> SubledgerControlContract | None:
        """Get contract by control account role."""
        for contract in self._contracts.values():
            if contract.control_account_role == control_account_role:
                return contract
        return None


class SubledgerReconciler:
    """Pure function reconciler for subledger/GL balances."""

    def reconcile(
        self,
        contract: SubledgerControlContract,
        subledger_balance: Money,
        control_account_balance: Money,
        as_of_date: date,
        checked_at: datetime,
        entries_checked: int = 0,
    ) -> ReconciliationResult:
        """Reconcile subledger balance with GL control account."""
        # Ensure same currency
        if subledger_balance.currency != control_account_balance.currency:
            raise ValueError(
                f"Currency mismatch: subledger={subledger_balance.currency}, "
                f"control={control_account_balance.currency}"
            )

        # Calculate variance
        # Convention: positive variance = subledger > control
        variance = subledger_balance - control_account_balance

        # Check tolerance
        is_within = contract.tolerance.is_within_tolerance(
            variance.amount,
            control_account_balance.amount,
        )

        result = ReconciliationResult(
            subledger_type=contract.subledger_type,
            as_of_date=as_of_date,
            subledger_balance=subledger_balance,
            control_account_balance=control_account_balance,
            variance=variance,
            is_reconciled=variance.is_zero,
            is_within_tolerance=is_within,
            tolerance_used=contract.tolerance,
            checked_at=checked_at,
            entries_checked=entries_checked,
        )

        if result.is_reconciled:
            logger.info(
                "subledger_validated",
                extra={
                    "subledger_type": contract.subledger_type.value,
                    "as_of_date": str(as_of_date),
                    "status": "reconciled",
                    "entries_checked": entries_checked,
                },
            )
        elif is_within:
            logger.info(
                "subledger_validated",
                extra={
                    "subledger_type": contract.subledger_type.value,
                    "as_of_date": str(as_of_date),
                    "status": "within_tolerance",
                    "variance": str(variance.amount),
                    "entries_checked": entries_checked,
                },
            )
        else:
            logger.warning(
                "subledger_violation",
                extra={
                    "subledger_type": contract.subledger_type.value,
                    "as_of_date": str(as_of_date),
                    "status": "out_of_balance",
                    "variance": str(variance.amount),
                    "subledger_balance": str(subledger_balance.amount),
                    "control_balance": str(control_account_balance.amount),
                    "entries_checked": entries_checked,
                },
            )

        return result

    def validate_post(
        self,
        contract: SubledgerControlContract,
        subledger_balance_before: Money,
        subledger_balance_after: Money,
        control_balance_before: Money,
        control_balance_after: Money,
        as_of_date: date,
        checked_at: datetime | None = None,
    ) -> list[ReconciliationViolation]:
        """Validate that a posting maintains the control contract."""
        violations: list[ReconciliationViolation] = []

        if not contract.enforce_on_post:
            return violations

        # Check balance after posting
        _checked_at = checked_at if checked_at is not None else datetime.min
        result = self.reconcile(
            contract=contract,
            subledger_balance=subledger_balance_after,
            control_account_balance=control_balance_after,
            as_of_date=as_of_date,
            checked_at=_checked_at,
        )

        if not result.is_within_tolerance:
            logger.warning(
                "subledger_post_violation",
                extra={
                    "subledger_type": contract.subledger_type.value,
                    "as_of_date": str(as_of_date),
                    "variance": str(result.variance.amount),
                    "violation_type": "out_of_balance",
                },
            )
            violations.append(
                ReconciliationViolation(
                    subledger_type=contract.subledger_type,
                    contract=contract,
                    result=result,
                    violation_type="out_of_balance",
                    message=(
                        f"Posting would cause {contract.subledger_type.value} "
                        f"to be out of balance with control account. "
                        f"Variance: {result.variance}"
                    ),
                    severity="error",
                    blocking=True,
                )
            )

        return violations

    def validate_period_close(
        self,
        contract: SubledgerControlContract,
        subledger_balance: Money,
        control_account_balance: Money,
        period_end_date: date,
        checked_at: datetime | None = None,
    ) -> list[ReconciliationViolation]:
        """Validate that subledger can close period."""
        violations: list[ReconciliationViolation] = []

        if not contract.enforce_on_close:
            return violations

        _checked_at = checked_at if checked_at is not None else datetime.min
        result = self.reconcile(
            contract=contract,
            subledger_balance=subledger_balance,
            control_account_balance=control_account_balance,
            as_of_date=period_end_date,
            checked_at=_checked_at,
        )

        if not result.is_reconciled and not result.is_within_tolerance:
            logger.warning(
                "subledger_period_close_blocked",
                extra={
                    "subledger_type": contract.subledger_type.value,
                    "period_end_date": str(period_end_date),
                    "variance": str(result.variance.amount),
                    "violation_type": "period_close_blocked",
                },
            )
            violations.append(
                ReconciliationViolation(
                    subledger_type=contract.subledger_type,
                    contract=contract,
                    result=result,
                    violation_type="period_close_blocked",
                    message=(
                        f"Cannot close period: {contract.subledger_type.value} "
                        f"is not reconciled with control account. "
                        f"Variance: {result.variance}"
                    ),
                    severity="error",
                    blocking=True,
                )
            )
        elif not result.is_reconciled and result.is_within_tolerance:
            logger.info(
                "subledger_period_close_tolerance_warning",
                extra={
                    "subledger_type": contract.subledger_type.value,
                    "period_end_date": str(period_end_date),
                    "variance": str(result.variance.amount),
                },
            )
            # Warn but don't block
            violations.append(
                ReconciliationViolation(
                    subledger_type=contract.subledger_type,
                    contract=contract,
                    result=result,
                    violation_type="tolerance_warning",
                    message=(
                        f"Warning: {contract.subledger_type.value} has variance "
                        f"of {result.variance} (within tolerance)"
                    ),
                    severity="warning",
                    blocking=False,
                )
            )

        return violations


# =============================================================================
# Standard Contract Definitions
# =============================================================================


def create_ap_control_contract() -> SubledgerControlContract:
    """Create standard AP control contract."""
    return SubledgerControlContract(
        binding=ControlAccountBinding(
            subledger_type=SubledgerType.AP,
            control_account_role="AP_CONTROL",
            control_account_code="2100",
            is_debit_normal=False,  # Credit normal (liability)
            currency="USD",
        ),
        timing=ReconciliationTiming.REAL_TIME,
        tolerance=ReconciliationTolerance.pennies(Decimal("0.01")),
        enforce_on_post=True,
        enforce_on_close=True,
    )


def create_ar_control_contract() -> SubledgerControlContract:
    """Create standard AR control contract."""
    return SubledgerControlContract(
        binding=ControlAccountBinding(
            subledger_type=SubledgerType.AR,
            control_account_role="AR_CONTROL",
            control_account_code="1200",
            is_debit_normal=True,  # Debit normal (asset)
            currency="USD",
        ),
        timing=ReconciliationTiming.REAL_TIME,
        tolerance=ReconciliationTolerance.pennies(Decimal("0.01")),
        enforce_on_post=True,
        enforce_on_close=True,
    )


def create_inventory_control_contract() -> SubledgerControlContract:
    """Create standard Inventory control contract."""
    return SubledgerControlContract(
        binding=ControlAccountBinding(
            subledger_type=SubledgerType.INVENTORY,
            control_account_role="INVENTORY_CONTROL",
            control_account_code="1400",
            is_debit_normal=True,  # Debit normal (asset)
            currency="USD",
        ),
        timing=ReconciliationTiming.PERIOD_END,  # Inventory often reconciled at period end
        tolerance=ReconciliationTolerance.pennies(Decimal("0.05")),  # Slightly higher tolerance
        enforce_on_post=False,  # Don't block individual postings
        enforce_on_close=True,
    )


def create_bank_control_contract() -> SubledgerControlContract:
    """Create standard Bank control contract."""
    return SubledgerControlContract(
        binding=ControlAccountBinding(
            subledger_type=SubledgerType.BANK,
            control_account_role="CASH_CONTROL",
            control_account_code="1000",
            is_debit_normal=True,  # Debit normal (asset)
            currency="USD",
        ),
        timing=ReconciliationTiming.DAILY,
        tolerance=ReconciliationTolerance.zero(),  # Must match exactly
        enforce_on_post=True,
        enforce_on_close=True,
    )


def create_default_control_registry() -> SubledgerControlRegistry:
    """Create a registry with standard control contracts."""
    registry = SubledgerControlRegistry()
    registry.register(create_ap_control_contract())
    registry.register(create_ar_control_contract())
    registry.register(create_inventory_control_contract())
    registry.register(create_bank_control_contract())
    return registry
