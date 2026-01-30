# Trace Bundle / Log Viewer — Implementation Plan

**Status:** Planned
**Source Spec:** `/log_viewer_plan.md`
**Date:** 2026-01-29

---

## Objective

Build a read-only trace bundle assembly system that reconstructs the complete lifecycle of any financial artifact by querying **existing** ledger tables, event records, audit events, economic links, interpretation outcomes, and structured JSON logs. Zero new persistent storage. Minimal new files — follows established codebase patterns.

## Primary Mandate

> The system already tells its own story. This feature merely learns how to listen.

No new tables, message buses, or trace stores. Everything derives from existing infrastructure.

---

## Key Architecture Observation

Existing selectors define DTOs **inline** (not in separate domain files):
- `journal_selector.py` defines `JournalEntryDTO`, `JournalLineDTO` alongside `JournalSelector`
- `ledger_selector.py` defines `TrialBalanceRow`, `AccountBalance`, `LedgerLine` alongside `LedgerSelector`

Hashing utilities extend `utils/hashing.py` (already has `canonicalize_json()`, `hash_payload()`, `hash_journal_entry()`, `hash_trial_balance()`).

**We follow these patterns exactly.**

---

## Existing Infrastructure Inventory

### Priority 1: Ledger Tables (already indexed, production-ready)

| Source | Key Tracing Fields |
|--------|-------------------|
| `JournalEntry` | `source_event_id`, `seq`, `status`, `posted_at`, `reversal_of_id`, `idempotency_key`, R21 fields (`coa_version`, `dimension_schema_version`, `rounding_policy_version`, `currency_registry_version`) |
| `JournalLine` | `journal_entry_id`, `account_id`, `side`, `amount`, `currency`, `dimensions`, `is_rounding`, `exchange_rate_id`, `line_seq` |
| `Account` | `code`, `name`, `account_type`, `normal_balance`, `tags` |

### Priority 2: Event Records

| Source | Key Tracing Fields |
|--------|-------------------|
| `Event` | `event_id` (UNIQUE), `event_type`, `payload`, `payload_hash`, `schema_version`, `producer`, `actor_id`, `occurred_at`, `effective_date`, `ingested_at` |
| `EconomicEvent` | `source_event_id`, `economic_type`, `profile_id`, `profile_version`, `trace_id`, R21 fields, `hash`, `prev_hash` |
| `InterpretationOutcome` | `source_event_id` (UNIQUE), `status`, `journal_entry_ids` (JSON), `reason_code`, `reason_detail`, `profile_id`, `profile_version`, `profile_hash`, `trace_id` |

### Priority 3: Economic Links

| Source | Key Tracing Fields |
|--------|-------------------|
| `EconomicLinkModel` | `link_type` (10 types), `parent_artifact_type/id`, `child_artifact_type/id`, `creating_event_id`, `link_metadata` |

### Priority 4: Structured Logs (JSON lines, queryable)

| Source | Key Tracing Fields |
|--------|-------------------|
| `LogContext` | `correlation_id`, `event_id`, `actor_id`, `producer`, `entry_id`, `trace_id` |
| PostingOrchestrator logs | `posting_started/completed/failed` with `correlation_id`, `duration_ms` |
| InterpretationCoordinator logs | `interpretation_started/completed/failed` with `trace_id`, `outcome_status` |

### Priority 5: Audit Events

| Source | Key Tracing Fields |
|--------|-------------------|
| `AuditEvent` | `seq`, `entity_type`, `entity_id`, `action` (15+ actions), `payload`, `hash`, `prev_hash`, `occurred_at` |

---

## Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| **D1:** DTOs inline in `trace_selector.py` | Follows `journal_selector.py` and `ledger_selector.py` pattern — DTOs live next to the selector that returns them |
| **D2:** Resolver in `selectors/` | Read-only queries only. Follows `LedgerSelector`, `JournalSelector` pattern. |
| **D3:** Bundle hash in `utils/hashing.py` | Extends existing file (already has `hash_journal_entry()`, `hash_trial_balance()`). No new file. |
| **D4:** Log query Protocol inline | Small Protocol defined in `trace_selector.py`. Only consumer is this selector. |
| **D5:** No new persistent tables | Spec mandates it. Everything assembles from existing data. |
| **D6:** Clock injection | `generated_at` uses injected `Clock` for deterministic testing. |
| **D7:** Optional LogQueryPort | Bundle still assembles without logs; declares `MissingFact` instead. |

---

## Implementation: `finance_kernel/selectors/trace_selector.py`

**The only new kernel file.** Contains everything: DTOs, log query Protocol, and the TraceSelector class.

### DTOs (frozen dataclasses, defined at top of file)

| DTO | Purpose | Maps From |
|-----|---------|-----------|
| `ArtifactIdentifier` | Anchor artifact (type + id) | Mirrors `ArtifactRef` from `economic_link.py` |
| `OriginEvent` | Canonical source event | `Event` fields |
| `JournalEntrySnapshot` | Entry + lines with account codes | `JournalEntry` + `JournalLine` + `Account.code` |
| `JournalLineSnapshot` | Single line | `JournalLine` fields |
| `InterpretationInfo` | Pipeline B outcome | `InterpretationOutcome` fields |
| `ReproducibilityInfo` | R21 snapshot | JournalEntry/EconomicEvent R21 columns |
| `TimelineEntry` | Action in timeline | `AuditEvent` or structured log record |
| `LifecycleLink` | Economic link | `EconomicLinkModel` fields |
| `ConflictInfo` | Dedup/protocol violations | `AuditEvent` with PROTOCOL_VIOLATION action |
| `IntegrityInfo` | Bundle hash + verifications | Computed from bundle content |
| `MissingFact` | Explicitly missing data | Declared when source unavailable |
| `TraceBundle` | Top-level container | All sections above |

### Protocol (inline)

`LogQueryPort` — Protocol with 3 methods: `query_by_correlation_id()`, `query_by_event_id()`, `query_by_trace_id()`. Optional — bundle assembles without it, declares `MissingFact` instead.

### Selector Class

```python
class TraceSelector(BaseSelector[JournalEntry]):
    def __init__(self, session, clock=None, log_query=None): ...

    # Public API
    def trace_by_event_id(self, event_id: UUID) -> TraceBundle
    def trace_by_journal_entry_id(self, entry_id: UUID) -> TraceBundle
    def trace_by_artifact_ref(self, artifact_type: str, artifact_id: UUID) -> TraceBundle
```

### Assembly Strategy (5 steps from spec)

1. **Anchor** — Identify artifact type, resolve to `event_id` + `entry_id(s)`
2. **Economic chain** — Query `EconomicLinkModel` by parent/child refs (indexed)
3. **Canonical event** — Query `Event` by `event_id` (unique index)
4. **Log entries** — Query `LogQueryPort` by `event_id`/`correlation_id` (time-bounded, optional)
5. **Config enrichment** — R21 snapshot from JournalEntry/EconomicEvent columns

### All Lookups Use Existing Indexes

| Query | Index Used |
|-------|-----------|
| `Event` by `event_id` | `uq_event_id` |
| `JournalEntry` by `source_event_id` | `idx_journal_source_event` |
| `JournalEntry` by `id` | Primary key |
| `InterpretationOutcome` by `source_event_id` | `uq_outcome_source_event` |
| `EconomicEvent` by `source_event_id` | `idx_econ_event_source` |
| `EconomicLinkModel` by parent | `idx_link_parent` |
| `EconomicLinkModel` by child | `idx_link_child` |
| `AuditEvent` by entity_type + entity_id | `idx_audit_entity` |
| `Account` by `id` | Primary key |

### Internal Methods

| Method | Purpose | Data Source |
|--------|---------|-------------|
| `_resolve_event()` | Load canonical event | `Event` table |
| `_resolve_journal_entries()` | Load entries by event | `JournalEntry` + `JournalLine` + `Account` |
| `_resolve_interpretation()` | Load Pipeline B outcome | `InterpretationOutcome` table |
| `_resolve_economic_event()` | Load economic event | `EconomicEvent` table |
| `_resolve_lifecycle_links()` | Load artifact links | `EconomicLinkModel` (depth=1) |
| `_resolve_audit_trail()` | Load audit chain segment | `AuditEvent` table |
| `_resolve_log_entries()` | Query structured logs | `LogQueryPort` (optional) |
| `_build_timeline()` | Merge + sort + dedup | All timeline sources |
| `_check_idempotency()` | Detect dedup/violations | `AuditEvent` PROTOCOL_VIOLATION records |
| `_extract_reproducibility()` | Pull R21 snapshot | JournalEntry/EconomicEvent R21 columns |
| `_verify_integrity()` | Check payload hash, balance, chain | Computed from data |
| `_collect_missing_facts()` | Declare gaps explicitly | All resolution failures |

---

## Modified Files (4 files, minimal changes)

### `finance_kernel/utils/hashing.py` — Add bundle hash function

Add `hash_trace_bundle(bundle_dict: dict) -> str` following the existing `hash_journal_entry()` pattern. Uses existing `canonicalize_json()` internally. Excludes volatile fields (`generated_at`, `trace_id`, `integrity.bundle_hash`).

### `finance_kernel/selectors/__init__.py` — Add export

Add `TraceSelector` to exports, following existing pattern.

### Logging gap-fill (3 one-line changes)

| File | Change |
|------|--------|
| `finance_kernel/services/posting_orchestrator.py` | Add `trace_id` to `LogContext.bind()` |
| `finance_kernel/services/auditor_service.py` | Add `entity_id` to log extra |
| `finance_kernel/services/link_graph_service.py` | Add `link_id` to log extra |

No behavioral changes. Only adds fields to existing structured log `extra={}` dicts.

---

## Tests

**New directory:** `tests/trace/`

| File | Type | Scope |
|------|------|-------|
| `tests/trace/__init__.py` | — | Empty init |
| `tests/trace/test_trace_bundle_dto.py` | Pure unit | Bundle creation, frozen enforcement, missing facts, all artifact types, empty states |
| `tests/trace/test_trace_serializer.py` | Pure unit | Hash determinism, hash excludes volatile fields, hash stability, canonical ordering |
| `tests/trace/test_trace_selector.py` | Integration (DB) | Happy path, includes lines/audit/links, reversals, rejected events, idempotency, protocol violations, R21 reproducibility, balance verification, works without LogQueryPort, no writes during assembly |
| `tests/trace/test_log_query_contract.py` | Pure unit | Protocol implementable by stub, None graceful |

---

## Complete File Summary

| Action | Path |
|--------|------|
| **NEW** | `finance_kernel/selectors/trace_selector.py` |
| **NEW** | `tests/trace/__init__.py` |
| **NEW** | `tests/trace/test_trace_bundle_dto.py` |
| **NEW** | `tests/trace/test_trace_serializer.py` |
| **NEW** | `tests/trace/test_trace_selector.py` |
| **NEW** | `tests/trace/test_log_query_contract.py` |
| **MODIFY** | `finance_kernel/utils/hashing.py` (add `hash_trace_bundle()`) |
| **MODIFY** | `finance_kernel/selectors/__init__.py` (add export) |
| **MODIFY** | `finance_kernel/services/posting_orchestrator.py` (1 line) |
| **MODIFY** | `finance_kernel/services/auditor_service.py` (1 line) |
| **MODIFY** | `finance_kernel/services/link_graph_service.py` (1 line) |

**1 new kernel file. 4 modified files. Zero new tables.**

---

## Verification

```bash
# 1. Pure unit tests (no DB)
python3 -m pytest tests/trace/test_trace_bundle_dto.py tests/trace/test_trace_serializer.py tests/trace/test_log_query_contract.py -v

# 2. Integration tests (requires DB)
python3 -m pytest tests/trace/test_trace_selector.py -v

# 3. Architecture boundary check (no forbidden imports)
python3 -m pytest tests/architecture/test_kernel_boundary.py -v

# 4. Existing tests still pass
python3 -m pytest tests/audit/ tests/posting/ -v --tb=short
```

---

## Design Constraints Honored

| Spec Requirement | How Honored |
|-----------------|-------------|
| No new persistent storage | Zero new tables. All assembly from existing data. |
| Logs are first-class facts | `LogQueryPort` protocol, log-derived `TimelineEntry` objects |
| Ledger is source of truth | Ledger tables are Priority 1 data source |
| Deterministic assembly | Canonical JSON, sorted timelines, hash-stable bundles |
| Minimal surface area | Only a resolver and a serializer. Everything else exists. |
| Missing facts explicit | `MissingFact` dataclass, never inferred or invented |
| Access control | Selector enforces ledger permissions first, then event, then log visibility |
| Performance | All indexed lookups, time-bounded log queries |
