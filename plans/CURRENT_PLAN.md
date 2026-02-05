# Active Plans

**Date:** 2026-02-01

---

## 1. Modular Approval Engine

**Status:** PHASE 10 COMPLETE -- Module workflow migration (AP first) done
**Full plan (archived):** `plans/archive/2026-02-04_approval-engine-plan.md`

Design and implement a fully modular, configuration-driven approval engine that
governs all state transitions across the system -- both human approval gates and
operational transitions. Approval policies are defined in YAML and compiled at
config time.

| Phase | Description | Status | Depends On |
|-------|------------|--------|------------|
| 0 | Consolidate workflow types into `finance_kernel/domain/workflow.py` | done | -- |
| 1 | Domain types (`finance_kernel/domain/approval.py`) | done | Phase 0 |
| 2 | Pure engine (`finance_engines/approval.py`) | done | Phase 1 |
| 3 | ORM models (`finance_kernel/models/approval.py`) | done | Phase 1 |
| 4 | Exceptions + audit actions | done | Phase 1 |
| 5 | Services (ApprovalService + WorkflowExecutor) | done | Phases 2,3,4 |
| 6 | Config schema additions | done | Phase 1 |
| 7 | YAML configuration | done | Phase 6 |
| 8 | Integration with posting pipeline | done | Phases 5,7 |
| 9 | Tests (188 across 6 files) | done | All phases |
| 10 | Module workflow migration (AP first) | done | Phase 9 |

### Phase 9 Test Results (2026-02-01)

| Test Suite | File | Count |
|---|---|---|
| Domain types | `tests/domain/test_approval_types.py` | 43 |
| Engine (pure) | `tests/engines/test_approval_engine.py` | 33 |
| ORM models | `tests/models/test_approval_models.py` | 28 |
| ApprovalService | `tests/services/test_approval_service.py` | 30 |
| WorkflowExecutor | `tests/services/test_workflow_executor.py` | 22 |
| Config compilation | `tests/config/test_approval_config.py` | 32 |
| **Total** | | **188** |

**Phase 10 (Module workflow migration — AP first):** Done. `finance_kernel/domain/workflow.py` — canonical Guard, ApprovalPolicyRef, Transition (requires_approval, approval_policy), Workflow (terminal_states). `finance_modules/ap/workflows.py` — imports from kernel; INVOICE_WORKFLOW and PAYMENT_WORKFLOW set requires_approval=True and approval_policy (ap_invoice_approval, ap_payment_approval, min_version=1); terminal_states declared. `finance_services/workflow_executor.py` — TransitionLike protocol extended with requires_approval and approval_policy. Verification: `tests/modules/test_workflow_transitions.py::TestPhase10APApprovalGatedTransitions` (3 tests). Rollout order for remaining modules: AR, Expense, Procurement, then others as needed.

**Invariants:** AL-1 through AL-11 (see full plan)
**Key decisions:** 17 decisions documented (see full plan)

---

## 2. ERP Data Ingestion System

**Status:** IN PROGRESS -- Phase 0 and Phase 1 done
**Full plan (archived):** `plans/archive/2026-02-04_erp-ingestion-plan.md`

Design and implement a configuration-driven ERP data ingestion system with
staging, per-record validation, and granular visibility into processing status.
YAML-driven field mappings, pre-packaged validators, and pluggable entity
promoters for migration from other ERPs. Full integration with structured
logging (LogContext) and hash-chained audit trail (AuditorService).

v4 simplification: reuses kernel's `EventFieldType` and `validate_field_type()`,
drops canonical event stream (AuditEvents sufficient), eliminates 3 redundant
config/domain types, removes transient statuses and per-record mapping snapshots.

| Phase | Description | Status | Depends On |
|-------|------------|--------|------------|
| 0 | Domain types (`finance_ingestion/domain/types.py`) | done | -- |
| 1 | Staging ORM models + AuditAction additions | done | Phase 0 |
| 2 | Source adapters (CSV, JSON) | done | -- |
| 3 | Import mapping config (YAML schema) | done | -- |
| 4 | Mapping engine + test harness (pure) | done | Phases 0, 3 |
| 5 | Validation pipeline (with intra-batch dependency resolution) | done | Phases 0, 4 |
| 6 | Import service (with structured logging) | done | Phases 1, 2, 4, 5 |
| 7 | Promotion service (SAVEPOINT atomicity, preflight graph, skip_blocked, audit) | done | Phases 1, 6 |
| 8 | Entity promoters | done | Phase 7 |
| 9 | Tests (~240 across 13 files) | done | All phases |

**Completed work (Phase 0–3):**
- Phase 0: `finance_ingestion/` package, `domain/types.py` (ImportBatch, ImportRecord, FieldMapping, ImportValidationRule, ImportMapping, status enums).
- Phase 1: `finance_ingestion/models/staging.py` (ImportBatchModel, ImportRecordModel, from_dto/to_dto); AuditAction IMPORT_* members in `audit_event.py`.
- Phase 2: `finance_ingestion/adapters/` (SourceAdapter, SourceProbe, CsvSourceAdapter, JsonSourceAdapter); `tests/ingestion/test_adapters.py` (11 tests).
- Phase 3: `finance_config/schema.py` (ImportFieldDef, ImportValidationDef, ImportMappingDef); `AccountingConfigurationSet.import_mappings`; loader/assembler; `tests/config/test_import_mapping_config.py` (5 tests).
- Phase 4: `finance_ingestion/mapping/engine.py` (apply_mapping, coerce_from_string, apply_transform; MappingResult, CoercionResult); `tests/ingestion/test_mapping_engine.py` (17 tests).
- Phase 5: `finance_ingestion/domain/validators.py` (validate_required_fields, validate_field_types, validate_currency_codes, validate_decimal_precision, validate_date_ranges, validate_batch_uniqueness; ENTITY_VALIDATORS); referential validators deferred to service layer; `tests/ingestion/test_validators.py` (15 tests).
- Phase 6: `finance_ingestion/services/import_service.py` (ImportService: probe_source, load_batch, validate_batch, get_batch_summary, get_batch_errors, get_record_detail, retry_record; compile_mapping_from_def; LogContext); `finance_modules/_orm_registry.py` (import finance_ingestion.models); `tests/ingestion/test_import_service.py` (5 tests).
- Phase 7: `finance_ingestion/services/promotion_service.py` (PromotionService: promote_batch, promote_record, compute_preflight_graph; PromotionResult, PromotionError, PreflightGraph, PreflightBlocker; SAVEPOINT per record via begin_nested(); optional AuditorService for IMPORT_RECORD_PROMOTED, IMPORT_BATCH_COMPLETED); `finance_ingestion/promoters/base.py` (EntityPromoter protocol, PromoteResult); `finance_ingestion/promoters/party.py` (PartyPromoter); `finance_kernel/services/auditor_service.py` (record_import_record_promoted, record_import_batch_completed); `tests/ingestion/test_promotion_service.py` (5 tests).

**Phase 7b (Mapping test harness):** Done. `finance_ingestion/mapping/test_harness.py` — `MappingTestRow`, `MappingTestReport`, pure `test_mapping(mapping, sample_rows)`; applies mapping + validators (required, types, currency, decimal, date ranges, entity validators, batch uniqueness); no DB. Optional tests in Phase 9: `tests/ingestion/test_mapping_harness.py`.

**Phase 8 (Entity promoters):** Done. `finance_ingestion/promoters/party.py` — PartyPromoter (kernel Party row; duplicate by party_code/code). `finance_ingestion/promoters/account.py` — AccountPromoter (kernel Account row; duplicate by code). `finance_ingestion/promoters/ap.py` — VendorPromoter (Party SUPPLIER + VendorProfileModel; duplicate by profile code). `finance_ingestion/promoters/ar.py` — CustomerPromoter (Party CUSTOMER + CustomerProfileModel; duplicate by profile code). `finance_ingestion/promoters/inventory.py` — ItemPromoter, LocationPromoter (stubs until InventoryItemModel/InventoryLocationModel exist). `finance_ingestion/promoters/journal.py` — OpeningBalancePromoter (stub until ModulePostingService integration). `finance_ingestion/promoters/__init__.py` — default_promoter_registry() and exports. Tests updated to use PartyPromoter; 53 ingestion tests pass.

**Phase 9 (Tests):** Done. Added: `tests/ingestion/test_domain_types.py` (13 tests: enums, ImportBatch/ImportRecord/FieldMapping/ImportMapping construction and immutability); `tests/ingestion/test_mapping_harness.py` (10 tests: run_mapping_test valid/invalid/pure/summary/empty); `tests/ingestion/test_staging_orm.py` (5 tests: batch/record round-trip, mapping_version/mapping_hash, validation_errors, batch–record relationship); `tests/ingestion/test_promoters.py` (18 tests: Party, Account, Vendor, Customer promote/duplicate/missing; Item/Location/OpeningBalance stubs); `tests/ingestion/test_audit_trail.py` (3 tests: IMPORT_RECORD_PROMOTED, IMPORT_BATCH_COMPLETED, payload promoted_entity_id); `tests/architecture/test_ingestion_boundary.py` (3 tests: kernel/modules/engines do not import finance_ingestion; modules may import finance_ingestion.models for ORM registry). Harness function renamed to `run_mapping_test` with alias `test_mapping` to avoid pytest collection. **103 ingestion + boundary tests pass.**

**Next steps:** ERP Ingestion plan phases complete. Optional: expand test counts per plan (~240), add test_config_loading, test_csv_adapter/test_json_adapter split if desired.

**Invariants:** IM-1 through IM-15 (see full plan)
**Key decisions:** 20 decisions documented (see full plan)

---

## 3. QuickBooks Loader — One-Command Onboarding

**Status:** Phase 1 done; Phases 2–5 not yet implemented  
**Full plan:** `plans/QBO_LOADER_PLAN.md`

A single flow so a QuickBooks user can: (1) put XLSX exports in a folder, (2) run one command, (3) align their Chart of Accounts with the system (system recommends which config COA matches; user maps input accounts to target COA), (4) have the system construct accounts, vendors, customers, and journal with correct dates and key fields (TBD placeholders or assigned codes), (5) get data that passes validation and promotes — no “fix 123 errors” cycles. The XLSX layout is fixed (standard QBO export), so one loader serves any QBO user.

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | CoA extraction + recommendation (input CoA vs config COA options; score and recommend) | **done** |
| 2 | CoA mapping UI (user maps each input account → target code or TBD; persist mapping) | not started |
| 3 | Construct journal and entities using mapping (resolve accounts, format dates, assign codes/TBD) | not started |
| 4 | One command: convert → recommend → map → construct → validate → promote | not started |
| 5 | Hardening, error messages, docs, E2E test | not started |

**Phase 1 completed:** `scripts/qbo/coa_extract.py`, `coa_config.py`, `coa_recommend.py`; CLI `scripts/recommend_coa.py --input path/to/qbo_accounts.json`; tests `tests/ingestion/test_qbo_coa_recommend.py` (16 tests). **Current building blocks:** `scripts/qbo/` (detect, read XLSX, CoA extract/recommend), `run_qbo_convert.py` (folder → JSON), `qbo_json.yaml` (import mappings), `finance_ingestion` (stage, validate, promote). **Gap:** CoA mapping step (Phase 2), single-command orchestration (Phase 4).

---

## 4. Reversal System -- Deferred Items & Improvements

**Source:** `plans/archive/2026-02-01_reversal-implementation.md` (completed)
**Status:** Open items from completed reversal implementation

### Deferred Functionality

| Item | Description | Priority |
|------|-------------|----------|
| Module-level void workflows | AP `void_invoice`, AR `void_payment`, etc. -- compose on top of `ReversalService` | Medium (per-module as needed) |
| Partial reversals | Reverse some lines only -- requires line-level selection + balance validation | Low (full reversals sufficient for now) |
| CorrectionEngine typed-plan refactor | Replace callback adapter with typed operations (`ReverseEntry`, `PostAdjustment`, `PostReplacement`) | Low (callback adapter works) |
| Reversed-status projection table | Materialized view for `is_reversed` -- derived property via unique index is sufficient today | Low (optimize if query patterns demand) |

### Deferred Tests (Gap Closure 2026-02-02)

| Item | Description | Status |
|------|-------------|--------|
| Concurrency tests | `test_concurrent_reversal_exactly_one_succeeds` (race two reversals), `test_atomicity_failure_after_entry_before_link` | Partial: idempotency test in `tests/concurrency/test_reversal_concurrency.py`; concurrent race deferred (DB unique on `reversal_of_id` enforces) |
| Selector consistency tests | `tests/selectors/test_reversal_queries.py` -- `is_reversed` derived from entry, trial balance correctness | **Done:** 3 tests (is_reversed after reversal, is_reversal on reversal entry, trial balance nets to zero after reversal) |
| Integration E2E tests | `tests/integration/test_reversal_e2e.py` -- multi-ledger reversal, post-close-reverse flow | **Done:** post-then-reverse same period, post-close-then-reverse in current period |

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

## 4. Kernel Architecture Hygiene (Completed 2026-02-01)

**Status:** DONE

Audited `finance_kernel/` for architectural violations. Found and fixed 2 issues:

### Fixed

1. **ContractService missing Clock injection** (`finance_kernel/services/contract_service.py`)
   - `get_contracts_needing_ice()` called `date.today()` directly
   - Added `Clock` parameter to `__init__` (optional, defaults to `SystemClock`)
   - Replaced `date.today()` with `self._clock.now().date()`
   - Follows same pattern as `PeriodService`, `ApprovalService`, etc.

2. **PolicyAuthorityBuilder calling `datetime.now()` in domain code** (`finance_kernel/domain/policy_authority.py`)
   - Builder `__init__` used `datetime.now()` as default for `_effective_from`
   - Changed default to `None` (domain code must be pure — no I/O, no clock)
   - Updated `PolicyAuthority.effective_from` type from `datetime` to `datetime | None`
   - All downstream code already handles `None` correctly (`is_effective()` treats `None` as always-effective)

### Acknowledged (no fix needed)

- `Contract.is_within_pop` model property uses `date.today()` — documented inline as acceptable for model layer; callers needing determinism use `ContractService` with injected Clock.

### Verified clean

- No forbidden imports (finance_modules/services/config) into kernel
- No `float` for money — `Decimal` throughout
- No `MAX(seq)+1` patterns
- Domain purity intact (no ORM/I/O imports)
- All 270 affected tests pass

---

## 5. Batch Processing & Job Scheduling (GAP-03)

**Status:** COMPLETE -- All 10 phases done, 289 tests passing
**Full plan:** `.claude/plans/lazy-orbiting-catmull.md`

Batch execution framework with per-item SAVEPOINT isolation, progress tracking,
retry, audit trail, and an in-process cron-like scheduler. Wraps existing module
batch methods (mass depreciation, payment runs, dunning, etc.) in a uniform
framework.

| Phase | Description | Status | Tests |
|-------|------------|--------|-------|
| 0 | Domain types (enums + frozen dataclasses) | done | 23 |
| 1 | ORM models (BatchJobModel, BatchItemModel, JobScheduleModel) | done | 16 |
| 2 | Exceptions + AuditAction extensions | done | 23 |
| 3 | Task Registry + BatchTask Protocol | done | 23 |
| 4 | Batch Executor (SAVEPOINT-per-item) | done | 23 |
| 5 | Schedule Evaluator (pure functions) | done | 43 |
| 6 | Config schema + YAML loading | done | 8 |
| 7 | Module task implementations (10 tasks) | done | 86 |
| 8 | In-process scheduler | done | 15 |
| 9 | Architecture boundary + integration + audit trail | done | 21 |
| 10 | BatchOrchestrator DI container | done | 16 |

### Files Created (20 new)

```
finance_batch/
    __init__.py
    orchestrator.py
    domain/
        __init__.py
        types.py
        schedule.py
    models/
        __init__.py
        batch.py
    tasks/
        __init__.py
        base.py
        ap_tasks.py
        ar_tasks.py
        assets_tasks.py
        cash_tasks.py
        gl_tasks.py
        payroll_tasks.py
        credit_loss_tasks.py
    services/
        __init__.py
        executor.py
        scheduler.py
```

### Files Modified (8)

- `finance_modules/_orm_registry.py` -- Added `import finance_batch.models`
- `finance_kernel/exceptions.py` -- 8 batch exceptions
- `finance_kernel/models/audit_event.py` -- 6 batch AuditActions
- `finance_kernel/services/auditor_service.py` -- 5 `record_batch_*()` methods
- `finance_config/schema.py` -- BatchScheduleDef + batch_schedules field
- `finance_config/loader.py` -- parse_batch_schedule()
- `finance_config/assembler.py` -- batch schedule loading

### Invariants Enforced

| Rule | Name | Verified |
|------|------|----------|
| BT-1 | SAVEPOINT isolation | test_integration.py |
| BT-2 | Job idempotency | test_executor.py, test_integration.py |
| BT-3 | Sequence monotonicity | test_executor.py |
| BT-4 | Clock injection | All services use DeterministicClock |
| BT-5 | Audit trail | test_audit_trail.py |
| BT-6 | Schedule determinism | test_schedule_evaluator.py |
| BT-7 | Max retry safety | test_executor.py |
| BT-8 | Concurrency guard | test_executor.py |
| BT-9 | No kernel imports | test_batch_boundary.py |
| BT-10 | Graceful shutdown | test_scheduler.py |

---

## 6. Lifecycle Reconciliation Engine (GAP-REC)

**Status:** COMPLETE -- All 6 phases done, 101 tests passing
**Full plan:** `.claude/plans/lazy-orbiting-catmull.md`

Lifecycle reconciliation engine detecting policy drift, account mapping inconsistencies, amount flow violations, temporal anomalies, and link chain completeness issues across business object lifecycles (PO -> Receipt -> Invoice -> Payment).

**Architecture:** Pure engine (`LifecycleReconciliationChecker`) + service wrapper (`LifecycleReconciliationService`) using `JournalSelector` for R21 queries. Engine receives a fully populated `LifecycleChain` (nodes + edges with R21 metadata) and returns `LifecycleCheckResult` with findings.

### 7 Check Categories

| # | Check | Code | Severity |
|---|-------|------|----------|
| 1 | Policy regime drift | `POLICY_REGIME_DRIFT` | WARNING |
| 2 | Account role remapping | `ACCOUNT_ROLE_REMAPPED` | ERROR |
| 3 | Amount flow violation | `AMOUNT_FLOW_VIOLATION` | ERROR |
| 4 | Temporal ordering violation | `TEMPORAL_ORDER_VIOLATION` | WARNING |
| 5 | Incomplete fulfillment chain | `CHAIN_INCOMPLETE` | WARNING |
| 6 | Orphaned link | `ORPHANED_LINK` | ERROR |
| 7 | Double-count risk | `DOUBLE_COUNT_RISK` | ERROR |

### Invariants: RC-1 through RC-7

| Rule | Name |
|------|------|
| RC-1 | Policy regime consistency |
| RC-2 | Account role stability |
| RC-3 | Amount flow conservation |
| RC-4 | Temporal monotonicity |
| RC-5 | Chain completeness |
| RC-6 | Link-entry correspondence |
| RC-7 | Allocation uniqueness |

### Phases

| Phase | Description | Status | Tests |
|-------|------------|--------|-------|
| 0 | Domain types (lifecycle_types.py) | done | 21 |
| 1 | Policy + account checks (RC-1, RC-2) | done | (in checker) |
| 2 | Amount + temporal checks (RC-3, RC-4) | done | (in checker) |
| 3 | Chain + allocation checks (RC-5, RC-6, RC-7) + run_all_checks | done | 42 |
| 4 | Service layer (chain builder via JournalSelector) | done | 15 |
| 5 | Integration tests + architecture boundary | done | 15 |
| 6 | Audit event integration | done | 8 |

### Files Created (8 new)

| File | Description |
|------|-------------|
| `finance_engines/reconciliation/lifecycle_types.py` | Domain types for lifecycle checks |
| `finance_engines/reconciliation/checker.py` | Pure lifecycle reconciliation checker engine |
| `finance_services/lifecycle_reconciliation_service.py` | Service wrapper (JournalSelector + engine invocation) |
| `tests/engines/test_lifecycle_recon_types.py` | Type tests (21) |
| `tests/engines/test_lifecycle_recon_checker.py` | Engine tests (42) |
| `tests/engines/test_lifecycle_recon_integration.py` | Integration tests (15) |
| `tests/services/test_lifecycle_reconciliation_service.py` | Service tests (15) |
| `tests/services/test_lifecycle_recon_audit.py` | Audit tests (8) |

### Files Modified (5)

| File | Change |
|------|--------|
| `finance_engines/reconciliation/__init__.py` | Export new types and checker |
| `finance_kernel/models/audit_event.py` | Add LIFECYCLE_CHECK_PASSED/FAILED/WARNING |
| `finance_kernel/services/auditor_service.py` | Add `record_lifecycle_check()` method |
| `finance_kernel/selectors/journal_selector.py` | Add R21 columns to DTO + `get_posted_entry_by_event()` |
| `tests/architecture/test_lifecycle_recon_boundary.py` | Architecture boundary enforcement |

### Verification

- All 101 GAP-REC tests pass
- Full regression: 4920 passed, 0 failed
- Architecture boundary: engine is pure (no I/O imports), service uses selectors (no direct model imports)

---

## 7. Bank Reconciliation Checker (GAP-BRC)

**Status:** COMPLETE -- All 4 phases done, 65 tests passing

Bank reconciliation checker engine detecting stale unmatched lines, cross-statement
balance discontinuities, duplicate GL matches, and unexplained variance on completed
reconciliations.

**Architecture:** Pure engine (`BankReconciliationChecker`) + service wrapper
(`BankReconciliationCheckService`). Engine receives a `BankReconContext` (statements +
lines + match state) and returns `BankReconCheckResult` with findings. Service does NOT
query ORM directly (caller builds context from cash module ORM).

### 4 Check Categories

| # | Check | Code | Severity |
|---|-------|------|----------|
| 1 | Stale unmatched lines | `STALE_UNMATCHED_LINE` | WARNING |
| 2 | Cross-statement balance continuity | `BALANCE_DISCONTINUITY` | ERROR |
| 3 | Duplicate GL match | `DUPLICATE_GL_MATCH` | ERROR |
| 4 | Unexplained variance | `UNEXPLAINED_VARIANCE` | WARNING |

### Invariants: BR-1 through BR-4

| Rule | Name |
|------|------|
| BR-1 | Timely matching |
| BR-2 | Balance continuity |
| BR-3 | Match uniqueness |
| BR-4 | Variance accountability |

### Files Created (7 new)

| File | Description |
|------|-------------|
| `finance_engines/reconciliation/bank_recon_types.py` | Domain types (12 tests) |
| `finance_engines/reconciliation/bank_checker.py` | Pure checker engine (33 tests) |
| `finance_services/bank_reconciliation_check_service.py` | Service wrapper (10 tests) |
| `tests/engines/test_bank_recon_types.py` | Type tests |
| `tests/engines/test_bank_recon_checker.py` | Checker tests |
| `tests/engines/test_bank_recon_integration.py` | Integration tests (7 tests) |
| `tests/services/test_bank_recon_check_service.py` | Service tests |

### Files Modified (2)

| File | Change |
|------|--------|
| `finance_engines/reconciliation/__init__.py` | Export bank recon types + checker |
| `tests/architecture/test_lifecycle_recon_boundary.py` | Add bank checker purity checks |

### Note on Pre-Existing Test Failures

Full regression shows 144 failed + 121 errors, all pre-existing (DB schema issues:
"parties" table does not exist, AR ORM tests, audit immutability tests). None of the
65 GAP-BRC tests are affected.

---

## 8. DCAA Compliance Gap Closure (Streams A/B/C)

**Status:** COMPLETE -- All 6 phases done across 3 streams, 147 new tests passing
**Full plan:** `.claude/plans/lively-skipping-biscuit.md`

Closed all DCAA (Defense Contract Audit Agency) compliance gaps across three
streams: timekeeping controls, expense controls, and rate controls. Implements
9 new invariants (D1-D9) covering FAR 31.201-2(d), DCAA CAM 6-406, CAS 418,
FAR 31.205-46, FAR 31.201-3, and JTR requirements.

### 9 DCAA Invariants

| ID | Name | FAR/CAS Ref | Stream |
|----|------|-------------|--------|
| D1 | DAILY_RECORDING -- entries submitted within N days | FAR 31.201-2(d) | A |
| D2 | SUPERVISOR_APPROVAL -- no labor charges without approval | DCAA CAM 6-406 | A |
| D3 | TOTAL_TIME_BALANCE -- all hours account for expected total | CAS 418 | A |
| D4 | NO_CONCURRENT_OVERLAP -- no overlapping charges | CAS 418 | A |
| D5 | CORRECTION_BY_REVERSAL -- corrections via reversal + new entry | R10 | A |
| D6 | PRE_TRAVEL_AUTH -- travel expenses require pre-authorization | FAR 31.205-46 | B |
| D7 | GSA_RATE_CAP -- per diem/lodging capped at GSA rates | JTR/FAR 31.205-46 | B |
| D8 | RATE_CEILING -- labor rates capped at contract maximums | FAR 31.201-3 | C |
| D9 | FLOOR_CHECK_AUDIT -- floor checks are append-only audit artifacts | DCAA CAM 6-406.3 | A |

### Phases (All Streams)

| Phase | Description | Status |
|-------|------------|--------|
| 0 | Domain types (frozen dataclasses, all 3 streams) | done |
| 1 | ORM models (all 3 streams) | done |
| 2 | Pure engines (zero I/O, all 3 streams) | done |
| 3 | Schemas + profiles + config (all 3 streams) | done |
| 4 | Service + workflow wiring (all 3 streams) | done |
| 5 | Tests + architecture boundary (all 3 streams) | done |

### Stream A: Timesheet Controls

- **Domain types:** `finance_modules/payroll/dcaa_types.py` -- TimesheetEntry, TimesheetSubmission, FloorCheck, TotalTimeRecord, TimesheetCorrection, ConcurrentWorkCheck + enums
- **ORM:** `finance_modules/payroll/dcaa_orm.py` -- TimesheetSubmissionModel, TimesheetEntryModel, FloorCheckModel, TimesheetCorrectionModel
- **Engine:** `finance_engines/timesheet_compliance.py` -- validate_daily_recording, validate_total_time_accounting, detect_concurrent_overlaps, compute_total_time_record, validate_correction_reversal (all pure)
- **Service:** `finance_modules/payroll/service.py` -- submit_timesheet (D1/D3/D4), approve_timesheet (D2 -- sole posting gate for labor charges), reject_timesheet, correct_timesheet_entry (D5), record_floor_check (D9)
- **Workflow:** `finance_modules/payroll/workflows.py` -- TIMESHEET_WORKFLOW (6 transitions, 5 guards)

### Stream B: Expense Controls

- **Domain types:** `finance_modules/expense/dcaa_types.py` -- TravelAuthorization, TravelCostEstimate, GSARate, GSARateTable, GSAComplianceResult, GSAViolation + enums
- **ORM:** `finance_modules/expense/dcaa_orm.py` -- TravelAuthorizationModel, TravelAuthLineModel
- **Engine:** `finance_engines/expense_compliance.py` -- validate_pre_travel_authorization, validate_expense_within_authorization, validate_gsa_compliance, validate_lodging_against_gsa, compute_allowable_per_diem, lookup_gsa_rate (all pure)
- **Service:** `finance_modules/expense/service.py` -- submit_travel_authorization (D6), approve_travel_authorization (D6), record_expense_report_with_gsa_check (D6/D7)
- **Workflow:** `finance_modules/expense/workflows.py` -- TRAVEL_AUTH_WORKFLOW (5 transitions, 2 guards)

### Stream C: Rate Controls

- **Domain types:** `finance_modules/contracts/rate_types.py` -- LaborRateSchedule, ContractRateCeiling, RateVerificationResult, IndirectRateRecord, RateReconciliationRecord + enums
- **ORM:** `finance_modules/contracts/rate_orm.py` -- LaborRateScheduleModel, ContractRateCeilingModel, IndirectRateModel, RateReconciliationModel
- **Engine:** `finance_engines/rate_compliance.py` -- verify_labor_rate, find_applicable_rate, find_contract_ceiling, compute_rate_reconciliation, compute_all_reconciliations (all pure)
- **Service:** `finance_modules/contracts/service.py` -- verify_and_record_labor_rate (D8), record_indirect_rate, run_fiscal_year_rate_reconciliation (GL posting via underapplied/overapplied profiles)

### Key Design Decisions

1. **Timesheet approval gates labor charges (D2):** `approve_timesheet()` is the ONLY path that posts labor cost events. Maps pay_code to event_type (regular/overtime/pto) and posts for each entry.
2. **GSA enforcement is pre-posting validation (D7):** Pure engine computes violations before the posting pipeline. When enabled, violations block posting.
3. **Floor checks are append-only audit artifacts (D9):** Never modify existing records. Discrepancies trigger human review.
4. **Rate verification at service layer (D8):** Pure engine computes result, service decides whether to reject based on config.
5. **All corrections use reversal pattern (D5/R10):** `TimesheetCorrection` models the chain: original -> reversal -> new entry.
6. **GSA rate table is config-driven (YAML):** Follows existing config pattern.

### Files Created (16 new)

**Source (9):**
- `finance_modules/payroll/dcaa_types.py`
- `finance_modules/payroll/dcaa_orm.py`
- `finance_modules/expense/dcaa_types.py`
- `finance_modules/expense/dcaa_orm.py`
- `finance_modules/contracts/rate_types.py`
- `finance_modules/contracts/rate_orm.py`
- `finance_engines/timesheet_compliance.py`
- `finance_engines/expense_compliance.py`
- `finance_engines/rate_compliance.py`

**Tests (7):**
- `tests/modules/test_dcaa_timesheet_types.py` (19 tests)
- `tests/modules/test_dcaa_expense_types.py` (17 tests)
- `tests/modules/test_dcaa_rate_types.py` (16 tests)
- `tests/engines/test_timesheet_compliance.py` (30 tests)
- `tests/engines/test_expense_compliance.py` (26 tests)
- `tests/engines/test_rate_compliance.py` (22 tests)
- `tests/architecture/test_dcaa_boundary.py` (7 tests -- engine purity, type purity, ORM boundary)

### Files Modified (6)

| File | Change |
|------|--------|
| `finance_modules/payroll/workflows.py` | Added TIMESHEET_WORKFLOW (5 guards, 6 transitions) |
| `finance_modules/payroll/service.py` | Added 5 DCAA service methods (submit/approve/reject/correct/floor_check) |
| `finance_modules/expense/workflows.py` | Added TRAVEL_AUTH_WORKFLOW (2 guards, 5 transitions) |
| `finance_modules/expense/service.py` | Added 3 DCAA service methods (submit_auth/approve_auth/gsa_check) |
| `finance_modules/contracts/service.py` | Added 3 DCAA service methods (verify_rate/record_rate/reconciliation) |
| `finance_modules/_orm_registry.py` | Registered DCAA ORM models (phases 0-1) |

### Test Results

| Suite | File | Count |
|-------|------|-------|
| Timesheet types | `tests/modules/test_dcaa_timesheet_types.py` | 19 |
| Expense types | `tests/modules/test_dcaa_expense_types.py` | 17 |
| Rate types | `tests/modules/test_dcaa_rate_types.py` | 16 |
| Timesheet engine | `tests/engines/test_timesheet_compliance.py` | 30 |
| Expense engine | `tests/engines/test_expense_compliance.py` | 26 |
| Rate engine | `tests/engines/test_rate_compliance.py` | 22 |
| Architecture boundary | `tests/architecture/test_dcaa_boundary.py` | 7 |
| **Total new** | | **147** |

Full regression: 4831 passed, no new failures introduced.

### Architecture Boundary Enforcement

- **Engine purity:** All 3 DCAA engines have zero forbidden imports (no ORM, no services, no config, no I/O, no datetime.now/date.today)
- **Type purity:** All 3 DCAA type files have zero forbidden imports
- **ORM boundary:** All 3 DCAA ORM files only import from kernel.db and own module types

---

## 9. Guard Wiring — All Modules

**Status:** DONE for AR, AP, Cash, GL, Inventory, WIP — all use action-specific workflows; no generic *_OTHER_WORKFLOW
**Directive:** `docs/WORKFLOW_DIRECTIVE.md` — **No generic workflows.** Every financial action must bind to a **specific lifecycle workflow** (R28). Enforcement: `tests/architecture/test_no_generic_workflow.py` (passes for AR, AP, Cash, GL, Inventory, WIP).

**Migrated modules:** AR, AP, Cash, GL, Inventory, WIP now define action-specific workflows and map each service method to exactly one workflow. Generic workflows (AR_OTHER_WORKFLOW, AP_OTHER_WORKFLOW, CASH_OTHER_WORKFLOW, GL_OTHER_WORKFLOW, INVENTORY_OTHER_WORKFLOW, WIP_OTHER_WORKFLOW) removed. Full plan: `plans/GUARD_WIRING_PLAN.md`.

**Scope:** 13 modules, ~45 guarded transitions, ~35 new guard evaluators, ~183 new tests.

| Phase | Description | Status |
|-------|------------|--------|
| 0 | Foundation: migrate workflow types, create guard helpers, register ~35 evaluators | pending |
| 1 | Simple modules: Cash, Budget, Lease, Tax, WIP, Assets (6 modules, 13 guards) | pending |
| 2 | Medium complexity: AR, GL, Inventory, Revenue (4 modules, 13 guards) | pending |
| 3 | High complexity / DCAA: Payroll, Expense, Procurement (3 modules, 18 guards) | pending |
| 4 | Tests (~183 new across ~14 test files) | pending |
| 5 | Documentation update (GUARD_WIRING_GAP.md) | pending |

**Key decisions:**
1. Helper extraction (`check_transition_result()`) over AP's repeated if/else branching
2. Boolean-passthrough for DCAA guards (service pre-computes, evaluator reads boolean)
3. Canonical type migration (Phase 0A) enables `requires_approval` and `approval_policy`
4. All constructors backward-compatible (`workflow_executor=None` default)
5. Guard evaluators default to False for missing context (fail-closed)

---

## 10. Manufacturing Order Cleanup (To Do)

**Status:** PLANNED
**Context:** We use **manufacturing orders** only (no work orders). Docstrings, workflow names, and user-facing text were updated to "manufacturing order"; code and DB names were left as-is to avoid a large refactor.

| Item | Description | Priority |
|------|-------------|----------|
| Python/ORM naming | Rename `WorkOrderModel` → `ManufacturingOrderModel`, `WorkOrder` / `WorkOrderLine` → `ManufacturingOrder` / `ManufacturingOrderLine` in `finance_modules/wip/` (models, orm, service, profiles); keep `WORK_ORDER_WORKFLOW` alias for backward compat | Medium |
| Parameter names | Rename `work_order_id` → `manufacturing_order_id` in WIP and inventory APIs (breaking; consider deprecation window or optional alias) | Low |
| DB schema | Optionally rename table `wip_work_orders` → `wip_manufacturing_orders` and related FKs/columns via migration; or keep table name and document as "legacy name" | Low |
| Payroll / inventory | Align `work_order_id` references in payroll and inventory modules with manufacturing-order terminology in docs only, or rename if doing full cleanup | Low |

**Done so far:** Workflow `MANUFACTURING_ORDER_WORKFLOW` (name `wip_manufacturing_order`), all docstrings/comments and GUARD_WIRING_GAP.md use "manufacturing order"; `WORK_ORDER_WORKFLOW` retained as alias.

---

## 11. Reconciliation Tooling — Future Efforts

**Status:** Observability hooks **done** (2026-02-04); remaining items planned as future work.

Reconciliation tooling was scoped as four features. One is implemented; the other three are captured here for future phases.

### Completed

| Feature | Description | Location |
|--------|-------------|----------|
| **Observability hooks** | Metrics/logs for match hit-rate, duplicate suggestion rate, time-to-clear, guard failures. Structured log events (`match_suggested`, `match_accepted`, `guard_failure`) from ReconciliationManager, invokers, and cash auto_reconcile. Not persisted to DB; consumed by log aggregators. | `finance_services/observability.py`; wired in `reconciliation_service.py`, `invokers.py`, `finance_modules/cash/service.py` |

### Future Efforts

| Feature | Description | Est. |
|--------|-------------|------|
| **Investigation views** | Prebuilt, one-click bundles per artifact (e.g. invoice, statement line) with all related links, suggestions, and state; exportable to CSV for audit or support. | 3–5 days |
| **Inline diff & why-not** | When a suggestion is rejected, show which guard/tolerance failed (e.g. variance type, threshold) and optional "approve with exception" flow for controlled override. | 2–4 days |
| **Batch assist (shadow mode)** | Run auto-match without committing; return a ranked decision queue (suggested matches + confidence) for human or system review before apply. | 3–5 days |

These can be tackled in any order; investigation views and batch assist are independent; inline diff/why-not can build on the same guard-failure metadata already emitted by observability.

---

## Next Steps

When resuming, check which plan the user wants to work on:
- **Reconciliation Tooling (future):** Section 11 — investigation views, inline diff/why-not, batch assist (shadow mode); observability hooks already done
- **Manufacturing Order Cleanup:** Section 10 above — rename WorkOrder/WorkOrderModel/work_order_id to manufacturing order naming (Python, APIs, optional DB migration)
- **Guard Wiring:** Read `plans/GUARD_WIRING_PLAN.md` -- planned, not started
- **Approval Engine:** Read `plans/archive/2026-02-04_approval-engine-plan.md` -- Phase 10 complete
- **ERP Ingestion:** Read `plans/archive/2026-02-04_erp-ingestion-plan.md` -- Phase 9 complete
- **Batch Processing:** GAP-03 complete, 289 tests passing
- **Lifecycle Reconciliation:** GAP-REC complete, 101 tests passing
- **Bank Reconciliation Checks:** GAP-BRC complete, 65 tests passing
- **DCAA Compliance:** All 3 streams complete, 147 tests passing
- **Reversal Hardening:** Pick items from section 3 above
