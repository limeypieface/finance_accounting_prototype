# Trace and audit investigation

Trace is the **audit investigation path**: given an event or journal entry, assemble a complete, read-only **trace bundle** — source event, journal entries and lines, interpretation outcome, R21 snapshot, economic links, audit trail, and integrity checks. No writes; purely for inquiry and compliance.

## What a trace bundle is

A **TraceBundle** is a frozen DTO produced by **TraceSelector** (`finance_kernel/selectors/trace_selector.py`). It contains:

| Section | Source | Description |
|--------|--------|-------------|
| **artifact** | Input | Anchor (event_id or journal_entry_id) used to start the trace. |
| **origin** | Event | Canonical source event: event_id, type, occurred_at, effective_date, payload_hash, producer. |
| **journal_entries** | JournalEntry + JournalLine | Posted entries and lines with account codes; R21 snapshot fields (coa_version, etc.). |
| **interpretation** | InterpretationOutcome | Status (POSTED/BLOCKED/REJECTED), profile, decision_log (if present). |
| **reproducibility** | R21 | Reference snapshot versions for deterministic replay. |
| **timeline** | AuditEvent + optional logs | Chronological actions: audit events and/or structured log entries (guard evaluation, role resolution, etc.). |
| **lifecycle_links** | EconomicLinkModel | Economic links (e.g. FULFILLED_BY, PAID_BY) involving the artifact. |
| **conflicts** | AuditEvent | Protocol violations or duplicate detection. |
| **integrity** | Computed | bundle_hash (deterministic over bundle content), payload_hash_verified, balance_verified, audit_chain_segment_valid. |
| **missing_facts** | Explicit | Declared missing data (e.g. no decision_log, no LogQueryPort); never inferred. |

The bundle hash is deterministic (volatile fields like `generated_at` and `trace_id` are excluded). See `finance_kernel/utils/hashing.hash_trace_bundle`.

## TraceSelector API

**Location:** `finance_kernel/selectors/trace_selector.py`

- **`trace_by_event_id(event_id: UUID) -> TraceBundle`** — Start from the source event; resolve all journal entries and outcomes for that event.
- **`trace_by_journal_entry_id(entry_id: UUID) -> TraceBundle`** — Start from a journal entry; resolve back to the source event and related data.
- **`trace_by_artifact_ref(artifact_type: str, artifact_id: UUID) -> TraceBundle`** — Start from an arbitrary artifact (e.g. invoice, payment); resolve via economic links to event and entries.

**Constructor:** `TraceSelector(session, clock=None, log_query=None)`. The optional **log_query** implements the **LogQueryPort** protocol (`query_by_event_id`, `query_by_correlation_id`, `query_by_trace_id`) so the timeline can include structured log records (e.g. from an in-process capture during posting).

## Decision journal and LogQueryPort

The **timeline** in a trace bundle can include:

1. **Audit events** — From the audit_events table (append-only hash chain).
2. **Structured logs** — From a **LogQueryPort** (e.g. **LogCapture**) that provides records by event_id / correlation_id / trace_id. When posting is run with **LogCapture** installed, those records are available to TraceSelector so the timeline shows profile selection, guard evaluation, role resolution, and balance validation.

**LogCapture** (`finance_kernel/services/log_capture.py`) is an in-memory handler that implements LogQueryPort. Usage: install it on the finance_kernel logger hierarchy during a posting call; pass the same capture instance into `TraceSelector(session, log_query=capture)` when building the bundle. No DB or file persistence; for that you would implement a different LogQueryPort (e.g. querying a log store by correlation_id).

InterpretationOutcome can also store a **decision_log** (tuple of dicts); if present, it is used for the timeline when no LogQueryPort is provided.

## Engine tracing (separate concern)

**finance_engines/tracer.py** provides **@traced_engine** for pure engine invocations. It emits **FINANCE_ENGINE_TRACE** log records (engine name, version, input fingerprint, duration). That is **invocation-level** tracing for engines (variance, allocation, etc.), not the same as the **TraceSelector** trace bundle. Engine traces can be consumed by a log aggregator or by services that record EngineTraceRecord for audit; they are not part of the TraceBundle DTO unless you feed them into a LogQueryPort that TraceSelector uses.

## Scripts and tests

| Item | Purpose |
|------|---------|
| **scripts/trace.py** | CLI: trace by `--event-id` or `--entry-id`; `--list` to list traceable entries; `--json` for raw bundle. Uses TraceSelector and the same renderer as the interactive T menu. |
| **scripts/trace_render.py** | Shared trace renderer (human-readable output). |
| **tests/trace/** | test_trace_selector.py, test_trace_bundle_dto.py, test_trace_serializer.py, test_log_query_contract.py. |
| **tests/demo/test_trace_bundle_demo.py** | Demo: post an event, capture logs, assemble bundle with LogCapture. |

## See also

- **finance_kernel/README.md** — Selectors (including trace_selector), invariants R11/L5/P15.
- **finance_kernel/selectors/trace_selector.py** — Full DTO and assembly logic.
- **finance_kernel/services/log_capture.py** — LogQueryPort implementation for in-process capture.
- **scripts/README.md** — How to run `trace.py` and other scripts.
