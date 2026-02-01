# Reversal System Implementation Plan (v2)

**Date:** 2026-02-01
**Status:** COMPLETE (all 6 phases done)
**Objective:** Implement journal entry reversals with zero mutations to posted records, single canonical linkage, and full posting-pipeline compliance.
**Final test count:** 4200 passed, 0 failed, 7 skipped, 0 errors

---

## Governing Principles

1. **Posted rows never change.** No POSTED → REVERSED exemption. No immutability carve-outs. Reversal state is a derived fact computed from the existence of a reversal entry.
2. **One canonical linkage.** `reversal_of_id` FK on the reversal entry is the single source of truth, enforced by a unique partial index. Link graph REVERSED_BY edge is secondary, written atomically in the same transaction.
3. **Reversal is a first-class posting mode.** Routes through JournalWriter with all validations (R4, R5, R9, R16, R21, R22, R24). No hand-rolled ORM inserts.
4. **Period semantics explicit and enforced.** Callers must choose `reverse_in_same_period` or `reverse_in_current_period`. No silent defaulting.
5. **Idempotency and atomicity mandatory.** Deterministic key `reversal:{original_entry_id}:{ledger_id}`, unique constraint enforced. All artifacts (entry, link, audit event) succeed or none succeed.

---

## What Already Exists (~65%)

| Component | Location | Status |
|-----------|----------|--------|
| `JournalEntryStatus.REVERSED` | `journal.py:68` | Enum value exists (will become dead code -- see Phase 1) |
| `reversal_of_id` FK | `journal.py:159` | Column exists, nullable, no unique constraint |
| `AuditorService.record_reversal()` | `auditor_service.py:304` | Ready to call |
| `LinkType.REVERSED_BY` | `economic_link.py` | max_children=1 |
| `LinkGraphService.is_reversed()` | `link_graph_service.py` | Works, passing tests |
| `CompensatingEntry.create_reversal()` | `unwind.py` | Pure line flipper, R4 validated |
| `ReversalError` hierarchy | `exceptions.py:79-81` | `EntryNotPostedError`, `EntryAlreadyReversedError` |
| K1 metamorphic tests | `test_equivalence.py:24-69` | 4 tests skipped |

---

## Implementation Phases

### Phase 1: Canonical Linkage Constraint + Derived Status -- DONE

**Status:** COMPLETE (4172 passed, 0 failed, 0 errors)

**Goal:** Establish `reversal_of_id` as the single source of truth with database-level enforcement. Remove dependency on mutable status.

**Files modified:**
- `finance_kernel/models/journal.py`

**What was done:**
- Added unique partial index `uq_journal_reversal_of` on `reversal_of_id WHERE NOT NULL`
- Added check constraint `chk_reversal_entry_valid`: `reversal_of_id IS NULL OR idempotency_key LIKE 'reversal:%'`
- Added `reversed_by` relationship (reverse of `reversal_of` FK, uselist=False, lazy=selectin)
- Rewrote `is_reversed` property: now derived from `self.reversed_by is not None` (not mutable status)
- Added `is_reversal` property: `self.reversal_of_id is not None`
- Documented `JournalEntryStatus.REVERSED` as deprecated (enum value retained for compatibility)
- `db/immutability.py` and `db/sql/*.sql` untouched (no exemptions added)

**Changes (original plan for reference):**

1. **Add unique partial index** on `reversal_of_id WHERE reversal_of_id IS NOT NULL`:
   ```python
   # In __table_args__:
   Index(
       "uq_journal_reversal_of",
       "reversal_of_id",
       unique=True,
       postgresql_where=text("reversal_of_id IS NOT NULL"),
   )
   ```
   This guarantees at most one reversal per original at the database level.

2. **Rewrite `is_reversed` property** from status check to canonical linkage query:
   ```python
   @property
   def is_reversed(self) -> bool:
       """True iff a posted reversal entry exists for this entry.

       Derived from canonical linkage (reversal_of_id), not mutable status.
       """
       # Loaded via relationship backref, or queried lazily
       return self._reversal_entry is not None
   ```
   Add a `_reversal_entry` relationship (backref from `reversal_of_id` FK).

3. **Add `is_reversal` property**:
   ```python
   @property
   def is_reversal(self) -> bool:
       """True iff this entry is itself a reversal of another entry."""
       return self.reversal_of_id is not None
   ```

4. **Do NOT remove `JournalEntryStatus.REVERSED`** from the enum (would break existing code). Document it as deprecated/unused.

5. **Do NOT add any immutability exemptions** to `db/immutability.py`. No changes to that file at all.

**Tests:**
- Unique constraint: inserting two reversal entries for the same original → IntegrityError
- `is_reversed` returns True when reversal entry exists, False otherwise
- `is_reversal` returns True on reversal entry, False on original

---

### Phase 2: JournalWriter.write_reversal() -- DONE

**Status:** COMPLETE (4172 passed, 0 failed, 0 errors)

**What was done:**
- Added `write_reversal()` method (~120 lines) to JournalWriter
- Reuses `_finalize_posting()` (R9 sequence, R21 snapshot validation)
- Reuses `_get_existing_entry()` for idempotency
- Explicit R4 balance check as defense-in-depth
- All reversal lines have `is_rounding=False` (R22)
- Idempotency key: `reversal:{original_entry.id}:{ledger_id}`
- Snapshot versions copied from original (documented as accounting policy)
- Flips DEBIT↔CREDIT, preserves: account_id, amount, currency, dimensions, exchange_rate_id, line_seq

**File:** `finance_kernel/services/journal_writer.py`

**New method: `write_reversal()`**
```python
def write_reversal(
    self,
    original_entry: JournalEntry,
    source_event_id: UUID,
    actor_id: UUID,
    effective_date: date,
    reason: str,
    event_type: str = "system.reversal",
) -> JournalEntry:
```

**Algorithm:**
1. Load original entry's lines (ordered by `line_seq`)
2. Build reversal lines by flipping `side` (DEBIT↔CREDIT), preserving:
   - `account_id` (exact COA code, no re-resolution needed)
   - `amount` (identical)
   - `currency` (identical)
   - `dimensions` (identical)
   - `exchange_rate_id` (identical -- same rates as original)
   - `is_rounding` (False on all reversal lines -- R22: only Bookkeeper creates rounding lines; reversals are mechanical inversions that should balance exactly)
   - `line_seq` (preserved for deterministic ordering)
3. Construct idempotency key: `reversal:{original_entry.id}:{ledger_id}`
   - Where `ledger_id` is extracted from `entry_metadata["ledger_id"]`
4. Create JournalEntry:
   - `source_event_id = source_event_id` (the reversal event)
   - `reversal_of_id = original_entry.id`
   - `effective_date = effective_date` (caller-provided, NOT defaulted)
   - `description = f"Reversal of entry {original_entry.seq}: {reason}"`
   - `entry_metadata = {"ledger_id": ..., "reversal_reason": reason, "original_entry_id": str(original_entry.id)}`
   - R21 snapshot versions: copy from original entry (same COA version)
5. Call `_validate_rounding_invariants()` (R5, R22)
6. Call `_finalize_posting()` (R9 sequence, DRAFT→POSTED)
7. Return the new POSTED reversal entry

**What this reuses from the existing pipeline:**
- `_finalize_posting()` → sequence allocation (R9), DRAFT→POSTED, posted_at
- `_validate_rounding_invariants()` → R5/R22
- R21 snapshot version validation
- Idempotency key uniqueness (R3/R8) via existing unique constraint

**What this skips (correctly):**
- Role resolution (L1) -- we have exact account IDs already
- Balance validation across roles -- we validate the reversal balances via R4 check
- Subledger control validation (G9) -- reversal is mechanical, not interpretive

---

### Phase 3: ReversalService (Orchestrator) -- DONE

**Status:** COMPLETE (4172 passed, 0 failed, 0 errors)

**What was done:**
- Created new file `finance_kernel/services/reversal_service.py` (~320 lines)
- `ReversalService` class: thin orchestrator with explicit period semantics
- Two public methods: `reverse_in_same_period()` and `reverse_in_current_period()`
- `ReversalResult` frozen dataclass for immutable return type
- Constructor accepts: session, journal_writer, auditor, link_graph, period_service, clock
- Internal `_load_and_validate()`: loads entry, checks POSTED, checks not already reversed
- Internal `_execute_reversal()`: 4-step atomic pipeline:
  1. Creates reversal Event (type: `system.reversal`, producer: `kernel.reversal_service`)
  2. Calls `JournalWriter.write_reversal()` for the journal entry
  3. Establishes `REVERSED_BY` economic link via `LinkGraphService.establish_link()`
  4. Records audit event via `AuditorService.record_reversal()`
- All steps execute in the same transaction (caller controls commit)
- Structured logging at every step for audit trail
- Follows existing service patterns (clock injection, session management, etc.)

**Constructor:**
```python
def __init__(
    self,
    session: Session,
    journal_writer: JournalWriter,
    auditor: AuditorService,
    link_graph: LinkGraphService,
    period_service: PeriodService,
    clock: Clock | None = None,
):
```

**Two methods with explicit period semantics:**

```python
def reverse_in_same_period(
    self,
    original_entry_id: UUID,
    reason: str,
    actor_id: UUID,
    reversal_event_id: UUID | None = None,
) -> ReversalResult:
    """Reverse into the original entry's period. Fails if period is closed."""

def reverse_in_current_period(
    self,
    original_entry_id: UUID,
    reason: str,
    actor_id: UUID,
    effective_date: date,
    reversal_event_id: UUID | None = None,
) -> ReversalResult:
    """Reverse into a specified open period. Required when original period is closed."""
```

**Shared algorithm (both methods):**
1. Load original entry by ID (raise if not found)
2. Validate: `original.status == POSTED` (raise `EntryNotPostedError`)
3. Validate: no existing reversal (query `reversal_of_id = original.id`; raise `EntryAlreadyReversedError`)
4. Validate: target effective_date falls in OPEN period (via `PeriodService`)
   - `reverse_in_same_period`: uses `original.effective_date`, fails if period closed
   - `reverse_in_current_period`: uses caller-provided `effective_date`
5. Create reversal Event (event_type: `system.reversal`, payload: `{original_entry_id, reason}`)
6. Call `JournalWriter.write_reversal(original, source_event_id, actor_id, effective_date, reason)`
7. Create REVERSED_BY economic link (parent=original, child=reversal) via `LinkGraphService.establish_link()`
8. Create audit event via `AuditorService.record_reversal()`
9. Return `ReversalResult`

**Atomicity:** Steps 5-8 execute in the same database transaction. If any step fails, the entire transaction rolls back. The unique constraint on `reversal_of_id` prevents races.

**Idempotency:** If `write_reversal()` detects a duplicate idempotency key (reversal already exists), it returns the existing reversal entry. The `EntryAlreadyReversedError` check in step 3 catches the logical case; the DB unique constraint catches the race condition.

---

### Phase 4: PostingOrchestrator Wiring -- DONE

**Status:** COMPLETE (4172 passed, 0 failed, 0 errors)

**What was done:**
- Added `from finance_kernel.services.reversal_service import ReversalService` import
- Wired `self.reversal_service = ReversalService(...)` after journal_writer, before outcome_recorder
- Follows existing pattern: public attribute (not private property), same session/clock

**File:** `finance_services/posting_orchestrator.py`

**Change:** Wire `ReversalService` into the DI container as public attribute:
```python
self.reversal_service = ReversalService(
    session=session,
    journal_writer=self.journal_writer,
    auditor=self.auditor,
    link_graph=self.link_graph,
    period_service=self.period_service,
    clock=self._clock,
)
```

---

### Phase 5: CorrectionEngine Integration -- DONE

**Status:** COMPLETE (4172 passed, 0 failed, 0 errors)

**What was done:**
- Added `JournalWriter.get_entry(entry_id)` public method for architecture-boundary-safe entry loading
- Added `PostingOrchestrator.make_correction_writer(actor_id, creating_event_id)` factory method
- Factory returns a `Callable[[CompensatingEntry], UUID]` callback that:
  1. Loads the original JournalEntry via `writer.get_entry()`
  2. Calls `writer.write_reversal()` with proper reversal_of_id, idempotency key, R9, R21
  3. Returns the reversal entry's ID
- Architecture boundary compliant: no `finance_kernel.models.journal` or `finance_engines` imports in posting_orchestrator.py source (both tests use raw string scanning)
- CorrectionEngine unchanged -- it already accepts `journal_entry_writer` callback

**Files modified:**
- `finance_kernel/services/journal_writer.py` -- added `get_entry(entry_id)` public method
- `finance_services/posting_orchestrator.py` -- added `make_correction_writer()` factory method

**Longer term:** CorrectionEngine should produce typed plan operations (`ReverseEntry`, `PostAdjustment`, `PostReplacement`) and an orchestrator executes them via the correct services. This is a larger refactor and can be deferred -- the callback adapter unblocks the immediate gap.

---

### Phase 6: Tests -- DONE

**Status:** COMPLETE (4200 passed, 0 failed, 7 skipped, 0 errors)

**6a. Unit tests** -- `tests/services/test_reversal_service.py` (new file) -- DONE (24 tests)

Happy path (TestReversalHappyPath, 4 tests):
- `test_reverse_in_same_period_creates_posted_reversal` -- creates POSTED entry with REVERSED_BY link
- `test_reverse_preserves_original_status` -- original stays POSTED (R10 compliance)
- `test_reverse_creates_audit_event` -- hash-chain linked
- `test_reverse_in_current_period_succeeds` -- cross-period reversal with different effective_date

Line fidelity (TestReversalLineFidelity, 6 tests):
- `test_reversal_flips_debit_credit_sides` -- DEBIT↔CREDIT mechanical inversion
- `test_reversal_preserves_amounts` -- identical amounts
- `test_reversal_preserves_account_ids` -- same accounts
- `test_reversal_preserves_currencies` -- same currency codes
- `test_reversal_preserves_line_seq_ordering` -- deterministic line ordering
- `test_multi_line_reversal_flips_all_lines` -- multi-line entry fidelity

Canonical linkage (TestCanonicalLinkage, 3 tests):
- `test_reversal_sets_reversal_of_id` -- FK populated
- `test_original_is_reversed_property_true` -- derived property works
- `test_reversal_is_reversal_property_true` -- derived property works

Error paths (TestReversalErrors, 3 tests):
- `test_reverse_draft_entry_fails` -- `EntryNotPostedError`
- `test_reverse_already_reversed_fails` -- `EntryAlreadyReversedError`
- `test_reverse_nonexistent_entry_fails` -- `ValueError`

Period semantics (TestPeriodSemantics, 2 tests):
- `test_reverse_in_same_period_closed_fails` -- `ClosedPeriodError`
- `test_reverse_in_current_period_closed_original_succeeds` -- cross-period when original period closed

Invariant preservation (TestInvariantPreservation, 6 tests):
- `test_reversal_lines_have_no_rounding_lines` -- R22 compliance
- `test_reversal_entry_has_valid_idempotency_key` -- deterministic key format
- `test_reversal_entry_has_r21_snapshot_versions` -- COA/dimension/rounding/currency versions copied
- `test_reversal_balances_per_currency` -- R4 balance check
- `test_reversal_has_correct_description` -- structured description
- `test_reversal_has_correct_metadata` -- reversal_reason + original_entry_id in metadata

**6e. K1 metamorphic tests** -- `tests/metamorphic/test_equivalence.py` -- DONE (4 tests)

- `test_post_reverse_returns_to_baseline` -- trial balance hash identity (K1.1)
- `test_reversed_entry_has_negated_lines` -- mechanical inversion (K1.2)
- `test_double_reverse_blocked` -- idempotency / constraint (K1.3)
- `test_reversal_in_different_period_preserves_identity` -- cross-period net-to-zero (K1.4)
- K2 split/merge tests remain skipped (deferred)

**Deferred (not blocking):**

- 6b. Concurrency tests -- `test_concurrent_reversal_exactly_one_succeeds`, `test_atomicity_failure_after_entry_before_link`. Race condition prevention is already guaranteed by the unique partial index on `reversal_of_id`; these tests would add confidence but are not blocking.
- 6c. Selector consistency tests -- `tests/selectors/test_reversal_queries.py`. Trial balance correctness is already verified by K1 metamorphic tests.
- 6f. Integration E2E tests -- `tests/integration/test_reversal_e2e.py`. Core flows already covered by unit + K1 metamorphic tests.

---

## Files Modified (All Phases)

| File | Change |
|------|--------|
| `finance_kernel/models/journal.py` | Unique partial index on `reversal_of_id`, `is_reversed` derived property, `is_reversal` property, `reversed_by` relationship |
| `finance_kernel/services/journal_writer.py` | Add `write_reversal()` method + `get_entry()` public method |
| **`finance_kernel/services/reversal_service.py`** | **NEW** -- thin orchestrator with explicit period semantics |
| `finance_services/posting_orchestrator.py` | Wire ReversalService + `make_correction_writer()` callback factory |
| `tests/metamorphic/test_equivalence.py` | Unskip + implement 4 K1 tests |
| **`tests/services/test_reversal_service.py`** | **NEW** -- 24 unit + invariant tests |

## Files NOT Modified (Intentionally)

| File | Why |
|------|-----|
| `finance_kernel/db/immutability.py` | **NO CHANGES.** No POSTED→REVERSED exemption. |
| `finance_kernel/db/sql/*.sql` | **NO CHANGES.** No trigger modifications. |
| `finance_kernel/services/auditor_service.py` | `record_reversal()` already exists |
| `finance_kernel/domain/economic_link.py` | `REVERSED_BY` already defined |
| `finance_kernel/services/link_graph_service.py` | Already works |
| `finance_kernel/exceptions.py` | Error types already defined |

---

## Verification

```bash
# Full regression (4200 passed, 0 failed -- verified 2026-02-01)
python3 -m pytest tests/ -v --tb=short

# Reversal-specific (24 unit + 4 metamorphic = 28 tests)
python3 -m pytest tests/services/test_reversal_service.py -v
python3 -m pytest tests/metamorphic/test_equivalence.py -v

# Immutability UNCHANGED (no new exemptions)
python3 -m pytest tests/unit/test_immutability.py -v

# Architecture boundary clean
python3 -m pytest tests/architecture/ -v
```

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| `is_reversed` query performance | Unique partial index on `reversal_of_id` makes reverse lookup O(1) |
| Dual linkage divergence (FK vs link graph) | Both written atomically in same transaction; link graph is secondary |
| Reversal lines don't balance | `write_reversal()` runs through `_validate_rounding_invariants()` and R4 check |
| Missing R21 snapshot on reversal | Copied from original entry; validated by `_finalize_posting()` |
| Race condition on double-reverse | Unique partial index on `reversal_of_id` → DB-level guarantee |
| Period semantics confusion | Two explicit methods, no defaults; caller must choose |
| CorrectionEngine still unwired | Callback adapter in Phase 5; full typed-plan refactor deferred |

---

## What This Plan Does NOT Do (Intentionally Deferred)

1. **Module-level void workflows** (AP void_invoice, AR void_payment, etc.) -- these compose on top of ReversalService and can be added per-module later.
2. **Partial reversals** (reverse some lines only) -- requires additional design for line-level selection and balance validation. Full reversals are the correct first step.
3. **CorrectionEngine typed-plan refactor** -- the callback adapter unblocks immediate use; the full `ReverseEntry`/`PostAdjustment`/`PostReplacement` operation model is a separate design task.
4. **Projection table for reversed status** -- derived property via unique index is sufficient. Can add a materialized view later if query patterns demand it.


Potential future opportunities:

## Specific improvements

1. **Reversal request idempotency at service layer**

   * Add a unique index on `(reversal_of_id, ledger_id)` in addition to the idempotency key.
   * In `ReversalService._load_and_validate()`, attempt a `SELECT ... FOR UPDATE` on the original entry row to serialize concurrent reversals before hitting the unique constraint.

2. **Policy snapshot for reversals**

   * Snapshot `posting_policy_version` and `posting_policy_hash` on the reversal entry, mirroring AL-2 from approvals.
   * Store in `entry_metadata` and validate on replay.

3. **Event-chain integrity**

   * Add `prev_event_id` to the reversal Event payload and validate that it matches the original entry’s source event.
   * Enforce a single-hop event chain for reversals in AuditorService.

4. **Ledger boundary enforcement**

   * Validate `ledger_id` on the original entry matches the ledger resolved for the reversal event.
   * Reject cross-ledger reversals explicitly.

5. **Dimension immutability check**

   * Add a guard in `write_reversal()` that verifies `dimensions_schema_version` on the reversal matches the original’s snapshot (fail fast if schema drift occurred).

6. **Link graph uniqueness**

   * Add a DB unique constraint on `(parent_id, link_type)` for `REVERSED_BY` to mirror the journal FK uniqueness at the graph layer.

7. **Effective date monotonicity**

   * Enforce `effective_date >= original.effective_date` for `reverse_in_current_period()` to prevent temporal inversion of economic facts.

8. **Audit hash chaining**

   * Include both original and reversal entry hashes in `record_reversal()` and compute a chained hash:

     * `hash = SHA256(prev_audit_hash || original_entry_hash || reversal_entry_hash || timestamp)`

9. **Reversal metadata contract**

   * Formalize `entry_metadata` schema with a dataclass and validator:

     * `{"ledger_id", "reversal_reason", "original_entry_id", "policy_version", "policy_hash"}`
   * Reject unknown keys.

10. **Performance index for selectors**

    * Add covering index on `journal_entries`:

      * `(reversal_of_id, effective_date, ledger_id)`
    * Optimizes `is_reversed` and trial balance queries that join on reversals.

11. **Multi-currency invariant**

    * Add explicit assertion that `original_entry.lines[*].currency` is uniform before reversal.
    * Fail if mixed-currency entries appear (prevents silent FX mismatches).

12. **Failure-mode test**

    * Add test where `LinkGraphService.establish_link()` fails after `write_reversal()` and assert full rollback (no orphan reversal entry).

13. **Deprecation cleanup**

    * Add a lint/architecture test that forbids any new reads or writes of `JournalEntryStatus.REVERSED` outside migration code.

14. **API symmetry**

    * Add `can_reverse(entry_id)` method to `ReversalService` for UI/agent preflight checks (period open, not already reversed, ledger match).

15. **Replay harness**

    * Add a deterministic replay test that replays original + reversal events against a blank ledger and asserts identical final balances and entry hashes.
