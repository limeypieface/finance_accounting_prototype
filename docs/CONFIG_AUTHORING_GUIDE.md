# Configuration Set Authoring Guide

How to create, modify, validate, and approve a configuration set for the finance kernel.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Directory Structure](#2-directory-structure)
3. [Creating a New Configuration Set](#3-creating-a-new-configuration-set)
4. [Fragment Reference](#4-fragment-reference)
5. [Guard Expression Language](#5-guard-expression-language)
6. [Validation Rules](#6-validation-rules)
7. [Lifecycle and Approval](#7-lifecycle-and-approval)
8. [Common Patterns](#8-common-patterns)
9. [Watch-Outs and Common Mistakes](#9-watch-outs-and-common-mistakes)
10. [Testing Your Config](#10-testing-your-config)
11. [Worked Example](#11-worked-example)

---

## 1. Overview

A **configuration set** is a collection of YAML fragments that defines how the accounting system behaves: which events trigger journal entries, which accounts are debited and credited, what validations run before posting, and what calculation engines are used.

### Key Principles

- **Declarative, not procedural.** Configuration says *what* to post, not *how*. No Python code changes are needed for business rule updates.
- **YAML is build/test tooling only.** At runtime the system uses a frozen, compiled `CompiledPolicyPack`. YAML files are never read at runtime.
- **Append-only versioning.** Each configuration set declares its predecessor. Published configs are never modified — they are superseded by new versions.
- **Role-based abstraction.** Policies reference semantic roles (e.g., `INVENTORY`, `ACCOUNTS_PAYABLE`), not account numbers. The chart of accounts maps roles to physical accounts.

### How Configuration Flows

```
YAML Fragments (you author these)
    |
    v
Assembler (composes fragments into one object)
    |
    v
Validator (checks business rules, expression safety)
    |
    v
Compiler (builds dispatch index, resolves engines, computes fingerprint)
    |
    v
CompiledPolicyPack (frozen, immutable — the ONLY runtime artifact)
```

All of this happens inside `get_active_config()`. You never call the assembler, validator, or compiler directly.

---

## 2. Directory Structure

Each configuration set is a directory under `finance_config/sets/`:

```
finance_config/sets/
  US-GAAP-2026-v1/              <-- one configuration set
    root.yaml                   REQUIRED  Identity, scope, capabilities
    chart_of_accounts.yaml      REQUIRED  Role-to-account bindings
    ledgers.yaml                optional  Ledger definitions
    engine_params.yaml          optional  Calculation engine parameters
    controls.yaml               optional  Global governance rules
    subledger_contracts.yaml    optional  Subledger ownership declarations
    APPROVED_FINGERPRINT        optional  Integrity pin (written by approval script)
    policies/                   REQUIRED  One YAML per business module
      inventory.yaml
      ap.yaml
      ar.yaml
      cash.yaml
      expense.yaml
      gl.yaml
      payroll.yaml
      procurement.yaml
      tax.yaml
      wip.yaml
      assets.yaml
      contracts.yaml
```

The assembler loads fragments in a fixed order: root first, then chart of accounts, ledgers, all policy files (sorted alphabetically), engine params, controls, subledger contracts.

---

## 3. Creating a New Configuration Set

Follow this sequence:

### Step 1: Create the directory

```bash
mkdir -p finance_config/sets/MY-CONFIG-v1/policies
```

### Step 2: Write root.yaml

Define identity, scope, and capabilities. Start with `status: draft`.

### Step 3: Write chart_of_accounts.yaml

Map every semantic role your policies will use to a physical account code and ledger.

### Step 4: Write ledgers.yaml

Define any subledgers (AP, AR, INVENTORY, etc.) and their required roles.

### Step 5: Write policy files

Create one YAML file per business module in the `policies/` subdirectory. Each file contains an array of policies under a `policies:` key.

### Step 6: Write engine_params.yaml (if needed)

Configure calculation engines referenced by your policies.

### Step 7: Write controls.yaml (if needed)

Add global governance rules that apply across all event types.

### Step 8: Validate locally

```bash
python -c "
from finance_config import get_active_config
from datetime import date
config = get_active_config('*', date(2026, 1, 1), config_dir=Path('finance_config/sets'))
print(f'Loaded: {config.config_id}, {len(config.policies)} policies')
"
```

If validation or compilation fails, you get an error with the exact problem.

### Step 9: Run tests

```bash
pytest tests/architecture/test_wiring_proof.py -v
```

### Step 10: Set status to `published` and approve

Update `status: published` in root.yaml, then:

```bash
python scripts/approve_config.py finance_config/sets/MY-CONFIG-v1
```

This writes the `APPROVED_FINGERPRINT` file. Commit both the YAML changes and the fingerprint file.

---

## 4. Fragment Reference

### 4.1 root.yaml

Defines identity, scope, feature gates, and precedence rules.

```yaml
config_id: US-GAAP-2026-v1          # Unique identifier (string, required)
version: 1                            # Version number (integer, required)
status: draft                         # Lifecycle status (required, see Section 7)
predecessor: null                     # config_id of previous version (string or null)

scope:
  legal_entity: '*'                   # '*' = all entities, or specific entity code
  jurisdiction: US                    # Country/region code
  regulatory_regime: GAAP             # GAAP | IFRS | DCAA | CAS
  currency: USD                       # Base currency (ISO 4217)
  effective_from: '2024-01-01'        # Start date (ISO 8601, required)
  effective_to: null                  # End date (null = no expiration)

capabilities:                         # Feature flags (boolean values)
  inventory: true
  ap: true
  ar: true
  cash: true
  expense: true
  gl: true
  payroll: true
  procurement: true
  tax: true
  wip: true
  assets: true
  contracts: true
  dcaa: false                         # Disabled capabilities filter out tagged policies
  ifrs: false
  multicurrency: false

precedence_rules:                     # How to resolve multiple matching policies
  - name: specificity_first
    description: More specific where-clause wins over generic
    rule_type: specificity            # specificity | priority | scope_depth
```

**Scope matching:** `get_active_config(legal_entity, as_of_date)` finds the config set whose `scope.legal_entity` matches (or is `*`) and whose `effective_from <= as_of_date <= effective_to`.

**Capabilities:** Policies tagged with `capability_tags: ["dcaa"]` are only admissible if `capabilities.dcaa: true`. This lets you share the same policy files across GAAP and DCAA configs by flipping flags.

### 4.2 chart_of_accounts.yaml

Maps semantic roles to physical accounts. Every role referenced by a policy's `ledger_effects` must have a binding here.

```yaml
role_bindings:
  - role: INVENTORY                   # Semantic role name (string, required)
    account_code: '1200'              # Physical account code (string, required)
    ledger: GL                        # Which ledger (string, default: GL)

  - role: STOCK_ON_HAND
    account_code: SL-1001
    ledger: INVENTORY                 # Subledger account
```

**Field reference:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `role` | string | yes | UPPER_SNAKE_CASE by convention |
| `account_code` | string | yes | GL: numeric (e.g., `'1200'`); subledger: `SL-` prefix |
| `ledger` | string | no | Default: `GL`. Must match a `ledger_id` in ledgers.yaml |

**Role naming conventions:**
- Assets: `CASH`, `INVENTORY`, `ACCOUNTS_RECEIVABLE`, `FIXED_ASSET`, `PREPAID_EXPENSE`
- Liabilities: `ACCOUNTS_PAYABLE`, `ACCRUED_LIABILITY`, `TAX_PAYABLE`
- Revenue: `REVENUE`, `INTEREST_INCOME`, `CONTRACT_REVENUE`
- Expense: `EXPENSE`, `SALARY_EXPENSE`, `DEPRECIATION_EXPENSE`
- Clearing: `GRNI`, `LABOR_CLEARING`, `EXPENSE_CLEARING`
- Variance: `INVENTORY_VARIANCE`, `PPV`, `OVERHEAD_VARIANCE`
- Subledger: `STOCK_ON_HAND`, `IN_TRANSIT`, `INVOICE`, `SUPPLIER_BALANCE`

### 4.3 ledgers.yaml

Defines ledger structures. The GL ledger is implicit — you only need to declare subledgers.

```yaml
ledgers:
  - ledger_id: GL                     # Unique ledger ID (string, required)
    name: General Ledger              # Display name (string, required)
    required_roles: []                # Roles that MUST be bound (list, optional)

  - ledger_id: INVENTORY
    name: Inventory Subledger
    required_roles:
      - STOCK_ON_HAND
      - IN_TRANSIT
      - SOLD
      - IN_PRODUCTION
      - SCRAPPED

  - ledger_id: AP
    name: Accounts Payable Subledger
    required_roles:
      - INVOICE
      - SUPPLIER_BALANCE
      - PAYMENT
```

### 4.4 policies/*.yaml

Each file contains policies for one business module. This is the most complex fragment.

```yaml
policies:
  - name: InventoryReceipt            # Unique name (PascalCase, required)
    version: 1                         # Version number (integer, default: 1)
    module: inventory                  # Owning module (string)
    description: Records receipt of inventory from supplier
    effective_from: '2024-01-01'       # When this policy activates (date, required)
    effective_to: null                 # When it expires (date or null)

    trigger:                           # WHEN this policy fires (required)
      event_type: inventory.receipt    # Canonical event name (required)
      schema_version: 1               # Event schema version (default: 1)
      where:                           # Optional filters (AND logic)
        - field: payload.has_variance
          value: true

    meaning:                           # WHAT this event means economically (required)
      economic_type: INVENTORY_INCREASE
      quantity_field: payload.quantity  # Payload field for quantity (optional)
      dimensions:                      # Analytics dimensions (optional)
        - org_unit
        - cost_center

    ledger_effects:                    # WHERE to post (required, at least one)
      - ledger: GL
        debit_role: INVENTORY
        credit_role: GRNI
      - ledger: INVENTORY              # Subledger posting
        debit_role: STOCK_ON_HAND
        credit_role: IN_TRANSIT

    guards:                            # Validation BEFORE posting (optional)
      - guard_type: reject             # reject | block | warn
        expression: payload.quantity <= 0
        reason_code: INVALID_QUANTITY
        message: Receipt quantity must be positive

    line_mappings:                     # HOW to construct journal lines (required)
      - role: INVENTORY
        side: debit
      - role: GRNI
        side: credit
      - role: STOCK_ON_HAND
        side: debit
        ledger: INVENTORY
      - role: IN_TRANSIT
        side: credit
        ledger: INVENTORY

    precedence:                        # Override rules (optional)
      mode: normal                     # normal | override
      priority: 0                      # Higher wins (integer)
      overrides: []                    # Policy names this one supersedes

    required_engines: []               # Engine names needed (list)
    engine_parameters_ref: null        # Key in engine_params.yaml (string)
    capability_tags: []                # Required capabilities (list)
```

#### 4.4.1 trigger

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `event_type` | string | yes | Dot-separated: `module.action` (e.g., `inventory.receipt`, `ap.invoice_received`) |
| `schema_version` | integer | no | Default: 1. Use for event schema evolution |
| `where` | list | no | Each entry: `{field: string, value: any}`. Multiple entries = AND logic |

**Where clause values:**
- String: `value: SALE` — exact match
- Boolean: `value: true` — boolean match
- Null: `value: null` or `value: None` — field must be absent/null
- Expression: `value: true` with `field: payload.quantity > 0` — expression evaluation

#### 4.4.2 meaning

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `economic_type` | string | yes | Business semantics (see taxonomy below) |
| `quantity_field` | string | no | Dot path into payload (e.g., `payload.quantity`, `payload.hours`) |
| `dimensions` | list | no | Analytics dimensions: `org_unit`, `cost_center`, `project`, `department`, `contract_number`, `jurisdiction`, etc. |

**Economic type taxonomy (partial):**

| Category | Types |
|----------|-------|
| Inventory | `INVENTORY_INCREASE`, `INVENTORY_DECREASE`, `INVENTORY_TRANSFER`, `INVENTORY_ADJUSTMENT`, `INVENTORY_REVALUATION`, `INVENTORY_TO_WIP` |
| Payables | `LIABILITY_INCREASE`, `LIABILITY_DECREASE`, `LIABILITY_SETTLEMENT` |
| Receivables | `REVENUE_RECOGNITION`, `REVENUE_REVERSAL` |
| Expense | `EXPENSE_RECOGNITION`, `EXPENSE_CAPITALIZATION`, `EXPENSE_SETTLEMENT` |
| Payroll | `PAYROLL_ACCRUAL`, `PAYROLL_DISBURSEMENT`, `LABOR_ACCRUAL`, `LABOR_ALLOCATION` |
| WIP | `WIP_MATERIAL_ISSUE`, `WIP_LABOR_CHARGE`, `WIP_OVERHEAD_APPLICATION`, `WIP_COMPLETION` |
| Assets | `FIXED_ASSET_INCREASE`, `FIXED_ASSET_DISPOSAL`, `DEPRECIATION_RECOGNITION` |
| Tax | `TAX_LIABILITY_INCREASE`, `TAX_LIABILITY_DECREASE`, `TAX_SETTLEMENT` |
| Contracts | `CONTRACT_COST_INCURRENCE`, `CONTRACT_BILLING`, `CONTRACT_INDIRECT_ALLOCATION` |
| FX | `FX_GAIN`, `FX_LOSS`, `FX_REVALUATION` |
| GL | `YEAR_END_CLOSE`, `INTERCOMPANY_TRANSFER`, `BUDGETARY_ENCUMBRANCE` |
| Cash | `BANK_INCREASE`, `BANK_DECREASE`, `BANK_TRANSFER`, `RECONCILIATION` |

#### 4.4.3 ledger_effects

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `ledger` | string | yes | Must match a `ledger_id` in ledgers.yaml |
| `debit_role` | string | yes | Semantic role to debit |
| `credit_role` | string | yes | Semantic role to credit |

Every ledger effect creates a balanced debit/credit pair. Most policies have at least a GL entry; complex ones also post to subledgers (AP, AR, INVENTORY, BANK, CONTRACT).

#### 4.4.4 guards

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `guard_type` | string | yes | `reject` = fail, `block` = require approval, `warn` = log only |
| `expression` | string | yes | Restricted expression (see Section 5) |
| `reason_code` | string | yes | UPPER_SNAKE_CASE machine code |
| `message` | string | yes | Human-readable explanation |

#### 4.4.5 line_mappings

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `role` | string | yes | Semantic role (must be in chart_of_accounts) |
| `side` | string | yes | `debit` or `credit` |
| `ledger` | string | no | Default: `GL` |
| `from_context` | string | no | Context variable for the amount |
| `foreach` | string | no | Payload array field to iterate over |

#### 4.4.6 precedence

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `mode` | string | yes | `normal` or `override` |
| `priority` | integer | no | Default: 0. Higher number wins |
| `overrides` | list | yes | Policy names this one replaces |

### 4.5 engine_params.yaml

```yaml
engines:
  - engine_name: variance              # Must match an ENGINE_CONTRACTS entry
    version_constraint: '1.*'          # Semver constraint
    parameters:
      tolerance_percent: 5.0
      tolerance_amount: 10.0

  - engine_name: allocation
    version_constraint: '1.*'
    parameters:
      method: proportional             # proportional | equal | custom
      rounding_method: largest_remainder

  - engine_name: matching
    version_constraint: '1.*'
    parameters:
      tolerance_percent: 5.0
      tolerance_amount: 10.0
      match_strategy: three_way        # two_way | three_way | four_way

  - engine_name: aging
    version_constraint: '1.*'
    parameters:
      buckets: [30, 60, 90, 120]       # Days for aging buckets

  - engine_name: tax
    version_constraint: '1.*'
    parameters:
      calculation_method: destination   # destination | origin
```

Parameters are validated against each engine's `parameter_schema` (defined in `finance_engines/contracts.py`). The compiler checks types, allowed values, and numeric ranges.

### 4.6 controls.yaml

Global governance rules applied before policy dispatch.

```yaml
controls:
  - name: positive_amount_required     # Unique name (required)
    applies_to: '*'                    # Event type pattern: '*' = all, 'payroll.*' = module
    action: reject                     # reject | block
    expression: payload.amount <= 0    # Restricted expression (see Section 5)
    reason_code: INVALID_AMOUNT        # UPPER_SNAKE_CASE
    message: Transaction amount must be positive
```

---

## 5. Guard Expression Language

Guards and controls use a **restricted expression language** that is validated as a safe AST subset. This prevents code injection while allowing useful business logic.

### Allowed

| Construct | Examples |
|-----------|---------|
| **Comparisons** | `payload.amount > 0`, `payload.hours <= 24`, `payload.status == "ACTIVE"`, `payload.type != "VOID"` |
| **Identity** | `payload.po_number is None`, `event.timestamp is not None` |
| **Membership** | `payload.category in ["A", "B", "C"]` |
| **Boolean logic** | `payload.x > 0 and payload.y > 0`, `not party.is_frozen` |
| **Arithmetic** | `payload.amount * 1.1 > payload.limit` |
| **Ternary** | `payload.ceiling if payload.ceiling else False` |
| **Functions** | `abs(payload.amount)`, `len(payload.items)`, `check_credit_limit(party, payload.amount)` |
| **Literals** | `True`, `False`, `None`, numbers, strings |

### Context Roots

Expressions can access these dot-prefixed objects:

| Root | Description |
|------|-------------|
| `payload` | Event payload fields (e.g., `payload.amount`, `payload.quantity`) |
| `party` | Supplier/customer attributes (e.g., `party.is_frozen`, `party.credit_limit`) |
| `contract` | Contract attributes (e.g., `contract.ceiling_fee`) |
| `event` | Event metadata (e.g., `event.timestamp`) |

### Forbidden

| Construct | Error |
|-----------|-------|
| Arbitrary function calls | `Disallowed function call: {name}` |
| Deep attribute chains (`a.b.c`) | `Disallowed attribute access` |
| Unknown variable names | `Disallowed name: {name}` |
| Lambda expressions | `Lambda expressions are not allowed` |
| Imports, eval, exec | `Disallowed AST node type` |

**Allowed functions:** `abs()`, `len()`, `check_credit_limit()`

### Examples from production policies

```yaml
# Reject negative amounts
expression: payload.amount <= 0

# Reject frozen suppliers
expression: party.is_frozen

# Block credit limit exceedance
expression: not check_credit_limit(party, payload.gross_amount)

# Reject excessive hours
expression: payload.hours > 24

# Conditional ceiling check
expression: payload.cumulative_fee > payload.ceiling_fee if payload.ceiling_fee else False

# Prevent self-transfer
expression: payload.from_bank_account_code == payload.to_bank_account_code

# Require contract ID
expression: payload.contract_id is not None
```

---

## 6. Validation Rules

The system runs two passes: **validation** (can produce warnings) and **compilation** (can produce fatal errors).

### 6.1 Validation Pass (validator.py)

| Check | Severity | Error message |
|-------|----------|---------------|
| Policy name+version uniqueness | ERROR | `Duplicate policy: {name}:v{version} appears more than once` |
| Guard expression syntax | ERROR | `Policy '{name}' guard: {ast_error} (expression: {expr})` |
| Control expression syntax | ERROR | `Control '{name}': {ast_error} (expression: {expr})` |
| GL roles have bindings | WARNING | `Policy '{name}' uses GL role '{role}' with no RoleBinding` |
| Capability tags declared | WARNING | `Policy '{name}' uses capability tag '{tag}' not declared in capabilities` |
| Capability coverage | WARNING | `Disabling capability '{cap}' would leave event_type '{et}' with no admissible policy` |
| Policies have ledger effects | ERROR | `Policy '{name}' has no ledger effects` |

If any ERROR-level check fails, `get_active_config()` raises `ValueError`.

### 6.2 Compilation Pass (compiler.py)

| Check | Category | Severity | Error message |
|-------|----------|----------|---------------|
| Guard AST | guard | error | `Invalid guard expression in policy '{name}': {msg}` |
| Dispatch ambiguity | dispatch | warning | `Potentially ambiguous dispatch for event_type '{et}': policies {names} share the same precedence` |
| Engine exists | engine | error | `Policy '{name}' requires engine '{engine}' but no contract exists` |
| Engine params ref | engine | error | `Policy '{name}' references engine params '{ref}' but no engine config exists` |
| Engine param type | engine_params | warning | `Engine '{name}' parameter '{param}' has type {actual}, expected {expected}` |
| Engine param enum | engine_params | warning | `Engine '{name}' parameter '{param}' value '{val}' not in allowed values` |
| Engine param range | engine_params | warning | `Engine '{name}' parameter '{param}' value {val} is below minimum {min}` |
| Unknown engine param | engine_params | warning | `Engine '{name}' config has unknown parameter '{param}'` |
| Role coverage | role | warning | `Policy '{name}' uses GL role '{role}' but no RoleBinding exists` |
| Capability tags | capability | warning | `Policy '{name}' uses capability tag '{tag}' not declared in capabilities` |
| Control AST | control | error | `Invalid control expression '{name}': {msg}` |

If any error-severity check fails, compilation raises `CompilationFailedError`.

### 6.3 Integrity Check (integrity.py)

After compilation, if an `APPROVED_FINGERPRINT` file exists, the compiled `canonical_fingerprint` must match. If not:

```
ConfigIntegrityError: Config integrity check failed for '{id}':
  pinned fingerprint {expected}... != compiled fingerprint {actual}...
```

---

## 7. Lifecycle and Approval

### 7.1 Status Transitions

```
DRAFT ──> REVIEWED ──> APPROVED ──> PUBLISHED ──> SUPERSEDED
  ^          |            |                          (terminal)
  |          v            v
  +-- (rejected) ---------+
```

| From | Allowed transitions |
|------|-------------------|
| DRAFT | REVIEWED |
| REVIEWED | APPROVED, DRAFT (if rejected) |
| APPROVED | PUBLISHED, DRAFT (if issues found) |
| PUBLISHED | SUPERSEDED |
| SUPERSEDED | (none — terminal) |

Set the status in `root.yaml`:

```yaml
status: draft       # While authoring
status: reviewed    # After peer review
status: approved    # After formal approval
status: published   # Live in production
status: superseded  # Replaced by newer version
```

`get_active_config()` prefers PUBLISHED status when multiple configs match.

### 7.2 Fingerprint Pinning

After setting `status: published`, run the approval script:

```bash
python scripts/approve_config.py finance_config/sets/MY-CONFIG-v1
```

This:
1. Assembles all fragments
2. Validates the config
3. Compiles to `CompiledPolicyPack`
4. Writes `pack.canonical_fingerprint` to `APPROVED_FINGERPRINT`

The fingerprint is a SHA-256 hash of the config's identity, scope, policy names, and role bindings. If anyone edits a YAML file without re-running the approval script, `get_active_config()` raises `ConfigIntegrityError`.

**Commit both** the YAML changes and the `APPROVED_FINGERPRINT` file.

### 7.3 Superseding a Config

To replace a published config:

1. Create a new directory (e.g., `US-GAAP-2026-v2/`)
2. Set `predecessor: US-GAAP-2026-v1` in the new root.yaml
3. Set `version: 2` and `status: draft`
4. Make your changes
5. Walk through the lifecycle: draft → reviewed → approved → published
6. Update the old config's status to `superseded`

---

## 8. Common Patterns

### 8.1 Multi-Ledger Posting

Most financial events post to both the GL and a subledger:

```yaml
ledger_effects:
  - ledger: GL
    debit_role: ACCOUNTS_PAYABLE
    credit_role: CASH
  - ledger: AP
    debit_role: SUPPLIER_BALANCE
    credit_role: PAYMENT

line_mappings:
  - role: ACCOUNTS_PAYABLE
    side: debit
  - role: CASH
    side: credit
  - role: SUPPLIER_BALANCE
    side: debit
    ledger: AP
  - role: PAYMENT
    side: credit
    ledger: AP
```

### 8.2 Where-Clause Specificity

When multiple policies handle the same `event_type`, use `where` clauses to route:

```yaml
# Generic case (no where clause)
- name: APInvoicePOMatched
  trigger:
    event_type: ap.invoice_received

# Specific case (where clause narrows the match)
- name: APInvoiceExpense
  trigger:
    event_type: ap.invoice_received
    where:
      - field: payload.po_number
        value: null              # Only matches when there's no PO
```

The system applies specificity-first resolution: a policy with a `where` clause wins over one without, for the same `event_type`.

### 8.3 Foreach Iteration

For events with line-item arrays (invoices, expense reports):

```yaml
line_mappings:
  - role: EXPENSE
    side: debit
    foreach: invoice_lines       # Creates one posting per line item
  - role: ACCOUNTS_PAYABLE
    side: credit
```

The `foreach` field names a payload array. The system iterates over it, creating separate journal lines per item.

### 8.4 From-Context Variables

When the posting amount differs from the event amount (e.g., computed values):

```yaml
line_mappings:
  - role: INVENTORY
    side: debit
    from_context: standard_total       # Amount from orchestrator computation
  - role: INVENTORY_VARIANCE
    side: debit
    from_context: variance_amount      # Variance amount
  - role: GRNI
    side: credit
```

Context variables are set by the posting orchestrator before journal line construction.

### 8.5 Precedence Overrides

Contract/DCAA policies override standard policies:

```yaml
- name: APInvoiceAllowable
  trigger:
    event_type: ap.invoice_received
    schema_version: 2
    where:
      - field: payload.allowability
        value: ALLOWABLE
  precedence:
    mode: override
    priority: 100                      # Higher than standard (0)
    overrides:
      - APInvoiceExpense
      - APInvoicePOMatched
  capability_tags:
    - dcaa                             # Only active when DCAA capability is enabled
```

### 8.6 Capability Gating

Tag policies with capabilities to enable/disable them per config:

```yaml
# In the policy
capability_tags:
  - dcaa

# In root.yaml
capabilities:
  dcaa: true    # This config enables DCAA policies
```

A policy is **admissible** only if ALL of its `capability_tags` are enabled. Policies with no tags are always admissible.

---

## 9. Watch-Outs and Common Mistakes

### 9.1 Dispatch Ambiguity

If two policies match the same `event_type` with the same `where` clause and same `priority`, the compiler emits a warning:

> Potentially ambiguous dispatch for event_type 'ap.invoice_received': policies [A, B] share the same precedence.

**Fix:** Give them different `where` clauses, different `priority` values, or non-overlapping `effective_from`/`effective_to` ranges.

### 9.2 Missing Role Bindings

If a policy references a GL role that has no binding in `chart_of_accounts.yaml`:

> Policy 'MyPolicy' uses GL role 'NEW_ROLE' with no RoleBinding

**Fix:** Add the role binding to `chart_of_accounts.yaml`.

### 9.3 Guard Expression Errors

Common mistakes in guard expressions:

| Mistake | Error |
|---------|-------|
| `payload.data.nested.field` | `Disallowed attribute access` — only one level of dot access |
| `some_function(x)` | `Disallowed function call` — only `abs`, `len`, `check_credit_limit` |
| `my_var > 5` | `Disallowed name: my_var` — only `payload`, `party`, `contract`, `event`, `True`, `False`, `None` |

### 9.4 Duplicate Policy Names

Each `name:version` combination must be unique across ALL policy files:

> Duplicate policy: MyPolicy:v1 appears more than once

Policy names are global — not scoped to their file.

### 9.5 Empty Ledger Effects

Every policy must have at least one ledger effect:

> Policy 'MyPolicy' has no ledger effects

### 9.6 Undeclared Capability Tags

If a policy uses a `capability_tags` value not in `root.yaml`'s `capabilities`:

> Policy 'MyPolicy' uses capability tag 'new_feature' not declared in capabilities

**Fix:** Add the capability to `root.yaml`.

### 9.7 Stale APPROVED_FINGERPRINT

If you edit any YAML file without re-running the approval script:

> Config integrity check failed for 'MY-CONFIG-v1': pinned fingerprint abc123... != compiled fingerprint def456...

**Fix:** Re-run `python scripts/approve_config.py finance_config/sets/MY-CONFIG-v1`.

### 9.8 Date Format

All dates must be ISO 8601 strings: `'2024-01-01'`. Using other formats causes parsing errors.

### 9.9 String Quoting for Account Codes

Account codes that look like numbers must be quoted in YAML:

```yaml
# WRONG — YAML parses 1200 as integer
account_code: 1200

# RIGHT — string preserved
account_code: '1200'
```

### 9.10 Engine Reference Errors

If a policy declares `required_engines: [myengine]` but no engine contract exists:

> Policy 'MyPolicy' requires engine 'myengine' but no contract exists

Engine contracts are defined in Python code (`finance_engines/contracts.py`), not in YAML. You cannot add new engines via configuration alone.

---

## 10. Testing Your Config

### Quick validation

```bash
python -c "
from pathlib import Path
from finance_config import get_active_config
from datetime import date
c = get_active_config('*', date(2026, 1, 1), config_dir=Path('finance_config/sets'))
print(f'{c.config_id}: {len(c.policies)} policies, {len(c.role_bindings)} role bindings')
print(f'Fingerprint: {c.canonical_fingerprint}')
"
```

### Architecture tests

```bash
pytest tests/architecture/test_wiring_proof.py -v
```

Tests that the config loads, compiles, has deterministic checksums, valid engine contracts, and complete role bindings.

### Full test suite

```bash
pytest tests/ -x --timeout=300
```

Many tests load the config via `get_active_config()`. If your config changes break policies, tests will fail.

### Approval

```bash
python scripts/approve_config.py finance_config/sets/MY-CONFIG-v1
```

If validation or compilation fails, the script prints the errors and exits non-zero.

---

## 11. Worked Example

Creating a minimal configuration set for a simple expense-tracking system.

### Directory

```bash
mkdir -p finance_config/sets/EXPENSE-ONLY-v1/policies
```

### root.yaml

```yaml
config_id: EXPENSE-ONLY-v1
version: 1
status: draft
predecessor: null

scope:
  legal_entity: '*'
  jurisdiction: US
  regulatory_regime: GAAP
  currency: USD
  effective_from: '2024-01-01'
  effective_to: null

capabilities:
  expense: true

precedence_rules:
  - name: specificity_first
    rule_type: specificity
```

### chart_of_accounts.yaml

```yaml
role_bindings:
  - role: EXPENSE
    account_code: '6000'
    ledger: GL
  - role: ACCOUNTS_PAYABLE
    account_code: '2000'
    ledger: GL
  - role: CASH
    account_code: '1000'
    ledger: GL
```

### ledgers.yaml

```yaml
ledgers:
  - ledger_id: GL
    name: General Ledger
    required_roles: []
```

### policies/expense.yaml

```yaml
policies:
  - name: ExpenseReportApproved
    version: 1
    module: expense
    description: Records an approved expense report
    effective_from: '2024-01-01'

    trigger:
      event_type: expense.report_approved

    meaning:
      economic_type: EXPENSE_RECOGNITION
      dimensions:
        - cost_center

    ledger_effects:
      - ledger: GL
        debit_role: EXPENSE
        credit_role: ACCOUNTS_PAYABLE

    guards:
      - guard_type: reject
        expression: payload.amount <= 0
        reason_code: INVALID_AMOUNT
        message: Expense amount must be positive

    line_mappings:
      - role: EXPENSE
        side: debit
      - role: ACCOUNTS_PAYABLE
        side: credit

  - name: ExpenseReimbursement
    version: 1
    module: expense
    description: Records payment of an approved expense
    effective_from: '2024-01-01'

    trigger:
      event_type: expense.reimbursement

    meaning:
      economic_type: LIABILITY_SETTLEMENT

    ledger_effects:
      - ledger: GL
        debit_role: ACCOUNTS_PAYABLE
        credit_role: CASH

    guards:
      - guard_type: reject
        expression: payload.amount <= 0
        reason_code: INVALID_AMOUNT
        message: Reimbursement amount must be positive

    line_mappings:
      - role: ACCOUNTS_PAYABLE
        side: debit
      - role: CASH
        side: credit
```

### Validate

```bash
python -c "
from pathlib import Path
from finance_config import get_active_config
from datetime import date
c = get_active_config('*', date(2024, 6, 1), config_dir=Path('finance_config/sets'))
print(f'{c.config_id}: {len(c.policies)} policies')
"
```

Expected output: `EXPENSE-ONLY-v1: 2 policies`

### Approve and publish

Update `status: published` in root.yaml, then:

```bash
python scripts/approve_config.py finance_config/sets/EXPENSE-ONLY-v1
```

Commit the YAML files and the generated `APPROVED_FINGERPRINT`.
