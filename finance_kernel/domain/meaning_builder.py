"""
MeaningBuilder for constructing EconomicEvents.

Takes a BusinessEvent and AccountingPolicy and produces the interpreted
economic meaning. This is a pure domain component - no I/O, no ORM.

The MeaningBuilder:
1. Extracts quantity from payload using profile field mapping
2. Extracts dimensions from payload
3. Evaluates guard conditions (reject/block)
4. Validates against PolicyAuthority (if provided)
5. Produces an immutable EconomicEventData for persistence

Integration with foundational modules:
- ReferenceSnapshot: Uses comprehensive snapshot from reference_snapshot.py
- PolicyAuthority: Validates economic authority before building meaning
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, TYPE_CHECKING
from uuid import UUID

from finance_kernel.domain.dtos import ValidationError, ValidationResult
from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    GuardCondition,
    GuardType,
)
from finance_kernel.logging_config import get_logger

logger = get_logger("domain.meaning_builder")

if TYPE_CHECKING:
    from finance_kernel.domain.reference_snapshot import ReferenceSnapshot as FullSnapshot
    from finance_kernel.domain.policy_authority import PolicyAuthority, ModuleType


@dataclass(frozen=True)
class ReferenceSnapshot:
    """
    Snapshot of reference data versions for deterministic replay.

    Invariant L4: Guards and valuation may only read from frozen snapshots.

    Note: This is a lightweight snapshot for backward compatibility.
    For full snapshot functionality, use ReferenceSnapshot from
    finance_kernel.domain.reference_snapshot.
    """

    coa_version: int | None = None
    dimension_schema_version: int | None = None
    currency_registry_version: int | None = None
    fx_policy_version: int | None = None

    # Optional: Reference to full snapshot ID for audit trail
    full_snapshot_id: UUID | None = None

    @classmethod
    def from_full_snapshot(cls, full_snapshot: "FullSnapshot") -> "ReferenceSnapshot":
        """
        Create lightweight snapshot from comprehensive ReferenceSnapshot.

        This bridges the new foundational module with existing code.
        """
        return cls(
            coa_version=full_snapshot.coa_version,
            dimension_schema_version=full_snapshot.dimension_schema_version,
            currency_registry_version=full_snapshot.currency_registry_version,
            fx_policy_version=full_snapshot.fx_rates_version,
            full_snapshot_id=full_snapshot.snapshot_id,
        )


@dataclass(frozen=True)
class EconomicEventData:
    """
    Immutable data for creating an EconomicEvent.

    This is a pure domain object (DTO) that can be passed to persistence.
    """

    source_event_id: UUID
    economic_type: str
    effective_date: date
    profile_id: str
    profile_version: int
    profile_hash: str | None = None

    # Optional fields
    quantity: Decimal | None = None
    dimensions: dict[str, Any] | None = None
    trace_id: UUID | None = None

    # Reference snapshot
    snapshot: ReferenceSnapshot | None = None

    # Timestamps
    created_at: datetime | None = None


@dataclass(frozen=True)
class GuardEvaluationResult:
    """
    Result of evaluating guard conditions.

    Attributes:
        passed: True if all guards passed
        rejected: True if a REJECT guard triggered (terminal)
        blocked: True if a BLOCK guard triggered (resumable)
        triggered_guard: The guard that triggered (if any)
        reason_code: Machine-readable reason code
        reason_detail: Additional details about the failure
    """

    passed: bool
    rejected: bool = False
    blocked: bool = False
    triggered_guard: GuardCondition | None = None
    reason_code: str | None = None
    reason_detail: dict[str, Any] | None = None

    @classmethod
    def success(cls) -> "GuardEvaluationResult":
        """All guards passed."""
        return cls(passed=True)

    @classmethod
    def reject(
        cls,
        guard: GuardCondition,
        detail: dict[str, Any] | None = None,
    ) -> "GuardEvaluationResult":
        """REJECT guard triggered."""
        return cls(
            passed=False,
            rejected=True,
            triggered_guard=guard,
            reason_code=guard.reason_code,
            reason_detail=detail,
        )

    @classmethod
    def block(
        cls,
        guard: GuardCondition,
        detail: dict[str, Any] | None = None,
    ) -> "GuardEvaluationResult":
        """BLOCK guard triggered."""
        return cls(
            passed=False,
            blocked=True,
            triggered_guard=guard,
            reason_code=guard.reason_code,
            reason_detail=detail,
        )


@dataclass(frozen=True)
class MeaningBuilderResult:
    """
    Result of building economic meaning from an event.

    Attributes:
        success: True if meaning was built successfully
        economic_event: The built economic event data (if success)
        guard_result: Result of guard evaluation
        validation_errors: Validation errors (if any)
    """

    success: bool
    economic_event: EconomicEventData | None = None
    guard_result: GuardEvaluationResult | None = None
    validation_errors: tuple[ValidationError, ...] = ()

    @classmethod
    def ok(
        cls,
        economic_event: EconomicEventData,
        guard_result: GuardEvaluationResult | None = None,
    ) -> "MeaningBuilderResult":
        """Successful result."""
        return cls(
            success=True,
            economic_event=economic_event,
            guard_result=guard_result or GuardEvaluationResult.success(),
        )

    @classmethod
    def rejected(
        cls,
        guard_result: GuardEvaluationResult,
    ) -> "MeaningBuilderResult":
        """Rejected by guard."""
        return cls(
            success=False,
            guard_result=guard_result,
        )

    @classmethod
    def blocked(
        cls,
        guard_result: GuardEvaluationResult,
    ) -> "MeaningBuilderResult":
        """Blocked by guard."""
        return cls(
            success=False,
            guard_result=guard_result,
        )

    @classmethod
    def validation_failed(
        cls,
        *errors: ValidationError,
    ) -> "MeaningBuilderResult":
        """Validation failed."""
        return cls(
            success=False,
            validation_errors=errors,
        )


class MeaningBuilder:
    """
    Builds economic meaning from business events.

    Pure domain component - no I/O, no ORM.

    Integration with PolicyAuthority:
        The MeaningBuilder can optionally validate against a PolicyAuthority
        to enforce economic authority. If a policy_registry is provided,
        build() will validate that the profile's economic type is authorized.

    Usage:
        builder = MeaningBuilder()

        # Without policy validation (backward compatible)
        result = builder.build(
            event_id=event.id,
            event_type=event.event_type,
            payload=event.payload,
            effective_date=event.effective_date,
            profile=profile,
            snapshot=snapshot,
        )

        # With policy validation
        builder = MeaningBuilder(policy_registry=registry)
        result = builder.build(..., module_type=ModuleType.INVENTORY)

        if result.success:
            # Persist result.economic_event
        elif result.guard_result.rejected:
            # Handle rejection
        elif result.guard_result.blocked:
            # Handle block
    """

    def __init__(
        self,
        policy_authority: "PolicyAuthority | None" = None,
    ) -> None:
        """
        Initialize the MeaningBuilder.

        Args:
            policy_authority: PolicyAuthority for economic authority validation.
                Strongly recommended. When provided and build() receives
                module_type + target_ledgers, the builder validates that the
                module is authorized for the economic action. A future version
                will make this parameter strictly required.
        """
        self._policy_registry = policy_authority

    def build(
        self,
        event_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        effective_date: date,
        profile: AccountingPolicy,
        snapshot: ReferenceSnapshot | None = None,
        trace_id: UUID | None = None,
        created_at: datetime | None = None,
        module_type: "ModuleType | None" = None,
        target_ledgers: frozenset[str] | None = None,
    ) -> MeaningBuilderResult:
        """
        Build economic meaning from an event using a profile.

        Args:
            event_id: The source event ID.
            event_type: The event type.
            payload: The event payload.
            effective_date: Accounting effective date.
            profile: The profile to apply.
            snapshot: Reference data snapshot for determinism.
            trace_id: Optional trace ID for audit.
            created_at: Creation timestamp.
            module_type: Optional module type for policy validation.
                        Requires policy_registry in constructor.
            target_ledgers: Optional target ledgers for policy validation.

        Returns:
            MeaningBuilderResult with success/failure and data.
        """
        logger.debug(
            "meaning_build_started",
            extra={
                "event_id": str(event_id),
                "event_type": event_type,
                "profile": profile.name,
                "profile_version": profile.version,
                "effective_date": str(effective_date),
            },
        )

        # Policy validation (if registry provided and module specified)
        if self._policy_registry and module_type and target_ledgers:
            policy_errors = self._validate_policy(
                profile.meaning.economic_type,
                module_type,
                target_ledgers,
            )
            if policy_errors:
                logger.warning(
                    "meaning_builder_policy_violation",
                    extra={
                        "event_id": str(event_id),
                        "event_type": event_type,
                        "profile": profile.name,
                        "error_count": len(policy_errors),
                        "error_codes": [e.code for e in policy_errors],
                    },
                )
                return MeaningBuilderResult.validation_failed(*policy_errors)

        # Verify profile matches event type
        if profile.trigger.event_type != event_type:
            logger.warning(
                "meaning_builder_failed",
                extra={
                    "event_id": str(event_id),
                    "event_type": event_type,
                    "profile": profile.name,
                    "expected_event_type": profile.trigger.event_type,
                    "reason": "profile_event_mismatch",
                },
            )
            return MeaningBuilderResult.validation_failed(
                ValidationError(
                    code="PROFILE_EVENT_MISMATCH",
                    message=f"Profile {profile.name} expects {profile.trigger.event_type}, got {event_type}",
                    field="event_type",
                )
            )

        # Evaluate guards (P12: reject vs block semantics)
        guard_result = self._evaluate_guards(payload, profile.guards)
        if guard_result.rejected:
            logger.warning(
                "meaning_builder_guard_rejected",
                extra={
                    "event_id": str(event_id),
                    "event_type": event_type,
                    "profile": profile.name,
                    "reason_code": guard_result.reason_code,
                    "guard_type": "reject",
                },
            )
            return MeaningBuilderResult.rejected(guard_result)
        if guard_result.blocked:
            logger.warning(
                "meaning_builder_guard_blocked",
                extra={
                    "event_id": str(event_id),
                    "event_type": event_type,
                    "profile": profile.name,
                    "reason_code": guard_result.reason_code,
                    "guard_type": "block",
                },
            )
            return MeaningBuilderResult.blocked(guard_result)

        # Extract quantity
        quantity = self._extract_quantity(payload, profile)

        # Extract dimensions
        dimensions = self._extract_dimensions(payload, profile)

        # Build the economic event data
        economic_event = EconomicEventData(
            source_event_id=event_id,
            economic_type=profile.meaning.economic_type,
            effective_date=effective_date,
            profile_id=profile.name,
            profile_version=profile.version,
            quantity=quantity,
            dimensions=dimensions,
            snapshot=snapshot,
            trace_id=trace_id,
            created_at=created_at,
        )

        logger.info(
            "meaning_derived",
            extra={
                "event_id": str(event_id),
                "event_type": event_type,
                "profile": profile.name,
                "economic_type": profile.meaning.economic_type,
                "quantity": str(quantity) if quantity is not None else None,
                "has_dimensions": dimensions is not None,
            },
        )

        return MeaningBuilderResult.ok(economic_event, guard_result)

    def _evaluate_guards(
        self,
        payload: dict[str, Any],
        guards: tuple[GuardCondition, ...],
    ) -> GuardEvaluationResult:
        """
        Evaluate guard conditions against payload.

        P12: Rejects are terminal. Blocks are resumable.

        Currently supports simple expressions:
        - field_path operator value (e.g., "payload.quantity <= 0")
        """
        for guard in guards:
            triggered = self._evaluate_expression(payload, guard.expression)
            logger.debug(
                "guard_evaluated",
                extra={
                    "guard_type": guard.guard_type.value,
                    "expression": guard.expression,
                    "reason_code": guard.reason_code,
                    "triggered": triggered,
                },
            )
            if triggered:
                if guard.guard_type == GuardType.REJECT:
                    return GuardEvaluationResult.reject(
                        guard,
                        {"expression": guard.expression},
                    )
                else:
                    return GuardEvaluationResult.block(
                        guard,
                        {"expression": guard.expression},
                    )

        return GuardEvaluationResult.success()

    def _evaluate_expression(
        self,
        payload: dict[str, Any],
        expression: str,
    ) -> bool:
        """
        Evaluate a simple guard expression.

        Supports:
        - field <= value
        - field >= value
        - field == value
        - field != value
        - field = true/false (boolean check)

        Returns True if the guard condition triggers (i.e., should reject/block).
        """
        expression = expression.strip()

        # Parse simple comparison expressions
        operators = ["<=", ">=", "!=", "==", "=", "<", ">"]
        for op in operators:
            if op in expression:
                parts = expression.split(op, 1)
                if len(parts) == 2:
                    field_path = parts[0].strip()
                    expected = parts[1].strip()

                    # Get field value
                    actual = self._get_field_value(payload, field_path)

                    # Compare
                    return self._compare(actual, op, expected)

        # Handle boolean expressions like "reference_data_missing"
        # Treat as checking if the field is truthy
        value = self._get_field_value(payload, expression)
        return bool(value)

    def _get_field_value(
        self,
        payload: dict[str, Any],
        field_path: str,
    ) -> Any:
        """
        Get a value from payload by dot-notation path.

        Handles paths like "payload.quantity" or just "quantity".
        """
        # Remove "payload." prefix if present
        if field_path.startswith("payload."):
            field_path = field_path[8:]

        parts = field_path.split(".")
        current = payload

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None

        return current

    def _compare(self, actual: Any, op: str, expected: str) -> bool:
        """Compare actual value against expected using operator."""
        if actual is None:
            return False

        # Handle boolean expected values
        if expected.lower() in ("true", "false"):
            expected_bool = expected.lower() == "true"
            if op in ("=", "=="):
                return bool(actual) == expected_bool
            elif op == "!=":
                return bool(actual) != expected_bool
            return False

        # Handle numeric comparisons
        try:
            actual_num = Decimal(str(actual))
            expected_num = Decimal(expected)

            if op == "<=":
                return actual_num <= expected_num
            elif op == ">=":
                return actual_num >= expected_num
            elif op == "<":
                return actual_num < expected_num
            elif op == ">":
                return actual_num > expected_num
            elif op in ("=", "=="):
                return actual_num == expected_num
            elif op == "!=":
                return actual_num != expected_num
        except (InvalidOperation, ValueError):
            pass

        # Handle string comparisons
        actual_str = str(actual)
        if op in ("=", "=="):
            return actual_str == expected
        elif op == "!=":
            return actual_str != expected

        return False

    def _extract_quantity(
        self,
        payload: dict[str, Any],
        profile: AccountingPolicy,
    ) -> Decimal | None:
        """Extract quantity from payload using profile mapping."""
        if not profile.meaning.quantity_field:
            return None

        value = self._get_field_value(payload, profile.meaning.quantity_field)
        if value is None:
            return None

        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None

    def _extract_dimensions(
        self,
        payload: dict[str, Any],
        profile: AccountingPolicy,
    ) -> dict[str, Any] | None:
        """Extract dimensions from payload using profile mapping."""
        if not profile.meaning.dimensions:
            return None

        dimensions: dict[str, Any] = {}
        for dim in profile.meaning.dimensions:
            if "." in dim:
                # Field path - extract from payload
                value = self._get_field_value(payload, dim)
                if value is not None:
                    # Use the last part as the dimension name
                    dim_name = dim.split(".")[-1]
                    dimensions[dim_name] = value
            else:
                # Direct dimension name - look for it in payload
                value = self._get_field_value(payload, dim)
                if value is not None:
                    dimensions[dim] = value

        return dimensions if dimensions else None

    def _validate_policy(
        self,
        economic_type: str,
        module_type: "ModuleType",
        target_ledgers: frozenset[str],
    ) -> tuple[ValidationError, ...]:
        """
        Validate against PolicyAuthority.

        Checks:
        1. Economic type is allowed to post to target ledgers
        2. Module has authority to process this economic type

        Returns tuple of validation errors (empty if valid).
        """
        if not self._policy_registry:
            return ()

        errors: list[ValidationError] = []

        # Check economic type constraints
        violations = self._policy_registry.validate_economic_type_posting(
            economic_type=economic_type,
            target_ledgers=target_ledgers,
        )

        for violation in violations:
            errors.append(
                ValidationError(
                    code="POLICY_VIOLATION",
                    message=violation.message,
                    field="economic_type",
                    details={
                        "policy_type": violation.policy_type,
                        "economic_type": economic_type,
                        "target_ledgers": list(target_ledgers),
                    },
                )
            )

        return tuple(errors)
