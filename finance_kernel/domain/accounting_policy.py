"""
AccountingPolicy -- Declarative governance object for event interpretation.

Responsibility:
    Defines the single source of policy truth.  An AccountingPolicy declares
    which business events it applies to (trigger), what economic meaning it
    derives (meaning), what ledger effects it creates, and what guard
    conditions can reject or block processing.

Architecture position:
    Kernel > Domain -- pure functional core, zero I/O.

Invariants enforced:
    P1  -- Exactly one profile matches any event (overlap detection at compile)
    P7  -- Ledger semantic completeness (required roles provided)
    P10 -- Field references validated against EventSchema
    P12 -- Guard conditions: REJECT is terminal, BLOCK is resumable
    L1  -- Ledger effects use account ROLES, not COA codes

Failure modes:
    - ValueError if name is empty, version < 1, or required fields missing

Audit relevance:
    AccountingPolicy is the authoritative interpretation law.  Auditors verify
    that the correct profile was selected (P1) and that guard conditions were
    evaluated honestly (P12).
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

from finance_kernel.logging_config import get_logger

logger = get_logger("domain.accounting_policy")


class PrecedenceMode(str, Enum):
    """
    Profile precedence mode.

    Contract:
        Used by PolicySelector to resolve overlap when multiple profiles
        match the same event.

    Guarantees:
        OVERRIDE profiles are evaluated before NORMAL profiles.
    """

    NORMAL = "normal"
    OVERRIDE = "override"


class GuardType(str, Enum):
    """
    Type of guard condition (P12).

    Contract:
        REJECT is terminal -- the event represents invalid economic reality.
        BLOCK is resumable -- the system cannot safely process yet but may retry.

    Guarantees:
        Guard evaluation produces deterministic, auditable outcomes.
    """

    REJECT = "reject"  # Terminal -- invalid economic reality
    BLOCK = "block"  # Resumable -- system cannot safely process yet


@dataclass(frozen=True)
class PolicyTrigger:
    """
    Defines when a profile applies to an event.

    Contract:
        Immutable trigger specification.  ``event_type`` must be non-empty.
        ``where`` clauses narrow dispatch (P1 overlap resolution).

    Guarantees:
        Frozen dataclass -- cannot be mutated after construction.

    Attributes:
        event_type: The event type this profile handles (e.g., "inventory.receipt")
        schema_version: The schema version this profile is written for
        where: Optional conditions on payload fields (field_path -> expected_value)
    """

    event_type: str
    schema_version: int = 1
    where: tuple[tuple[str, Any], ...] = ()  # Immutable conditions

    def matches_event_type(self, event_type: str) -> bool:
        """Check if this trigger matches an event type."""
        return self.event_type == event_type


@dataclass(frozen=True)
class PolicyMeaning:
    """
    Defines the economic meaning derived from an event.

    Contract:
        ``economic_type`` must be non-empty.  ``quantity_field`` and
        ``dimensions`` are optional and validated by P10 field reference checks.

    Guarantees:
        Frozen dataclass -- cannot be mutated after construction.

    Attributes:
        economic_type: The type of economic event (e.g., "InventoryIncrease")
        quantity_field: Field path for quantity (e.g., "payload.quantity")
        dimensions: List of dimension fields to capture
    """

    economic_type: str
    quantity_field: str | None = None
    dimensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class LedgerEffect:
    """
    Defines a ledger posting effect.

    Uses AccountRoles, not COA accounts (per L1 invariant).

    Contract:
        ``debit_role`` and ``credit_role`` are semantic role names resolved
        to COA accounts at posting time by the JournalWriter (L1).

    Guarantees:
        Frozen dataclass -- cannot be mutated after construction.

    Non-goals:
        Does NOT verify that the roles exist in the LedgerRegistry (that
        is P7 validation in PolicyCompiler).

    Attributes:
        ledger: Ledger identifier (e.g., "GL", "inventory_subledger")
        debit_role: AccountRole for debit side
        credit_role: AccountRole for credit side
    """

    ledger: str
    debit_role: str
    credit_role: str


@dataclass(frozen=True)
class GuardCondition:
    """
    A guard condition that can reject or block event processing (P12).

    Contract:
        ``guard_type`` is either REJECT (terminal) or BLOCK (resumable).
        ``reason_code`` is machine-readable (R18).

    Guarantees:
        Frozen dataclass -- cannot be mutated after construction.

    Attributes:
        guard_type: REJECT (terminal) or BLOCK (resumable)
        expression: The condition expression (e.g., "payload.quantity <= 0")
        reason_code: Machine-readable reason code (R18)
        message: Human-readable message
    """

    guard_type: GuardType
    expression: str
    reason_code: str
    message: str = ""


@dataclass(frozen=True)
class PolicyPrecedence:
    """
    Defines profile precedence for overlap resolution.

    Attributes:
        mode: NORMAL or OVERRIDE
        priority: Numeric priority (higher wins)
        overrides: Names of profiles this one explicitly overrides
    """

    mode: PrecedenceMode = PrecedenceMode.NORMAL
    priority: int = 0
    overrides: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountingPolicy:
    """
    The primary governance object for event interpretation.

    Profiles define the law.  The engine enforces it.

    Contract:
        ``name`` must be non-empty, ``version`` >= 1, ``trigger.event_type``
        and ``meaning.economic_type`` must be non-empty.

    Guarantees:
        - Frozen dataclass -- cannot be mutated after construction.
        - ``is_effective_on(date)`` checks effective date range.
        - ``matches_scope(value)`` provides wildcard-based scope matching.
        - ``get_field_references()`` returns all referenced payload fields for
          P10 validation.

    Non-goals:
        - Does NOT store in a database (pure domain artifact).
        - Does NOT evaluate guards (MeaningBuilder does that).
        - Does NOT resolve roles to COA accounts (JournalWriter does that).

    Attributes:
        name: Unique profile name
        version: Profile version (for change tracking)
        trigger: When this profile applies
        meaning: What economic meaning is derived
        ledger_effects: What ledger postings are created
        effective_from: Start of effective date range
        effective_to: End of effective date range (None = open-ended)
        scope: Scope pattern for matching (e.g., "SKU:*", "project:PRJ-001")
        precedence: Precedence rules for overlap resolution
        valuation_model: Reference to valuation model (no inline expressions -- P8)
        guards: Reject and block conditions (P12)
        description: Human-readable description
    """

    name: str
    version: int
    trigger: PolicyTrigger
    meaning: PolicyMeaning
    ledger_effects: tuple[LedgerEffect, ...]

    # Effective date range
    effective_from: date
    effective_to: date | None = None  # None = open-ended

    # Scope and precedence
    scope: str = "*"  # Default: matches all
    precedence: PolicyPrecedence = field(default_factory=PolicyPrecedence)

    # Valuation (no inline expressions - model reference only)
    valuation_model: str | None = None

    # Guards (reject/block conditions)
    guards: tuple[GuardCondition, ...] = ()

    # Engine binding
    required_engines: tuple[str, ...] = ()
    engine_parameters_ref: str | None = None

    # Intent construction (e.g. "payload_lines" = build from event payload.lines)
    intent_source: str | None = None

    # Metadata
    description: str = ""

    def __post_init__(self) -> None:
        """Validate profile configuration."""
        # INVARIANT: P1 -- profile must have a name for unique identification
        if not self.name:
            raise ValueError("Profile name is required")
        if self.version < 1:
            raise ValueError("Profile version must be >= 1")
        # INVARIANT: P1 -- trigger must specify which event type to match
        if not self.trigger.event_type:
            raise ValueError("Trigger event_type is required")
        if not self.meaning.economic_type:
            raise ValueError("Meaning economic_type is required")

    @property
    def profile_key(self) -> str:
        """Unique key for this profile version."""
        return f"{self.name}:v{self.version}"

    def is_effective_on(self, check_date: date) -> bool:
        """Check if profile is effective on a given date."""
        if check_date < self.effective_from:
            logger.debug(
                "profile_not_effective",
                extra={
                    "profile": self.name,
                    "check_date": str(check_date),
                    "effective_from": str(self.effective_from),
                    "reason": "before_effective_from",
                },
            )
            return False
        if self.effective_to is not None and check_date > self.effective_to:
            logger.debug(
                "profile_not_effective",
                extra={
                    "profile": self.name,
                    "check_date": str(check_date),
                    "effective_to": str(self.effective_to),
                    "reason": "after_effective_to",
                },
            )
            return False
        return True

    def matches_scope(self, scope_value: str) -> bool:
        """
        Check if profile scope matches a given value.

        Simple wildcard matching:
        - "*" matches everything
        - "prefix:*" matches anything starting with "prefix:"
        - Exact match otherwise
        """
        if self.scope == "*":
            return True
        if self.scope.endswith(":*"):
            prefix = self.scope[:-1]  # Remove trailing "*"
            matched = scope_value.startswith(prefix)
            if not matched:
                logger.debug(
                    "profile_scope_mismatch",
                    extra={
                        "profile": self.name,
                        "profile_scope": self.scope,
                        "scope_value": scope_value,
                    },
                )
            return matched
        matched = self.scope == scope_value
        if not matched:
            logger.debug(
                "profile_scope_mismatch",
                extra={
                    "profile": self.name,
                    "profile_scope": self.scope,
                    "scope_value": scope_value,
                },
            )
        return matched

    def get_field_references(self) -> frozenset[str]:
        """
        Get all field paths referenced by this profile.

        Used for P10 validation against event schema.
        """
        fields: set[str] = set()

        # From trigger where conditions
        for field_path, _ in self.trigger.where:
            fields.add(field_path)

        # From meaning
        if self.meaning.quantity_field:
            fields.add(self.meaning.quantity_field)
        for dim in self.meaning.dimensions:
            if "." in dim:  # If it's a field path
                fields.add(dim)

        # From guards
        # Note: Guard expressions would need parsing for full field extraction
        # For now, we don't extract from complex expressions

        return frozenset(fields)

    def get_reject_guards(self) -> tuple[GuardCondition, ...]:
        """Get all REJECT guards."""
        return tuple(g for g in self.guards if g.guard_type == GuardType.REJECT)

    def get_block_guards(self) -> tuple[GuardCondition, ...]:
        """Get all BLOCK guards."""
        return tuple(g for g in self.guards if g.guard_type == GuardType.BLOCK)
