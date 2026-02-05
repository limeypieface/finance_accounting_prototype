"""
AccountingConfigurationSet schema.

Defines the human-authored, reviewable source artifact for accounting
configuration. This is the canonical data model — YAML fragments are
parsed into these types by the loader, composed by the assembler, and
compiled into a CompiledPolicyPack by the compiler.

Key distinction:
  AccountingConfigurationSet = source artifact (human-authored, versioned)
  CompiledPolicyPack         = runtime artifact (machine-validated, frozen)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from finance_config.lifecycle import ConfigStatus
from finance_kernel.domain.schemas.base import EventFieldType

# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigScope:
    """Scope of applicability for a configuration set."""

    legal_entity: str
    jurisdiction: str
    regulatory_regime: str  # GAAP, IFRS, DCAA, CAS
    currency: str
    effective_from: date
    effective_to: date | None = None


# ---------------------------------------------------------------------------
# Policy definitions (declarative data, no executable logic)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyTriggerDef:
    """Defines when a policy applies to an event."""

    event_type: str
    schema_version: int = 1
    where: tuple[tuple[str, Any], ...] = ()  # (field_path, expected_value)


@dataclass(frozen=True)
class PolicyMeaningDef:
    """Defines the economic meaning derived from an event."""

    economic_type: str
    quantity_field: str | None = None
    dimensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class LedgerEffectDef:
    """Defines a ledger posting effect using account roles."""

    ledger: str
    debit_role: str
    credit_role: str


@dataclass(frozen=True)
class GuardDef:
    """A guard condition using restricted AST expressions."""

    guard_type: str  # "reject" or "warn"
    expression: str  # Restricted AST expression
    reason_code: str
    message: str = ""


@dataclass(frozen=True)
class PrecedenceDef:
    """Precedence rules for policy overlap resolution."""

    mode: str = "normal"  # "normal" or "override"
    priority: int = 0
    overrides: tuple[str, ...] = ()


@dataclass(frozen=True)
class LineMappingDef:
    """Line mapping for accounting intent construction."""

    role: str
    side: str  # "debit" or "credit"
    ledger: str = "GL"
    from_context: str | None = None
    foreach: str | None = None


@dataclass(frozen=True)
class PolicyDefinition:
    """Declarative policy data — no executable logic.

    This is the configuration-side representation of what the kernel knows
    as an AccountingPolicy.
    """

    name: str
    version: int
    trigger: PolicyTriggerDef
    meaning: PolicyMeaningDef
    ledger_effects: tuple[LedgerEffectDef, ...]
    guards: tuple[GuardDef, ...] = ()
    effective_from: date = field(default_factory=lambda: date(2024, 1, 1))
    effective_to: date | None = None
    scope: str = "*"
    precedence: PrecedenceDef | None = None
    valuation_model: str | None = None
    line_mappings: tuple[LineMappingDef, ...] = ()  # For intent construction
    intent_source: str | None = None  # e.g. "payload_lines" for import.historical_journal

    # Engine binding
    required_engines: tuple[str, ...] = ()
    engine_parameters_ref: str | None = None  # Key in engine_configs
    variance_disposition: str | None = None  # "post", "capitalize", "allocate", "write_off"

    # Capability tagging
    capability_tags: tuple[str, ...] = ()  # e.g., ("DCAA",), ("IFRS",)

    description: str = ""
    module: str = ""  # Owning module name


# ---------------------------------------------------------------------------
# Role bindings (account role → COA code)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleBinding:
    """Maps an account role to a COA account code."""

    role: str  # e.g., "INVENTORY"
    ledger: str  # e.g., "GL"
    account_code: str  # e.g., "1200"
    effective_from: date = field(default_factory=lambda: date(2024, 1, 1))
    effective_to: date | None = None


# ---------------------------------------------------------------------------
# Ledger definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerDefinition:
    """Defines a ledger and its required roles."""

    ledger_id: str  # e.g., "GL", "INVENTORY"
    name: str
    required_roles: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Controls and governance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlRule:
    """A governance control rule that can reject or block events."""

    name: str
    applies_to: str  # Event type pattern ("payroll.*" or "*")
    action: str  # "reject" or "block"
    expression: str  # Restricted AST expression
    reason_code: str
    message: str = ""


# ---------------------------------------------------------------------------
# Engine configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineConfigDef:
    """Engine parameter configuration."""

    engine_name: str
    version_constraint: str = "*"
    parameters: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Subledger contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubledgerContractDef:
    """Defines a subledger contract between modules.

    Extended fields (timing, tolerance, enforcement) drive the compiler
    bridge that builds SubledgerControlContract instances at config time.
    """

    subledger_id: str
    owner_module: str
    control_account_role: str
    entry_types: tuple[str, ...] = ()
    is_debit_normal: bool = True
    timing: str = "real_time"  # real_time | daily | period_end
    tolerance_type: str = "none"  # none | absolute | percentage
    tolerance_amount: str = "0"  # For absolute tolerance
    tolerance_percentage: str = "0"  # For percentage tolerance
    enforce_on_post: bool = True
    enforce_on_close: bool = True


# ---------------------------------------------------------------------------
# Precedence rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrecedenceRule:
    """Global precedence rule for policy resolution."""

    name: str
    description: str = ""
    rule_type: str = "specificity"  # specificity, priority, scope_depth


# ---------------------------------------------------------------------------
# Approval policy definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalRuleDef:
    """YAML-authored approval rule."""

    rule_name: str
    priority: int
    min_amount: str | None = None
    max_amount: str | None = None
    required_roles: tuple[str, ...] = ()
    min_approvers: int = 1
    require_distinct_roles: bool = False
    guard_expression: str | None = None
    auto_approve_below: str | None = None
    escalation_timeout_hours: int | None = None


@dataclass(frozen=True)
class ApprovalPolicyDef:
    """YAML-authored approval policy."""

    policy_name: str
    version: int = 1
    applies_to_workflow: str = ""
    applies_to_action: str | None = None
    policy_currency: str | None = None
    rules: tuple[ApprovalRuleDef, ...] = ()
    effective_from: str | None = None
    effective_to: str | None = None


# ---------------------------------------------------------------------------
# Import mapping (ERP ingestion Phase 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportFieldDef:
    """Single field mapping: source column -> target field with type and transform."""

    source: str
    target: str
    field_type: EventFieldType = EventFieldType.STRING
    required: bool = False
    default: Any = None
    format: str | None = None  # Date/time format string
    transform: str | None = None  # "upper", "lower", "strip", "trim", "to_decimal"


@dataclass(frozen=True)
class ImportValidationDef:
    """Single validation rule for import records (batch/system/record scope)."""

    rule_type: str  # "unique", "exists", "expression", "cross_field"
    fields: tuple[str, ...] = ()
    scope: str = "batch"  # "batch", "system", "record"
    reference_entity: str | None = None  # For "exists" rules
    expression: str | None = None  # For "expression" rules
    message: str = ""


@dataclass(frozen=True)
class ImportMappingDef:
    """Declarative import mapping: source format, field mappings, validations, tier."""

    name: str
    version: int = 1
    entity_type: str = ""
    source_format: str = "csv"
    source_options: dict[str, Any] = field(default_factory=dict)
    field_mappings: tuple[ImportFieldDef, ...] = ()
    validations: tuple[ImportValidationDef, ...] = ()
    dependency_tier: int = 0


# ---------------------------------------------------------------------------
# Batch schedule definitions (BATCH_PROCESSING_PLAN Phase 6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchScheduleDef:
    """Declarative batch job schedule definition from YAML."""

    name: str
    task_type: str
    frequency: str  # Matches ScheduleFrequency values
    parameters: dict[str, Any] = field(default_factory=dict)
    cron_expression: str | None = None
    max_retries: int = 3
    is_active: bool = True
    legal_entity: str | None = None


# ---------------------------------------------------------------------------
# RBAC (config-driven, role-holistic, boundary-enforced)
# ---------------------------------------------------------------------------

# Permission taxonomy: valid <domain>.<object>.<authority> (economic verbs only).
# Compiler rule: every permission in roles/overrides must be in this set;
# every permission in this set must be assigned to ≥1 role (no orphaning).
PERMISSION_TAXONOMY: frozenset[str] = frozenset({
    # AP
    "ap.invoice.enter", "ap.invoice.view", "ap.invoice.approve",
    "ap.payment.enter", "ap.payment.release", "ap.payment.approve",
    "ap.hold.vendor", "ap.aging.view",
    # AR
    "ar.invoice.enter", "ar.invoice.view", "ar.credit_memo.issue",
    "ar.payment.apply", "ar.aging.view",
    # GL
    "gl.journal.post", "journal.post.manual", "period.close",
    "gl.reconcile", "gl.export.full",
    # Reporting / export
    "reporting.sensitive", "reporting.export.bulk", "pii.export",
    # RBAC admin
    "rbac.role.define", "rbac.sod.modify", "rbac.assignment.approve",
})


@dataclass(frozen=True)
class RbacConfigMetadata:
    """RBAC config version and effective dates (migration governance)."""

    version: str
    effective_from: date
    supersedes: str | None = None


@dataclass(frozen=True)
class AuthorityRulesDef:
    """Authority role activation rules."""

    authority_role_required: bool = True
    multi_role_actions_allowed: bool = False


@dataclass(frozen=True)
class RoleDef:
    """Single role definition: permissions and optional inheritance."""

    name: str
    permissions: tuple[str, ...]
    inherits: tuple[str, ...] = ()


@dataclass(frozen=True)
class PermissionConflictsDef:
    """SoD permission/role conflicts by severity."""

    hard_block: tuple[tuple[str, ...], ...] = ()  # e.g. (("ap.invoice.approve", "ap.payment.release"),)
    soft_warn: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class SegregationOfDutiesDef:
    """SoD: role conflicts, permission conflicts (with severity), lifecycle conflicts."""

    role_conflicts: tuple[tuple[str, ...], ...] = ()  # e.g. (("ap_clerk", "ap_manager"),)
    permission_conflicts: PermissionConflictsDef = field(default_factory=PermissionConflictsDef)
    lifecycle_conflicts: tuple[tuple[str, tuple[str, ...]], ...] = ()  # e.g. (("ap_invoice", ("enter", "approve")),)


@dataclass(frozen=True)
class OverrideRoleDef:
    """Time-bound override role (emergency, DR, etc.)."""

    name: str
    permissions: tuple[str, ...]
    expiry: str  # e.g. "24h"
    requires_dual_approval: bool = True


@dataclass(frozen=True)
class HierarchyRulesDef:
    """Role hierarchy constraints."""

    inheritance_depth_limit: int = 2


@dataclass(frozen=True)
class RbacConfigDef:
    """Full RBAC configuration (optional per config set)."""

    rbac_config: RbacConfigMetadata
    authority_rules: AuthorityRulesDef
    roles: tuple[RoleDef, ...]
    segregation_of_duties: SegregationOfDutiesDef
    override_roles: tuple[OverrideRoleDef, ...] = ()
    hierarchy_rules: HierarchyRulesDef = field(default_factory=HierarchyRulesDef)


# ---------------------------------------------------------------------------
# Top-level configuration set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountingConfigurationSet:
    """Human-authored, reviewable source artifact for accounting configuration.

    Composed from YAML fragments by the assembler. Compiled into a
    CompiledPolicyPack for runtime use. YAML loading is build/test
    tooling only — no service or orchestrator ever touches this at runtime.

    Attributes:
        config_id: Unique identifier (e.g., "US-GAAP-2026-v1")
        version: Configuration version number
        checksum: SHA-256 of canonical serialization
        predecessor: Previous config_id (append-only chain)
        scope: Applicability scope
        status: Lifecycle status
        policies: All policy definitions
        policy_precedence_rules: Global precedence rules
        role_bindings: Account role → COA code mappings
        ledger_definitions: Ledger definitions
        engine_configs: Engine parameter configurations
        controls: Governance control rules
        capabilities: Feature gates (e.g., {"dcaa": True})
        subledger_contracts: Subledger integration contracts
    """

    config_id: str
    version: int
    checksum: str
    scope: ConfigScope
    status: ConfigStatus
    policies: tuple[PolicyDefinition, ...]
    role_bindings: tuple[RoleBinding, ...]

    predecessor: str | None = None
    policy_precedence_rules: tuple[PrecedenceRule, ...] = ()
    ledger_definitions: tuple[LedgerDefinition, ...] = ()
    engine_configs: tuple[EngineConfigDef, ...] = ()
    controls: tuple[ControlRule, ...] = ()
    capabilities: dict[str, bool] = field(default_factory=dict)
    subledger_contracts: tuple[SubledgerContractDef, ...] = ()
    approval_policies: tuple[ApprovalPolicyDef, ...] = ()
    import_mappings: tuple[ImportMappingDef, ...] = ()
    batch_schedules: tuple[BatchScheduleDef, ...] = ()
    rbac: RbacConfigDef | None = None
