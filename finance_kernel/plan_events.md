    Phase 1 spec: event binding and economic interpretation kernel — primitives and non-breakable guarantees

Purpose

Phase 1 defines the minimum event binding and interpretation layer that unifies operational, economic, and financial activity under a single, deterministic contract. This module serves as the canonical bridge between real-world facts emitted by upstream systems and the finance kernel’s legally governed journal and ledgers.

It must remain correct under retries, concurrency, crashes, replays, policy changes, period locking, schema evolution, and adversarial input. All downstream modules (inventory, manufacturing, purchasing, T&E, assets, projects, cash, contracts) emit BusinessEvents that are classified, interpreted, and resolved into EconomicEvents that may trigger state transitions and financial postings.

This module does not replace ledgers. It governs how facts become financially and operationally meaningful before they reach them, and it enforces total accountability so no accepted fact can disappear.

Non-goals

* No UI beyond internal inspection and debugging
* No financial reporting, statements, or consolidations
* No direct journal or ledger writes by source systems
* No domain-specific workflows (PO approval, expense approval, production routing)
* No forecasting, simulation, or scenario modeling
* No master data management (accounts, vendors, items, employees)

Terms

* BusinessEvent: an immutable statement that something happened in the real world or a source system
* EventSchema: a versioned schema contract for a BusinessEvent payload
* InterpretationOutcome: the terminal disposition of a BusinessEvent (POSTED, NON_POSTING, REJECTED, POSTING_BLOCKED)
* EconomicEvent: a deterministic, interpreted fact derived from a BusinessEvent and policies
* EconomicTypeContract: the declared posting and dimension contract for an economic_type
* EntityRef: a universal pointer to any domain object
* Policy: versioned, effective-dated rules that govern interpretation and behavior
* Classification: mapping logic that assigns economic meaning to events
* PostingRule: mapping from EconomicEvents to journal entries (per ledger/ruleset)
* Projection: a deterministic reducer that builds read models and balances from events
* StateMachine: a governed lifecycle engine driven by EconomicEvents
* Correction: an append-only adjustment or reversal linked to a prior event or derived artifact

Core primitives

EventSchema

Versioned schema definition for BusinessEvent payloads.

Structure

* schema_id
* event_type (string / enum)
* schema_version
* source_system (optional qualifier)
* json_schema (or equivalent)
* effective_from
* effective_to
* created_at
* created_by

Hard invariants

* A BusinessEvent cannot be accepted unless an EventSchema exists for (event_type, schema_version)
* EventSchema versions are immutable once referenced by any accepted BusinessEvent
* Unknown event_type or schema_version is rejected (fail closed)

BusinessEvent

The atomic unit of truth. All source systems write facts in this form.

Structure

* event_id (UUID)
* event_key (idempotency key, globally unique)
* event_seq (monotonic, system-assigned)
* event_type (string / enum)
* schema_version
* occurred_at (timestamp, source-reported)
* ingested_at (timestamp, system-recorded)
* source_system
* actor_id (optional)
* subject_ref (EntityRef)
* payload (schema-validated JSON)
* prev_hash
* hash

Hard invariants

* Immutable once accepted
* Exactly one BusinessEvent exists per event_key
* event_seq is strictly increasing and unique
* Hash-chained for tamper detection
* Replayable in event_seq order; occurred_at is informational and may be out of order
* Contains no derived meaning, accounting, or workflow state

EntityRef

Universal reference type used to bind events, state, and postings without domain coupling.

Structure

* entity_type
* entity_id

Hard invariants

* Must resolve to an existing domain object at time of binding (or be explicitly allowed as “external reference” by policy)
* May reference any module (Project, Asset, Employee, PO, MO, SKU, Contract, Account, Location)
* Never enforces foreign key constraints across modules

Policy

Versioned, effective-dated configuration that defines how events are interpreted and governed.

Structure

* policy_id
* policy_type
* effective_from
* effective_to
* scope (EntityRef or wildcard)
* ruleset (JSON / DSL)
* version
* created_at
* created_by

Hard invariants

* Policies are immutable once active
* Policy version used for an interpretation must be stored on all derived records
* Historical replays must use the policy version effective at the BusinessEvent.occurred_at time (or an explicitly defined alternative basis such as ingested_at) and this basis must be fixed per policy_type

Classification

Mapping layer that assigns economic meaning to BusinessEvents.

Structure

* classification_id
* event_type
* conditions (rule expression over payload and context)
* economic_type
* attributes (dimensions, tags, flags)
* policy_id
* version

Hard invariants

* Deterministic resolution for any valid BusinessEvent under a given policy + classification version set
* Must produce exactly one EconomicEvent per BusinessEvent or explicitly reject the BusinessEvent
* If multiple classifications match, the event is rejected as CLASSIFICATION_AMBIGUOUS (fail closed)
* Classification logic must be policy-driven, not hard-coded

EconomicTypeContract

Declares the posting and dimensional requirements for each economic_type.

Structure

* economic_type
* posting_required (bool)
* required_ledgers (list of ledger_type or ledger_id)
* allows_non_posting (bool)
* allowed_non_posting_reason_codes (list)
* required_dimensions (list)
* valuation_requirements (optional: quantity required, value required, currency rules)
* effective_from
* effective_to
* version

Hard invariants

* An EconomicEvent cannot finalize unless it satisfies required_dimensions for its economic_type contract
* If posting_required=true and no PostingRule matches for a required ledger, the outcome must be POSTING_BLOCKED (not NON_POSTING)
* NON_POSTING is permitted only when allows_non_posting=true and reason_code is in the allowed list for the contract
* Contract versions are immutable once referenced

EconomicEvent

Resolved, interpreted fact used by both operational state and financial posting layers.

Structure

* econ_event_id
* source_event_id (unique)
* economic_type
* quantity (optional, typed)
* value (Money, optional)
* dimensions (typed key/value set)
* effective_date (economic effective date)
* accounting_basis_timestamp (occurred_at or ingested_at as governed by policy)
* subject_ref (EntityRef)
* policy_version
* classification_id
* economic_type_contract_version
* valuation_basis (optional: price source, fx source, rate timestamp)
* prev_hash
* hash

Hard invariants

* Immutable once created
* Exactly one EconomicEvent exists per source_event_id
* Fully traceable to source BusinessEvent
* Deterministic under replay (same BusinessEvents + same versions → identical EconomicEvents)
* Contains no UI or workflow concepts
* Must satisfy its EconomicTypeContract (dims, valuation requirements) or it cannot finalize

StateMachine

Generic lifecycle engine driven by EconomicEvents.

Structure

* machine_id
* machine_type
* bound_entity (EntityRef)
* current_state
* allowed_transitions
* transition_rules (policy-driven)
* state_version
* prev_hash
* hash

Hard invariants

* State changes only occur via EconomicEvents
* No direct field mutation of state columns
* All transitions are auditable, deterministic under replay, and reversible via compensating events

PostingRule

Pure financial mapping from EconomicEvents into journal entries, parameterized by ledger/ruleset.

Structure

* posting_rule_id
* ruleset_id
* ruleset_version
* target_ledger_type (or ledger_id)
* economic_type
* conditions
* debit_account
* credit_account
* dimension_mapping
* required_dimensions (optional, may refine contract)
* policy_id
* effective_from
* effective_to

Hard invariants

* PostingRules are immutable once active
* Every JournalEntry must reference the PostingRule (ruleset_id, ruleset_version) used
* PostingRules must be selected deterministically under replay; if multiple rules match, fail closed (POSTING_RULE_AMBIGUOUS)
* Multiple ledgers may use different PostingRule sets over the same EconomicEvents

InterpretationOutcome

Terminal disposition of each accepted BusinessEvent.

Structure

* outcome_id
* source_event_id (unique)
* status: POSTED | NON_POSTING | REJECTED | POSTING_BLOCKED
* econ_event_id (nullable)
* journal_entry_ids (nullable)
* ledgers_impacted (nullable)
* policy_version (nullable)
* classification_id (nullable)
* economic_type (nullable)
* economic_type_contract_version (nullable)
* posting_ruleset_versions (nullable)
* reason_code
* created_at
* prev_hash
* hash

Hard invariants

* Every accepted BusinessEvent must have exactly one InterpretationOutcome
* POSTED requires econ_event_id and at least one journal_entry_id
* NON_POSTING requires econ_event_id, reason_code, and must be permitted by EconomicTypeContract
* POSTING_BLOCKED requires econ_event_id and reason_code; it is a hard failure state that must be visible and countable
* REJECTED is durable and queryable with reason_code; rejected events do not create EconomicEvents or postings

Projection

Deterministic read-model builder.

Structure

* projection_id
* projection_type
* source_type (BusinessEvent or EconomicEvent or JournalEntry)
* reducer_definition
* snapshot_interval
* projection_version
* prev_hash
* hash

Hard invariants

* Rebuildable from source events alone (snapshots are optimizations, not sources of truth)
* Must be versioned and auditable
* Reducers must consume inputs in event_seq (or journal seq) order
* Projection corrections must be achieved via replay, not in-place mutation

Correction (append-only adjustments and reversals)

All fixes are expressed as new events; nothing is edited.

Structure (minimum)

* correction_event_id (BusinessEvent.event_id)
* corrects_event_id (optional)
* corrects_econ_event_id (optional)
* corrects_journal_entry_id (optional)
* correction_type: ADJUST | REVERSE | RECLASSIFY
* delta_payload (typed, schema-validated)

Hard invariants

* No mutation of BusinessEvent, EconomicEvent, JournalEntry, or prior projections
* Corrections must link to the original artifact(s) they adjust
* Corrections must be deterministic under replay and produce compensating EconomicEvents and JournalEntries as required

Universal flow

Real-world action
→ BusinessEvent (schema validated, sequenced, hash-chained)
→ Classification + Policy (deterministic, fail-closed)
→ EconomicEvent (contract validated)
→ InterpretationOutcome (terminal, durable)
→

* StateMachine transitions
* PostingRules → JournalEntry → Ledger
* Projections → operational and financial read models

This flow is mandatory for all modules.

Ledger relationship

This module does not store financial balances. It governs what is allowed to become financially real and ensures no accepted fact can disappear without an explicit terminal outcome.

Hard boundary

* No BusinessEvent or EconomicEvent may write directly to a Ledger
* Only JournalEntries produced by PostingRules may affect ledger balances
* Every JournalEntry must reference source_economic_event_id, posting_rule_version (ruleset_id + ruleset_version), and policy_version
* Idempotency must be enforced across (source_economic_event_id, ledger_id, ruleset_version) so retries cannot double-post

Ledger definition (external dependency)

* ledger_id
* ledger_type (GL, Subledger, Management, Statutory)
* posting_ruleset_version
* period_regime
* effective_from
* effective_to

Hard invariants

* All ledger balances must be derivable from the Journal alone
* Period locking applies at the Journal and Ledger layer, not the BusinessEvent layer
* Ledger results must be reproducible under replay using recorded policy and ruleset versions

Period governance and late events

Rules

* EconomicEvent.effective_date expresses economic timing
* JournalEntry.posting_period is governed by the ledger’s period_regime and posting policies
* If effective_date falls in a closed period:

  * either REJECT (reason_code=CLOSED_PERIOD)
  * or POST as PriorPeriodAdjustment in the current open period with explicit linkage to the intended historical period (reason_code=PPA)

Hard invariants

* Closed periods are never silently altered
* Period routing decisions must be deterministic and replayable (policy-driven)
* All PPAs must be traceable to originating BusinessEvent and EconomicEvent

Non-breakable guarantees

Determinism

* Replaying the same BusinessEvents with the same EventSchema, policy, classification, contract, and posting ruleset versions must produce identical EconomicEvents, InterpretationOutcomes, JournalEntries, and ledger balances

Idempotency

* Duplicate BusinessEvents by event_key must never produce duplicate EconomicEvents or postings
* Exactly one EconomicEvent exists per source_event_id
* Exactly one JournalEntry exists per (source_economic_event_id, ledger_id, ruleset_version)

Total accountability (no economic leakage)

* Every accepted BusinessEvent must have exactly one InterpretationOutcome
* There are no “accepted but unaccounted” events; POSTING_BLOCKED is explicit and countable

Traceability

* Every EconomicEvent links to exactly one BusinessEvent
* Every JournalEntry links to exactly one EconomicEvent
* Every ledger balance is explainable as a sum of JournalEntries

Governance

* EventSchema, Policy, Classification, EconomicTypeContract, and PostingRule versions are immutable once referenced
* Historical financial results must remain reproducible under audit

Isolation

* Source systems cannot influence financial outcomes except through BusinessEvents
* Finance kernel cannot influence operational systems except through projections and published balances; it cannot mutate source events

Concurrency safety

* Event ingestion, classification, and posting must be safe under parallel writes and retries
* All ambiguity conditions fail closed (classification ambiguity, posting rule ambiguity)

Minimal Phase 1 scope

Required primitives

* EventSchema
* BusinessEvent
* EntityRef
* Policy
* Classification
* EconomicTypeContract
* EconomicEvent
* PostingRule
* InterpretationOutcome
* Projection
* Correction (as an event pattern, not a separate mutable object)

Deferred to later phases

* Complex StateMachine orchestration
* Multi-basis accounting ledgers beyond a small fixed set
* What-if and simulation engines
* Cross-ledger reconciliation tooling beyond invariant checks and exception surfacing

Design constraints

* No module-specific pipelines or custom posting paths
* No domain logic embedded in kernel code
* All behavior must live in Policy, Classification, EconomicTypeContract, or PostingRule definitions
* All derived objects must be replayable from BusinessEvents alone
* Fail closed on ambiguity or missing configuration; never “best effort” a posting

Compliance alignment

This module must support:

* Full audit trace from financial statements to source events (SOX)
* Cost traceability from ledger to operational facts (DCAA / CAS)
* Period finality with late-event handling and explicit PPAs (FAR / statutory accounting)
* Multi-basis rule application over identical facts via multiple ledgers and rulesets (GAAP / tax / management)
* Tamper-evident event history (hash chaining)

Mental model

This module is not an ERP feature. It is a fact interpretation system.

Events describe reality.
EconomicEvents describe meaning.
Ledgers describe law.

Addendum: configuration and financial safety invariants

Purpose

This addendum defines mandatory, non-breakable invariants that govern the activation, execution, and lifecycle of all configuration artifacts (EventSchema, Policy, Classification, EconomicTypeContract, PostingRule, Projection) and all derived financial and operational outputs. These invariants exist to prevent silent economic errors, non-determinism, and configuration-induced defects.

These rules are binding across all modules and environments.

---

Configuration activation invariants

1. Compile gate
   No configuration artifact may become active unless it passes a compile step that proves:

* Schema validity and version integrity
* Reference integrity (all referenced fields, dimensions, accounts, ledgers, and entity types exist)
* Deterministic evaluation (no ambiguous rule matches)
* Posting coverage for all EconomicTypeContracts where posting_required=true

Failure at compile time prevents activation.

2. Immutability after use
   Once a configuration version is referenced by any BusinessEvent, EconomicEvent, or JournalEntry, it becomes immutable. Changes require a new version with a new effective window.

3. Effective-dating coherence
   Configuration versions must not overlap in effective windows for the same scope and type. At any timestamp and scope, at most one version may be active.

---

Classification and interpretation invariants

4. Total classification coverage
   For every accepted BusinessEvent, exactly one of the following must occur:

* One Classification resolves to one EconomicEvent
* The BusinessEvent is REJECTED with a durable reason_code

There is no “accepted but unclassified” state.

5. Deterministic resolution
   If more than one Classification rule matches a BusinessEvent, the event must be rejected with reason_code=CLASSIFICATION_AMBIGUOUS. “First match wins” behavior is prohibited.

6. Contract enforcement
   An EconomicEvent cannot finalize unless it satisfies its EconomicTypeContract, including:

* Required dimensions
* Valuation requirements (quantity/value presence, currency rules, FX source)
* Posting requirements

Violations result in POSTING_BLOCKED with an explicit reason_code.

---

Posting and ledger invariants

7. Posting coverage
   For any EconomicEvent where posting_required=true:

* A JournalEntry must be created for every ledger in required_ledgers
* Or the InterpretationOutcome must be POSTING_BLOCKED

Silent non-posting is prohibited.

8. Posting determinism
   If more than one PostingRule matches for the same (ledger, ruleset, EconomicEvent), posting must fail closed with reason_code=POSTING_RULE_AMBIGUOUS.

9. Ledger balancing
   Every JournalEntry must satisfy:

* Sum(debits) = Sum(credits)
* All referenced accounts are active for the posting period
* All required dimensions for the target ledger and account are present

Failure prevents commit.

10. Idempotent posting
    For each (source_economic_event_id, ledger_id, ruleset_version) tuple, at most one JournalEntry may exist. Retries must reuse or reference the original posting.

---

Valuation and replay invariants

11. Valuation determinism
    For any EconomicEvent with monetary value, the following must be explicitly recorded:

* Valuation source (price basis)
* FX rate source
* Rate timestamp
* Rounding policy

Replays must reproduce identical monetary values within defined rounding tolerances.

12. Replay equivalence
    Rebuilding the system from BusinessEvents using the same configuration versions must reproduce identical:

* EconomicEvents
* InterpretationOutcomes
* JournalEntries (including posting_period assignment)
* Ledger balances

Deviations are defects.

---

Period and governance invariants

13. Closed-period protection
    If an EconomicEvent.effective_date falls in a closed period:

* The event must be REJECTED, or
* Posted as a PriorPeriodAdjustment in the current open period with explicit linkage to the intended historical period

Closed periods must never be silently altered.

14. Terminal state exhaustiveness
    Every accepted BusinessEvent must have exactly one InterpretationOutcome in the set:

* POSTED
* NON_POSTING
* REJECTED
* POSTING_BLOCKED

Terminal states are mutually exclusive and irreversible.

---

Reconciliation and control invariants

15. Control account reconciliation
    For every control ledger and corresponding subledger projection, the following must hold per period:

* Subledger total = GL control account balance
  Or an explicit ExceptionEvent must be emitted and recorded.

16. Economic lifecycle closure
    For economic types that imply settlement or clearing (e.g., GRNI, AP, AR, employee payable):

* There must exist a valid clearing path defined by policy
* Open balances beyond policy thresholds must generate ExceptionEvents

---

Change promotion invariants

17. Shadow execution
    A new configuration version may not be promoted to active for posting unless it has:

* Been executed in shadow mode against a representative historical BusinessEvent set
* Produced zero deltas outside an approved change manifest

18. Differential approval
    Any approved delta must be documented with:

* Economic rationale
* Affected economic_types and ledgers
* Effective date window
* Rollback plan

---

Operational monitoring invariants

19. Coverage monitoring
    The system must continuously assert:

* count(BusinessEvent accepted) = count(InterpretationOutcome)
* count(POSTING_BLOCKED) and count(REJECTED) are visible, queryable, and alertable

20. Leakage detection
    Invariant monitors must exist for:

* Inventory subledger vs inventory control account
* AP open items vs AP control account
* AR open items vs AR control account
* Cash ledger vs bank cleared balances
* Asset register vs fixed asset control account

Mismatches must emit ExceptionEvents, not silent logs.

---

Design principle

Configuration is treated as executable law, not metadata.

If a rule cannot be compiled, proven deterministic, replayed, reconciled, and audited, it cannot be allowed to govern economic reality.
