Below is your **corrected Phase 1 spec** with the four architectural gaps closed, without changing your structure or tone. This is now internally consistent, deterministic, audit-safe, and period-governed.

---

## Phase 1 spec: finance kernel primitives and non-breakable guarantees

## Purpose

Phase 1 defines the minimum accounting kernel that can serve as a system of record in an event-based ERP. It must remain correct under retries, concurrency, crashes, replays, and adversarial input patterns. All downstream modules (inventory, purchasing, manufacturing, AR/AP) will produce events that map into this kernel.

## Non-goals

* No AP/AR documents (bills, invoices) as first-class workflow entities
* No allocations, burdens, revenue recognition, WIP, or costing pools
* No UI beyond internal tooling for inspection and debugging

## Terms

* Event: a business fact emitted by a source system/module
* Posting: creating a JournalEntry + JournalLines from an event
* Journal: append-only record of postings
* Ledger: derived view over posted journal lines
* Projection: any materialized view derived from journal lines (rebuildable)
* Idempotency: repeated submissions of the same event produce exactly one posted outcome
* Immutability: posted facts cannot be edited or deleted
* Effective date: the accounting date used for fiscal periods and reporting
* Sequence: a monotonic global ordering number assigned at posting time

## Architecture overview

### Canonical flow

Event (immutable identity)
→ posting function (deterministic)
→ write-ahead persistence (idempotency + atomicity)
→ journal entry + lines (append-only, ordered)
→ projections (optional caches, always rebuildable)

### Single source of financial truth

The only source of truth is posted JournalLines. Everything else is derived, cached, or navigational.

## Data contracts

## Event (financially postable)

This is the minimum event envelope the finance kernel accepts.

### Required fields

* event_id: UUID (globally unique)
* event_type: string (namespaced, stable)
* occurred_at: timestamp (when it happened in reality)
* effective_date: date (accounting date for fiscal periods)
* actor_id: string/UUID
* producer: string (module/system name)
* payload: JSON (domain-specific)
* payload_hash: bytes/string (hash of canonicalized payload)
* schema_version: int

### Hard invariants

* event_id is never reused
* (event_id, payload_hash) is immutable; if an event_id arrives with a different payload_hash, it is rejected as a protocol violation
* occurred_at and effective_date are immutable for a given event_id
* effective_date determines fiscal period inclusion, locking, and reporting

### Required indexes

* event_id unique
* (event_type, effective_date)
* (effective_date, occurred_at)

## Money

### Fields

* amount: decimal(38, 9)
* currency: ISO 4217 code

### Hard invariants

* No floats in any path
* Rounding is centralized, deterministic, and versioned
* No implicit conversions; conversion requires explicit ExchangeRate and explicit operation

## ExchangeRate

### Fields

* rate_id: UUID
* from_currency
* to_currency
* rate: decimal
* effective_at: timestamp
* source: string
* created_at: timestamp

### Hard invariants

* Rates are append-only
* Posting uses a frozen rate selection (rate_id is stored on the JournalEntry or JournalLine)
* Historical postings never re-evaluate rates

## Account (chart of accounts)

### Fields

* account_id: stable identifier
* name
* type: asset | liability | equity | revenue | expense
* normal_balance: debit | credit
* is_active: bool
* tags: set (direct, indirect, unallowable, billable, rounding, etc.)

### Hard invariants

* Accounts referenced by posted lines cannot be deleted
* account_id is immutable
* type and normal_balance are immutable once referenced
* At least one **rounding account** must exist per currency or ledger

## Dimensions (Phase 1)

### Baseline dimension set

* org_unit_id (or legal_entity_id)
* project_id (nullable but typed)
* contract_id (nullable but typed)

### Hard invariants

* Dimension keys are fixed identifiers, not free text
* Dimension values are stable IDs; names may change without altering history
* Required dimensions are enforced by posting rules

## JournalEntry

### Fields

* journal_entry_id: UUID
* source_event_id: UUID
* source_event_type: string
* occurred_at: timestamp
* effective_date: date
* posted_at: timestamp
* actor_id
* status: draft | posted | reversed
* reversal_of_journal_entry_id: UUID nullable
* idempotency_key: string
* posting_rule_set_version: int
* seq: bigint (monotonic global sequence)
* metadata: JSON

### Hard invariants

* Posted JournalEntry is immutable
* Exactly one posted JournalEntry exists per idempotency_key
* Every posted JournalEntry has a unique, increasing seq
* Ledger rebuilds must process entries in seq order
* Reversals create new JournalEntries; originals never change

## JournalLine

### Fields

* journal_line_id: UUID
* journal_entry_id
* account_id
* side: debit | credit
* amount: Money
* dimensions: map<dimension_key, dimension_value>
* is_rounding: boolean
* line_memo: string nullable

### Hard invariants

* A line is either debit or credit, never both
* account_id must exist and be active at time of posting
* Required dimensions must be present
* If multi-currency rounding produces a remainder, exactly one line must be marked is_rounding=true

## Idempotency, atomicity, and concurrency

## Idempotency model

### Idempotency key

```
idempotency_key = producer + ":" + event_type + ":" + event_id
```

### Hard invariants

* Posting the same idempotency_key always returns the same JournalEntry
* Posting is safe under retries and concurrency without caller coordination

### Storage enforcement

* Unique constraint on JournalEntry(idempotency_key)
* Draft rows must resolve to exactly one posted row

## Atomicity

A posting must be all-or-nothing:

* Either the JournalEntry and all JournalLines exist and are posted
* Or nothing exists that affects the ledger

## Concurrency

* Concurrent posts of the same event converge to one JournalEntry
* Concurrent posts of different events do not block each other
* Partial drafts must be lock-resolved or deterministically healed

## Overwrite protection

If the same event_id arrives with a different payload_hash:

* Reject with hard error
* Emit AuditEvent (protocol violation)
* Require correction via a new event_id

## Period control and locking

## Fiscal period model

* Financial reporting is governed by effective_date, not occurred_at
* Fiscal periods have status: open | closed

### Hard invariants

* No JournalEntry may be posted with an effective_date inside a closed period
* Corrections to closed periods must post into the current open period
* Closed periods are immutable once closed

## Posting algorithm

## API surface

* ingest_event(event_envelope) → accepted | rejected
* post_event(event_id) → journal_entry_id | already_posted
* get_journal_entry(journal_entry_id) → entry + lines
* reverse_journal_entry(journal_entry_id, reversal_event_envelope) → reversal_entry_id
* ledger_query(filters, as_of_effective_date) → derived lines / balances

## Required posting transaction

Within a single database transaction:

1. Validate event envelope

   * event_id not seen with different payload_hash
   * schema_version supported
   * effective_date not in closed period

2. Create or fetch JournalEntry by idempotency_key

   * Insert draft
   * On conflict, fetch existing row
   * If posted or reversed, return idempotent success
   * If draft, acquire row lock

3. Compute proposed lines

   * posting_rules(event, rule_version) → line_specs

4. Validate posting

   * Balanced per currency
   * Required dimensions present
   * Accounts valid

5. Handle rounding

   * If currency conversion produces remainder
   * Add explicit rounding line to rounding account
   * Mark is_rounding=true

6. Insert JournalLines

7. Finalize posting

   * Assign seq = next monotonic value
   * Set posted_at
   * Set status=posted

## Determinism

### Hard invariants

* Given the same event, rule version, and historical reference data, posting produces identical JournalLines in identical order
* Exchange rate selection, rounding rules, and dimension policies are frozen into the JournalEntry

## Immutability and correction

## Immutability

Once posted:

* JournalEntry cannot change (except non-computational metadata)
* JournalLines cannot change
* Deletes are disallowed

## Corrections

* Reversals: full negation of original lines
* Adjustments: new postings applying deltas (allowed by model, not required in Phase 1)

## Ledger and projections

## Ledger definition

Ledger is a derived view over posted JournalLines where:

```
effective_date <= as_of_effective_date
status = posted
```

Ordered by:

```
seq ASC
```

## No stored balances

Prohibited:

* Any persistent balance table as source of truth

Allowed:

* Rebuildable projections and caches

## Audit and tamper evidence

## AuditEvent

### Fields

* audit_event_id
* entity_type
* entity_id
* action
* actor_id
* occurred_at
* payload_hash
* prev_hash
* hash

### Hard invariants

* Hash chain must validate end-to-end
* Audit records are append-only

## Minimum coverage

* Event ingested
* Event rejected
* JournalEntry posted
* JournalEntry reversed
* Period violation detected
* Protocol violation detected

## Testing requirements

## A. Unit tests: primitives

Money

* Decimal math, rounding determinism, serialization stability
* Float constructors prohibited
* Currency mismatch hard fail

ExchangeRate

* Deterministic selection by effective_at
* Frozen rate_id preserves historical correctness

Account

* Immutability after first posting
* Deletion prohibited once referenced

## B. Posting invariants

* Unbalanced entries rejected
* Invalid accounts rejected
* Missing dimensions rejected
* Cross-currency without conversion rejected
* Deterministic line ordering and hashing

## C. Idempotency

* N retries → one JournalEntry
* Payload mismatch → reject
* 10,000 duplicates → one posting

## D. Concurrency

* 100 parallel same-event posts → one JournalEntry
* 10,000 parallel distinct events → full success, no deadlocks
* Draft crash recovery → exactly one final posting

## E. Crash safety

* Kill during post → retry converges
* No partial ledger effects

## F. Replay

* Drop projections → rebuild matches prior balances
* T1 snapshot + delta = T2 snapshot

## G. Security

* API mutation attempts fail
* Direct DB tampering breaks audit chain

## Acceptance criteria

Phase 1 is complete when:

* JournalLines are the only financial truth
* Posting is idempotent, atomic, and ordered
* Closed periods cannot be altered
* Multi-currency always force-balances
* Ledger rebuilds are deterministic and identical

## Implementation checklist

### Work package 1: schema and constraints

* Event, Account, ExchangeRate, JournalEntry, JournalLine, AuditEvent, FiscalPeriod
* Unique idempotency key
* Period lock enforcement
* Monotonic sequence generator

### Work package 2: posting engine

* Deterministic rule engine
* Transactional posting with row locks
* Rounding line injection
* Reversal support

### Work package 3: ledger queries

* As-of effective_date queries
* Trial balance grouped by account, currency, dimensions

### Work package 4: test harness

* Concurrency runner
* Crash injector
* Replay validator
* Determinism hash checker

### Work package 5: audit

* Hash chain implementation
* Coverage validation

## Notes on future-proofing

* posting_rule_set_version on JournalEntry
* schema_version + payload_hash on Event
* Typed dimensions with policy enforcement
* Frozen exchange rate selection per posting

## Addendum A: Phase 1 verification and certification test suite

## Purpose

This addendum defines the authoritative test cases required to certify the Phase 1 finance kernel as a system of record. These tests prove the kernel preserves financial truth under retries, concurrency, crashes, replays, time, rule evolution, period closing, multi-currency rounding, and adversarial input.

Passing this suite is a release gate. Failure in any Critical test blocks promotion to downstream modules.

---

## Test philosophy and coverage model

Phase 1 certification requires three complementary layers:

1. Scenario tests
   Prove the “happy path” and known failure modes for each supported event type.

2. Invariant property tests
   Prove “this can never happen” across large, systematically generated permutations.

3. Chaos and longevity tests
   Prove invariants hold under concurrency, crash injection, replay, rule upgrades, schema evolution, and period closes over extended time horizons.

Each test case defines:

* What it proves
* Why it exists
* How it is executed
* How success is measured
* Required permutation coverage

---

## Test classification

Tests are grouped by invariant class, not by component. All tests must be automated, repeatable, and machine-verifiable.

Severity levels:

* Critical: financial truth or auditability compromised
* Major: determinism, safety, compliance, or recovery weakened
* Minor: operational, performance, or ergonomics regression

---

## Test harness requirements

All suites assume a shared harness with the following capabilities:

* Canonical event generator per supported event_type
* Mutation engine supporting:

  * Single-field sweeps
  * Pairwise field coverage
  * Bounded fuzzing within schema and domain constraints
* Concurrency runner with configurable worker counts, jitter, and retry policies
* Crash injector with deterministic fault-point targeting
* Replay runner capable of dropping projections and rebuilding from journal only
* Determinism hash tool producing stable, canonical hashes of trial balance and selected ledger slices
* Coverage auditor that reports field-pair, boundary, and distribution coverage

All success signals must be derived from invariant assertions, not UI output or logs.

---

## Canonical success signals

These invariants must hold in all tests:

* Exactly one posted JournalEntry per idempotency_key
* No posted JournalEntry in a closed period by effective_date
* JournalEntry and JournalLines are immutable after posting
* Double-entry balance holds per currency, including rounding handling
* Ledger rebuild produces an identical trial balance hash
* Every JournalLine traces to an Event and a valid audit hash chain
* No partial or transient state affects the ledger

---

## 1. Identity and protocol integrity

### A1. Event identity immutability

Severity: Critical

Proves
An accepted event cannot be altered or reinterpreted.

Why
If events can be overwritten, the journal is not a system of record.

How

1. Ingest event E1 with payload P1.
2. Re-ingest E1 with payload P2 where P2 ≠ P1.

Permutation coverage (required)

* Single-field mutation sweep:

  * payload
  * occurred_at
  * effective_date
  * producer
  * event_type
  * schema_version
* Pairwise coverage across:

  * payload_hash
  * occurred_at
  * effective_date
  * producer
  * event_type

Success criteria

* Any re-ingest with same event_id and different payload_hash is rejected.
* No JournalEntry is created or modified.
* AuditEvent is emitted with deterministic violation code and classification.

---

### A2. Idempotency contract

Severity: Critical

Proves
Duplicate delivery cannot create duplicate financial facts.

Why
Distributed systems retry. Finance cannot duplicate.

How

1. Ingest E1.
2. Call post_event(E1) N times (N ≥ 100) with retries enabled.

Permutation coverage (required)

* N ∈ {2, 10, 100, 1,000}
* Call patterns:

  * sequential
  * burst
  * jittered
* Pairwise coverage across:

  * worker count
  * retry policy
  * crash fault points (B1)

Success criteria

* Exactly one posted JournalEntry exists for the idempotency_key.
* All calls return the same journal_entry_id or “already posted.”
* Ledger effect is exactly once.

---

### A3. Idempotency poisoning protection

Severity: Critical

Proves
Producer identity participates in financial identity.

Why
Two producers must not collide on the same event_id.

How

1. Ingest E1 from producer A.
2. Attempt to ingest E1 from producer B with the same event_id.

Permutation coverage (required)

* Producer variants:

  * case changes
  * whitespace variants
  * unknown producer
  * empty producer
* Pairwise coverage across:

  * producer
  * event_type
  * event_id

Success criteria

* Behavior matches documented policy (reject or namespace isolate).
* No overwrite or merge occurs.
* AuditEvent emitted with collision classification.

---

## 2. Atomicity and crash safety

### B1. Partial write prevention

Severity: Critical

Proves
No half-posted financial facts can exist.

Why
Partial postings corrupt the ledger and evade detection.

How

Inject crash at each fault point, then retry:

1. After draft JournalEntry insert
2. After partial JournalLine insert
3. After all lines inserted, before status=posted
4. After status=posted, before projections update

Permutation coverage (required)

* Single-threaded and concurrent posting
* Event types with and without rounding behavior

Success criteria

* System converges to exactly one complete posted JournalEntry.
* No orphan lines or drafts affect ledger or projections.
* Determinism hash matches clean-run baseline.

---

### B2. Transaction isolation under load

Severity: Major

Proves
Parallel postings do not globally serialize or deadlock.

How

Post 10,000 distinct events across 100 workers with jitter and retries.

Permutation coverage (required)

* Pairwise coverage across:

  * worker count {10, 50, 100, 250}
  * event mix (single vs mixed)
  * dimension sparsity (minimal vs full)
  * currency mix (single vs multi)

Success criteria

* All events post successfully.
* Deadlocks below configured threshold.
* Latency within SLO.
* No invariant violations.

---

## 3. Ordering and determinism

### C1. Monotonic sequence enforcement

Severity: Critical

Proves
Global ordering is stable under concurrency and failure.

How

1. Post events concurrently.
2. Extract seq values.

Permutation coverage (required)

* Same-event and multi-event concurrency
* Crash injection during seq assignment

Success criteria

* seq is strictly increasing.
* No duplicates.
* No reuse after rollback.

---

### C2. Rebuild determinism across permutations

Severity: Critical

Proves
Storage order does not affect financial truth.

How

1. Generate ≥100k mixed events.
2. Post them.
3. Drop projections.
4. Rebuild by seq.

Permutation coverage (required)

* Replay orders:

  * original
  * shuffled
  * chunked {1, 10, 1,000}
* Rule upgrades mid-stream (G1)

Success criteria

* Trial balance hash matches baseline.
* Ledger slice hashes match baseline.
* Rounding totals stay within declared policy bounds.

---

## 4. Period integrity

### D1. Closed period enforcement

Severity: Critical

Proves
Closed books cannot be altered.

How

1. Close period P1.
2. Attempt posting with effective_date in P1.

Permutation coverage (required)

* effective_date sweep:

  * inside P1
  * boundaries
  * adjacent open period
* Pairwise coverage across:

  * event_type
  * actor role
  * concurrency

Success criteria

* All illegal posts rejected.
* AuditEvent logged.
* No JournalEntry created.

---

### D2. Forward-only correction

Severity: Critical

Proves
Corrections preserve historical truth.

How

1. Post entry in open period P1.
2. Close P1.
3. Attempt reversal in P1 (must fail).
4. Post forward adjustment in P2.

Permutation coverage (required)

* Entries with:

  * rounding lines
  * multiple dimensions
  * multi-currency
* Pairwise coverage across:

  * adjustment type
  * reference style

Success criteria

* Step 3 rejected.
* Step 4 succeeds.
* P1 hash unchanged.
* P2 reflects delta with trace linkage.

---

### D3. Boundary classification

Severity: Major

Proves
effective_date drives accounting, not occurred_at.

How

Post events where occurred_at and effective_date cross boundaries.

Permutation coverage (required)

* Timezone normalization sweeps
* Midnight and DST boundary conditions

Success criteria

* Period logic depends only on effective_date.
* occurred_at has no ledger impact.

---

## 5. Multi-currency and rounding integrity

### E1. Force-balance guarantee

Severity: Critical

Proves
Rounding never breaks double-entry.

How

Generate conversion events that create remainders.

Permutation coverage (required)

* Sweep:

  * currency pairs
  * rate precision
  * amount magnitude
  * sign
* Pairwise coverage across:

  * rate timestamp
  * precision
  * rounding rule version

Success criteria

* Entry balances per currency.
* Exactly one rounding line when required.
* Rounding line is traceable and classified.

---

### E2. Zero-remainder suppression

Severity: Minor

Proves
No unnecessary rounding lines are created.

How

Generate exact-balance conversions.

Permutation coverage (required)

* Randomized constructions across pairs and magnitudes

Success criteria

* No rounding line.
* Entry balances.

---

### E3. Cascading conversion safety

Severity: Major

Proves
Rounding does not compound.

How

Compare:

* A → C directly
* A → B → C via two rates

Permutation coverage (required)

* Multiple currency triangles
* Multiple rate sources

Success criteria

* Net difference within declared rounding bounds.
* No multiple rounding lines.
* Invariants hold.

---

## 6. Dimension stability

### F1. Historical dimension immutability

Severity: Major

Proves
Renames do not rewrite history.

How

1. Post entries.
2. Rename dimensions.
3. Rebuild and report.

Permutation coverage (required)

* Dimension sets:

  * org
  * org + project
  * org + contract
  * org + project + contract

Success criteria

* Stable IDs preserved.
* Reports reflect new names only.

---

### F2. Dimension deletion protection

Severity: Critical

Proves
Traceability cannot be broken.

How

Attempt deletion of referenced dimensions.

Permutation coverage (required)

* Each dimension type with mixed historical usage

Success criteria

* Operation rejected.
* AuditEvent logged.
* No dangling references.

---

## 7. Rule evolution

### G1. Backward compatibility with differential replay

Severity: Critical

Proves
Upgrades do not rewrite history.

How

1. Post corpus under rule version N.
2. Upgrade to N+1.
3. Replay from genesis.

Permutation coverage (required)

* Upgrade timing:

  * between posts
  * during load
  * during replay
* Include multi-currency and rounding cases

Success criteria

* N-version entries reproduce identically.
* N+1 entries follow new rules.
* No reinterpretation of history.

---

### G2. Mixed-version ledger correctness

Severity: Major

Proves
Reports remain correct across rule generations.

How

Build ledger with entries from multiple rule versions.

Permutation coverage (required)

* Distribution across versions and event types

Success criteria

* Trial balance correct.
* No rule leakage.

---

## 8. Adversarial input

### H1. Payload fuzzing

Severity: Major

Proves
Malformed input cannot corrupt state.

How

Generate invalid payloads:

* Invalid JSON
* Unknown dimensions
* Missing required fields
* Type mismatches
* Extreme numeric values

Permutation coverage (required)

* Pairwise coverage across invalid classes and event types

Success criteria

* All rejected.
* No JournalEntries created.
* No residual drafts.
* AuditEvents logged.

---

### H2. Oversize payload

Severity: Minor

Proves
System fails safely under extreme input.

How

Send payloads above limit.

Success criteria

* Deterministic rejection.
* No resource exhaustion.
* No persistence.

---

## 9. Audit survivability

### I1. Full trace walk

Severity: Critical

Proves
Every dollar is explainable.

How

Randomly sample ≥1,000 JournalLines and verify:

JournalLine → JournalEntry → Event → Audit chain

Permutation coverage (required)

* Multiple periods
* Multiple currencies
* Rounding and non-rounding lines
* Multiple rule versions

Success criteria

* No broken links.
* Hash chain validates.

---

### I2. Tamper detection

Severity: Critical

Proves
Silent modification is detectable.

How

Single-field mutation sweep across AuditEvent fields.

Success criteria

* Hash validation fails for every mutation.
* Integrity violation flagged.

---

## 10. Performance and scale

### J1. Posting latency SLO

Severity: Major

Proves
Operational viability under realistic load.

How

Sustain target TPS with mixed workload.

Permutation coverage (required)

* Pairwise coverage across:

  * worker count
  * event mix
  * currency mix
  * dimension density
  * rule versions

Success criteria

* P95 and P99 within thresholds.
* Zero invariant violations.

---

### J2. Rebuild SLO

Severity: Major

Proves
Disaster recovery is practical and correct.

How

Rebuild from ≥10M JournalLines.

Permutation coverage (required)

* Batch sizes {1, 10, 1,000, 100,000}
* Forced projection drop
* Backward-compatible schema migration

Success criteria

* Completes within recovery window.
* Trial balance hash matches baseline.
* No missing entries.

---

## 11. Metamorphic and equivalence testing

### K1. Post + reverse equivalence

Severity: Major

Proves
Reversal is a true inverse.

How

For randomized postings:

1. Post E
2. Reverse E (open period only)
3. Compare ledger to baseline

Permutation coverage (required)

* Multiple event types
* Multiple currency configurations

Success criteria

* Ledger hash matches baseline.

---

### K2. Split/merge equivalence

Severity: Major

Proves
Equivalent decompositions preserve truth.

How

Compare:

* Single posting P
* Two postings P1 + P2 where P1 + P2 ≡ P

Success criteria

* Trial balance identical.
* Traceability intact.

---

## Certification criteria

Phase 1 is certified when:

* All Critical tests pass for 7 consecutive days in CI and nightly chaos runs
* No Major regressions remain open
* Trial balance hash is stable across three full rebuilds and two shuffled replays
* Nightly invariant fuzzing completes with zero invariant violations over the agreed minimum corpus size

---

## Operational signal

When this suite is green, the finance kernel is:

* Legally defensible
* Audit survivable
* Distributed-system safe
* Upgrade tolerant
* Scale stable

Downstream modules may integrate without undermining financial truth.

