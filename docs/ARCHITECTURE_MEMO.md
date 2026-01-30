# Architecture Memo: Event-Sourced Double-Entry Accounting System

**Date:** January 2026
**Status:** Working Draft

---

## 1. Executive Summary

### What We Built

We built an event-sourced, append-only, double-entry accounting system from first principles. The system accepts business events — a purchase order receipt, a payroll run, an invoice payment — and produces immutable journal entries that comply with generally accepted accounting principles. Every financial truth in the system is a journal line. There are no stored balances, no running totals, no mutable state. Trial balances, financial statements, and audit trails are all derived by reading the journal forward.

The system is designed for organizations that require auditable, deterministic, regulation-compliant financial records — from commercial enterprises operating under US GAAP to defense contractors subject to DCAA cost accounting standards and FAR/DFARS compliance.

### Why We Built It This Way

Traditional ERP accounting systems embed posting rules in application code, scatter business logic across stored procedures, and rely on mutable ledger balances that drift over time. When an auditor asks "why does this number exist?", the answer requires archeology across multiple systems, versions, and manual reconciliations.

We took a different approach. Every business event enters the system exactly once, is interpreted by a declarative policy, and produces journal entries that are cryptographically sealed and immutable. The system records not just *what* happened, but *why* — the policy that was in force, the configuration version that governed the posting, and the complete decision trail that led from event to journal entry. This decision journal is persisted on every posting and can be queried after the fact by auditors, compliance teams, or AI systems seeking to understand the financial narrative.

### Key Learnings

**Immutability simplifies everything.** When records cannot be changed, there are no race conditions on balances, no "last write wins" bugs, no reconciliation drift. Corrections are explicit reversal entries that tell their own story.

**Declarative policy beats imperative code.** Moving posting rules into YAML policy definitions — rather than embedding them in code — means accountants and auditors can review, version, and approve the rules that govern financial recording without reading Python.

**The decision journal is the audit trail.** Structured logs captured during every posting are persisted alongside the outcome. This means any journal entry can be traced back to the exact policy, configuration version, role resolution, balance check, and sequence allocation that produced it. An LLM reading this trail can explain in plain language why a specific dollar amount landed in a specific account.

**Pure functions and clock injection make replay deterministic.** Because the domain core has zero side effects and all timestamps are injected, the same event processed with the same configuration will always produce the same journal entry — a property that underpins audit replay and regulatory compliance.

---

## 2. Architecture Overview

### The Five-Layer Stack

The system is organized into five layers with strict dependency rules. Each layer may only import from layers below it, never above. These boundaries are enforced by automated architecture tests that run on every build.

```
finance_modules/      Thin ERP modules (AP, AR, Inventory, Payroll, etc.)
    |                 Declarative: profiles, workflows, config schemas
    v
finance_services/     Stateful orchestration over engines + kernel
    |
    v
finance_engines/      Pure calculation engines (variance, allocation, tax)
    |                 May ONLY import finance_kernel/domain/values
    v
finance_config/       YAML-driven configuration, single entrypoint
    |
    v
finance_kernel/       Core: domain, services, models, db, selectors
```

**Who:** The kernel is the foundational truth layer — it owns journal entries, events, audit trails, and immutability enforcement. Engines perform pure mathematical computations (allocation, tax, variance analysis) and are invoked at runtime through a central `EngineDispatcher` that reads engine requirements from compiled policies. Configuration defines the accounting policies, chart of accounts, and governance controls for a specific organization. Services orchestrate stateful operations that span engines and kernel, coordinated by a `PostingOrchestrator` that owns the lifecycle of all kernel services. Modules represent the ERP-level business processes (accounts payable, inventory, payroll) that translate business activities into accounting events.

**What:** The system processes business events into immutable journal entries through a policy-driven interpretation pipeline. Events are matched to accounting policies, policies define which accounts to debit and credit using semantic roles, and those roles are resolved to specific chart-of-accounts codes at posting time. When a policy declares engine dependencies (variance, allocation, tax), the `EngineDispatcher` invokes those engines with configuration-driven parameters before intent construction, and every invocation produces a traced audit record.

**When:** Configuration is assembled and compiled at build time from YAML fragments. At runtime, the sole entrypoint `get_active_config()` returns a frozen, validated `CompiledPolicyPack`. The pack's engine contracts, resolved parameters, and policy-level engine bindings are consumed by the `EngineDispatcher` at posting time. Posting happens within a single database transaction — either all journal lines for an event are committed, or none are.

**Where:** The system runs against PostgreSQL 15+, which provides the transaction isolation, row-level locking, and trigger-based immutability enforcement the architecture requires.

**Why:** Audit compliance, regulatory determinism, and the ability to answer "why does this number exist?" from the data alone — without relying on tribal knowledge, manual reconciliation, or reading application source code.

### Why These Properties Matter

**Append-only immutability** means the ledger is a sealed record. Once a journal entry is posted, no code path — not ORM, not raw SQL, not direct database access — can modify it. This is enforced at three levels: ORM event listeners, PostgreSQL triggers, and session-level guards. An auditor can trust that the journal they see today is the same journal that existed at close-of-period.

**Event sourcing** means the complete history is preserved. There is no "current balance" that overwrites yesterday's balance. Trial balances are computed by reading the journal forward, which means the system can produce a balance as of any date without maintaining snapshot tables.

**Declarative policy** means the rules are reviewable artifacts. An accounting policy that says "when we receive inventory, debit account 1200 and credit account 2100" is expressed in YAML, not buried in a switch statement. Policy changes follow a governed lifecycle (Draft → Reviewed → Approved → Published → Superseded) with cryptographic checksums.

**Deterministic replay** means the same inputs always produce the same outputs. Every journal entry records the exact versions of the chart of accounts, dimension schema, rounding policy, and currency registry that were in force at posting time. Given those versions and the original event, the posting can be mechanically reproduced.

---

## 3. The Finance Kernel

The kernel is the innermost layer of the system. It owns the foundational primitives — journal entries, events, audit trails, economic links, immutability enforcement — and exposes them through pure domain logic, ORM models, stateful services, and read-only selectors.

### 3.1 Domain Layer (`finance_kernel/domain/`)

The domain layer is pure logic with zero I/O. It cannot import from the database, services, or selectors packages. Every function in this layer is a deterministic transformation from inputs to outputs.

**Accounting Policy (`accounting_policy.py`).** Defines the declarative structure of an accounting policy: a trigger (which event types it matches), a meaning (what economic reality it represents), ledger effects (which accounts to debit and credit), and guards (conditions that reject or block events). Policies use semantic account *roles* — not chart-of-accounts codes — so the same policy can serve different organizations with different account numbering.

**Policy Compiler (`policy_compiler.py`).** Compiles raw policy definitions into executable form. Validates guard expressions against a restricted AST (no arbitrary code execution), checks for ambiguous dispatch (two policies matching the same event), and produces a frozen policy pack.

**Policy Selector (`policy_selector.py`).** Given an event, selects exactly one matching policy using where-clause dispatch. If zero policies match, the event is rejected. If multiple policies match, precedence rules (specificity, priority, scope depth) resolve the ambiguity. Exactly one policy per event is an invariant (P1).

**Policy Authority (`policy_authority.py`).** Governs policy admissibility — checks effective date ranges, ensures the policy is from a published configuration, and enforces precedence rules.

**Meaning Builder (`meaning_builder.py`).** Extracts economic meaning from a business event using the selected policy's meaning definition. Produces a structured representation of what the event *means* economically (inventory increase, revenue recognition, expense accrual).

**Accounting Intent (`accounting_intent.py`).** The intermediate representation between policy interpretation and journal writing. An AccountingIntent expresses the desired posting using account *roles* (e.g., "debit INVENTORY, credit GRNI") rather than specific account codes. Role resolution to COA codes happens at posting time.

**Ledger Registry (`ledger_registry.py`).** Maps semantic account roles to actual chart-of-accounts codes. Each role resolves to exactly one account per ledger (invariant L1). Role bindings are effective-dated and versioned.

**Economic Link (`economic_link.py`).** Models first-class relationships between financial artifacts. A purchase order is FULFILLED_BY a receipt; a receipt is PAID_BY a payment; an entry is REVERSED_BY a correction. These "why pointers" let auditors trace the complete lifecycle of any business transaction across documents and time.

**Valuation (`valuation.py`).** Pure valuation functions for inventory costing (FIFO, LIFO, weighted average, standard cost) and cost-layer management. No I/O — these are mathematical functions over cost lot data.

### 3.2 Models Layer (`finance_kernel/models/`)

The models layer defines SQLAlchemy ORM models that map to PostgreSQL tables. Each model declares its immutability constraints.

**JournalEntry.** The primary financial record. Each entry has a source event ID, an idempotency key (unique constraint), a monotonic sequence number, a status (DRAFT/POSTED/REVERSED), and R21 snapshot columns recording the exact configuration versions in force at posting time. Once posted, journal entries cannot be modified — enforced by ORM listeners and 26 PostgreSQL triggers.

**JournalLine.** Individual debit or credit within a journal entry. Records the account ID, side (debit/credit), amount (Numeric(38,9), always positive, never float), currency (ISO 4217), dimensions, and whether the line is a rounding adjustment. Lines are immutable once their parent entry is posted.

**Event.** The canonical incoming business event. Records the event type, payload (JSON), payload hash (SHA-256), actor, producer, occurred-at timestamp, and effective date. Events are append-only — they can never be modified or deleted.

**AuditEvent.** The tamper-evident audit log. Each audit event has a sequence number, entity reference, action, payload, and a cryptographic hash chain (`hash = H(payload_hash + prev_hash)`). This chain makes retroactive modification detectable.

**InterpretationOutcome.** Records the terminal state of every event interpretation: POSTED (journal entries written), BLOCKED (valid but cannot process yet), REJECTED (invalid economic reality), PROVISIONAL (awaiting confirmation), or NON_POSTING (valid, no financial effect). Exactly one outcome per event (invariant P15). The `decision_log` JSON column stores the complete structured decision journal captured during the posting pipeline.

**EconomicLinkModel.** Stores immutable relationships between financial artifacts. Each link records its type (FULFILLED_BY, PAID_BY, REVERSED_BY, etc.), parent and child artifact references, and the creating event ID.

**Account.** Chart of accounts with hierarchical structure (parent-child), account type classification, normal balance side, and structural immutability when referenced by journal lines.

**FiscalPeriod.** Period lifecycle management with status (OPEN/CLOSED) and adjustment policy. No posting to closed periods (invariant R12).

**Party.** Customer, supplier, and employee records with credit limits and blocked-status enforcement.

**Contract.** Government contract models with CLIN structure, billing types (CPFF, T&M, FFP), ceiling tracking, and DCAA compliance metadata.

### 3.3 Services Layer (`finance_kernel/services/`)

Services are the imperative shell — they perform I/O, own transaction boundaries, and orchestrate the pure domain logic.

**PostingOrchestrator.** The central factory for all kernel services. Every kernel service is created once by the orchestrator and injected into consumers — no service may create other services internally. This eliminates duplicate instances (e.g., multiple SequenceService objects competing for sequence numbers), enables test double injection, and provides a single point of lifecycle control. The orchestrator accepts a database session, a `CompiledPolicyPack`, a `RoleResolver`, and an optional clock.

**EngineDispatcher.** The runtime engine dispatch layer. When a policy declares `required_engines` (e.g., `["variance", "allocation"]`), the dispatcher reads the policy's `engine_parameters_ref`, looks up `resolved_engine_params` from the compiled pack, validates inputs against the engine contract's parameter schema, invokes the registered engine invoker, and collects `EngineTraceRecord` entries. The dispatcher is wired into InterpretationCoordinator — engine invocation happens after policy selection and meaning building, but before intent construction. Engine outputs are merged into the event payload for downstream line mapping. If no engines are required, the dispatcher is a no-op.

**InterpretationCoordinator.** The primary posting pipeline. Accepts a business event, selects a policy, extracts meaning, dispatches required engines via EngineDispatcher, builds an accounting intent using roles, writes journal entries via JournalWriter, records the outcome, and persists the decision journal. The coordinator installs a LogCapture handler internally so every structured log emitted during the pipeline is automatically saved to `InterpretationOutcome.decision_log`.

**JournalWriter.** Resolves account roles to COA codes (invariant L1), validates balance per currency (debits = credits), allocates monotonic sequence numbers, creates journal lines, verifies R21 reference snapshot invariants (rejecting stale snapshots), and enforces subledger-to-GL reconciliation contracts when `enforce_on_post` is set. Multi-ledger postings from a single accounting intent are atomic (invariant P11).

**OutcomeRecorder.** Records interpretation outcomes as durable financial cases. Enforces one outcome per event (invariant P15) via unique constraint. Every posting attempt — success or failure — produces a persistent outcome record with failure context (failure type, failure message, engine trace references, payload fingerprint). Status transitions follow an explicit state machine: PENDING may become POSTED or FAILED; FAILED may become RETRYING or ABANDONED; RETRYING may become POSTED or FAILED; POSTED and ABANDONED are terminal. Failed outcomes are surfaced through a work queue model for human review and retry.

**IngestorService.** Validates incoming events: payload hash verification (invariant R2), duplicate detection via idempotency key, and protocol violation detection (same event ID with different payload).

**SequenceService.** Allocates strictly monotonic sequence numbers via a locked counter row. The pattern `MAX(seq)+1` is forbidden (invariant R9) because it is a race condition under concurrent load. The service uses `SELECT ... FOR UPDATE` on a dedicated counter row. Created once by PostingOrchestrator and shared across all consumers.

**AuditorService.** Maintains the cryptographic hash chain for audit events (`hash = H(payload_hash + prev_hash)`, invariant R11). Validates chain integrity on demand.

**PeriodService.** Manages fiscal period lifecycle. Enforces no-posting-to-closed-periods (R12) and adjustment policy (R13). At period close, validates subledger-to-GL reconciliation for all control contracts with `enforce_on_close` set. Period close is serialized via row-level locking.

**LinkGraphService.** Persists economic links, detects cycles (invariant L3 — acyclic enforcement per link type), and provides graph traversal queries for lifecycle tracing. Cycle detection walks from child back to parent via the same link type before allowing a new link.

**Mandatory Guards.** Three guard layers ensure no posting bypasses authority validation:

1. **PolicyAuthority is required.** MeaningBuilder requires a `PolicyAuthority` instance at construction — there is no code path that skips module authorization or economic type posting constraint validation.
2. **Compilation receipt is required.** PolicySelector.register() requires a `CompilationReceipt` produced by PolicyCompiler. Uncompiled policies cannot enter the dispatch index.
3. **Actor validation is required.** ModulePostingService.post_event() verifies that the `actor_id` references a valid, active party before processing. Frozen or nonexistent actors are rejected.

### 3.4 Selectors Layer (`finance_kernel/selectors/`)

Selectors are read-only query services that assemble derived views from existing data without creating new persistent state.

**TraceSelector.** The trace bundle assembler. Given an event ID or journal entry ID, it reconstructs the complete lifecycle: the source event, journal entries with lines and account codes, interpretation outcome with decision journal, R21 reproducibility snapshot, economic links, audit trail, structured log timeline, conflict/protocol violation history, and integrity verification (payload hash, balance, audit chain). The result is a `TraceBundle` — a frozen dataclass containing everything an auditor needs to understand a single posting.

**LedgerSelector.** Computes trial balances, account balances, and ledger summaries by reading journal lines forward. No stored balances — everything is derived.

### 3.5 Database Layer (`finance_kernel/db/`)

**Engine Management (`engine.py`).** PostgreSQL connection pooling, session factory, and transactional scope management.

**Immutability Enforcement (`immutability.py`).** ORM event listeners (`before_update`, `before_delete`, `before_flush`) that raise `ImmutabilityViolationError` when code attempts to modify protected records.

**PostgreSQL Triggers (`triggers.py`, `sql/`).** Twenty-six database-level triggers across eleven SQL files that enforce immutability at the storage layer. These catch raw SQL updates, bulk modifications, and direct psql access that bypasses the ORM. Both ORM and database triggers must be circumvented to modify a protected record — defense in depth.

---

## 4. Finance Modules

Modules are thin ERP-level wrappers that translate business processes into accounting events. Each module follows a uniform structure: profiles (declarative accounting policies), config (account mappings and module settings), service (stateful business operations), workflows (multi-step business processes), and models (module-specific data structures).

### 4.1 General Ledger (GL)

The General Ledger module handles core GL operations that don't belong to a specific subledger: manual journal entries, period-end closing entries, intercompany transactions, retained earnings calculations, and dividend distributions. It provides the foundational debit/credit mechanics that all other modules build upon.

### 4.2 Accounts Payable (AP)

The AP module manages the supplier payment lifecycle: invoice receipt, three-way matching (PO to receipt to invoice), payment processing, credit memo application, and supplier balance tracking. Profiles define the accounting entries for each stage — invoice recording debits expense and credits AP; payment debits AP and credits cash. The AP subledger maintains per-supplier balances that reconcile to the GL control account.

### 4.3 Accounts Receivable (AR)

AR manages the customer revenue cycle: invoice issuance, payment receipt, discount application, bad debt provisioning, and aging analysis. The module tracks open receivables, applies cash receipts, and manages allowance for doubtful accounts. Each customer interaction flows through a profile that produces the appropriate journal entries.

### 4.4 Inventory

The inventory module handles physical stock movements and their financial effects: receipts from purchase orders, issues to production or sales, inter-location transfers, physical count adjustments, scrap write-offs, and standard cost revaluations. It maintains a dual-ledger model — the GL records the aggregate financial position while the inventory subledger tracks individual stock movements (stock on hand, in transit, in production, sold, scrapped).

### 4.5 Procurement

Procurement manages the purchasing lifecycle from requisition through purchase order to goods receipt. It handles encumbrance accounting (committing budget before spending), purchase commitments, and the linkage between PO lines and inventory receipts. Procurement events trigger the economic links that connect purchase orders to receipts to invoices.

### 4.6 Payroll

The payroll module processes compensation events: salary accruals, wage payments, overtime calculations, PTO accruals, tax withholdings (federal, state, FICA), and benefits processing. Each payroll run produces journal entries that debit expense accounts by category and credit accrued liabilities and tax payable accounts. The module supports labor cost distribution for organizations that allocate labor to projects or cost centers.

### 4.7 Cash Management

Cash management tracks bank account activity: deposits, withdrawals, transfers between accounts, bank fee processing, and bank reconciliation. It maintains a bank subledger with reconciliation status tracking (available, deposit, withdrawal, reconciled, pending) and supports the matching of bank statement lines to internal transactions.

### 4.8 Fixed Assets

The assets module manages the lifecycle of capital assets: acquisition, depreciation (straight-line and other methods), impairment, disposal, and construction-in-progress (CIP). Each depreciation run produces journal entries debiting depreciation expense and crediting accumulated depreciation. Asset disposals calculate gain or loss and reverse the accumulated depreciation.

### 4.9 Expense Management

Expense management processes employee expense reports: submission, approval workflows, reimbursement, corporate card reconciliation, and advance clearing. The module distinguishes between project-billable expenses (which flow to WIP) and general expenses, and supports cost allowability classification for government contractors.

### 4.10 Tax

The tax module computes and records tax obligations: sales tax collection, use tax accrual, income tax provision, VAT settlement, and tax refund processing. It maintains separate accounts for each tax type and jurisdiction, and produces the journal entries for periodic tax remittance and filing.

### 4.11 Work-in-Process (WIP)

WIP tracks manufacturing and production costs: material issues to production, labor charges, overhead allocation, job completion, variance analysis (labor, material, overhead), scrap, and rework. It bridges inventory, payroll, and overhead allocation into a unified cost accumulation model.

### 4.12 Contracts

The contracts module supports government and commercial contract accounting: contract lifecycle management (active, completed, closed-out), CLIN-level cost tracking, billing by contract type (CPFF — Cost Plus Fixed Fee, T&M — Time and Materials, FFP — Firm Fixed Price), indirect rate application, and DCAA compliance. It handles the specialized accounting requirements of defense contractors, including incurred cost reporting, provisional billing rates, and cost allowability determination.

---

## 5. Calculation Engines

Engines are pure functions — no I/O, no database access, fully deterministic. They accept value objects from the kernel domain and return computed results.

**Variance Calculator.** Computes price, quantity, mix, and efficiency variances between standard and actual costs. Used by inventory and WIP modules to analyze cost deviations.

**Allocation Engine.** Distributes costs across recipients using configurable methods (proportional, step-down, direct, activity-based). Handles rounding to ensure the allocated total equals the source amount exactly.

**Allocation Cascade.** Multi-step indirect cost allocation for DCAA-compliant rate computation. Builds a cascade of allocation steps (fringe → overhead → G&A) and executes them sequentially, computing the fully burdened cost of each contract.

**Matching Engine.** Three-way matching (PO to receipt to invoice) with configurable tolerances. Returns match status, discrepancy details, and tolerance evaluation.

**Aging Calculator.** Computes aging buckets (current, 30-day, 60-day, 90-day, 90+ day) for receivables and payables. Used by AR and AP modules for aging reports and bad debt provisioning.

**Tax Calculator.** Computes tax amounts by type and jurisdiction. Handles sales tax, use tax, income tax, and withholding calculations.

**Billing Engine.** Computes government contract billing amounts by contract type (CPFF, T&M, FFP). Handles indirect cost application, fee calculation, rate adjustment, withholding, and funding limit enforcement.

**ICE Engine (Incurred Cost Electronically).** Compiles DCAA Incurred Cost Electronically submissions — the standardized format for reporting incurred costs on government contracts. Produces Schedules A through J per DCAA requirements.

**Valuation Engine.** Cost lot management and inventory costing methods (FIFO, LIFO, weighted average, standard cost). Pure functions over cost layer data.

**Reconciliation Engine.** Document matching, payment application, and bank reconciliation logic. Pure state computation without I/O.

**Correction Engine.** Computes unwind plans for error correction — identifies affected artifacts, generates compensating entries, and validates the mathematical integrity of the correction.

**Tracer Engine.** Pure computation support for trace bundle assembly.

### 5.1 Engine Contracts and Runtime Dispatch

Every engine declares a contract (`finance_engines/contracts.py`) that specifies:

- **Engine name and version** — identity and semantic versioning.
- **Parameter schema** — a JSON Schema defining the configuration parameters the engine accepts (e.g., `tolerance_percent`, `allocation_method`, `match_strategy`). Validated at both compile time (against engine_params.yaml) and runtime (before invocation).
- **Input fingerprint rules** — which input fields to hash for deterministic audit logging. Two invocations with the same fingerprint must produce the same result.

At runtime, policies declare their engine dependencies via `required_engines` and `engine_parameters_ref` in the YAML policy definition. The `EngineDispatcher` reads these declarations from the compiled policy, resolves the parameters from `CompiledPolicyPack.resolved_engine_params`, and invokes the correct engine with the correct configuration. This means engine behavior is driven by YAML configuration, not hardcoded in module services.

### 5.2 Engine Tracing

Every engine entry point is decorated with `@traced_engine`, which produces a `FINANCE_ENGINE_TRACE` structured log record containing:

- Engine name and version
- Input fingerprint (SHA-256 prefix of canonicalized input fields)
- Execution duration in milliseconds
- The function that was invoked

These trace records are captured by the InterpretationCoordinator's LogCapture and persisted in the `InterpretationOutcome.decision_log`. This means every engine computation that contributed to a journal entry is recoverable from the database — an auditor can see exactly which variance calculation, allocation method, or tax computation produced a given financial result.

### 5.3 Variance Disposition

When a variance engine computes a difference between expected and actual costs, the `VarianceDisposition` determines what happens to that variance:

- **POST_TO_VARIANCE_ACCOUNT** — Variance is expensed to a dedicated variance GL account (e.g., Purchase Price Variance, Labor Variance).
- **CAPITALIZE_TO_INVENTORY** — Variance is absorbed into the inventory cost (appropriate for immaterial variances).
- **ALLOCATE_TO_COGS** — Variance is allocated proportionally to Cost of Goods Sold.
- **WRITE_OFF** — Variance is immediately expensed (appropriate for period-end cleanup).

The disposition is declared in the policy YAML (`variance_disposition` field) and read by the `EngineDispatcher` to route variance amounts to the correct ledger effect.

---

## 6. Configuration System

### 6.1 Design Philosophy

The configuration system is built on a key insight: accounting rules should be *data*, not *code*. When a policy that governs how inventory receipts are recorded lives in a YAML file rather than a Python function, it can be reviewed by accountants, versioned alongside the chart of accounts, and approved through a governed lifecycle — all without deploying new software.

### 6.2 Configuration Structure

A configuration set is a directory of YAML fragments that collectively define how an organization does its accounting:

```
sets/US-GAAP-2026-v1/
  root.yaml                 # Identity, scope, capabilities, precedence
  chart_of_accounts.yaml    # Role-to-COA-code bindings
  ledgers.yaml              # Ledger definitions (GL, AP subledger, etc.)
  engine_params.yaml        # Calculation engine parameters
  controls.yaml             # Governance controls
  policies/                 # One YAML per domain
     inventory.yaml         # 11 inventory policies
     ap.yaml                # AP policies
     ar.yaml                # AR policies
     payroll.yaml           # Payroll policies
     ... (12 domain files)
```

### 6.3 Core Components

**Root Manifest (`root.yaml`).** Declares the configuration identity, version, scope (legal entity, jurisdiction, regulatory regime, currency, effective dates), enabled capabilities (which modules are active), and precedence rules for policy conflict resolution. The scope determines which organization and time period this configuration governs.

**Chart of Accounts (`chart_of_accounts.yaml`).** Maps semantic account roles to COA codes and ledgers. There are approximately 170 role bindings in the baseline US GAAP configuration, covering everything from basic asset/liability/equity accounts to specialized roles for government contracting (WIP_DIRECT_LABOR, WIP_FRINGE, INDIRECT_RATE_VARIANCE). This is the key extensibility point — a defense contractor and a retail company use the same posting policies but different role bindings.

**Ledger Definitions (`ledgers.yaml`).** Defines the ledgers in use: General Ledger, Inventory Subledger, AP Subledger, Bank Subledger, Contract Subledger. Each ledger declares the account roles it requires.

**Policy Definitions (`policies/*.yaml`).** Each policy is a declarative rule with seven components:
- **Trigger:** Which event type activates this policy, with optional where-clause predicates for specialization (e.g., `inventory.issue` where `payload.issue_type == "SALE"` vs. `"SCRAP"`)
- **Meaning:** The economic interpretation (INVENTORY_INCREASE, REVENUE_RECOGNITION, EXPENSE_ACCRUAL), including optional `variance_disposition` for variance routing
- **Ledger Effects:** Which ledgers to post to, and which roles to debit and credit
- **Guards:** Rejection conditions expressed in a restricted AST (e.g., `payload.quantity <= 0` rejects with INVALID_QUANTITY)
- **Line Mappings:** How to construct journal lines from the accounting intent
- **Required Engines:** Which calculation engines must run before posting (e.g., `["variance"]`, `["allocation", "allocation_cascade"]`). The `EngineDispatcher` reads this field at runtime and invokes the declared engines.
- **Engine Parameters Ref:** A key into `engine_params.yaml` that supplies configuration to the required engines (e.g., `variance` resolves to `tolerance_percent: 0.01`, `tolerance_amount: 100`)

**Engine Parameters (`engine_params.yaml`).** Configuration for calculation engines — allocation methods, variance thresholds, matching tolerances, aging bucket boundaries, tax calculation methods, and DCAA cascade rates. These parameters are compiled into `FrozenEngineParams` objects and consumed by the `EngineDispatcher` at posting time. Every parameter is validated against the engine contract's JSON Schema during compilation.

**Controls (`controls.yaml`).** Global governance rules that apply across all events. For example, a control requiring `payload.amount > 0` rejects any event with a non-positive amount regardless of event type.

### 6.4 Build Pipeline

Configuration flows through a three-stage pipeline:

1. **Assembly.** The assembler (`assembler.py`) reads all YAML fragments from a configuration set directory and composes them into a single `AccountingConfigurationSet` — a frozen dataclass containing all policies, role bindings, ledger definitions, engine parameters, and controls. A SHA-256 checksum is computed over the assembled data.

2. **Validation.** The validator (`validator.py`) checks structural integrity: every policy's required engines exist, every guard expression parses successfully in the restricted AST, no two policies create ambiguous dispatch, all role bindings reference roles used by policies, and all capability tags reference declared capabilities.

3. **Compilation.** The compiler (`compiler.py`) produces a `CompiledPolicyPack` — the frozen, machine-validated runtime artifact. Guard expressions are pre-validated, policies are indexed for fast dispatch, engine contracts are resolved and parameter schemas validated, and the pack receives a canonical fingerprint. The `CompiledPolicyPack` is the *only* object that the runtime posting pipeline accepts. Every compiled field is consumed at runtime — engine contracts by the `EngineDispatcher`, resolved engine parameters by engine invokers, policies by the `PolicySelector`, role bindings by the `JournalWriter`, and the canonical fingerprint by the integrity verifier. Architecture tests enforce that no compiled field exists without a runtime consumer.

### 6.5 Configuration Lifecycle

Configuration sets follow a governed lifecycle with explicit state transitions:

```
DRAFT → REVIEWED → APPROVED → PUBLISHED → SUPERSEDED
```

Only PUBLISHED configurations can be used for posting. Superseded configurations remain in the system for audit replay — if an auditor needs to re-derive a journal entry from three years ago, the exact configuration that was in force at that time is preserved.

An optional fingerprint pin mechanism (`integrity.py`) allows organizations to cryptographically seal an approved configuration. If a pin file exists, the system verifies that the compiled pack's canonical fingerprint matches the pinned value. Any unauthorized modification to the YAML fragments would produce a different fingerprint and be rejected at runtime.

### 6.6 Tailoring to Company Needs

To configure the system for a specific organization, an implementer creates a new configuration set directory:

1. **Set the scope** in `root.yaml`: legal entity, jurisdiction (US, EU), regulatory regime (GAAP, IFRS, DCAA/CAS), base currency, and effective dates.

2. **Enable capabilities**: A defense contractor enables `dcaa: true` and `contracts: true`; a retail company enables `inventory: true` and `ar: true` but may leave `wip: false` and `contracts: false`.

3. **Define the chart of accounts**: Map the standard semantic roles (INVENTORY, ACCOUNTS_PAYABLE, REVENUE) to the company's actual account codes. A company using account 12000 for inventory instead of 1200 simply changes the role binding — no policy changes needed.

4. **Customize policies**: Add company-specific where-clause predicates, adjust guard thresholds, or add new policies for custom event types. The precedence system ensures more specific policies override generic ones.

5. **Configure engines**: Set allocation methods, variance thresholds, and aging parameters appropriate to the business.

6. **Add controls**: Define company-specific approval thresholds, spending limits, or compliance checks.

The result is a complete accounting configuration that can be reviewed, approved, and published without changing any application code.

---

## 7. Traceability, Testing, and Audit

### 7.1 The Decision Journal

Every posting through the InterpretationCoordinator automatically captures a complete decision journal — a structured log of every function, role resolution, balance check, and posting decision that occurred during the pipeline. This journal is persisted as a JSON array on the `InterpretationOutcome` record, making it queryable from the database without any external log infrastructure.

The decision journal captures:

- **Interpretation Started:** Which profile was selected, which event is being processed, the effective date, and the number of target ledgers.
- **Configuration Snapshot (R21):** The exact version numbers of the chart of accounts, dimension schema, rounding policy, and currency registry in force at interpretation time.
- **Engine Dispatch:** For each required engine, the engine name and version, input fingerprint, resolved parameters from configuration, execution duration, and output summary. This means every variance calculation, allocation method, or tax computation that contributed to a journal entry is recoverable from the decision journal.
- **Balance Validated:** For each currency in each ledger, the sum of debits, sum of credits, and whether they balance.
- **Role Resolved:** For each journal line, which semantic role was resolved to which COA account code, on which ledger, using which COA version.
- **Subledger Reconciliation:** For each subledger control contract with `enforce_on_post`, the before and after balances and whether the reconciliation check passed.
- **Line Written:** Each journal line with its sequence number, role, account code, side, amount, currency, and rounding flag.
- **Invariant Checked:** R21 reference snapshot verification confirming the journal entry recorded the correct configuration versions and that the snapshot is current (not stale).
- **Journal Entry Created:** The entry ID, status, sequence number, idempotency key, effective date, and posting timestamp.
- **Outcome Recorded:** The terminal status (POSTED, REJECTED, BLOCKED), the profile that processed it, and the journal entry IDs produced.
- **Reproducibility Proof:** Canonical hashes of the input (accounting intent) and output (journal entries), enabling mechanical verification that the same inputs produce the same outputs.
- **FINANCE_KERNEL_TRACE:** A final summary with policy name, version, outcome status, and hash proofs.

### 7.2 The Trace Bundle

The TraceSelector assembles a complete `TraceBundle` for any financial artifact — given an event ID or journal entry ID, it reconstructs the full lifecycle:

- **Origin Event:** The canonical source event with its payload hash, actor, producer, and timestamps.
- **Journal Entries:** All entries and lines with account codes, amounts, currencies, and R21 snapshot fields.
- **Interpretation Outcome:** The policy that processed the event, its version, the outcome status, and the full decision journal.
- **Reproducibility Info:** R21 version numbers enabling deterministic replay.
- **Decision Journal Timeline:** Chronologically sorted structured log entries merged with audit trail events.
- **Lifecycle Links:** Economic relationships (FULFILLED_BY, PAID_BY, REVERSED_BY) connecting this artifact to related business documents.
- **Integrity Verification:** Payload hash verification, balance verification (debits = credits), and audit chain segment validation.
- **Missing Facts:** Explicit declaration of any data the selector could not resolve — never inferred, never invented.

A CLI tool (`scripts/trace.py`) exposes this capability directly:

```bash
python3 scripts/trace.py --event-id <uuid>        # Human-readable trace
python3 scripts/trace.py --entry-id <uuid>         # Trace from journal entry
python3 scripts/trace.py --event-id <uuid> --json  # Machine-readable JSON
```

### 7.3 LLM-Readable Audit Trails

The trace bundle and decision journal are designed to be consumed by large language models for audit analysis. When an auditor or compliance officer needs to understand why a specific dollar amount appears in a specific account, they can:

1. Pull the trace bundle for the journal entry in question.
2. Feed the JSON trace to an LLM with a question like "Explain why $500,000 was debited to account 1200."
3. The LLM reads the decision journal and can explain: "The event was an inventory receipt (event type `inventory.receipt`). The InventoryReceipt policy (version 1) was selected. The policy defined a GL posting: debit role INVENTORY, credit role GRNI. Role INVENTORY was resolved to account 1200 using COA version 1. The amount of $500,000 was the `payload.amount` from the source event. Balance was validated: debits $500,000 = credits $500,000 in USD. The entry was posted with sequence number 42."

This capability transforms audit from archeology into conversation. The decision journal provides the structured data; the LLM provides the narrative interpretation.

### 7.4 Test Suite

The test suite contains over 2,800 tests organized across 24 categories:

| Category | Purpose |
|----------|---------|
| **posting** | Core posting mechanics: balance, idempotency, period lock |
| **audit** | Hash chain integrity, immutability enforcement, failed posting audit trails |
| **concurrency** | Sequence safety under load, race condition prevention |
| **adversarial** | Attack vectors: account deletion while referenced, producer immutability, rounding abuse |
| **architecture** | Import boundary enforcement, kernel isolation, primitive reuse, actor validation |
| **domain** | Business profiles, policy registry, event schemas, interpretation invariants, subledger control |
| **replay** | Deterministic replay, rule version governance |
| **crash** | Fault injection: what happens when the system fails mid-transaction |
| **fuzzing** | Hypothesis-based property testing, adversarial input generation |
| **metamorphic** | Metamorphic testing: does reversing a posting and re-posting produce the original result? |
| **security** | SQL injection prevention, input sanitization, privilege escalation |
| **database_security** | PostgreSQL trigger enforcement, raw SQL modification attempts |
| **modules** | Per-module integration tests (AP, AR, Inventory, Payroll, etc.) |
| **engines** | Pure engine computation tests |
| **services** | Service-level integration tests |
| **integration** | End-to-end pipeline tests |
| **multicurrency** | Multi-currency posting and conversion |
| **trace** | Trace bundle assembly, DTO contracts, log query protocol |
| **demo** | Full trace demo with auditor-readable output |
| **unit** | Unit tests for individual components |
| **period** | Fiscal period lifecycle and enforcement |
| **reporting** | Financial statement generation |

Every invariant (R1-R24, L1-L5, P1-P15) has corresponding tests in at least the unit, concurrency, and adversarial categories. The architecture tests enforce import boundaries automatically — if a developer accidentally imports from `finance_modules` inside `finance_kernel`, the test fails.

### 7.5 Invariant Summary

The system enforces 24 kernel invariants, 5 interpretation-layer invariants, and 3 posting invariants. Six of these are designated as non-negotiable — no configuration or policy may override them:

1. **Double-entry balance:** Debits equal credits per currency per entry.
2. **Immutability:** Posted records cannot be modified (ORM + 26 PostgreSQL triggers).
3. **Period lock:** No posting to closed fiscal periods.
4. **Link legality:** Economic links follow type specifications.
5. **Sequence monotonicity:** Sequences are strictly monotonic, gap-safe.
6. **Idempotency:** N retries of the same event produce exactly one journal entry.

These invariants are not configurable options. They are structural properties of the system that cannot be bypassed regardless of the accounting policy, configuration, or organizational requirements in force.

### 7.6 Runtime Guard Enforcement

Beyond the six core invariants, the system enforces a set of runtime guard layers that prevent bypass:

**Subledger-to-GL reconciliation.** Every subledger (AP, AR, Bank, Inventory, Fixed Assets) declares a control contract specifying its GL control account and reconciliation tolerance. The system enforces these contracts at two points: at posting time (when `enforce_on_post` is set, the journal writer validates that the posting maintains reconciliation) and at period close (when `enforce_on_close` is set, closing is blocked if the subledger is out of balance with its control account).

**Reference snapshot freshness.** The journal writer validates that the reference snapshot attached to an accounting intent is current — not stale from a previous configuration version. If the chart of accounts, dimension schema, or rounding policy has changed since the snapshot was captured, posting is rejected with `StaleReferenceSnapshotError`.

**Link graph acyclicity (L3).** When establishing an economic link, the LinkGraphService walks from the proposed child back to the proposed parent via the same link type. If the parent is reachable from the child, the link would create a cycle, and `LinkCycleError` is raised.

**Correction period lock.** The CorrectionEngine validates that artifacts being voided or corrected are not in closed fiscal periods. A document posted in a closed period cannot be unwound — the correction must use a current-period adjusting entry instead.

### 7.7 Financial Exception Lifecycle

Every posting attempt — whether it succeeds or fails — produces a durable outcome record. Failed postings do not disappear into transient errors or log files. They become first-class financial cases that are visible, owned, and retriable.

**Outcome state machine.** Every outcome follows an explicit state machine:

```
PENDING → POSTED      (success)
PENDING → FAILED      (guard, engine, or policy failure)
FAILED  → RETRYING    (human initiates retry)
RETRYING → POSTED     (retry succeeds)
RETRYING → FAILED     (retry fails again)
FAILED  → ABANDONED   (human gives up)
```

POSTED and ABANDONED are terminal states. A POSTED outcome implies a corresponding journal entry exists. A FAILED or ABANDONED outcome implies no journal entry exists.

**Failure context.** Every failed outcome records structured failure context: the failure type (RECONCILIATION, SNAPSHOT, AUTHORITY, CONTRACT, ENGINE), the failure message, engine trace references, and the payload fingerprint. This enables filtering and routing exceptions to the right human reviewer.

**Retry contract.** When a failed outcome is retried, the original event payload, actor ID, and event timestamp are immutable — these are facts of what happened. What *can* change between attempts: the compiled policy pack (rules may have been corrected), the reference snapshot (reference data may have been updated), and external master data (parties, contracts, items). Each retry produces a new engine trace set.

**Work queue.** Failed outcomes are surfaced through a logical inbox model with views by failure type, policy name, age (for SLA/escalation tracking), and actor. This enables financial operations teams to triage and resolve exceptions without ad hoc database queries.

---

*This memo describes the system architecture. The codebase is under active development on branch `feature/economic-link-primitive`. Implementation of the full engine dispatch, orchestrator, mandatory guards, runtime enforcement points, module rewiring, and financial exception lifecycle is tracked in `docs/WIRING_COMPLETION_PLAN.md`.*
