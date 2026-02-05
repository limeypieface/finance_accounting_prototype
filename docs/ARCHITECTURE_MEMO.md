

# Architecture memo: prototype event-sourced double-entry accounting system

**Date:** February 2026

## Purpose

This memo documents a prototype finance and accounting system built to explore a single architectural question: what happens if financial recording and operational governance are treated as a shared core of the system, rather than as logic embedded separately in each business module.

The intent was not to replicate the feature set of a mature ERP. The intent was to test whether centralizing interpretation — what a business event means financially, how it is recorded, and what authority is required before it can proceed — produces a system that is more stable over time, easier to govern, and easier to explain when something goes wrong.

This memo describes what was built, what it made easier, what it made harder, and what it suggests about how finance systems behave when governance and explainability are treated as primary design constraints rather than secondary features.

## Context and problem framing

In most ERP systems, each module learns how to “do accounting” on its own. Accounts payable posts invoices one way. Inventory posts receipts another. Payroll accrues wages in its own logic. Over time, the same type of economic reality is translated into financial records through multiple, slightly different paths.

This produces three practical problems.

First, behavior drifts. The same transaction can produce different financial outcomes depending on where it entered the system or which version of a module handled it.

Second, change becomes risky. Adjusting posting behavior often requires code changes, database migrations, and broad regression testing. From the business side, this feels like policy changes are inherently dangerous, even when the change itself is simple.

Third, explanation becomes reconstruction. When a number is questioned, the answer usually lives across logs, code paths, configuration screens, and manual processes. The system records the result, but not the reasoning.

The prototype was designed to test whether these problems are a consequence of how responsibilities are divided, rather than of accounting complexity itself.

## Core approach

The system treats every business action as an event. An event is not posted directly to the ledger. It is interpreted.

Interpretation happens in a small, stable core that decides three things:

* What this event means economically
* What financial entries should result
* What authority or conditions must be satisfied before it can proceed

The result of that interpretation is an immutable journal entry, along with a durable record of how the decision was made. Modules become sources of events and owners of workflows, not owners of financial meaning.

This shifts complexity forward. More work happens in the design of the core and the way rules are defined and governed. In exchange, the outer parts of the system become thinner, more consistent, and easier to change without destabilizing the financial foundation.

## Design principles and structure

The following principles emerged as the system took shape and remained stable across multiple iterations.

**Put the right responsibilities in the right places.**
The system is designed so different kinds of rules live in different parts of the core, instead of being mixed together.

**The kernel holds accounting law.**
This is where the non-negotiables live: double-entry rules, period locks, immutability of posted records, and the basic structure of a ledger. These are the things that should never change, no matter what product you sell, what country you operate in, or what policy is in force.

**The engines and services handle math and economics.**
This is where calculations happen: allocations, variances, tax calculations, depreciation, matching, and pricing logic. These are mathematical and economic problems, not policy decisions. By keeping them here, the same engines can be reused across many parts of the business.

**The modules and configuration express preference and regulation.**
This is where you capture how *your* company wants to operate: which accounts you use, who must approve what, what limits apply, and how local regulations are enforced. These rules can change by country, by entity, or over time without touching the core of the system.

**Why this matters for the business.**
When these concerns are separated, changes become safer. A new regulation or internal policy update does not risk breaking the financial foundation. A new product or contract type can reuse the same calculation engines. The core remains stable while the business adapts around it.

**The practical outcome.**
You get a system that reflects both law and preference clearly. The hard rules of accounting are protected. The flexible rules of how you run the business are easy to change. And the logic that turns activity into numbers is consistent everywhere, instead of being rewritten in every module.

**The larger benefit.**
This structure reduces long-term system risk. It lowers the cost of compliance, speeds up change, and makes it easier for leaders, auditors, and operators to understand what the system is doing and why.

## What was built

The prototype is an event-sourced, append-only, double-entry accounting system. It does not store balances as mutable fields. Every financial fact is a journal line. All reports, balances, and statements are derived by reading the journal forward.

At the center is a kernel that owns:

* Journal entries and journal lines
* Business events and their payloads
* Accounting policies and dispatch rules
* Immutability enforcement and period controls
* Economic links between related financial artifacts
* Audit records and trace data

Surrounding the kernel are calculation engines and orchestration services that perform economic and mathematical work, and outer modules that manage workflows and emit events.

The system is designed so that a purchase order receipt, a payroll run, or a contract billing event all flow through the same interpretation pipeline, regardless of which module originated them.

## Configuration as a governed artifact

Posting behavior is defined in configuration rather than in application code.

Policies are written as declarative rules that describe:

* Which events they apply to
* What economic meaning they assign
* Which account roles are debited and credited
* What guards or conditions block or reject the event
* Which calculation engines must run before posting

These policies are compiled, validated, and versioned into a runtime configuration pack. Only published configurations can be used for posting. Superseded configurations remain available for audit and replay.

This turns accounting behavior into something that can be reviewed, approved, and governed in the same way as other high-impact business policies.

## How posting works in practice

When an event enters the system, it passes through a single pipeline:

1. The event is validated and checked for duplication.
2. A policy is selected based on the event type and conditions.
3. The event’s economic meaning is constructed.
4. Required calculation engines are invoked.
5. An accounting intent is built using semantic account roles.
6. Roles are resolved to chart-of-accounts codes.
7. Debits and credits are validated per currency.
8. Journal entries and subledger entries are written atomically.
9. A complete decision record is persisted alongside the outcome.

If any step fails, nothing is written. The event remains as a failed case with structured failure context that can be reviewed and retried.

## Explainability and traceability

Every posting produces two things: a financial record and a decision record.

The decision record captures which policy was selected, which rules were evaluated, which engines ran, how accounts were resolved, and how balances were checked. This record is stored with the posting, not reconstructed later.

From this, the system can assemble a full trace for any number in the ledger:

* The original business event
* The policy that interpreted it
* The calculations that were applied
* The accounts that were selected
* The approvals or guards that were enforced
* The journal entries that resulted

This makes explanation a direct query, not an investigation.

## Trade-offs observed

This approach is not simpler than traditional designs. It is more deliberate.

The core requires careful design before the first transaction is posted. Configuration governance must be real, not nominal. The organization must be willing to treat policy publication and approval as a controlled process.

In return, the system becomes easier to change safely, easier to audit, and harder to quietly corrupt.

The main risk is concentration. When meaning and authority live in a small core, mistakes in that core have broad impact. This pushes responsibility toward strong testing, versioning discipline, and separation of duties around configuration and kernel changes.

## Implications for operations

For finance and compliance teams, the system behaves more like a governed platform than a collection of features. Accounting rules are visible, reviewable, and tied to formal approval. Exceptions become work items instead of log entries. Period close becomes a validation process rather than a reconciliation exercise.

For leadership, the financial system becomes easier to reason about. Changes in regulation, structure, or business model map to configuration changes rather than rewrites of financial logic.

## Implications for engineering

For engineers, accounting logic has a single home. Modules do not re-implement posting behavior. They define events and workflows. The kernel, engines, and policies handle interpretation.

This reduces duplication and long-term drift. It also creates a stable interface for automation. Agents and tools can read the same decision records that auditors do, rather than inferring behavior from scattered implementation details.

## Key risks and mitigation

This design changes where risk lives. Instead of being spread across many modules and teams, a large share of financial meaning and authority is concentrated in a small core and its configuration. That has advantages, but it also creates new failure modes that must be managed deliberately.

Concentration of responsibility risk.
When meaning, posting rules, and approval logic live in a single kernel, mistakes in that layer affect the entire system. A defect or poor design decision can have broad financial impact.

Mitigation: Keep the kernel small and slow to change. Treat kernel changes like accounting standard changes, not feature work. Require versioned interfaces, full replay testing, and architectural review before any modification is allowed into production.

Policy error risk.
Moving behavior into configuration shifts the primary failure mode from software bugs to rule mistakes. A policy can be valid, approved, and still economically wrong, and it can apply at scale.

Mitigation: Use a formal lifecycle for policy changes. Require review and approval by both finance and systems owners. Run new policies against historical data in simulation before publication. Maintain policy comparisons that show financial impact, not just text differences.

Performance and throughput risk.
Interpretation, calculation, guard checks, and trace capture add work to every posting. At high volumes, this can become a constraint.

Mitigation: Separate high-risk and low-risk transaction classes. Allow simpler, faster paths for low-impact events. Precompile policy indexes and engine bindings. Partition financial tables by period or entity. Allow trace persistence to run asynchronously where regulation permits.

Operational maturity risk.
This system assumes discipline around configuration management, approvals, and exception handling. Organizations without strong financial operations can misuse it or bypass controls.

Mitigation: Provide conservative default policies and starter configurations. Restrict posting capabilities early. Expand flexibility only as governance processes prove reliable.

Human trust risk.
If users experience the system as technical or opaque, they may work around it, even if it is explainable in principle.

Mitigation: Make explanations part of normal workflows. Show “why this posted” alongside “what posted.” Generate readable policy documentation from configuration and keep it accessible to finance teams.

Fragmentation risk.
Different entities, regions, or time periods can accumulate divergent rule sets, recreating inconsistency under a different form.

Mitigation: Use baseline policy sets with explicit, visible overrides. Report on differences between configurations. Protect a small set of global invariants that cannot be changed locally.

Privilege concentration risk.
The ability to publish configuration becomes a form of financial authority. A small number of people can change how money is recorded across the organization.

Mitigation: Enforce separation of duties. Require multi-party approval for high-impact changes. Maintain immutable publication logs and independent review paths.

Failure containment risk.
If the kernel or configuration system fails, large volumes of transactions can be blocked.

Mitigation: Support a staging or quarantine mode where events are accepted and queued without posting. Provide independent tools for trace inspection and replay outside the live system.

Regulatory acceptance risk.
Auditors and regulators may be unfamiliar with a policy-driven, event-based accounting model, even if results are compliant.

Mitigation: Maintain formal mappings from system behavior to accounting and regulatory requirements. Use trace demonstrations as part of audit preparation. Validate the approach with external reviewers early.

Governance scalability risk.
As the number of policies and configurations grows, review and approval can become a bottleneck.

Mitigation: Tier changes by impact. Low-risk changes follow fast paths. High-risk changes require full review. Provide impact analysis that shows which accounts, processes, and controls are affected by a change.

Ecosystem alignment risk.
This approach does not match how most ERP tools and consultants think about finance systems.

Mitigation: Expose conventional interfaces and reports. Provide standard exports and “ERP-shaped” APIs. Use adapters to integrate with existing tools rather than forcing them to adopt the internal model.

Adoption timing risk.
The benefits of this design compound over time. Early deployments may not justify the initial investment on feature breadth alone.

Mitigation: Lead with areas where governance and traceability are already painful, such as compliance-heavy subledgers, approvals, or audit support. Expand outward once the core proves its value.

Summary.
The main risk is not whether the system can be built. It is whether the organization is prepared to operate it. This architecture turns accounting into a governed system of rules, authority, and interpretation. For organizations that can sustain that discipline, it becomes a long-term advantage. For those that cannot, it becomes a single point of failure.

## Conclusion

This prototype does not prove that this architecture is universally better. It does show that much of the complexity in finance systems comes from duplicated structure rather than from accounting itself.

By centralizing interpretation, governance, and audit into a stable core, the system becomes more consistent, more explainable, and more adaptable over time.

The open question is not technical. It is organizational. This design assumes a business is willing to treat financial behavior as a governed system of rules and authority, not just as a byproduct of application code. For organizations prepared to operate that way, the architecture offers a different balance between control and change than traditional ERP systems provide.

---

## Appendix A: Module and engine coverage

### A.1 Finance modules

Modules are thin ERP-level wrappers that translate business processes into accounting events. Each module follows a uniform structure: profiles (declarative accounting policies), config (account mappings and module settings), service (stateful business operations), workflows (multi-step business processes), and models (module-specific data structures). Modules emit events and manage workflows but do not embed accounting logic.

**General Ledger (GL).** Core GL operations that don't belong to a specific subledger: manual journal entries, period-end closing entries, intercompany transactions, retained earnings calculations, and dividend distributions. Provides the foundational debit/credit mechanics that all other modules build upon.

**Accounts Payable (AP).** Supplier payment lifecycle: invoice receipt, three-way matching (PO to receipt to invoice), payment processing, credit memo application, and supplier balance tracking. Profiles define the accounting entries for each stage — invoice recording debits expense and credits AP; payment debits AP and credits cash. The AP subledger maintains per-supplier balances that reconcile to the GL control account.

**Accounts Receivable (AR).** Customer revenue cycle: invoice issuance, payment receipt, discount application, bad debt provisioning, and aging analysis. Tracks open receivables, applies cash receipts, and manages allowance for doubtful accounts.

**Inventory.** Physical stock movements and their financial effects: receipts from purchase orders, issues to production or sales, inter-location transfers, physical count adjustments, scrap write-offs, and standard cost revaluations. Maintains a dual-ledger model — the GL records the aggregate financial position while the inventory subledger tracks individual stock movements.

**Procurement.** Purchasing lifecycle from requisition through purchase order to goods receipt. Handles encumbrance accounting (committing budget before spending), purchase commitments, and the linkage between PO lines and inventory receipts.

**Payroll.** Compensation events: salary accruals, wage payments, overtime calculations, PTO accruals, tax withholdings (federal, state, FICA), and benefits processing. Supports labor cost distribution for organizations that allocate labor to projects or cost centers.

**Cash Management.** Bank account activity: deposits, withdrawals, transfers between accounts, bank fee processing, and bank reconciliation. Maintains a bank subledger with reconciliation status tracking.

**Fixed Assets.** Capital asset lifecycle: acquisition, depreciation (straight-line and other methods), impairment, disposal, and construction-in-progress (CIP). Depreciation runs produce journal entries debiting depreciation expense and crediting accumulated depreciation.

**Expense Management.** Employee expense reports: submission, approval workflows, reimbursement, corporate card reconciliation, and advance clearing. Distinguishes between project-billable expenses (which flow to WIP) and general expenses, and supports cost allowability classification for government contractors.

**Tax.** Tax obligations: sales tax collection, use tax accrual, income tax provision, VAT settlement, and tax refund processing. Maintains separate accounts for each tax type and jurisdiction.

**Work-in-Process (WIP).** Manufacturing and production costs: material issues to production, labor charges, overhead allocation, job completion, variance analysis (labor, material, overhead), scrap, and rework. Bridges inventory, payroll, and overhead allocation into a unified cost accumulation model.

**Contracts.** Government and commercial contract accounting: contract lifecycle management (active, completed, closed-out), CLIN-level cost tracking, billing by contract type (CPFF — Cost Plus Fixed Fee, T&M — Time and Materials, FFP — Firm Fixed Price), indirect rate application, and DCAA compliance. Handles incurred cost reporting, provisional billing rates, and cost allowability determination.

**Revenue, Budget, Lease, Project, Intercompany, Credit Loss.** Additional modules covering revenue recognition (ASC 606), budget planning and variance, lease accounting (ASC 842), project cost accumulation, intercompany elimination, and expected credit loss provisioning (ASC 326).

### A.2 Calculation engines

Engines are pure functions — no I/O, no database access, fully deterministic. They accept value objects from the kernel domain and return computed results.

| Engine | Purpose |
|--------|---------|
| **Variance Calculator** | Price, quantity, mix, and efficiency variances between standard and actual costs |
| **Allocation Engine** | Cost distribution across recipients (proportional, step-down, direct, activity-based) |
| **Allocation Cascade** | Multi-step indirect cost allocation for DCAA-compliant rate computation (fringe → overhead → G&A) |
| **Matching Engine** | Three-way matching (PO to receipt to invoice) with configurable tolerances |
| **Aging Calculator** | Aging buckets (current, 30-day, 60-day, 90-day, 90+) for receivables and payables |
| **Tax Calculator** | Tax amounts by type and jurisdiction (sales, use, income, withholding) |
| **Billing Engine** | Government contract billing by type (CPFF, T&M, FFP) with indirect cost application |
| **ICE Engine** | DCAA Incurred Cost Electronically submissions (Schedules A through J) |
| **Valuation Engine** | Cost lot management and inventory costing (FIFO, LIFO, weighted average, standard cost) |
| **Reconciliation Engine** | Document matching, payment application, and bank reconciliation |
| **Correction Engine** | Unwind plans for error correction: affected artifacts, compensating entries, mathematical integrity |
| **Approval Engine** | Pure rule evaluation for approvals: rule selection by thresholds, auto-approval, actor authority |
| **Tracer Engine** | Pure computation support for trace bundle assembly |

---

## Appendix B: Non-negotiable rules

### B.1 Six core rules

These six rules are enforced unconditionally. No configuration or policy may override them:

1. **Double-entry balance** — Debits equal credits per currency per entry.
2. **Immutability** — Posted records cannot be modified.
3. **Period lock** — No posting to closed fiscal periods.
4. **Link legality** — Economic links follow type specifications.
5. **Sequence monotonicity** — Sequences are strictly monotonic, gap-safe.
6. **Idempotency** — N retries of the same event produce exactly one journal entry.

### B.2 Full rule registry (R1–R27)

| Rule | Name | Summary |
|------|------|---------|
| R1 | Event immutability | Payload hash check, ORM + DB triggers |
| R2 | Payload hash verification | Same event_id + different payload = protocol violation |
| R3 | Idempotency key uniqueness | UNIQUE constraint + row locking |
| R4 | Balance per currency | Debits = Credits per currency |
| R5 | Rounding line uniqueness | At most one rounding line per entry; threshold enforced |
| R6 | Replay safety | No stored balances — trial balance computed from journal |
| R7 | Transaction boundaries | Each service owns its transaction |
| R8 | Idempotency locking | UniqueConstraint + `SELECT ... FOR UPDATE` |
| R9 | Sequence safety | Locked counter row. `MAX(seq)+1` is forbidden. |
| R10 | Posted record immutability | ORM listeners + 26 PostgreSQL triggers |
| R11 | Audit chain integrity | `hash = H(payload_hash + prev_hash)` |
| R12 | Closed period enforcement | No posting to CLOSED periods |
| R13 | Adjustment policy | `allows_adjustments=True` required |
| R14 | No central dispatch | Strategy registry, no if/switch on event_type |
| R15 | Open/closed compliance | New event type = new strategy only |
| R16 | ISO 4217 enforcement | Currency validation at boundary |
| R17 | Precision-derived tolerance | Rounding tolerance from currency precision |
| R18 | Deterministic errors | Typed exceptions with machine-readable codes |
| R19 | No silent correction | Failures are explicit or produce rounding lines |
| R20 | Test class mapping | Tiered: critical (unit+concurrency), important (unit), architectural |
| R21 | Reference snapshot determinism | JournalEntry records version IDs at posting time |
| R22 | Rounding line isolation | Only the core posting engine may create rounding lines |
| R23 | Strategy lifecycle governance | Version ranges + replay policy per strategy |
| R24 | Canonical ledger hash | Deterministic hash over sorted entries |
| R25 | Kernel primitives only | All monetary/quantity/rate/artifact types from finance_kernel; no parallel types in modules |
| R26 | Journal is the system of record | Module ORM is operational projection only; derivable from journal + link graph |
| R27 | Matching is operational | Variance treatment and ledger impact defined by kernel policy, not module logic |

### B.3 Subledger rules (SL-G1–SL-G10)

| ID | Rule | Enforcement |
|----|------|-------------|
| SL-G1 | **Transaction atomicity** — GL journal writes and subledger entry creation occur in the same database transaction. | Service commits only after both succeed. |
| SL-G2 | **Subledger idempotency** — Unique constraint on `(journal_entry_id, subledger_type, source_line_id)` prevents duplicate entries under retry. | Database unique constraint + service-level duplicate detection. |
| SL-G3 | **Per-currency reconciliation** — Balance comparison and reconciliation occur per currency, not on converted aggregates. | Selectors and journal writer enforce currency parameter. |
| SL-G4 | **Snapshot isolation** — All balance queries (GL and subledger) use the same database session and transaction. | Journal writer passes its session to selectors. |
| SL-G5 | **Post-time enforcement** — When enabled and reconciliation fails, the transaction aborts. No partial state persists. | Journal writer subledger control validation. |
| SL-G6 | **Period-close enforcement** — When enabled and reconciliation fails, GL close is blocked and a failure report is persisted for audit. | Subledger period service. |
| SL-G7 | **Phase completion gate** — No development phase is complete until architecture, idempotency, and atomicity tests pass. | Test suite enforcement. |
| SL-G8 | **Reconciliation concurrency** — Reconciliation writes acquire row locks before updating status. Prevents double-matching. | Concrete subledger service implementations. |
| SL-G9 | **Engine-to-kernel dependency** — Calculation engines may import from the kernel domain. The kernel must never import from engines. | Architecture tests. |
| SL-G10 | **Currency code normalization** — All currency values are normalized to uppercase 3-letter ISO 4217 at ingestion. | R16 enforcement at system boundary. |

### B.4 Approval governance rules (AL-1–AL-11)

| ID | Rule | Enforcement |
|----|------|-------------|
| AL-1 | **Lifecycle state machine** — Status values constrained by DB check constraint; terminal states cannot be changed. | DB check constraint + service-level transition validation. |
| AL-2 | **Policy version snapshot** — Policy version and hash are captured at request creation and never modified. | Write-once fields set at creation. |
| AL-3 | **Currency coherence** — Request currency must match policy currency when policy declares one. | Service-level validation at creation. |
| AL-5 | **Policy drift detection** — Policy downgrades between creation and decision are rejected; upgrades are allowed with audit event. | Service-level version comparison + audit trail. |
| AL-7 | **Decision uniqueness** — Same actor cannot decide twice on the same request. | UNIQUE(request_id, actor_id) constraint. |
| AL-8 | **Tamper evidence** — Request hash is computed at creation and verified on every retrieval. | SHA-256 hash stored at creation; recomputed and compared on read. |
| AL-10 | **Request idempotency** — No duplicate pending requests for the same entity/transition. | Partial unique index on pending/escalated requests. |
| AL-11 | **Covering index** — Optimized query path for pending entity lookups. | Composite index on entity_type, entity_id, status, created_at. |

---

## Appendix C: Kernel architecture reference

This appendix contains detailed technical descriptions of each kernel component. It is reference material for engineers working in the codebase.

### C.1 Domain layer (`finance_kernel/domain/`)

The domain layer is pure logic with zero I/O. It cannot import from the database, services, or selectors packages. Every function in this layer is a deterministic transformation from inputs to outputs.

**Accounting Policy (`accounting_policy.py`).** Defines the declarative structure of an accounting policy: a trigger (which event types it matches), a meaning (what economic reality it represents), ledger effects (which accounts to debit and credit), and guards (conditions that reject or block events). Policies use semantic account roles — not chart-of-accounts codes — so the same policy can serve different organizations with different account numbering.

**Policy Compiler (`policy_compiler.py`).** Compiles raw policy definitions into executable form. Validates guard expressions against a restricted AST (no arbitrary code execution), checks for ambiguous dispatch (two policies matching the same event), and produces a frozen policy pack.

**Policy Selector (`policy_selector.py`).** Given an event, selects exactly one matching policy using where-clause dispatch. If zero policies match, the event is rejected. If multiple policies match, precedence rules (specificity, priority, scope depth) resolve the ambiguity. Exactly one policy per event is an invariant (P1).

**Policy Authority (`policy_authority.py`).** Governs policy admissibility — checks effective date ranges, ensures the policy is from a published configuration, and enforces precedence rules.

**Meaning Builder (`meaning_builder.py`).** Extracts economic meaning from a business event using the selected policy's meaning definition. Produces a structured representation of what the event means economically (inventory increase, revenue recognition, expense accrual).

**Accounting Intent (`accounting_intent.py`).** The intermediate representation between policy interpretation and journal writing. An AccountingIntent expresses the desired posting using account roles (e.g., "debit INVENTORY, credit GRNI") rather than specific account codes. Role resolution to COA codes happens at posting time.

**Ledger Registry (`ledger_registry.py`).** Maps semantic account roles to actual chart-of-accounts codes. Each role resolves to exactly one account per ledger (invariant L1). Role bindings are effective-dated and versioned.

**Economic Link (`economic_link.py`).** Models first-class relationships between financial artifacts. A purchase order is FULFILLED_BY a receipt; a receipt is PAID_BY a payment; an entry is REVERSED_BY a correction. These "why pointers" let auditors trace the complete lifecycle of any business transaction across documents and time.

**Valuation (`valuation.py`).** Pure valuation functions for inventory costing (FIFO, LIFO, weighted average, standard cost) and cost-layer management. No I/O — mathematical functions over cost lot data.

### C.2 Models layer (`finance_kernel/models/`)

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

**SubledgerEntryModel.** Entity-level derived index linked to GL journal entries. Records subledger type, entity ID, source document type, journal entry linkage, side, amount, currency, effective date, and reconciliation status. Immutability is enforced on financial fields after posting; reconciliation status fields remain mutable for controlled updates. Idempotency via unique constraint on `(journal_entry_id, subledger_type, source_line_id)` (invariant SL-G2).

**SubledgerReconciliationModel.** Match-level reconciliation history — records individual entry-to-entry pairs with reconciled amount, timestamp, and match completeness flag.

**ReconciliationFailureReportModel.** Period-close audit artifact. When subledger-to-GL reconciliation fails during period close (SL-G6), captures GL control balance, subledger aggregate balance, delta amount, per-entity breakdown, and timestamp. Always immutable (append-only).

**SubledgerPeriodStatusModel.** Tracks close state of each subledger per fiscal period. Status: OPEN, RECONCILING, CLOSED. Unique constraint on `(subledger_type, period_code)`.

**ApprovalRequestModel / ApprovalDecisionModel.** Approval governance persistence. Requests track the full approval lifecycle with policy version snapshots, tamper-evident hashing, and duplicate prevention. Decisions are append-only with a unique constraint on `(request_id, actor_id)`.

**Module-level ORM models.** Each of the 18 ERP modules has its own ORM models (106 total) following the kernel's established patterns: UUID primary keys with timestamps, frozen-DTO round-trip methods, and immutability enforcement on financial fields.

### C.3 Services layer (`finance_kernel/services/`)

Services are the imperative shell — they perform I/O, own transaction boundaries, and orchestrate the pure domain logic.

**PostingOrchestrator.** The central DI container and service factory. Creates services once per transaction and injects them, preventing duplicate instances. Exposes: AuditorService, PeriodService, LinkGraphService, ReferenceSnapshotService, PartyService, ContractService, IngestorService, JournalWriter, ReversalService, ApprovalService, WorkflowExecutor, OutcomeRecorder, EngineDispatcher, InterpretationCoordinator, MeaningBuilder, and subledger services.

**EngineDispatcher.** Runtime engine dispatch. When a policy declares `required_engines`, the dispatcher resolves parameters from the compiled pack, validates inputs against the engine contract's schema, invokes the engine, and collects trace records. Wired into InterpretationCoordinator — engine invocation happens after policy selection, before intent construction.

**InterpretationCoordinator.** The primary posting pipeline. Accepts a business event, selects a policy, extracts meaning, dispatches engines, builds an accounting intent, writes journal entries, records the outcome, and persists the decision journal. **Invariant:** When a policy declares `required_engines`, success requires one success trace per required engine — no engine run implies no `all_succeeded=True` (prevents silent no-op postings from a mocked dispatcher).

**JournalWriter.** Resolves account roles to COA codes (L1), validates balance per currency, allocates sequence numbers, creates journal lines, verifies R21 reference snapshots, and enforces subledger reconciliation contracts. Multi-ledger postings are atomic (P11).

**OutcomeRecorder.** Records interpretation outcomes. Enforces one outcome per event (P15). Status transitions: PENDING → POSTED/FAILED; FAILED → RETRYING/ABANDONED; RETRYING → POSTED/FAILED. Failed outcomes surface through a work queue for human review.

**IngestorService.** Validates incoming events: payload hash verification (R2), duplicate detection, protocol violation detection.

**SequenceService.** Allocates monotonic sequence numbers via locked counter row. `MAX(seq)+1` is forbidden (R9).

**AuditorService.** Maintains the cryptographic hash chain for audit events (R11). Validates chain integrity on demand.

**ApprovalService.** Manages approval request lifecycle: creation with tamper-evident hashing (AL-8), decision recording with policy drift detection (AL-5), auto-approval, cancellation, and expiry. Tamper detection recomputes hashes on retrieval.

**WorkflowExecutor.** Runtime transition executor. Validates transitions, resolves approval policies, evaluates auto-approval, creates approval requests, and gates transitions on approval status.

**ReversalService.** Journal entry reversal orchestrator. Validates preconditions, delegates to JournalWriter, creates REVERSED_BY economic link, records audit event — all atomically.

**PeriodService.** Fiscal period lifecycle. Enforces R12/R13. At period close, validates subledger reconciliation. Serialized via row-level locking.

**LinkGraphService.** Persists economic links, detects cycles (L3), provides graph traversal for lifecycle tracing.

**Concrete SubledgerServices (AP, AR, Inventory, Bank, Contract).** Five implementations owning entity-specific validation, subledger entry persistence, and balance queries. Transaction-atomic with journal writes (SL-G1). Idempotent via unique constraint (SL-G2).

**SubledgerPeriodService.** Orchestrates subledger period close. Reconciles aggregate balances, creates failure reports on mismatch (SL-G6), blocks GL close until subledgers reconcile.

**SubledgerPostingBridge.** Bridges AccountingIntent to concrete subledger service calls after journal lines are committed. Resolves entity IDs from event payloads using convention-based field lookup.

### C.4 Selectors layer (`finance_kernel/selectors/`)

Read-only query services that assemble derived views without creating new persistent state.

**TraceSelector.** Trace bundle assembler. Given an event ID or journal entry ID, reconstructs the complete lifecycle: source event, journal entries with lines, interpretation outcome with decision journal, R21 snapshot, economic links, audit trail, integrity verification.

**LedgerSelector.** Computes trial balances, account balances, and ledger summaries by reading journal lines forward. No stored balances.

**SubledgerSelector.** Read-only subledger queries. Eight methods: `get_entry`, `get_entries_by_entity`, `get_entries_by_journal_entry`, `get_open_items`, `get_balance`, `get_aggregate_balance`, `get_reconciliation_history`, `count_entries`. All balance methods require currency parameter (SL-G3). Uses caller's session (SL-G4).

### C.5 Database layer (`finance_kernel/db/`)

**Engine management (`engine.py`).** PostgreSQL connection pooling, session factory, transactional scope management.

**Immutability enforcement (`immutability.py`, `triggers.py`, `sql/`).** Three-layer defense: ORM event listeners, 26 PostgreSQL triggers across 11 SQL files, session-level flush guards.

### C.6 Engine contracts and runtime dispatch

Every engine declares a contract specifying engine name/version, parameter schema (JSON Schema), and input fingerprint rules. At runtime, the EngineDispatcher reads policy engine declarations, resolves parameters, validates against the contract schema, and invokes the engine. Every invocation is traced via `@traced_engine` decorator and persisted in the decision journal.

### C.7 Variance disposition

When a variance engine computes a difference between expected and actual costs, the disposition determines routing:

- **POST_TO_VARIANCE_ACCOUNT** — Expensed to a dedicated variance GL account.
- **CAPITALIZE_TO_INVENTORY** — Absorbed into inventory cost.
- **ALLOCATE_TO_COGS** — Allocated proportionally to Cost of Goods Sold.
- **WRITE_OFF** — Immediately expensed.

Declared in policy YAML and read by the EngineDispatcher.

---

## Appendix D: Configuration reference

### D.1 Configuration structure

A configuration set is a directory of YAML fragments:

```
sets/US-GAAP-2026-v1/
  root.yaml                   # Identity, scope, capabilities, precedence
  chart_of_accounts.yaml      # Role-to-COA-code bindings
  ledgers.yaml                # Ledger definitions
  engine_params.yaml          # Calculation engine parameters
  controls.yaml               # Governance controls
  subledger_contracts.yaml    # Subledger control contracts
  approval_policies.yaml      # Approval rules
  policies/                   # One YAML per domain
     inventory.yaml
     ap.yaml
     ar.yaml
     payroll.yaml
     ... (12 domain files)
```

### D.2 Core components

**Root manifest (`root.yaml`).** Configuration identity, version, scope (legal entity, jurisdiction, regulatory regime, currency, effective dates), enabled capabilities, and precedence rules.

**Chart of accounts (`chart_of_accounts.yaml`).** Maps semantic account roles to COA codes and ledgers. ~170 role bindings in the baseline US GAAP configuration, from basic accounts to specialized government contracting roles (WIP_DIRECT_LABOR, WIP_FRINGE, INDIRECT_RATE_VARIANCE).

**Ledger definitions (`ledgers.yaml`).** Ledgers in use: General Ledger, Inventory Subledger, AP Subledger, Bank Subledger, Contract Subledger. Each declares required account roles.

**Policy definitions (`policies/*.yaml`).** Each policy declares: trigger (event type + where-clause predicates), meaning (economic interpretation), ledger effects (roles to debit/credit), guards (rejection conditions in restricted AST), line mappings, required engines, and engine parameters ref.

**Engine parameters (`engine_params.yaml`).** Configuration for calculation engines — allocation methods, variance thresholds, matching tolerances, aging buckets, tax methods, DCAA cascade rates. Validated against engine contract JSON Schemas at compilation.

**Subledger contracts (`subledger_contracts.yaml`).** Subledger-to-GL control contracts specifying GL control account role, sign convention, reconciliation timing, tolerance, and enforcement flags. Control account roles are resolved to COA codes at compile time.

**Approval policies (`approval_policies.yaml`).** Rules governing approval authority: workflow targets, prioritized rules with amount ranges, required roles, minimum approver counts, auto-approval thresholds, guard expressions, and escalation timeouts. SHA-256 policy hash computed for drift detection.

**Controls (`controls.yaml`).** Global governance rules that apply across all events.

### D.3 Build pipeline

Configuration flows through three stages:

1. **Assembly.** Reads YAML fragments, composes a single frozen `AccountingConfigurationSet`, computes SHA-256 checksum.
2. **Validation.** Structural integrity: engine existence, guard expression parsing, dispatch ambiguity detection, role binding coverage, capability tag references.
3. **Compilation.** Produces `CompiledPolicyPack` — the frozen runtime artifact. Guard expressions pre-validated, policies indexed, engine contracts resolved, subledger contracts compiled, canonical fingerprint assigned. Architecture tests enforce that every compiled field has a runtime consumer.

### D.4 Fingerprint pinning

Optional mechanism to cryptographically seal approved configurations. If a pin file exists, the compiled pack's canonical fingerprint must match the pinned value. Unauthorized YAML modifications produce a different fingerprint and are rejected at runtime.

---

## Appendix E: Testing and runtime enforcement

### E.1 Test suite

Over 4,500 tests across 24 categories:

| Category | Purpose |
|----------|---------|
| **posting** | Balance, idempotency, period lock |
| **audit** | Hash chain integrity, immutability enforcement |
| **concurrency** | Sequence safety under load, race conditions |
| **adversarial** | Account deletion while referenced, rounding abuse |
| **architecture** | Import boundaries, kernel isolation, primitive reuse |
| **domain** | Profiles, policy registry, event schemas, subledger control |
| **replay** | Deterministic replay, rule version governance |
| **crash** | Fault injection: failure mid-transaction |
| **fuzzing** | Hypothesis-based property testing |
| **metamorphic** | Reversal and re-posting equivalence |
| **security** | SQL injection, input sanitization, privilege escalation |
| **database_security** | PostgreSQL trigger enforcement, raw SQL attempts |
| **modules** | Per-module integration (AP, AR, Inventory, etc.) |
| **engines** | Pure engine computation |
| **services** | Service-level integration |
| **integration** | End-to-end pipeline |
| **multicurrency** | Multi-currency posting and conversion |
| **trace** | Trace bundle assembly, DTO contracts |
| **demo** | Auditor-readable trace output |
| **unit** | Individual components |
| **period** | Fiscal period lifecycle |
| **reporting** | Financial statement generation |

Every non-negotiable rule has corresponding tests. Architecture tests enforce import boundaries automatically.

### E.2 Runtime guard enforcement

**Subledger-to-GL reconciliation.** Five subledgers each declare a control contract. Enforced at post-time (SL-G5: transaction aborts on mismatch) and period-close (SL-G6: GL close blocked, failure report persisted). Both use per-currency comparison (SL-G3) within the same database transaction (SL-G4). Subledgers close before GL in declared order: inventory, WIP, AR, AP, assets, payroll, GL.

**Reference snapshot freshness.** The journal writer validates that reference snapshots are current. Stale snapshots (from a previous configuration version) are rejected.

**Link graph acyclicity (L3).** Before creating an economic link, the system walks from child to parent via the same link type. If the parent is reachable, the link would create a cycle and is rejected.

**Correction period lock.** Artifacts in closed fiscal periods cannot be voided or corrected. The correction must use a current-period adjusting entry.

### E.3 Financial exception lifecycle

Every posting attempt produces a durable outcome record. Failed postings become first-class financial cases.

**Outcome state machine:**

```
PENDING → POSTED      (success)
PENDING → FAILED      (guard, engine, or policy failure)
FAILED  → RETRYING    (human initiates retry)
RETRYING → POSTED     (retry succeeds)
RETRYING → FAILED     (retry fails again)
FAILED  → ABANDONED   (human gives up)
```

POSTED and ABANDONED are terminal. Failed outcomes record structured failure context (type, message, engine trace references, payload fingerprint) and surface through a work queue for human review.
