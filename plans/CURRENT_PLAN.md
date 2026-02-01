# Active Plans

**Date:** 2026-02-01

---

## 1. Modular Approval Engine

**Status:** NOT STARTED -- plan approved, awaiting implementation
**Full plan:** `plans/APPROVAL_ENGINE_PLAN.md`

Design and implement a fully modular, configuration-driven approval engine that
governs all state transitions across the system -- both human approval gates and
operational transitions. Approval policies are defined in YAML and compiled at
config time.

| Phase | Description | Status | Depends On |
|-------|------------|--------|------------|
| 0 | Consolidate workflow types into `finance_kernel/domain/workflow.py` | pending | -- |
| 1 | Domain types (`finance_kernel/domain/approval.py`) | pending | Phase 0 |
| 2 | Pure engine (`finance_engines/approval.py`) | pending | Phase 1 |
| 3 | ORM models (`finance_kernel/models/approval.py`) | pending | Phase 1 |
| 4 | Exceptions + audit actions | pending | Phase 1 |
| 5 | Services (ApprovalService + WorkflowExecutor) | pending | Phases 2,3,4 |
| 6 | Config schema additions | pending | Phase 1 |
| 7 | YAML configuration | pending | Phase 6 |
| 8 | Integration with posting pipeline | pending | Phases 5,7 |
| 9 | Tests (~180 across 7 files) | pending | All phases |
| 10 | Module workflow migration (AP first) | pending | Phase 8 |

**Invariants:** AL-1 through AL-11 (see full plan)
**Key decisions:** 17 decisions documented (see full plan)

---

## 2. ERP Data Ingestion System

**Status:** NOT STARTED -- plan approved (v2), awaiting implementation
**Full plan:** `plans/ERP_INGESTION_PLAN.md`

Design and implement a configuration-driven ERP data ingestion system with
staging, per-record validation, and granular visibility into processing status.
YAML-driven field mappings, pre-packaged validators, and pluggable entity
promoters for migration from other ERPs. Full integration with structured
logging (LogContext) and hash-chained audit trail (AuditorService).

| Phase | Description | Status | Depends On |
|-------|------------|--------|------------|
| 0 | Domain types (`finance_ingestion/domain/types.py`) | pending | -- |
| 1 | Staging ORM models + AuditAction additions | pending | Phase 0 |
| 2 | Source adapters (CSV, JSON) | pending | -- |
| 3 | Import mapping config (YAML schema) | pending | -- |
| 4 | Mapping engine + test harness (pure) | pending | Phases 0, 3 |
| 5 | Validation pipeline | pending | Phases 0, 4 |
| 6 | Import service (with structured logging) | pending | Phases 1, 2, 4, 5 |
| 7 | Promotion service (preflight graph, event stream, audit) | pending | Phases 1, 6 |
| 8 | Entity promoters | pending | Phase 7 |
| 9 | Tests (~240 across 13 files) | pending | All phases |

**Invariants:** IM-1 through IM-14 (see full plan)
**Key decisions:** 16 decisions documented (see full plan)

---

## 3. Reversal System -- Deferred Items & Improvements

**Source:** `plans/archive/2026-02-01_reversal-implementation.md` (completed)
**Status:** Open items from completed reversal implementation

### Deferred Functionality

| Item | Description | Priority |
|------|-------------|----------|
| Module-level void workflows | AP `void_invoice`, AR `void_payment`, etc. -- compose on top of `ReversalService` | Medium (per-module as needed) |
| Partial reversals | Reverse some lines only -- requires line-level selection + balance validation | Low (full reversals sufficient for now) |
| CorrectionEngine typed-plan refactor | Replace callback adapter with typed operations (`ReverseEntry`, `PostAdjustment`, `PostReplacement`) | Low (callback adapter works) |
| Reversed-status projection table | Materialized view for `is_reversed` -- derived property via unique index is sufficient today | Low (optimize if query patterns demand) |

### Deferred Tests

| Item | Description |
|------|-------------|
| Concurrency tests | `test_concurrent_reversal_exactly_one_succeeds` (race two reversals, unique constraint ensures one wins), `test_atomicity_failure_after_entry_before_link` (force failure, assert nothing persisted) |
| Selector consistency tests | `tests/selectors/test_reversal_queries.py` -- `is_reversed` derived from entry, trial balance correctness, reversal entry included in balances |
| Integration E2E tests | `tests/integration/test_reversal_e2e.py` -- multi-ledger reversal, post-close-reverse flow |

### Specific Improvements (Hardening Opportunities)

1. **Reversal request idempotency at service layer** -- Add `SELECT ... FOR UPDATE` on original entry row in `_load_and_validate()` to serialize concurrent reversals before hitting the unique constraint.
2. **Policy snapshot for reversals** -- Snapshot `posting_policy_version` and `posting_policy_hash` on the reversal entry (mirrors AL-2). Store in `entry_metadata`.
3. **Event-chain integrity** -- Add `prev_event_id` to the reversal Event payload; validate it matches the original entry's source event. Enforce single-hop event chain in AuditorService.
4. **Ledger boundary enforcement** -- Validate `ledger_id` on original entry matches the ledger resolved for the reversal. Reject cross-ledger reversals explicitly.
5. **Dimension immutability check** -- Guard in `write_reversal()` that verifies `dimensions_schema_version` matches the original's snapshot (fail fast on schema drift).
6. **Link graph uniqueness** -- DB unique constraint on `(parent_id, link_type)` for `REVERSED_BY` to mirror journal FK uniqueness at the graph layer.
7. **Effective date monotonicity** -- Enforce `effective_date >= original.effective_date` for `reverse_in_current_period()` to prevent temporal inversion.
8. **Audit hash chaining** -- Include both original and reversal entry hashes in `record_reversal()`: `hash = SHA256(prev_audit_hash || original_entry_hash || reversal_entry_hash || timestamp)`.
9. **Reversal metadata contract** -- Formalize `entry_metadata` schema with a dataclass and validator; reject unknown keys.
10. **Performance index for selectors** -- Covering index on `journal_entries`: `(reversal_of_id, effective_date, ledger_id)`.
11. **Multi-currency invariant** -- Assert `original_entry.lines[*].currency` is uniform before reversal; fail on mixed-currency entries.
12. **Failure-mode test** -- Test where `LinkGraphService.establish_link()` fails after `write_reversal()` and assert full rollback.
13. **Deprecation cleanup** -- Lint/architecture test forbidding new reads/writes of `JournalEntryStatus.REVERSED` outside migration code.
14. **API symmetry** -- Add `can_reverse(entry_id)` method to `ReversalService` for UI/agent preflight checks.
15. **Replay harness** -- Deterministic replay test: replay original + reversal events against blank ledger, assert identical final balances and entry hashes.

---

## Next Steps

When resuming, check which plan the user wants to work on:
- **Approval Engine:** Read `plans/APPROVAL_ENGINE_PLAN.md`, start with Phase 0
- **ERP Ingestion:** Read `plans/ERP_INGESTION_PLAN.md`, start with Phase 0
- **Reversal Hardening:** Pick items from section 3 above
