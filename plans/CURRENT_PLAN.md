# Current Plan: Deprecate Pipeline A + Decision Journal for Audit Traceability

## Status: ALL PHASES COMPLETE (including Phase 6: DB Persistence)

---

## Objective
1. Remove Pipeline A (PostingOrchestrator) — zero production callers, only tests used it
2. Migrate all tests to Pipeline B (InterpretationCoordinator)
3. Add Decision Journal for audit traceability via enhanced structured logs
4. Persist decision journal on InterpretationOutcome (no external LogCapture needed)

## Completed Work

### Phase 1: Migrate Test Files (DONE)
- Deleted 4 orchestrator-specific test files (test_orchestrator_attack_vectors, test_r8_idempotency_locking, test_rule_version, test_open_closed)
- Deleted 13 duplicate Pipeline A test files
- Migrated 10 unique test files to Pipeline B:
  - tests/concurrency/test_r9_sequence_safety.py
  - tests/multicurrency/test_triangle_conversions.py
  - tests/adversarial/test_account_deletion_protection.py
  - tests/adversarial/test_rounding_account_deletion.py
  - tests/architecture/test_actor_validation.py
  - tests/adversarial/test_producer_immutability.py
  - tests/audit/test_failed_posting_audit.py
  - tests/crash/test_fault_injection.py
  - tests/fuzzing/test_hypothesis_fuzzing.py
  - tests/fuzzing/test_adversarial.py

### Phase 2: Delete Pipeline A Production Code (DONE)
- Deleted finance_kernel/services/posting_orchestrator.py (486 lines)
- Deleted finance_kernel/services/reference_data_loader.py
- Cleaned up finance_kernel/services/__init__.py (removed Pipeline A exports)
- Cleaned up tests/conftest.py (removed Pipeline A fixtures)

### Phase 3: Add Missing Log Statements to Pipeline B Services (DONE)
Added 8 new structured log statements across 3 services:

**journal_writer.py:**
1. `balance_validated` — per currency: ledger_id, currency, sum_debit, sum_credit, balanced
2. `role_resolved` — per line: role, account_code, account_id, ledger_id, coa_version, side, amount, currency
3. `line_written` — per line: entry_id, line_seq, role, account_code, side, amount, currency, is_rounding
4. `invariant_checked` — R21_REFERENCE_SNAPSHOT: entry_id, passed, all version numbers
5. `journal_entry_created` — entry_id, status, seq, idempotency_key, effective_date, posted_at, profile_id, ledger_id

**interpretation_coordinator.py:**
6. `config_in_force` — all R21 version numbers at interpretation time (coa, dimension, rounding, currency)
7. `reproducibility_proof` — input_hash (canonical hash of intent), output_hash (canonical hash of result)
8. Enhanced `FINANCE_KERNEL_TRACE` with input_hash + output_hash
9. Enhanced `interpretation_started` with profile_version, econ_event_id, effective_date, ledger_count

**outcome_recorder.py:**
10. Enhanced `outcome_recorded` for POSTED status with econ_event_id, journal_entry_ids, profile details

### Phase 4: Build LogCapture + Wire into TraceSelector (DONE)
- NEW: `finance_kernel/services/log_capture.py`
  - In-process `logging.Handler` that captures all structured log records
  - Implements `LogQueryPort` protocol (query_by_correlation_id, query_by_event_id, query_by_trace_id)
  - Context manager support (`with capture: ...`)
  - Records stored as JSON dicts with all structured fields
- TraceSelector already accepts `log_query: LogQueryPort | None` — no changes needed

### Phase 5: Update Demo Test (DONE)
- MODIFIED: `tests/demo/test_trace_bundle_demo.py`
  - Wires LogCapture as context manager around posting
  - Passes LogCapture to TraceSelector as `log_query=capture`
  - Prints full "DECISION JOURNAL" section with auditor-readable formatting per log action
  - Demonstrates bidirectional lookup (trace by event_id AND by journal_entry_id)
  - Asserts 0 missing facts (LogCapture provides all structured log data)
  - Asserts all 7 key decision steps present in timeline

### Phase 6: Persist Decision Journal on InterpretationOutcome (DONE)
Every posting now automatically saves its full decision journal to the DB. No wrapping with LogCapture needed by callers.

**Files changed:**

1. **`finance_kernel/models/interpretation_outcome.py`** — Added `decision_log` JSON column
   - Stores list of structured log dicts captured during the posting pipeline
   - Populated automatically by InterpretationCoordinator via LogCapture

2. **`finance_kernel/services/outcome_recorder.py`** — Added `decision_log` parameter to all 5 recording methods
   - `record_posted()`, `record_rejected()`, `record_blocked()`, `record_provisional()`, `record_non_posting()`

3. **`finance_kernel/services/interpretation_coordinator.py`** — LogCapture installed automatically
   - LogCapture installed before LogContext binding in `interpret_and_post()`
   - After result, writes `result.outcome.decision_log = capture.records` and flushes
   - Uninstalled in finally block

4. **`finance_kernel/selectors/trace_selector.py`** — Reads decision_log from DB first
   - `_resolve_log_entries()` checks `InterpretationOutcome.decision_log` before LogQueryPort
   - `_load_decision_log()` — new helper to query persisted decision log
   - `_records_to_timeline()` — extracted shared log-to-TimelineEntry conversion
   - `InterpretationInfo` DTO now includes `decision_log` field
   - LogQueryPort still works as fallback for non-coordinator postings

5. **`tests/demo/test_trace_bundle_demo.py`** — Simplified
   - Removed explicit LogCapture wrapping
   - Removed `log_query=capture` from TraceSelector
   - Timeline populates from DB-persisted `decision_log` automatically
   - New assertion: `bundle.interpretation.decision_log is not None`

6. **`tests/trace/test_trace_selector.py`** — Updated expected_source string
   - `test_no_log_query_declares_missing_fact` expects `"interpretation_outcome.decision_log"`

### Verification
- Full test suite: **2832 passed**, 134 failed (all pre-existing module PROFILE_NOT_FOUND), 11 skipped
- Zero regressions from any phase
- Demo test: 21 assertions pass, 17 structured log records in timeline from DB
- No external LogCapture wrapping needed — every posting persists its decision journal automatically

---

## Decisions Made
- Pipeline B (InterpretationCoordinator) is the sole posting pipeline going forward
- Decision Journal persisted as JSON on `InterpretationOutcome.decision_log` column
- InterpretationCoordinator installs LogCapture internally — callers don't need to know
- TraceSelector reads from DB first, falls back to LogQueryPort, then MissingFact
- StrategyRegistry stays in domain layer (pure code) — only PostingOrchestrator was deleted
- LogCapture is a stdlib logging.Handler — zero external dependencies
- Reproducibility proof uses canonical JSON hashing of intent inputs and result outputs
- TraceSelector reads decision_log from DB = complete auditor-readable story without any setup
