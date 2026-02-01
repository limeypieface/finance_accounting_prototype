"""PolicyCompiler -- Validates profiles before registration."""

from dataclasses import dataclass
from datetime import date

from finance_kernel.domain.accounting_policy import AccountingPolicy
from finance_kernel.domain.dtos import ValidationError, ValidationResult
from finance_kernel.domain.event_validator import validate_field_references
from finance_kernel.domain.ledger_registry import LedgerRegistry
from finance_kernel.domain.policy_selector import PolicySelector
from finance_kernel.domain.schemas.registry import EventSchemaRegistry
from finance_kernel.logging_config import get_logger

logger = get_logger("domain.policy_compiler")


@dataclass(frozen=True)
class CompilationResult:
    """Result of profile compilation."""

    success: bool
    errors: tuple[ValidationError, ...]
    warnings: tuple[ValidationError, ...]

    @classmethod
    def ok(cls, warnings: tuple[ValidationError, ...] = ()) -> "CompilationResult":
        """Create successful result."""
        return cls(success=True, errors=(), warnings=warnings)

    @classmethod
    def fail(cls, *errors: ValidationError) -> "CompilationResult":
        """Create failed result."""
        return cls(success=False, errors=errors, warnings=())


class PolicyCompiler:
    """Compiles and validates AccountingPolicy objects (P1, P7, P10)."""

    def __init__(
        self,
        check_overlaps: bool = True,
        check_schema: bool = True,
        check_ledger: bool = True,
    ):
        self.check_overlaps = check_overlaps
        self.check_schema = check_schema
        self.check_ledger = check_ledger

    def compile(self, profile: AccountingPolicy) -> CompilationResult:
        """Compile and validate a profile."""
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []

        logger.debug(
            "compilation_started",
            extra={
                "profile": profile.name,
                "profile_version": profile.version,
                "event_type": profile.trigger.event_type,
                "checks": {
                    "overlaps": self.check_overlaps,
                    "schema": self.check_schema,
                    "ledger": self.check_ledger,
                },
            },
        )

        # Basic structure validation
        structure_errors = self._validate_structure(profile)
        errors.extend(structure_errors)

        # INVARIANT: P1 -- overlap detection (exactly one profile per event)
        if self.check_overlaps:
            overlap_errors = self._validate_no_overlaps(profile)
            errors.extend(overlap_errors)

        # INVARIANT: P10 -- field references validated against event schema
        if self.check_schema:
            schema_result = self._validate_field_references(profile)
            errors.extend(schema_result.errors)
            warnings.extend(schema_result.warnings)

        # INVARIANT: P7 -- ledger semantic completeness (required roles provided)
        if self.check_ledger:
            ledger_errors = self._validate_ledger_requirements(profile)
            errors.extend(ledger_errors)

        if errors:
            logger.error(
                "compilation_failed",
                extra={
                    "profile": profile.name,
                    "profile_version": profile.version,
                    "error_count": len(errors),
                    "error_codes": [e.code for e in errors],
                    "warning_count": len(warnings),
                },
            )
            return CompilationResult.fail(*errors)

        logger.info(
            "profile_compiled",
            extra={
                "profile": profile.name,
                "profile_version": profile.version,
                "event_type": profile.trigger.event_type,
                "warning_count": len(warnings),
            },
        )
        return CompilationResult.ok(tuple(warnings))

    def _validate_structure(self, profile: AccountingPolicy) -> list[ValidationError]:
        """Validate basic profile structure."""
        errors: list[ValidationError] = []

        # Check effective date range
        if profile.effective_to is not None:
            if profile.effective_to < profile.effective_from:
                errors.append(
                    ValidationError(
                        code="INVALID_EFFECTIVE_RANGE",
                        message="effective_to must be >= effective_from",
                        field="effective_to",
                    )
                )

        # Check ledger effects
        if not profile.ledger_effects:
            errors.append(
                ValidationError(
                    code="NO_LEDGER_EFFECTS",
                    message="Profile must have at least one ledger effect",
                    field="ledger_effects",
                )
            )

        return errors

    def _validate_no_overlaps(self, profile: AccountingPolicy) -> list[ValidationError]:
        """P1: Validate that adding this profile won't create ambiguous matches."""
        errors: list[ValidationError] = []

        # Get existing profiles for same event type
        existing = PolicySelector.list_by_event_type(profile.trigger.event_type)

        for other in existing:
            # Skip if same profile (different version update)
            if other.name == profile.name:
                continue

            # Check for overlap
            if self._profiles_overlap(profile, other):
                # Check if precedence can resolve
                if not self._precedence_resolves(profile, other):
                    logger.warning(
                        "profile_overlap_detected",
                        extra={
                            "profile": profile.name,
                            "conflicting_profile": other.name,
                            "event_type": profile.trigger.event_type,
                        },
                    )
                    errors.append(
                        ValidationError(
                            code="PROFILE_OVERLAP",
                            message=(
                                f"Profile '{profile.name}' overlaps with '{other.name}' "
                                f"and cannot be resolved by precedence rules"
                            ),
                            field="trigger",
                            details={
                                "conflicting_profile": other.name,
                                "event_type": profile.trigger.event_type,
                            },
                        )
                    )

        return errors

    def _profiles_overlap(
        self, p1: AccountingPolicy, p2: AccountingPolicy
    ) -> bool:
        """Check if two profiles could match the same event."""
        # Different event types don't overlap
        if p1.trigger.event_type != p2.trigger.event_type:
            return False

        # Check effective date overlap
        if not self._date_ranges_overlap(
            p1.effective_from, p1.effective_to,
            p2.effective_from, p2.effective_to,
        ):
            return False

        # Check scope overlap
        if not self._scopes_overlap(p1.scope, p2.scope):
            return False

        return True

    def _date_ranges_overlap(
        self,
        start1: date, end1: date | None,
        start2: date, end2: date | None,
    ) -> bool:
        """Check if two date ranges overlap."""
        # Handle open-ended ranges
        effective_end1 = end1 or date.max
        effective_end2 = end2 or date.max

        # Ranges overlap if start1 <= end2 and start2 <= end1
        return start1 <= effective_end2 and start2 <= effective_end1

    def _scopes_overlap(self, scope1: str, scope2: str) -> bool:
        """Check if two scopes could match the same value."""
        # Wildcard matches everything
        if scope1 == "*" or scope2 == "*":
            return True

        # Both are prefix wildcards
        if scope1.endswith(":*") and scope2.endswith(":*"):
            prefix1 = scope1[:-1]
            prefix2 = scope2[:-1]
            # Overlap if one is prefix of other
            return prefix1.startswith(prefix2) or prefix2.startswith(prefix1)

        # One is prefix, one is exact
        if scope1.endswith(":*"):
            return scope2.startswith(scope1[:-1])
        if scope2.endswith(":*"):
            return scope1.startswith(scope2[:-1])

        # Both exact - overlap only if equal
        return scope1 == scope2

    def _precedence_resolves(
        self, p1: AccountingPolicy, p2: AccountingPolicy
    ) -> bool:
        """Check if precedence rules can resolve overlap."""
        # Explicit override resolves
        if p1.name in p2.precedence.overrides or p2.name in p1.precedence.overrides:
            return True

        # Different scope specificity resolves
        spec1 = self._scope_specificity(p1.scope)
        spec2 = self._scope_specificity(p2.scope)
        if spec1 != spec2:
            return True

        # Different priority resolves
        if p1.precedence.priority != p2.precedence.priority:
            return True

        # Override mode vs normal resolves
        if p1.precedence.mode != p2.precedence.mode:
            return True

        return False

    def _scope_specificity(self, scope: str) -> int:
        """Calculate scope specificity."""
        if scope == "*":
            return 0
        if scope.endswith(":*"):
            return len(scope) - 1
        return len(scope) + 100

    def _validate_field_references(
        self, profile: AccountingPolicy
    ) -> CompilationResult:
        """P10: Validate field references against event schema."""
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []

        event_type = profile.trigger.event_type
        schema_version = profile.trigger.schema_version

        # Check if schema is registered
        if not EventSchemaRegistry.has_schema(event_type, schema_version):
            # Warning, not error - schema might not be registered yet
            warnings.append(
                ValidationError(
                    code="SCHEMA_NOT_REGISTERED",
                    message=(
                        f"No schema registered for {event_type} v{schema_version}. "
                        "Field references cannot be validated."
                    ),
                    field="trigger.event_type",
                )
            )
            return CompilationResult(success=True, errors=(), warnings=tuple(warnings))

        # Get schema and validate field references
        schema = EventSchemaRegistry.get(event_type, schema_version)
        field_refs = profile.get_field_references()

        ref_errors = validate_field_references(field_refs, schema)
        for err in ref_errors:
            errors.append(
                ValidationError(
                    code="INVALID_FIELD_REFERENCE",
                    message=err.message,
                    field=err.field,
                    details={"profile": profile.name, "event_type": event_type},
                )
            )

        return CompilationResult(
            success=len(errors) == 0,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def _validate_ledger_requirements(
        self, profile: AccountingPolicy
    ) -> list[ValidationError]:
        """P7: Validate ledger semantic completeness."""
        errors: list[ValidationError] = []
        economic_type = profile.meaning.economic_type

        for effect in profile.ledger_effects:
            ledger_id = effect.ledger

            # Check if ledger is registered
            if not LedgerRegistry.has_ledger(ledger_id):
                # Warning - ledger might be valid but not registered
                continue

            # Get required roles for this economic type
            required_roles = LedgerRegistry.get_required_roles(ledger_id, economic_type)

            if not required_roles:
                # No requirements defined for this economic type
                continue

            # Check if profile provides required roles
            provided_roles = {effect.debit_role, effect.credit_role}
            missing_roles = set(required_roles) - provided_roles

            if missing_roles:
                errors.append(
                    ValidationError(
                        code="MISSING_REQUIRED_ROLES",
                        message=(
                            f"Profile '{profile.name}' is missing required roles "
                            f"for {economic_type} on {ledger_id}: {sorted(missing_roles)}"
                        ),
                        field="ledger_effects",
                        details={
                            "ledger": ledger_id,
                            "economic_type": economic_type,
                            "missing_roles": list(missing_roles),
                            "required_roles": list(required_roles),
                        },
                    )
                )

        return errors

    def compile_and_register(self, profile: AccountingPolicy) -> CompilationResult:
        """Compile and register a profile if validation passes."""
        result = self.compile(profile)
        if result.success:
            PolicySelector.register(profile)
            logger.info(
                "profile_compiled_and_registered",
                extra={
                    "profile": profile.name,
                    "profile_version": profile.version,
                    "event_type": profile.trigger.event_type,
                },
            )
        return result
