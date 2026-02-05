# RBAC Design: Config-Driven, Role-Holistic, Boundary-Enforced

**Status:** Design approved. Implementation not started.  
**Alignment:** Config-driven, holistic roles, boundary enforcement only. Kernel remains actor-agnostic.

---

## Executive position

Adopt **config-defined RBAC with holistic roles**, under three hard constraints:

1. **Kernel remains actor-agnostic** — No identity or role resolution inside posting/ledger logic.
2. **Authorization resolves outside posting/ledger logic** — Enforcement only at service/API boundaries.
3. **Permissions bind to economic actions, not UI verbs** — Audit- and policy-friendly authority model.

---

## What to preserve

### Config as single source of truth

Roles, permissions, hierarchy, and SoD live in **configuration** (same pattern as posting policies, approval policies, workflows, COA). Benefits:

- Auditability and versioning.
- Deterministic replay when RBAC state is snapshotted with transactions or approvals.

**Keep:** RBAC config versioned inside the same configuration set model.

### Holistic role abstraction

Named **business roles** (AP Clerk, Controller) are the right abstraction because:

- Approval policies already reference roles.
- Auditors reason in job functions, not permission strings.
- SoD frameworks (DCAA, SOX) operate on functional roles.

**Avoid:** Flat permission assignments per user; that breaks explainability and SoD reasoning.

### Boundary enforcement only

Enforce at **five** boundaries (do not push authorization into kernel posting or ledger logic):

1. Approval execution.
2. Workflow / transition execution.
3. Sensitive reporting reads.
4. **Data export** — CSV exports, API bulk pulls, data lake sync (e.g. `reporting.export.bulk`, `pii.export`, `gl.export.full`). Prevents sensitive data exfiltration.
5. **Configuration mutation** — Role definitions, SoD matrices, permission taxonomy, assignments (e.g. `rbac.role.define`, `rbac.sod.modify`, `rbac.assignment.approve`). Prevents RBAC from being self-modifiable by privileged users.

**Do not** push authorization into kernel posting or ledger mutation logic. That would contaminate financial determinism.

---

## Structural requirements

### 1. Authority role vs assigned roles — and activation semantics

Today `actor_role` is passed into approvals, which risks self-declared escalation and ambiguity when users hold multiple roles.

Refactor into:

- **Assigned roles:** All roles the actor possesses (from IdP or internal store). **Default session role set ≠ active authority role.**
- **Authority role:** The role **under which** the actor is executing this action. The actor must **select or assert** authority role per action; the system validates eligibility.

Every approval and posting check must validate:

- Authority role ∈ assigned roles.
- Authority role grants the required permission.
- Authority role does not violate SoD relative to the transaction lifecycle.

This preserves explainability (e.g. “Actor approved **as AP Manager**”), prevents implicit escalation, and supports dual-hat users (e.g. Controller + AP Manager) by requiring explicit “acting as” per action.

**Config constraints:**

- `authority_role_required: true | false` — When true, every action must declare an authority role; no default escalation.
- `multi_role_actions_allowed: false` — A single action is executed under one authority role only (no “combined” role for one operation).

### 2. Permissions = economic verbs only (taxonomy governance)

Do **not** use UI-shaped permissions (e.g. `ap.invoice.screen.open`, `button.submit`).

Permissions must map to **financial or operational authority**. **Naming invariant:** `<domain>.<object>.<authority>`.

| Good | Avoid |
|------|--------|
| `ap.invoice.enter` | `ap.invoice.screen.open` |
| `ap.invoice.approve` | `button.approve` |
| `ap.payment.release` | `manage`, `edit`, `access` (generic verbs) |
| `period.close` | — |
| `journal.post.manual` | — |
| `ar.credit_memo.issue` | — |
| `gl.journal.post` | — |

**Require:** Every permission must have an economic or compliance authority mapping. **Disallow:** UI verbs and generic verbs. **Config compiler validation:** Every permission in the taxonomy must be assigned to ≥1 role (no permission orphaning — prevents dead policy paths and approval routing failures).

This enables direct audit mapping, policy simulation, and SoD analysis across the lifecycle.

### 3. SoD in three layers

SoD must model **lifecycle conflicts**, not only role pairs.

#### Layer 1: Role conflict SoD

Classic incompatible job functions (e.g. same person cannot be both AP clerk and AP manager in a way that violates policy).

#### Layer 2: Permission conflict SoD

Incompatible permissions even across roles, e.g.:

- `ap.invoice.approve` vs `ap.payment.release`

#### Layer 3: Transaction lifecycle SoD

Same actor cannot perform **sequential critical actions on the same economic object**, e.g.:

- Enter invoice → Approve invoice → Release payment

Even if role assignment would allow, lifecycle SoD should block or flag. Essential for DCAA / SOX traceability.

**Lifecycle SoD persistence:** To enforce this, the system must **track per-object actor involvement**. Without persistence, lifecycle SoD is unenforceable. Example ledger extension (outside kernel, e.g. in services or a dedicated audit store):

```
economic_object_actor_log:
  object_id
  object_type
  actor_id
  authority_role
  action
  timestamp
```

The lifecycle SoD validator **queries this log** before allowing a sequential authority action on the same object. Implement **lifecycle actor log before** adding posting/transition enforcement so the log exists when guards run.

#### SoD conflict severity

Not all conflicts are equal. Classify in config:

- **hard_block** — Action denied (e.g. defense contractors).
- **soft_warn** — Allowed with audit warning (e.g. small firms that accept overlap).

```yaml
sod_conflicts:
  hard_block:
    - [ap.invoice.approve, ap.payment.release]
  soft_warn:
    - [ap_clerk, ap_manager]
```

### 4. Temporal RBAC snapshotting

Authorization cannot be evaluated only at runtime. **Authority state must be frozen at decision time** for:

- Approval events
- Period close
- Manual journal posting

Store with the event (or approval record):

- `actor_id`
- `authority_role`
- `assigned_roles_hash` (or snapshot of assigned roles)
- `rbac_config_version`
- **`authority_context`** — Scoped authority for audit reconstruction: e.g. `legal_entity`, `business_unit`, `contract_id` (so “acting as AP Manager for entity X” is reconstructable).

This ensures later audits can reconstruct whether authority was valid at the time and within what scope.

### 5. Config scoping

- **Core permissions:** Global (permission taxonomy).
- **Role definitions:** Tenant- or config-set–scoped.
- **SoD rules:** Tenant- or config-set–scoped.
- **Versioning:** With config sets so defense contractors (or others) can impose stricter SoD without code change.

### 6. Resource scoping (horizontal overreach prevention)

The permission model must support **resource scoping**, not only action. Otherwise a user with `ap.invoice.approve` could approve invoices across the entire enterprise.

**Optional dimension on permissions or role bindings:**

- **Dimensions:** legal_entity, business_unit, program, facility, contract (etc.).
- **Constraint:** e.g. `actor.assigned_entities` — actor may only act within entities (or BUs, programs) to which they are assigned.

Example:

```yaml
permission_scope:
  dimension: legal_entity
  constraint: actor.assigned_entities
```

Enforcement: authorization service resolves actor’s assigned scope and checks that the resource (invoice’s entity, contract’s program) is within that scope.

### 7. Policy override framework

Approvals already support policy routing. RBAC must support **temporary or contextual overrides** for exceptional cases (emergency payment release, disaster recovery posting, wartime contracting surge).

**Override construct:**

- Time-bound (expiry).
- Dual-approved (or similar control).
- Separately audited (override grant and override use are distinct audit events).

Example:

```yaml
override_roles:
  emergency_controller:
    permissions: [ap.payment.release]
    expiry: 24h
    requires_dual_approval: true
```

Overrides must **not** retroactively invalidate normal RBAC; they are a separate, audited path.

### 8. RBAC config migration governance

RBAC config is versioned; **migration semantics** must be explicit:

- **RBAC changes cannot retroactively invalidate historical approvals.** Past decisions were valid under the config at that time.
- **Config effective dates must be explicit** so replay and audit use the correct historical version.
- **Replay must use historical RBAC version** (e.g. from snapshot on the event).

**Config metadata:**

```yaml
rbac_config:
  version: v3.2
  effective_from: 2026-01-01
  supersedes: v3.1
```

---

## Enforcement architecture

### Actor resolution layer

Resolves:

```
actor_id → assigned_roles[]
```

Sources: IdP claims, internal role-assignment tables, or hybrid sync. **Kernel never queries this.**

### Authorization service

Evaluates:

```
(actor_id, authority_role, permission, resource_context)
```

Checks:

1. Authority role ∈ assigned roles.
2. Authority role grants permission (including hierarchy).
3. Resource scope: resource (entity, BU, contract) within actor’s assigned scope when permission_scope is defined.
4. SoD: role, permission, and lifecycle conflicts (including severity: hard_block vs soft_warn).
5. Time-bound: use RBAC config version and effective_from for the decision time; no retroactive invalidation.

Returns: allow / deny + audit reason.

### Approval integration

Approval engine must validate:

- Authority role matches the approval rule’s required role.
- Authority role has the approval permission.
- Actor’s lifecycle involvement on this object does not violate SoD.

Do **not** rely solely on role name matching.

---

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| **Role explosion** | Constrain role taxonomy to job functions, not micro-duties. |
| **Permission drift** | Permission namespace governance; `<domain>.<object>.<authority>`; disallow UI/generic verbs. |
| **Permission orphaning** | Config compiler: every permission must map to ≥1 role. |
| **Runtime-only authorization** | Snapshot authority metadata with economic events. |
| **Kernel contamination** | Enforce only at service boundaries; no RBAC inside posting/ledger. |
| **Role inheritance loops** | DAG validation at config load; enforce `inheritance_depth_limit`. |
| **SoD combinatorial explosion** | Provide SoD **templates by domain** (AP baseline, AR baseline, GL baseline) so tenants extend rather than define from scratch. |

---

## Recommended config shape

```yaml
rbac_config:
  version: v3.2
  effective_from: 2026-01-01
  supersedes: v3.1

authority_rules:
  authority_role_required: true
  multi_role_actions_allowed: false

roles:
  ap_clerk:
    permissions:
      - ap.invoice.enter
      - ap.invoice.view
      - ap.payment.enter

  ap_manager:
    inherits: [ap_clerk]
    permissions:
      - ap.invoice.approve
      - ap.payment.approve

  controller:
    permissions:
      - period.close
      - journal.post.manual
      - reporting.sensitive
      - reporting.export.bulk
      - gl.export.full

  rbac_admin:
    permissions:
      - rbac.role.define
      - rbac.sod.modify
      - rbac.assignment.approve

segregation_of_duties:
  role_conflicts:
    - [ap_clerk, ap_manager]

  permission_conflicts:
    hard_block:
      - [ap.invoice.approve, ap.payment.release]
    soft_warn:
      - [ap_clerk, ap_manager]

  lifecycle_conflicts:
    ap_invoice:
      - [enter, approve]
      - [approve, pay]

override_roles:
  emergency_controller:
    permissions: [ap.payment.release]
    expiry: 24h
    requires_dual_approval: true

hierarchy_rules:
  inheritance_depth_limit: 2
```

**Export and admin permissions (examples):** `reporting.export.bulk`, `pii.export`, `gl.export.full` for data export boundary; `rbac.role.define`, `rbac.sod.modify`, `rbac.assignment.approve` for configuration mutation boundary.

---

## Implementation sequence

Order matters: do **lifecycle logging before enforcement** so the actor log exists when guards run.

1. **Permission taxonomy governance** — Define `<domain>.<object>.<authority>`; economic/compliance mapping only; compiler rule: every permission ≥1 role.
2. **RBAC config schema + compiler validation** — Schema, loader, DAG validation for hierarchy, effective_from/supersedes, permission-orphan check.
3. **Role hierarchy + SoD validation engine** — Resolve inheritance; evaluate role/permission/lifecycle SoD; severity (hard_block vs soft_warn).
4. **Authorization service** — Evaluates (actor_id, authority_role, permission, resource_context); scope-bound and time-bound.
5. **Approval integration** — Authority role ∈ assigned roles; permission grant; lifecycle SoD check.
6. **Lifecycle actor log** — Persist `economic_object_actor_log` (object_id, object_type, actor_id, authority_role, action, timestamp). Build this **before** posting/transition guards.
7. **Posting / transition guards** — At service boundaries; call authorization service; enforce authority_role_required.
8. **Snapshot embedding** — Add authority snapshot (including authority_context) to approval/post/close events.
9. **Reporting + export gating** — Enforce `reporting.export.bulk`, `pii.export`, `gl.export.full` at export boundary.
10. **Config mutation gating** — Enforce `rbac.role.define`, `rbac.sod.modify`, `rbac.assignment.approve` for RBAC admin actions.

**Do not start with UI gating.** Start with approval and posting authority.

---

## Seven design rules (invariants)

1. **Kernel remains identity-agnostic** — No actor/role resolution or RBAC logic in kernel.
2. **Permissions bind to economic authority** — No UI verbs; `<domain>.<object>.<authority>`; audit- and policy-friendly.
3. **Authority role is explicit per action** — Caller declares “acting as” role; validated against assigned roles and permission; no implicit escalation.
4. **SoD spans role, permission, and lifecycle** — Three layers; lifecycle SoD on same economic object; severity (hard_block / soft_warn).
5. **Authority state is snapshotted with events** — Reconstructable at audit time; include authority_context (scope).
6. **Authority is scope-bound** — Permissions may be constrained by entity/program/contract context; horizontal overreach prevented via resource scoping.
7. **Authority is time-bound** — Evaluated against historical config and assignment state; replay uses historical RBAC version; no retroactive invalidation of past approvals.

These preserve financial determinism, audit defensibility, and compliance alignment while keeping authorization evolvable through configuration.

---

## Final assessment

With the refinements above, the design is **implementation-ready** and **enterprise-grade**.

**Strengths:**

- Clean kernel isolation; no identity or RBAC in posting/ledger.
- Config governance alignment (versioning, effective dates, migration rules).
- Audit defensibility (snapshot, authority_context, lifecycle actor log, overrides separately audited).
- Compliance compatibility (SoD severity, scope-bound authority, DCAA/SOX-friendly).
- Deterministic replay preserved (historical RBAC version, no retroactive invalidation).

**Additions captured before build:**

- Authority activation semantics (assert per action; authority_role_required; multi_role_actions_allowed).
- Resource scoping (legal_entity, BU, program, facility, contract; horizontal overreach prevention).
- Override framework (time-bound, dual-approved, separately audited).
- Lifecycle SoD persistence (economic_object_actor_log; validator queries before allowing sequential actions).
- RBAC config migration governance (effective_from, supersedes; replay uses historical version).
- Export and config-mutation enforcement boundaries (five boundaries total).
- Permission taxonomy governance (naming invariant; no orphaning).
- SoD conflict severity (hard_block vs soft_warn).
- Snapshot payload completeness (authority_context).
- Implementation risks and mitigations (inheritance loops, orphaning, SoD explosion; templates by domain).
- Implementation sequence refined (lifecycle log before enforcement; 10 steps).
- Two additional invariants (scope-bound, time-bound).

The model remains consistent with the event-sourced financial architecture and is regulator-defensible.
