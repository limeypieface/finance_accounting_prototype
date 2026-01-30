Below is the same specification, revised so that the **default and explicit design goal is to exhaust existing infrastructure first**, and that any new persistence or systems are treated as a last resort. The agent implementing this should assume that **structured logs and existing immutable audit records are the primary data source for the trace bundle**.

---

### Purpose

Provide a deterministic, human-readable “story” for any accounting artifact (journal entry, posting group, reversal, adjustment, or failed posting attempt). A user can point at a journal entry and see its full lineage: originating business event, every engine and service that evaluated it, what decisions were made, why they were made, what configuration and rule versions were used, what locks or invariants constrained it, and how the artifact relates to prior and subsequent financial actions.

The system must, by default, **derive this story entirely from existing infrastructure**: immutable ledger records and structured logs. New tables, services, or tracing platforms are introduced only if a required fact cannot be recovered from those sources.

---

### Primary design mandate

**Assume the system already knows everything it needs.
Your job is to assemble and render, not to record more.**

Any proposal to add:

* new databases
* new message buses
* new trace stores
* new persistent “story” tables

must first demonstrate that the same fact cannot be deterministically reconstructed from:

* journal entries and lines
* posting groups
* event records
* configuration/version archives
* period snapshots
* structured logs

---

### Core design principles

**1. Logs are first-class facts**
Structured logs are treated as a durable, queryable record of decisions, not as ephemeral debugging output.

**2. Ledger remains the source of truth**
The story explains the ledger. It never overrides or supplements it with parallel state.

**3. Deterministic assembly, not runtime tracing**
The system reconstructs a trace by walking foreign keys and log correlations, not by maintaining a live execution graph.

**4. Minimal surface area**
The only new code is a resolver and a serializer. Everything else is already in the system.

---

### Required logging contract (non-negotiable)

Every engine, guard, and rule resolver must already emit structured logs that meet this schema. If they do not, that is the only change permitted.

**Mandatory fields per log entry**

* `timestamp`
* `service_name`
* `component`
* `event_id`
* `attempt_id`
* `action` (enum)
* `outcome` (enum)
* `reason_code` (enum)
* `reason_params` (small structured object)
* `config_version_id`
* `engine_version`

These logs must be:

* JSON structured
* immutable once written
* retained for the same duration as financial records
* queryable by `attempt_id` and `event_id`

---

### Data sources (in priority order)

The resolver must use these sources in this order and only fall back when something is missing.

1. **Ledger tables**

   * JournalEntry
   * JournalLine
   * PostingGroup
   * Correction/Reversal links

2. **Event records**

   * Canonical business events
   * Receipt/deduplication records

3. **Configuration archives**

   * Posting rules
   * Guard definitions
   * Rule versions
   * Period definitions

4. **Structured logs**

   * Engine decisions
   * Guard evaluations
   * Rule matches
   * Period assignment
   * Failure reasons

5. **Only if required: AuditEvent tables**

   * Use only if logs cannot meet retention, immutability, or query guarantees

---

### Trace bundle assembly strategy

#### Step 1: Anchor on the ledger

Start with the artifact ID:

* journal_entry_id
* posting_group_id
* or event_id

Resolve:

* posting_group_id
* attempt_id
* event_id
* config_version_id
* period_id

These should already exist as foreign keys or indexed fields.

#### Step 2: Resolve the economic chain

From posting_group:

* follow correction/reversal links forward and backward
* follow obligation/asset references

This comes entirely from ledger state.

#### Step 3: Pull canonical event

From event_id:

* event_type
* source_module
* event_time
* payload hash or summary

#### Step 4: Query logs by correlation keys

Use:

* `attempt_id` as the primary filter
* `event_id` as a secondary filter

Pull:

* guard evaluations
* rule resolution logs
* period resolution logs
* dedupe/conflict logs
* failure logs

#### Step 5: Enrich from config archive

Use `config_version_id` to:

* load posting rule definition
* load guard definitions
* load period definitions

Only for reference and narrative context, not as truth.

---

### Trace bundle format (unchanged, but explicitly log-derived)

All fields in the bundle must be sourced from:

* ledger tables, or
* structured logs, or
* config archives

No field may be “inferred” or “computed” in a way that cannot be reproduced.

The bundle schema remains:

```yaml
trace_bundle_version: "1.0"
trace_id: "uuid"
generated_at: "timestamp"

artifact:
  type: "JOURNAL_ENTRY | POSTING_GROUP | POSTING_ATTEMPT | EVENT"
  id: "uuid"

reproducibility:
  engine_version: "string"
  config_version_id: "uuid"
  trace_input_hash: "sha256"
  period_snapshot_id: "uuid"

origin:
  event: { ... }

timeline:
  posting_attempts: [ ... ]

lifecycle_links: [ ... ]
conflicts_and_dedupe: [ ... ]
integrity: { ... }
redactions: [ ... ]
```

Each field must include a comment in the implementation referencing:

* source table, or
* log field name

---

### Determinism and reproducibility

* Logs must be sorted by:

  * timestamp
  * service_name
  * component
  * log sequence number (if available)
* The bundle serializer must:

  * produce canonical key ordering
  * normalize timestamps to ISO-8601
  * exclude volatile fields (request IDs, hostnames)
* `trace_input_hash` is computed over the normalized bundle.

---

### Failure handling

If a required fact is missing from logs:

* The bundle must explicitly include:

  ```yaml
  missing_facts:
    - fact: "GUARD_EVALUATION"
      expected_source: "structured_logs"
      correlation_key: "attempt_id"
  ```

This prevents silent narrative invention.

---

### Access control

The resolver enforces:

* ledger-level permissions first
* then event-level permissions
* then log visibility

If logs contain sensitive internal detail, fields are redacted at bundle generation, not in the narrative phase.

---

### Performance constraints

* Resolver must operate using indexed lookups:

  * journal_entry_id → posting_group_id
  * posting_group_id → attempt_id
  * attempt_id → logs
* Log queries must be bounded by:

  * attempt_id
  * time window derived from posting_attempt_time ± configurable buffer

---

### Acceptance criteria

1. A complete trace bundle can be produced **without any new persistent storage**
2. All decision steps are sourced from structured logs or ledger tables
3. Bundle generation is deterministic and hash-stable
4. Missing information is explicitly declared, not inferred
5. The system can operate with logs as the only decision-history source

---

### Design constraint for agents and implementers

**Before adding any new table, service, or persistence layer, demonstrate:**

* Which specific required bundle field cannot be sourced from:

  * ledger
  * event store
  * config archive
  * structured logs
* Why that source cannot be extended with a new log field instead

This keeps the system aligned with your original architectural intent:
**The system already tells its own story. This feature merely learns how to listen.**
