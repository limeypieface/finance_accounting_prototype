# Modular Approval Engine

**Objective:** Design and implement a fully modular, configuration-driven approval engine that governs all state transitions across the system -- both human approval gates and operational transitions.

**User Requirements (from Q&A):**
- Scope: ALL state transitions (human approvals AND operational transitions)
- Authority: Role + threshold rules in YAML (interface for org hierarchy plug-in later)
- Posting link: Configurable per policy (some transitions gate posting, some don't)

---

## Current State Analysis

### What Exists
- **16 workflow definitions** across 19 module `workflows.py` files (AP, AR, Cash, etc.)
- **Frozen dataclasses**: `Guard(name, description)`, `Transition(from_state, to_state, action, guard, posts_entry)`, `Workflow(name, description, initial_state, states, transitions)`
- **Guard AST system** (`finance_config/guard_ast.py`): Restricted expression validator allowing `payload.*`, `party.*`, `contract.*`, `event.*` field access
- **GuardCondition** in `finance_kernel/domain/accounting_policy.py`: `guard_type` (REJECT/BLOCK), `expression`, `reason_code`
- **MeaningBuilder** evaluates guards and returns `GuardEvaluationResult(passed, rejected, blocked, triggered_guard)`
- **ModulePostingStatus.GUARD_BLOCKED/GUARD_REJECTED**: Existing status codes in posting pipeline
- **No workflow executor**: Guards are metadata only -- declared but never evaluated at runtime (12+ xfail tests in `tests/modules/test_guard_execution.py`)

### The Gap
Workflows are declared but never executed. There is no:
1. Transition executor that validates and fires state changes
2. Approval request/decision lifecycle
3. Role-based authority rules
4. Threshold-based routing (amount determines required approver level)
5. Audit trail for approval decisions
6. Configuration-driven approval policies

---

## Architecture

### Layer Placement

```
finance_config/                  YAML schemas + approval policy definitions
    |
finance_modules/*/workflows.py   Existing workflow declarations (unchanged)
    |
finance_engines/approval.py      Pure computation: rule evaluation, threshold checks
    |
finance_kernel/domain/approval.py    Pure domain types (frozen dataclasses)
finance_kernel/models/approval.py    ORM persistence models
finance_kernel/services/approval_service.py      Approval lifecycle management
finance_kernel/services/workflow_executor.py     State machine execution
```

### Dependency Rules (Enforced by Architecture Tests)
- `finance_kernel/domain/approval.py` -- ZERO I/O, imports only from `domain/values`, `domain/clock`
- `finance_engines/approval.py` -- Pure computation, imports only from `finance_kernel/domain/`
- `finance_kernel/services/` -- May import from `domain/`, `models/`, `db/`
- `finance_config/` -- Independent, no kernel/engine imports

---

## Phase 0: Consolidate Workflow Types

**Goal:** Move the `Guard`, `Transition`, `Workflow` dataclasses from being duplicated per-module into a single canonical location.

### Current Problem
Each module's `workflows.py` re-declares identical `Guard`, `Transition`, `Workflow` classes. These are structurally identical but separate Python types.

### Changes
- **Create** `finance_kernel/domain/workflow.py` -- canonical frozen dataclasses
- **Modify** all 19 `finance_modules/*/workflows.py` -- import from kernel instead of re-declaring
- **Add** `requires_approval: bool = False` field to canonical `Transition`
- **Add** `approval_policy: ApprovalPolicyRef | None = None` field to canonical `Transition` (typed reference to YAML policy)

### Canonical Types

```python
# finance_kernel/domain/workflow.py

@dataclass(frozen=True)
class Guard:
    name: str
    description: str

@dataclass(frozen=True)
class ApprovalPolicyRef:
    """Typed reference from a workflow transition to an approval policy.

    min_version prevents silent weakening of controls when policies evolve.
    A transition declaring min_version=2 will reject resolution under policy v1.
    """
    policy_name: str
    min_version: int | None = None           # Minimum acceptable policy version

@dataclass(frozen=True)
class Transition:
    from_state: str
    to_state: str
    action: str
    guard: Guard | None = None
    posts_entry: bool = False
    requires_approval: bool = False          # NEW
    approval_policy: ApprovalPolicyRef | None = None  # NEW -- typed policy reference

@dataclass(frozen=True)
class Workflow:
    name: str
    description: str
    initial_state: str
    states: tuple[str, ...]
    transitions: tuple[Transition, ...]
    terminal_states: tuple[str, ...] = ()    # NEW -- states with no outgoing transitions
```

### Files Modified
- `finance_kernel/domain/workflow.py` (NEW)
- `finance_modules/ap/workflows.py` (import change)
- `finance_modules/ar/workflows.py` (import change)
- ... (all 19 workflow files)

---

## Phase 1: Domain Types (`finance_kernel/domain/approval.py`)

**Goal:** Define all pure value objects for the approval system.

### Types

```python
# Approval request states
class ApprovalStatus(str, Enum):
    PENDING = "pending"           # Awaiting decision
    APPROVED = "approved"         # Approved by authorized actor (TERMINAL)
    REJECTED = "rejected"         # Rejected by authorized actor (TERMINAL)
    ESCALATED = "escalated"       # Escalated to higher authority
    EXPIRED = "expired"           # Timed out without decision (TERMINAL)
    CANCELLED = "cancelled"       # Cancelled by requestor (TERMINAL)
    AUTO_APPROVED = "auto_approved"  # Below threshold, auto-approved (TERMINAL)

# INVARIANT [AL-1]: ApprovalRequest lifecycle state machine.
# Enforced in ApprovalService.record_decision() AND via DB check constraint.
#
#   PENDING  --> APPROVED | REJECTED | ESCALATED | EXPIRED | CANCELLED | AUTO_APPROVED
#   ESCALATED --> APPROVED | REJECTED | EXPIRED
#   APPROVED / REJECTED / EXPIRED / CANCELLED / AUTO_APPROVED --> (terminal, no transitions)
#
APPROVAL_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset({
        ApprovalStatus.APPROVED, ApprovalStatus.REJECTED,
        ApprovalStatus.ESCALATED, ApprovalStatus.EXPIRED,
        ApprovalStatus.CANCELLED, ApprovalStatus.AUTO_APPROVED,
    }),
    ApprovalStatus.ESCALATED: frozenset({
        ApprovalStatus.APPROVED, ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
    }),
    # Terminal states -- no outgoing transitions
    ApprovalStatus.APPROVED: frozenset(),
    ApprovalStatus.REJECTED: frozenset(),
    ApprovalStatus.EXPIRED: frozenset(),
    ApprovalStatus.CANCELLED: frozenset(),
    ApprovalStatus.AUTO_APPROVED: frozenset(),
}

TERMINAL_APPROVAL_STATUSES: frozenset[ApprovalStatus] = frozenset({
    ApprovalStatus.APPROVED, ApprovalStatus.REJECTED,
    ApprovalStatus.EXPIRED, ApprovalStatus.CANCELLED,
    ApprovalStatus.AUTO_APPROVED,
})

class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"
    REQUEST_INFO = "request_info"

@dataclass(frozen=True)
class ApprovalRule:
    """A single rule in an approval policy. Pure data."""
    rule_name: str
    priority: int                           # [AL-6] Deterministic evaluation order
    min_amount: Decimal | None = None       # Threshold floor (inclusive)
    max_amount: Decimal | None = None       # Threshold ceiling (exclusive)
    required_roles: tuple[str, ...] = ()    # Any of these roles may approve
    min_approvers: int = 1                  # How many approvals needed
    require_distinct_roles: bool = False    # [AL-9] Approvals must come from distinct roles
    guard_expression: str | None = None     # Additional guard (restricted AST)
    auto_approve_below: Decimal | None = None  # Below this = auto-approve
    escalation_timeout_hours: int | None = None

@dataclass(frozen=True)
class ApprovalPolicy:
    """A named, versioned approval policy. Pure data."""
    policy_name: str
    version: int
    applies_to_workflow: str                # Workflow name
    applies_to_action: str | None = None    # Specific action, or None = all
    rules: tuple[ApprovalRule, ...] = ()
    effective_from: date | None = None
    effective_to: date | None = None
    policy_currency: str | None = None      # [AL-3] Currency for threshold comparison
    policy_hash: str | None = None          # [AL-2] SHA-256 of canonical serialization

@dataclass(frozen=True)
class ApprovalRequest:
    """An immutable snapshot of an approval request.

    INVARIANT [AL-2]: policy_version and policy_hash are snapshotted at
    creation time, mirroring R21 (reference snapshot determinism) for the
    ledger.  This ensures approval decisions can be deterministically
    replayed against the exact policy that was active when the request
    was created.

    INVARIANT [AL-3]: amount is always expressed in the policy's declared
    currency (policy_currency).  If the source entity uses a different
    currency, the caller must normalize before creating the request.
    The approval engine compares amount against rule thresholds in this
    single currency -- no implicit conversion.
    """
    request_id: UUID
    workflow_name: str
    entity_type: str                        # "invoice", "payment", etc.
    entity_id: UUID
    transition_action: str                  # The action requiring approval
    from_state: str
    to_state: str
    policy_name: str
    policy_version: int                     # [AL-2] Snapshotted at creation
    policy_hash: str | None = None          # [AL-2] SHA-256 of policy at creation
    amount: Money | None = None             # [AL-3] Always in policy_currency
    currency: str = "USD"                   # [AL-3] Must match policy_currency
    requestor_id: UUID = field(default_factory=uuid4)
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    matched_rule: str | None = None         # Which rule matched
    original_request_hash: str | None = None  # [AL-8] SHA-256 tamper evidence
    decisions: tuple[ApprovalDecisionRecord, ...] = ()

@dataclass(frozen=True)
class ApprovalDecisionRecord:
    """Record of a single approval decision. Immutable."""
    decision_id: UUID
    request_id: UUID
    actor_id: UUID
    actor_role: str
    decision: ApprovalDecision
    comment: str = ""
    decided_at: datetime | None = None

@dataclass(frozen=True)
class ApprovalEvaluation:
    """Result of evaluating whether a transition needs/has approval. Pure."""
    needs_approval: bool
    is_approved: bool = False
    is_rejected: bool = False
    matched_rule: ApprovalRule | None = None
    required_approvers: int = 0
    current_approvers: int = 0
    auto_approved: bool = False
    reason: str = ""
```

### Files Created
- `finance_kernel/domain/approval.py`

---

## Phase 2: Pure Engine (`finance_engines/approval.py`)

**Goal:** Pure computation engine for approval rule evaluation. Zero I/O.

### Functions

```python
def evaluate_approval_requirement(
    transition: Transition,
    policy: ApprovalPolicy | None,
    amount: Money | None,
    context: dict[str, Any] | None = None,
) -> ApprovalEvaluation:
    """Determine if a transition requires approval and which rule applies."""

def evaluate_approval_decision(
    request: ApprovalRequest,
    rule: ApprovalRule,
    decisions: tuple[ApprovalDecisionRecord, ...],
) -> ApprovalEvaluation:
    """Given current decisions, determine if approval threshold is met.

    [AL-9]: If rule.require_distinct_roles is True, only counts approvals
    from distinct actor_roles toward min_approvers threshold.
    """

def select_matching_rule(
    rules: tuple[ApprovalRule, ...],
    amount: Money | None,
    context: dict[str, Any] | None = None,
) -> ApprovalRule | None:
    """Select matching rule based on amount thresholds and guards.

    [AL-6]: Rules are sorted by priority (ascending) before evaluation.
    First match wins. Compiler enforces unique priorities per policy.
    """

def validate_actor_authority(
    actor_role: str,
    rule: ApprovalRule,
) -> bool:
    """Check if actor's role is authorized to approve under this rule."""
```

### Key Design Decisions
- All functions are pure -- receive data, return data
- Guard expressions evaluated via `guard_ast.py` (reuse existing infrastructure)
- No database, no clock, no side effects
- Amount thresholds use `Decimal` comparison (never float)

### Files Created
- `finance_engines/approval.py`

---

## Phase 3: ORM Models (`finance_kernel/models/approval.py`)

**Goal:** Persist approval requests and decisions.

### Models

```python
class ApprovalRequestModel(Base):
    __tablename__ = "approval_requests"

    request_id: Mapped[UUID] = mapped_column(primary_key=True)
    workflow_name: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[UUID]
    transition_action: Mapped[str] = mapped_column(String(100))
    from_state: Mapped[str] = mapped_column(String(50))
    to_state: Mapped[str] = mapped_column(String(50))
    policy_name: Mapped[str] = mapped_column(String(200))
    policy_version: Mapped[int]                                   # [AL-2] Snapshotted
    policy_hash: Mapped[str | None] = mapped_column(String(64))   # [AL-2] SHA-256
    matched_rule: Mapped[str | None] = mapped_column(String(200))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(38, 9))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    requestor_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(50))  # ApprovalStatus.value
    created_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]
    original_request_hash: Mapped[str] = mapped_column(String(64))  # [AL-8] Tamper evidence

    # Relationships
    decisions: Mapped[list["ApprovalDecisionModel"]] = relationship(...)

    __table_args__ = (
        # [AL-1] Lifecycle state machine enforced at DB level
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'escalated', "
            "'expired', 'cancelled', 'auto_approved')",
            name="ck_approval_requests_valid_status",
        ),
        # [AL-10] Request idempotency -- prevent duplicate pending requests
        # for the same transition on the same entity
        UniqueConstraint(
            "workflow_name", "entity_type", "entity_id",
            "transition_action", "from_state", "to_state", "status",
            name="uq_approval_requests_pending_transition",
        ),
        # [AL-11] Covering index for get_pending_for_entity()
        Index("ix_approval_requests_entity_status",
              "entity_type", "entity_id", "status", "created_at"),
        # [AL-7] Expiry enforcement index
        Index("ix_approval_requests_expiry", "status", "created_at"),
    )

class ApprovalDecisionModel(Base):
    __tablename__ = "approval_decisions"

    decision_id: Mapped[UUID] = mapped_column(primary_key=True)
    request_id: Mapped[UUID] = mapped_column(ForeignKey("approval_requests.request_id"))
    actor_id: Mapped[UUID]
    actor_role: Mapped[str] = mapped_column(String(100))
    decision: Mapped[str] = mapped_column(String(50))  # ApprovalDecision.value
    comment: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime]

    __table_args__ = (
        Index("ix_approval_decisions_request_id", "request_id"),
        # [AL-7] Decision uniqueness -- same actor cannot approve twice
        UniqueConstraint("request_id", "actor_id",
                         name="uq_approval_decisions_actor"),
    )
```

### Immutability and Lifecycle Enforcement

- `ApprovalDecisionModel` -- always append-only (decisions are immutable records)
- `ApprovalRequestModel.status` -- mutable but **lifecycle-constrained** [AL-1]:
  - Three enforcement layers (mirroring the posting pipeline's three-layer defense):
    1. **Pure domain**: `APPROVAL_TRANSITIONS` dict in `domain/approval.py` -- consulted by engine
    2. **Service layer**: `ApprovalService.record_decision()` validates transition before persisting
    3. **DB trigger**: `trg_approval_request_lifecycle` rejects invalid status transitions
  - Terminal states (APPROVED, REJECTED, EXPIRED, CANCELLED, AUTO_APPROVED) cannot be changed
  - This prevents "resurrection" of blocked transitions via direct DB manipulation
- `policy_version` + `policy_hash` are **write-once** -- set at creation, never updated [AL-2]
- `original_request_hash` is **write-once** -- computed at creation, verified on every load [AL-8]
- `UNIQUE(request_id, actor_id)` on decisions prevents same actor approving twice [AL-7]
- `UNIQUE(workflow, entity, transition, status)` on requests prevents duplicate pending requests [AL-10]
- Covering index `(entity_type, entity_id, status, created_at)` for `get_pending_for_entity()` [AL-11]
- Expiry index `(status, created_at)` for `expire_stale_requests()` [AL-7]
- Both models get audit events via `AuditorService`

### Files Created
- `finance_kernel/models/approval.py`

---

## Phase 4: Exceptions and Audit Actions

### Exceptions (add to `finance_kernel/exceptions.py`)

```python
class ApprovalRequiredError(FinanceKernelError):
    """Transition requires approval that has not been granted."""
    code = "APPROVAL_REQUIRED"

class ApprovalNotFoundError(FinanceKernelError):
    """Referenced approval request does not exist."""
    code = "APPROVAL_NOT_FOUND"

class UnauthorizedApproverError(FinanceKernelError):
    """Actor does not have the required role to approve."""
    code = "UNAUTHORIZED_APPROVER"

class ApprovalAlreadyResolvedError(FinanceKernelError):
    """Approval request has already been resolved."""
    code = "APPROVAL_ALREADY_RESOLVED"

class InvalidApprovalTransitionError(FinanceKernelError):
    """Status transition violates the approval lifecycle state machine [AL-1]."""
    code = "INVALID_APPROVAL_TRANSITION"

class PolicyDriftError(FinanceKernelError):
    """Active policy version is lower than the snapshotted version [AL-5]."""
    code = "POLICY_DRIFT"

class CurrencyMismatchError(FinanceKernelError):
    """Request currency does not match the policy's declared currency [AL-3]."""
    code = "APPROVAL_CURRENCY_MISMATCH"

class DuplicateApprovalError(FinanceKernelError):
    """Same actor attempted to approve the same request twice [AL-7]."""
    code = "DUPLICATE_APPROVAL"

class TamperDetectedError(FinanceKernelError):
    """Approval request hash does not match stored hash [AL-8]."""
    code = "APPROVAL_TAMPER_DETECTED"

class DuplicateApprovalRequestError(FinanceKernelError):
    """Duplicate pending approval request for the same transition [AL-10]."""
    code = "DUPLICATE_APPROVAL_REQUEST"
```

### Audit Actions (add to `finance_kernel/services/auditor_service.py`)

```python
# New audit action constants
APPROVAL_REQUESTED = "approval_requested"
APPROVAL_GRANTED = "approval_granted"
APPROVAL_REJECTED = "approval_rejected"
APPROVAL_ESCALATED = "approval_escalated"
APPROVAL_AUTO_APPROVED = "approval_auto_approved"
APPROVAL_EXPIRED = "approval_expired"
APPROVAL_CANCELLED = "approval_cancelled"
APPROVAL_POLICY_VERSION_DRIFT = "approval_policy_version_drift"  # [AL-5]
APPROVAL_TAMPER_DETECTED = "approval_tamper_detected"            # [AL-8]
```

**[AL-7] Auto-approval audit payload** -- The `APPROVAL_AUTO_APPROVED` audit event
MUST include in its `extra` dict:
- `matched_rule`: rule name that triggered auto-approval
- `threshold_value`: the `auto_approve_below` Decimal from the rule
- `evaluated_amount`: the amount that was compared against the threshold
- `policy_name`, `policy_version`, `policy_hash`: full policy provenance

This makes auto-approvals indistinguishable from human approvals in terms of
audit traceability. An auto-approve without these fields is a silent bypass.

### Files Modified
- `finance_kernel/exceptions.py`
- `finance_kernel/services/auditor_service.py`

---

## Phase 5: Services

### 5a: ApprovalService (`finance_kernel/services/approval_service.py`)

Manages the approval request/decision lifecycle.

```python
class ApprovalService:
    def __init__(
        self,
        session: Session,
        approval_policies: dict[str, ApprovalPolicy],  # From compiled config
        clock: Clock | None = None,
    ):
        self._session = session
        self._policies = approval_policies
        self._clock = clock or SystemClock()
        self._auditor = AuditorService(session, clock)

    def create_request(
        self,
        workflow_name: str,
        entity_type: str,
        entity_id: UUID,
        transition_action: str,
        from_state: str,
        to_state: str,
        policy_name: str,
        matched_rule: str,
        requestor_id: UUID,
        amount: Decimal | None = None,
        currency: str = "USD",
    ) -> ApprovalRequest:
        """Create a new approval request. Returns frozen DTO.

        INVARIANT [AL-2]: Snapshots policy_version and policy_hash at creation
        time from the currently active compiled policy.
        INVARIANT [AL-3]: Validates currency matches policy_currency (if set).
        """

    def record_decision(
        self,
        request_id: UUID,
        actor_id: UUID,
        actor_role: str,
        decision: ApprovalDecision,
        comment: str = "",
    ) -> ApprovalRequest:
        """Record a decision on an approval request. Returns updated frozen DTO.

        INVARIANT [AL-1]: Validates status transition against APPROVAL_TRANSITIONS
        before persisting.  Raises ApprovalAlreadyResolvedError if current status
        is terminal.

        INVARIANT [AL-5]: On resolution (approve/reject), verifies the active
        policy version matches the snapshotted policy_version.  If policy has
        changed since request creation:
          - If new version is HIGHER: resolution proceeds, but an audit event
            is emitted with action "approval_policy_version_drift" recording
            both original and current versions.
          - If new version is LOWER (downgrade): raises PolicyDriftError.
            A downgraded policy cannot resolve a request created under a
            stricter version.
        """

    def get_request(self, request_id: UUID) -> ApprovalRequest:
        """Get approval request by ID.

        [AL-8]: Recomputes original_request_hash on load and compares
        against the stored value. If mismatch, emits APPROVAL_TAMPER_DETECTED
        audit event and raises TamperDetectedError.
        """

    def get_pending_for_entity(
        self,
        entity_type: str,
        entity_id: UUID,
    ) -> list[ApprovalRequest]:
        """Get all pending approval requests for an entity."""

    def cancel_request(self, request_id: UUID, actor_id: UUID) -> ApprovalRequest:
        """Cancel a pending approval request.

        INVARIANT [AL-1]: Only PENDING or ESCALATED requests can be cancelled.
        """

    def expire_stale_requests(self, as_of: datetime) -> list[UUID]:
        """Expire requests past their escalation timeout.

        INVARIANT [AL-1]: Only PENDING or ESCALATED requests can be expired.

        Concurrency: Uses pg_advisory_xact_lock(hash('approval_expiry'))
        to prevent double-expiry races when run from a scheduled job.
        Index ix_approval_requests_expiry (status, created_at) ensures
        this query does not full-scan.
        """
```

### 5b: WorkflowExecutor (`finance_kernel/services/workflow_executor.py`)

Executes state transitions with approval enforcement.

**INVARIANT [AL-4]: WorkflowExecutor is a thin coordinator.**
It owns ONLY the orchestration sequence:
1. Validate transition exists in workflow
2. Delegate approval evaluation to `finance_engines/approval.py` (pure)
3. Delegate approval persistence to `ApprovalService`
4. Delegate role resolution to `OrgHierarchyProvider`
5. Return `TransitionResult`

It MUST NOT contain:
- Approval rule evaluation logic (belongs in engine)
- Role/hierarchy resolution logic (belongs in OrgHierarchyProvider)
- Policy compilation or selection logic (belongs in config/compiler)
- Direct ORM queries (belongs in ApprovalService)

This is enforced by code review discipline and architecture tests that verify
the executor imports only from `domain/`, `services/`, and the engine layer.

```python
class WorkflowExecutor:
    """Executes workflow transitions with approval gate enforcement.

    INVARIANT [AL-4]: Thin coordinator -- delegates all domain logic to
    the approval engine, all persistence to ApprovalService, and all
    role resolution to OrgHierarchyProvider.
    """

    def __init__(
        self,
        session: Session,
        approval_service: ApprovalService,
        approval_policies: dict[str, ApprovalPolicy],  # From compiled config
        clock: Clock | None = None,
        org_hierarchy: OrgHierarchyProvider | None = None,
    ):
        ...

    def execute_transition(
        self,
        workflow: Workflow,
        entity_type: str,
        entity_id: UUID,
        current_state: str,
        action: str,
        actor_id: UUID,
        actor_role: str,
        amount: Money | None = None,
        context: dict[str, Any] | None = None,
        approval_request_id: UUID | None = None,  # For pre-approved transitions
    ) -> TransitionResult:
        """
        Execute a state transition.

        Returns TransitionResult with:
        - success: bool
        - new_state: str (if success)
        - approval_required: bool (if blocked)
        - approval_request_id: UUID (if approval was created)
        - posts_entry: bool (if transition triggers posting)
        """

    def resume_after_approval(
        self,
        approval_request_id: UUID,
        actor_id: UUID,
        actor_role: str,
    ) -> TransitionResult:
        """Resume a transition that was blocked pending approval."""

@dataclass(frozen=True)
class TransitionResult:
    success: bool
    new_state: str | None = None
    approval_required: bool = False
    approval_request_id: UUID | None = None
    posts_entry: bool = False
    reason: str = ""
```

### 5c: OrgHierarchyProvider Protocol

```python
# finance_kernel/domain/approval.py (Protocol definition)

class OrgHierarchyProvider(Protocol):
    """Pluggable interface for organizational hierarchy lookups."""

    def get_actor_roles(self, actor_id: UUID) -> tuple[str, ...]:
        """Return all roles for an actor."""
        ...

    def get_approval_chain(self, actor_id: UUID) -> tuple[UUID, ...]:
        """Return the chain of approvers above this actor."""
        ...

    def has_role(self, actor_id: UUID, role: str) -> bool:
        """Check if actor has a specific role."""
        ...
```

A default `StaticRoleProvider` will be provided for initial use that returns roles from a simple dict mapping. The full org hierarchy can be plugged in later.

### Files Created
- `finance_kernel/services/approval_service.py`
- `finance_kernel/services/workflow_executor.py`

---

## Phase 6: Config Schema Additions

### New Config Types (`finance_config/schema.py`)

```python
@dataclass(frozen=True)
class ApprovalRuleDef:
    """YAML-authored approval rule."""
    rule_name: str
    priority: int                           # [AL-6] Deterministic evaluation order
    min_amount: str | None = None           # Decimal string
    max_amount: str | None = None
    required_roles: tuple[str, ...] = ()
    min_approvers: int = 1
    require_distinct_roles: bool = False    # [AL-9] Require distinct role per approver
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
    policy_currency: str | None = None      # [AL-3] ISO 4217 code for thresholds
    rules: tuple[ApprovalRuleDef, ...] = ()
    effective_from: str | None = None       # ISO date string
    effective_to: str | None = None
```

### AccountingConfigurationSet Addition

```python
@dataclass(frozen=True)
class AccountingConfigurationSet:
    ...
    approval_policies: tuple[ApprovalPolicyDef, ...] = ()  # NEW
```

### Compiler Addition

The compiler validates approval policies:
- Guard expressions validated via `guard_ast.validate_guard_expression()`
- Amount thresholds are valid Decimal strings
- Referenced workflows exist
- Rule ranges don't overlap within a policy
- [AL-6] Rule priorities are unique within each policy
- [AL-3] `policy_currency` is a valid ISO 4217 code (if set)
- [AL-2] Computes `policy_hash` (SHA-256 of canonical serialization) for each compiled policy
- [AL-8] `ApprovalPolicyRef.policy_name` on transitions references a compiled policy that applies to the correct workflow/action
- [AL-8] `ApprovalPolicyRef.min_version` on transitions does not exceed the compiled policy version
- Produces compiled `ApprovalPolicy` domain objects with rules sorted by priority

### Loader Addition

New YAML section `approval_policies:` parsed into `ApprovalPolicyDef` tuples.

### Files Modified
- `finance_config/schema.py` (add `ApprovalRuleDef`, `ApprovalPolicyDef`, field on `AccountingConfigurationSet`)
- `finance_config/loader.py` (parse `approval_policies` YAML section)
- `finance_config/compiler.py` (validate + compile approval policies)
- `finance_config/assembler.py` (compose approval policy fragments)

---

## Phase 7: YAML Configuration

### Example `config/approval_policies.yaml`

```yaml
approval_policies:
  - policy_name: "ap_invoice_approval"
    version: 1
    applies_to_workflow: "ap_invoice"
    applies_to_action: "approve"
    policy_currency: "USD"                # [AL-3] Thresholds are in USD
    rules:
      - rule_name: "auto_approve_small"
        priority: 10
        auto_approve_below: "500.00"
      - rule_name: "manager_approval"
        priority: 20
        min_amount: "500.00"
        max_amount: "10000.00"
        required_roles: ["ap_manager", "finance_manager"]
        min_approvers: 1
      - rule_name: "director_approval"
        priority: 30
        min_amount: "10000.00"
        max_amount: "100000.00"
        required_roles: ["finance_director", "cfo"]
        min_approvers: 1
      - rule_name: "executive_approval"
        priority: 40
        min_amount: "100000.00"
        required_roles: ["cfo", "ceo"]
        min_approvers: 2
        require_distinct_roles: true      # [AL-9] Both approvers must hold different roles
        escalation_timeout_hours: 48

  - policy_name: "ap_payment_approval"
    version: 1
    applies_to_workflow: "ap_payment"
    applies_to_action: "approve"
    policy_currency: "USD"                # [AL-3] Thresholds are in USD
    rules:
      - rule_name: "payment_approval"
        priority: 10
        required_roles: ["treasury_manager", "cfo"]
        min_approvers: 1
        guard_expression: "payload.amount > 0"
```

---

## Phase 8: Integration with Posting Pipeline

### How Approval Gates Posting

For transitions where `posts_entry=True` AND `requires_approval=True`:

1. Module service calls `WorkflowExecutor.execute_transition()`
2. Executor checks if approval is needed (via engine)
3. If approval needed and not yet granted → returns `TransitionResult(approval_required=True)`
4. Module service returns `ModulePostingStatus.GUARD_BLOCKED` (reuses existing status)
5. When approval is granted, `resume_after_approval()` fires the transition
6. If `posts_entry=True`, the module service calls `ModulePostingService.post_event()` as normal

### Integration Point in ModulePostingService

No changes to `ModulePostingService` itself. The approval gate sits BEFORE the posting pipeline is called, at the module service level. This keeps the posting pipeline pure (it already handles `GUARD_BLOCKED`).

### Module Service Pattern

```python
# In any module service (e.g., finance_modules/ap/service.py):

def approve_invoice(self, invoice_id, actor_id, actor_role, ...):
    result = self._workflow_executor.execute_transition(
        workflow=INVOICE_WORKFLOW,
        entity_type="invoice",
        entity_id=invoice_id,
        current_state=invoice.status,
        action="approve",
        actor_id=actor_id,
        actor_role=actor_role,
        amount=invoice.amount,
    )
    if result.approval_required:
        return ApprovalPendingResult(request_id=result.approval_request_id)
    if result.success and result.posts_entry:
        return self._post_event(...)  # Existing posting logic
    return result
```

---

## Phase 9: Tests

### Test Files

| File | Scope | Tests |
|------|-------|-------|
| `tests/domain/test_approval_types.py` | Domain type construction, immutability, enum values | ~20 |
| `tests/engines/test_approval_engine.py` | Pure rule evaluation, threshold matching, authority checks | ~30 |
| `tests/models/test_approval_orm.py` | ORM round-trip, FK constraints, indexes | ~15 |
| `tests/services/test_approval_service.py` | Request lifecycle, decision recording, audit events | ~25 |
| `tests/services/test_workflow_executor.py` | Transition execution, approval gates, resume flow | ~25 |
| `tests/config/test_approval_config.py` | YAML loading, compilation, validation errors | ~15 |
| `tests/architecture/test_approval_boundary.py` | Import boundary enforcement | ~5 |

### Key Test Scenarios

**Core approval flow:**
1. Auto-approve below threshold
2. Single approver with correct role
3. Multi-approver requirement (2 of 3 must approve)
4. Rejection blocks transition
5. Escalation on timeout
6. Unauthorized role cannot approve
7. Amount threshold routing (small -> manager, large -> director)
8. Guard expression evaluation on approval rules
9. Resume-after-approval flow
10. Posting-linked transitions block posting until approved
11. Non-posting transitions execute immediately when approved

**Lifecycle state machine [AL-1]:**
12. PENDING -> APPROVED succeeds
13. PENDING -> REJECTED succeeds
14. APPROVED -> PENDING raises InvalidApprovalTransitionError
15. REJECTED -> APPROVED raises InvalidApprovalTransitionError
16. CANCELLED -> any raises InvalidApprovalTransitionError
17. DB trigger rejects direct UPDATE of terminal status via raw SQL

**Policy version snapshotting [AL-2]:**
18. Created request stores policy_version and policy_hash
19. Resolution under same policy version succeeds
20. Resolution under newer policy version succeeds + emits drift audit event
21. Resolution under older (downgraded) policy version raises PolicyDriftError
22. ApprovalPolicyRef.min_version rejects policy below minimum at transition time

**Currency contract [AL-3]:**
23. Request with matching currency accepted
24. Request with mismatching currency raises CurrencyMismatchError
25. Compiler rejects policy with overlapping threshold ranges in same currency
26. Compiler warns if policy has no policy_currency set (default: accept any)

**Executor thinness [AL-4]:**
27. Architecture test: WorkflowExecutor contains no Decimal comparison logic
28. Architecture test: WorkflowExecutor contains no direct ORM query calls

**Deterministic rule ordering [AL-6]:**
29. Rules evaluated in priority order regardless of tuple position
30. Compiler rejects duplicate priorities within a policy
31. Lower priority number wins (priority=10 before priority=20)

**Decision uniqueness [AL-7]:**
32. Same actor approving twice raises DuplicateApprovalError
33. Different actors approving same request succeeds
34. DB unique constraint enforced via raw SQL test

**Request tamper evidence [AL-8]:**
35. Hash computed at creation matches recomputation on load
36. Corrupted hash detected on load raises TamperDetectedError + audit event
37. Hash covers all immutable request fields (policy, entity, transition, amount)

**Role diversity [AL-9]:**
38. require_distinct_roles=False: two actors with same role both count
39. require_distinct_roles=True: two actors with same role count as 1
40. require_distinct_roles=True + min_approvers=2: needs 2 distinct roles

**Request idempotency [AL-10]:**
41. Creating duplicate pending request for same transition raises DuplicateApprovalRequestError
42. Creating request for same transition after prior was REJECTED succeeds (different status)
43. DB unique constraint tested via concurrent insertion

**Auto-approval audit [AL-7 audit]:**
44. Auto-approval audit event contains matched_rule, threshold, amount, policy provenance
45. Auto-approval without full audit payload fails assertion

**Expiry enforcement:**
46. expire_stale_requests uses advisory lock (no double-expiry in concurrent test)
47. Only PENDING/ESCALATED requests expired; terminal states untouched

---

## Phase 10: Module Workflow Migration (Incremental)

Mark specific transitions as `requires_approval=True` in existing module workflows. This is incremental -- start with AP as the reference implementation.

### AP Invoice Workflow (Updated)

```python
Transition("pending_approval", "approved", action="approve",
           guard=APPROVAL_THRESHOLD_MET,
           requires_approval=True,
           approval_policy=ApprovalPolicyRef(
               policy_name="ap_invoice_approval",
               min_version=1,
           )),
```

### Rollout Order
1. AP (invoice + payment) -- reference implementation
2. AR (credit memos)
3. Expense (expense reports)
4. Procurement (purchase orders)
5. Remaining modules as needed

---

## Implementation Order

| Phase | Description | Depends On |
|-------|------------|------------|
| 0 | Consolidate workflow types | -- |
| 1 | Domain types | Phase 0 |
| 2 | Pure engine | Phase 1 |
| 3 | ORM models | Phase 1 |
| 4 | Exceptions + audit actions | Phase 1 |
| 5 | Services | Phases 2, 3, 4 |
| 6 | Config schema | Phase 1 |
| 7 | YAML config | Phase 6 |
| 8 | Posting integration | Phases 5, 7 |
| 9 | Tests | All phases |
| 10 | Module migration | Phase 8 |

Phases 2, 3, 4, and 6 can be parallelized after Phase 1.

---

## Verification

1. **Unit tests pass**: `python3 -m pytest tests/ -v --tb=short` (all existing + new tests)
2. **Architecture tests pass**: `python3 -m pytest tests/architecture/ -v --tb=short`
3. **Lint clean**: `make lint` reports no new errors
4. **Type check**: `make typecheck` passes on new files
5. **Round-trip test**: Create approval request → record decision → verify ORM persistence → verify DTO conversion
6. **Integration test**: AP invoice → submit → match → request approval → approve (with policy) → post → verify journal entry
7. **Auto-approve test**: Small AP invoice → auto-approved → post without human intervention
8. **Rejection test**: Reject approval → verify transition blocked → verify no journal entry

---

## Approval Invariants

| Rule | Name | Summary | Enforcement |
|------|------|---------|-------------|
| AL-1 | Lifecycle state machine | ApprovalRequest status follows defined transitions; terminal states are irreversible | Domain dict + service validation + DB trigger |
| AL-2 | Policy version snapshot | policy_version and policy_hash are snapshotted at request creation; enables deterministic replay | Service (create_request) + write-once ORM columns |
| AL-3 | Currency normalization | Threshold comparison uses a single declared policy_currency; no implicit FX conversion | Service validation + compiler enforcement |
| AL-4 | Executor thinness | WorkflowExecutor is a thin coordinator; all domain logic lives in engine/services | Architecture tests + code review |
| AL-5 | Policy drift protection | Resolution under downgraded policy is rejected; resolution under upgraded policy is logged | Service (record_decision) + audit event |
| AL-6 | Deterministic rule ordering | Rules evaluated by explicit `priority` field, not YAML position; unique priorities per policy | Compiler validation + engine sort |
| AL-7 | Decision uniqueness | Same actor cannot approve the same request twice | DB unique constraint `(request_id, actor_id)` + service check |
| AL-8 | Request tamper evidence | SHA-256 hash of canonical request fields computed at creation, verified on every load | Service (create_request, get_request) + ORM column |
| AL-9 | Role diversity | When `require_distinct_roles=True`, approvals count only from distinct `actor_role` values | Engine (evaluate_approval_decision) |
| AL-10 | Request idempotency | No duplicate pending approval requests for the same entity/transition | DB unique constraint + service check |
| AL-11 | Selector performance | Covering index on `(entity_type, entity_id, status, created_at)` for `get_pending_for_entity()` | DB index |

---

## Key Decisions

1. **Workflow types in kernel** -- Guards, Transitions, Workflows are cross-cutting domain concepts that belong in kernel/domain/
2. **Approval models in kernel** -- Approval is a cross-cutting concern used by all modules
3. **Pure engine in finance_engines/** -- Rule evaluation is pure computation, no I/O
4. **OrgHierarchyProvider as Protocol** -- Pluggable interface, default StaticRoleProvider ships first
5. **Approval gate before posting pipeline** -- The approval check happens at the module service level, not inside ModulePostingService. This keeps posting pipeline pure.
6. **Reuse GUARD_BLOCKED status** -- No new posting status needed; approval-blocked transitions return the existing `GUARD_BLOCKED`
7. **Amount thresholds use Decimal** -- Consistent with R16/R17 (never float for money)
8. **Guard expressions reuse guard_ast.py** -- No new expression language needed
9. **ApprovalPolicyRef with min_version** -- Typed reference prevents silent weakening of controls when policies evolve
10. **Three-layer lifecycle enforcement** -- Domain + service + DB trigger, mirroring the posting pipeline's immutability defense
11. **Policy snapshot on request creation** -- Mirrors R21 (reference snapshot determinism) for the ledger; approvals are governance events deserving the same determinism standard
12. **Explicit currency contract** -- Policies declare their threshold currency; callers normalize; engine never converts
13. **Deterministic rule ordering** -- Priority field, not YAML position; eliminates serializer/merge ambiguity
14. **Decision uniqueness at DB level** -- `UNIQUE(request_id, actor_id)` prevents governance failure of double-approval
15. **Request tamper evidence** -- SHA-256 hash verified on every load, mirroring R1/R2 pattern for events
16. **Role diversity as policy option** -- `require_distinct_roles` defaults to False; opt-in per rule, not blanket
17. **Escalation authority deferred** -- `escalate_to_roles` reserved for v2 once OrgHierarchyProvider is proven
