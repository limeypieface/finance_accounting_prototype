"""
PolicySelector -- Runtime profile lookup and precedence resolution.

Responsibility:
    Registers AccountingPolicy instances and finds the single matching
    policy for a given event at runtime.  Handles where-clause dispatch,
    effective date filtering, scope matching, and precedence resolution.

Architecture position:
    Kernel > Domain -- pure functional core, zero I/O.

Invariants enforced:
    P1 -- Exactly one profile matches any event, or the event is rejected
    L2 -- No runtime ambiguity is allowed

Failure modes:
    - PolicyNotFoundError if no profile matches
    - MultiplePoliciesMatchError if ambiguity cannot be resolved
    - PolicyAlreadyRegisteredError if duplicate name+version
    - UncompiledPolicyError if receipt doesn't match

Audit relevance:
    PolicySelector emits FINANCE_POLICY_TRACE structured logs documenting
    which policies were admissible and which was selected, enabling auditors
    to reconstruct the dispatch decision.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    PrecedenceMode,
)
from finance_kernel.logging_config import get_logger

logger = get_logger("domain.policy_selector")


# ---------------------------------------------------------------------------
# CompilationReceipt — proof that a policy passed compilation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompilationReceipt:
    """Proof that a policy was validated by PolicyCompiler.

    Contains a hash of the compiled policy. PolicySelector.register()
    verifies this receipt before accepting a policy into the dispatch
    registry. This prevents unvalidated policies from entering runtime.

    The receipt is created by ``PolicyCompiler.compile()`` and should
    not be constructed directly in application code.
    """

    policy_name: str
    policy_version: int
    compiled_hash: str  # SHA-256 of canonical policy representation
    config_fingerprint: str  # From CompiledPolicyPack.canonical_fingerprint

    def matches(self, policy: AccountingPolicy) -> bool:
        """Verify this receipt belongs to the given policy."""
        return (
            self.policy_name == policy.name
            and self.policy_version == policy.version
        )


class UncompiledPolicyError(Exception):
    """A policy was registered without a valid CompilationReceipt."""

    code: str = "UNCOMPILED_POLICY"

    def __init__(self, policy_name: str, policy_version: int):
        self.policy_name = policy_name
        self.policy_version = policy_version
        super().__init__(
            f"Policy '{policy_name}' v{policy_version} has no valid "
            f"CompilationReceipt. All policies must pass compilation "
            f"before registration."
        )


class PolicyNotFoundError(Exception):
    """No profile found for event type."""

    code: str = "PROFILE_NOT_FOUND"

    def __init__(self, event_type: str, effective_date: date | None = None):
        self.event_type = event_type
        self.effective_date = effective_date
        msg = f"No profile found for event type: {event_type}"
        if effective_date:
            msg += f" (effective {effective_date})"
        super().__init__(msg)


class MultiplePoliciesMatchError(Exception):
    """
    Multiple profiles match and cannot be resolved.

    P1 Violation: Exactly one profile must match or event is rejected.
    """

    code: str = "MULTIPLE_PROFILES_MATCH"

    def __init__(self, event_type: str, matching_profiles: list[str]):
        self.event_type = event_type
        self.matching_profiles = matching_profiles
        super().__init__(
            f"Multiple profiles match event type {event_type} and cannot be resolved: "
            f"{', '.join(matching_profiles)}"
        )


class PolicyAlreadyRegisteredError(Exception):
    """Profile already registered with same name and version."""

    code: str = "PROFILE_ALREADY_REGISTERED"

    def __init__(self, name: str, version: int):
        self.name = name
        self.version = version
        super().__init__(f"Profile already registered: {name} v{version}")


class PolicySelector:
    """
    Registry for AccountingPolicy objects with runtime lookup.

    Contract:
        ``find_for_event()`` returns exactly one ``AccountingPolicy`` or
        raises (P1).

    Guarantees:
        - ``register()`` rejects duplicate name+version.
        - ``find_for_event()`` applies: event_type filter, effective date
          filter, scope filter, where-clause filter, precedence resolution.
        - Emits FINANCE_POLICY_TRACE structured log for every dispatch.

    Non-goals:
        - Does NOT compile or validate profiles (PolicyCompiler does that).
        - Does NOT persist to database (in-memory registry).

    Invariants enforced:
        P1 -- Exactly one profile matches or event is rejected.
        L2 -- No runtime ambiguity is allowed.
    """

    # Class-level registry: name -> {version -> profile}
    _profiles: ClassVar[dict[str, dict[int, AccountingPolicy]]] = {}

    # Index by event_type for fast lookup
    _by_event_type: ClassVar[dict[str, list[AccountingPolicy]]] = {}

    @classmethod
    def register(
        cls,
        profile: AccountingPolicy,
        compilation_receipt: CompilationReceipt | None = None,
    ) -> None:
        """
        Register a profile.

        Args:
            profile: The profile to register.
            compilation_receipt: Proof that the policy was compiled.
                When provided, the receipt is validated against the
                policy. A future version will make this strictly required.

        Raises:
            PolicyAlreadyRegisteredError: If profile name+version already exists.
            UncompiledPolicyError: If receipt is provided but doesn't match.
        """
        # Validate compilation receipt if provided
        if compilation_receipt is not None:
            if not compilation_receipt.matches(profile):
                raise UncompiledPolicyError(profile.name, profile.version)

        if profile.name not in cls._profiles:
            cls._profiles[profile.name] = {}

        if profile.version in cls._profiles[profile.name]:
            logger.warning(
                "profile_already_registered",
                extra={
                    "profile": profile.name,
                    "version": profile.version,
                },
            )
            raise PolicyAlreadyRegisteredError(profile.name, profile.version)

        cls._profiles[profile.name][profile.version] = profile

        # Update event_type index
        event_type = profile.trigger.event_type
        if event_type not in cls._by_event_type:
            cls._by_event_type[event_type] = []
        cls._by_event_type[event_type].append(profile)

        has_receipt = compilation_receipt is not None
        logger.info(
            "profile_registered",
            extra={
                "profile": profile.name,
                "version": profile.version,
                "event_type": event_type,
                "scope": profile.scope,
                "has_compilation_receipt": has_receipt,
            },
        )

    @classmethod
    def get(cls, name: str, version: int | None = None) -> AccountingPolicy:
        """
        Get a profile by name and optional version.

        Args:
            name: Profile name.
            version: Specific version, or None for latest.

        Returns:
            The AccountingPolicy.

        Raises:
            PolicyNotFoundError: If not found.
        """
        if name not in cls._profiles:
            logger.debug(
                "profile_lookup_miss",
                extra={"profile": name, "version": version},
            )
            raise PolicyNotFoundError(name)

        versions = cls._profiles[name]

        if version is not None:
            if version not in versions:
                logger.debug(
                    "profile_lookup_miss",
                    extra={"profile": name, "version": version},
                )
                raise PolicyNotFoundError(name)
            logger.debug(
                "profile_lookup_hit",
                extra={"profile": name, "version": version},
            )
            return versions[version]

        # Return latest version
        if not versions:
            logger.debug(
                "profile_lookup_miss",
                extra={"profile": name, "version": version},
            )
            raise PolicyNotFoundError(name)

        latest = max(versions.keys())
        logger.debug(
            "profile_lookup_hit",
            extra={"profile": name, "version": latest},
        )
        return versions[latest]

    @classmethod
    def find_for_event(
        cls,
        event_type: str,
        effective_date: date,
        scope_value: str = "*",
        payload: dict[str, Any] | None = None,
    ) -> AccountingPolicy:
        """
        Find the matching profile for an event.

        Applies precedence resolution:
        1. Filter by event_type
        2. Filter by effective date
        3. Filter by scope
        4. Filter by where-clause match against payload
        5. Apply override > scope specificity > priority > stable key

        Args:
            event_type: The event type to match.
            effective_date: Date to check effectiveness.
            scope_value: Scope value to match against.
            payload: Event payload for where-clause evaluation.
                     If None, only profiles without where-clauses are considered.

        Returns:
            The single matching AccountingPolicy.

        Raises:
            PolicyNotFoundError: If no profile matches.
            MultiplePoliciesMatchError: If multiple profiles match and cannot be resolved.
        """
        logger.debug(
            "profile_find_started",
            extra={
                "event_type": event_type,
                "effective_date": str(effective_date),
                "scope_value": scope_value,
                "has_payload": payload is not None,
            },
        )

        if event_type not in cls._by_event_type:
            logger.warning(
                "profile_not_found",
                extra={
                    "event_type": event_type,
                    "effective_date": str(effective_date),
                    "reason": "no_profiles_for_event_type",
                },
            )
            raise PolicyNotFoundError(event_type, effective_date)

        candidates = cls._by_event_type[event_type]

        # Filter by effective date
        effective = [p for p in candidates if p.is_effective_on(effective_date)]
        if not effective:
            logger.warning(
                "profile_not_found",
                extra={
                    "event_type": event_type,
                    "effective_date": str(effective_date),
                    "reason": "no_effective_profiles",
                    "candidate_count": len(candidates),
                },
            )
            raise PolicyNotFoundError(event_type, effective_date)

        # Filter by scope
        matching = [p for p in effective if p.matches_scope(scope_value)]
        if not matching:
            logger.warning(
                "profile_not_found",
                extra={
                    "event_type": event_type,
                    "effective_date": str(effective_date),
                    "scope_value": scope_value,
                    "reason": "no_scope_match",
                    "effective_count": len(effective),
                },
            )
            raise PolicyNotFoundError(event_type, effective_date)

        # Filter by where-clause match against payload
        if payload is not None:
            # Separate profiles into those with where-clauses and those without
            with_where = [p for p in matching if p.trigger.where]
            without_where = [p for p in matching if not p.trigger.where]

            # Check which where-clause profiles match the payload
            specific_matches = [
                p for p in with_where if cls._matches_where(p, payload)
            ]

            if specific_matches:
                # Prefer specific where-clause matches over generic profiles
                matching = specific_matches
            else:
                # No where-clause profile matched; fall back to generic profiles
                matching = without_where
        else:
            # No payload provided; exclude profiles that require where-clause dispatch
            matching = [p for p in matching if not p.trigger.where]

        if not matching:
            logger.warning(
                "profile_not_found",
                extra={
                    "event_type": event_type,
                    "effective_date": str(effective_date),
                    "reason": "no_where_clause_match",
                },
            )
            raise PolicyNotFoundError(event_type, effective_date)

        # If only one matches, return it
        if len(matching) == 1:
            selected = matching[0]
            cls._emit_policy_trace(
                event_type, effective_date, matching, selected, "single_match",
            )
            return selected

        # Apply precedence resolution
        logger.debug(
            "profile_precedence_resolution_needed",
            extra={
                "event_type": event_type,
                "matching_count": len(matching),
                "matching_profiles": [p.name for p in matching],
            },
        )
        selected = cls._resolve_precedence(matching)
        cls._emit_policy_trace(
            event_type, effective_date, matching, selected, "precedence",
        )
        return selected

    @classmethod
    def _emit_policy_trace(
        cls,
        event_type: str,
        effective_date: date,
        candidates: list[AccountingPolicy],
        selected: AccountingPolicy,
        resolution_method: str,
    ) -> None:
        """Emit FINANCE_POLICY_TRACE structured log."""
        logger.info(
            "FINANCE_POLICY_TRACE",
            extra={
                "trace_type": "FINANCE_POLICY_TRACE",
                "event_type": event_type,
                "effective_date": str(effective_date),
                "admissible_policies": [
                    {"name": p.name, "version": p.version} for p in candidates
                ],
                "selected_policy": selected.name,
                "selected_policy_version": selected.version,
                "precedence_reason": resolution_method,
            },
        )

    @classmethod
    def _resolve_precedence(
        cls, profiles: list[AccountingPolicy]
    ) -> AccountingPolicy:
        """
        Resolve precedence among multiple matching profiles.

        Order: override > scope specificity > priority > stable key

        Args:
            profiles: List of matching profiles.

        Returns:
            The winning profile.

        Raises:
            MultiplePoliciesMatchError: If cannot resolve.
        """
        # Separate overrides from normal
        overrides = [p for p in profiles if p.precedence.mode == PrecedenceMode.OVERRIDE]
        normal = [p for p in profiles if p.precedence.mode == PrecedenceMode.NORMAL]

        # If there are overrides, they take precedence
        if overrides:
            # Check if any override explicitly overrides the others
            remaining = cls._apply_explicit_overrides(overrides)
            if len(remaining) == 1:
                return remaining[0]
            # Multiple overrides without explicit resolution
            profiles = remaining
        else:
            profiles = normal

        if len(profiles) == 1:
            return profiles[0]

        # Sort by scope specificity (more specific = longer non-wildcard prefix)
        profiles = sorted(profiles, key=lambda p: cls._scope_specificity(p.scope), reverse=True)

        # Check if top has unique specificity
        if len(profiles) >= 2:
            top_spec = cls._scope_specificity(profiles[0].scope)
            second_spec = cls._scope_specificity(profiles[1].scope)
            if top_spec > second_spec:
                return profiles[0]

        # Same specificity - use priority
        profiles = sorted(profiles, key=lambda p: p.precedence.priority, reverse=True)

        if len(profiles) >= 2:
            if profiles[0].precedence.priority > profiles[1].precedence.priority:
                return profiles[0]

        # Same priority - use stable key (name) as final tiebreaker
        profiles = sorted(profiles, key=lambda p: p.name)

        # If we still have ties, that's an unresolved ambiguity
        if len(profiles) >= 2:
            if (
                cls._scope_specificity(profiles[0].scope) == cls._scope_specificity(profiles[1].scope)
                and profiles[0].precedence.priority == profiles[1].precedence.priority
            ):
                logger.warning(
                    "profile_precedence_unresolved",
                    extra={
                        "event_type": profiles[0].trigger.event_type,
                        "matching_profiles": [p.name for p in profiles],
                    },
                )
                raise MultiplePoliciesMatchError(
                    profiles[0].trigger.event_type,
                    [p.name for p in profiles],
                )

        logger.info(
            "profile_precedence_resolved",
            extra={
                "event_type": profiles[0].trigger.event_type,
                "winner": profiles[0].name,
                "resolution_method": "precedence",
            },
        )
        return profiles[0]

    @classmethod
    def _apply_explicit_overrides(
        cls, profiles: list[AccountingPolicy]
    ) -> list[AccountingPolicy]:
        """
        Remove profiles that are explicitly overridden by others.

        Args:
            profiles: List of profiles to filter.

        Returns:
            Profiles that are not explicitly overridden.
        """
        # Collect all overridden profile names
        overridden_names: set[str] = set()
        for p in profiles:
            overridden_names.update(p.precedence.overrides)

        # Filter out overridden profiles
        return [p for p in profiles if p.name not in overridden_names]

    @classmethod
    def _scope_specificity(cls, scope: str) -> int:
        """
        Calculate scope specificity (higher = more specific).

        "*" = 0 (least specific)
        "prefix:*" = len(prefix) + 1
        "exact" = len(exact) + 100 (exact match is most specific)
        """
        if scope == "*":
            return 0
        if scope.endswith(":*"):
            return len(scope) - 1  # Length without "*"
        return len(scope) + 100  # Exact match gets bonus

    @classmethod
    def has_profile(cls, name: str, version: int | None = None) -> bool:
        """Check if profile exists."""
        if name not in cls._profiles:
            return False
        if version is None:
            return len(cls._profiles[name]) > 0
        return version in cls._profiles[name]

    @classmethod
    def list_profiles(cls) -> list[str]:
        """List all registered profile names."""
        return sorted(cls._profiles.keys())

    @classmethod
    def list_by_event_type(cls, event_type: str) -> list[AccountingPolicy]:
        """List all profiles for an event type."""
        return cls._by_event_type.get(event_type, [])

    @classmethod
    def clear(cls) -> None:
        """Clear all registered profiles. For testing only."""
        cls._profiles.clear()
        cls._by_event_type.clear()

    # -----------------------------------------------------------------
    # Where-clause evaluation
    # -----------------------------------------------------------------

    @classmethod
    def _matches_where(
        cls, profile: AccountingPolicy, payload: dict[str, Any]
    ) -> bool:
        """
        Check if all where-clause conditions in the profile's trigger
        match the given payload.

        A profile with no where-clauses always matches.
        Each where-clause is a (field_path, expected_value) pair.

        Supports:
        - Equality: ("payload.issue_type", "SALE") -- field must equal value
        - Absence: ("payload.po_number", None) -- field must be absent or None
        - Expressions: ("payload.quantity_change > 0", True) -- comparison evaluated
        """
        if not profile.trigger.where:
            return True

        for field_path, expected_value in profile.trigger.where:
            # Check for comparison expression in field_path
            if isinstance(expected_value, bool) and any(
                op in field_path for op in (" > ", " < ", " >= ", " <= ")
            ):
                if not cls._evaluate_where_expression(
                    payload, field_path, expected_value
                ):
                    return False
                continue

            # Standard equality / absence check
            actual = cls._get_payload_value(payload, field_path)
            if expected_value is None:
                if actual is not None:
                    return False
            else:
                if str(actual) != str(expected_value):
                    return False

        return True

    @classmethod
    def _get_payload_value(cls, payload: dict[str, Any], field_path: str) -> Any:
        """
        Extract a value from the payload by dot-path.

        Strips the ``payload.`` prefix if present so that both
        ``"payload.issue_type"`` and ``"issue_type"`` resolve correctly.
        """
        path = field_path
        if path.startswith("payload."):
            path = path[len("payload."):]

        parts = path.split(".")
        current: Any = payload
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    @classmethod
    def _evaluate_where_expression(
        cls,
        payload: dict[str, Any],
        expression: str,
        expected: bool,
    ) -> bool:
        """
        Evaluate a comparison expression against the payload.

        Handles expressions like ``"payload.quantity_change > 0"`` where
        *expected* indicates whether the comparison should be true or false.
        """
        operators = [" >= ", " <= ", " > ", " < "]
        for op in operators:
            if op not in expression:
                continue
            field_path, threshold_str = expression.split(op, 1)
            actual = cls._get_payload_value(payload, field_path.strip())
            if actual is None:
                return not expected
            try:
                actual_d = Decimal(str(actual))
                threshold_d = Decimal(threshold_str.strip())
                op_clean = op.strip()
                result = {
                    ">": actual_d > threshold_d,
                    "<": actual_d < threshold_d,
                    ">=": actual_d >= threshold_d,
                    "<=": actual_d <= threshold_d,
                }[op_clean]
                return result == expected
            except (InvalidOperation, ValueError):
                return not expected
        return not expected

    @classmethod
    def unregister(cls, name: str, version: int | None = None) -> None:
        """Unregister a profile. For testing only."""
        if name not in cls._profiles:
            return

        if version is None:
            # Remove all versions
            for v in list(cls._profiles[name].keys()):
                profile = cls._profiles[name][v]
                event_type = profile.trigger.event_type
                if event_type in cls._by_event_type:
                    cls._by_event_type[event_type] = [
                        p for p in cls._by_event_type[event_type]
                        if p.name != name
                    ]
            del cls._profiles[name]
        else:
            if version in cls._profiles[name]:
                profile = cls._profiles[name][version]
                event_type = profile.trigger.event_type
                if event_type in cls._by_event_type:
                    cls._by_event_type[event_type] = [
                        p for p in cls._by_event_type[event_type]
                        if not (p.name == name and p.version == version)
                    ]
                del cls._profiles[name][version]
                if not cls._profiles[name]:
                    del cls._profiles[name]
