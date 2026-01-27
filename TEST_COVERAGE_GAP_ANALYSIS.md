# Test Coverage Gap Analysis

**Generated:** 2026-01-26
**Updated:** 2026-01-26
**Current Tests:** 226
**Analysis against:** Finance Kernel Test Checklist + Addendum A Certification Suite

Legend: ✅ = Covered | ⚠️ = Partial | ❌ = Missing

---

## Executive Summary

**Core Intent:** ✅ Achieved - The kernel functions as a system of record with correct behavior under retries, concurrency, and adversarial input.

**Full Certification (Addendum A):** ⚠️ Incomplete - Missing rule evolution tests (G1-G2), metamorphic tests (K1-K2), and full permutation coverage.

**Quick Wins Identified:**
1. G2 Rule version preservation test (15 min) - TODO
2. K1 Post+reverse equivalence (requires reversal service implementation first)

**Blocking Items:**
- Reversal service not implemented (blocks K1-K2 metamorphic testing)
- No fault injection infrastructure (blocks B1 full crash testing)

---

## Summary

| Category | Items | Covered | Partial | Missing |
|----------|-------|---------|---------|---------|
| 1. Domain invariants | 7 | 7 | 0 | 0 |
| 2. Strategy system | 6 | 4 | 2 | 0 |
| 3. Bookkeeper behavior | 6 | 5 | 1 | 0 |
| 4. Idempotency | 4 | 4 | 0 | 0 |
| 5. Sequence integrity | 5 | 4 | 1 | 0 |
| 6. Transaction atomicity | 4 | 3 | 1 | 0 |
| 7. Period governance | 7 | 6 | 1 | 0 |
| 8. Adjustment policy | 5 | 4 | 1 | 0 |
| 9. Audit chain integrity | 5 | 5 | 0 | 0 |
| 10. Persistence layer | 5 | 4 | 1 | 0 |
| 11. Concurrency/isolation | 6 | 5 | 1 | 0 |
| 12. Error classification | 3 | 2 | 1 | 0 |
| 13. Replay and recovery | 4 | 2 | 1 | 1 |
| 14. Security boundaries | 4 | 1 | 0 | 3 |
| 15. Determinism and time | 3 | 1 | 2 | 0 |
| 16. Schema/migration | 3 | 1 | 0 | 2 |
| 17. Performance | 3 | 2 | 1 | 0 |
| 18. Observability | 3 | 1 | 0 | 2 |
| 19. Data integrity | 3 | 2 | 1 | 0 |
| 20. Contract tests | 3 | 0 | 0 | 3 |
| **TOTAL** | **91** | **63** | **14** | **14** |

**Coverage: 69% fully covered, 15% partially covered, 15% missing**

---

## Detailed Analysis

### 1. Domain invariants (pure layer) ✅ FULLY COVERED

| Item | Status | Test(s) |
|------|--------|---------|
| Money cannot mix currencies without explicit exchange rate | ✅ | `test_money_prevents_cross_currency_operations` |
| Negative amounts rejected at DTO boundary | ✅ | `test_line_spec_rejects_negative_amount`, `test_negative_amount_in_lines` |
| ProposedJournalEntry always balances or reports exact imbalance | ✅ | `test_proposed_entry_balance_calculation`, `test_proposed_entry_detects_imbalance` |
| Rounding produces deterministic, currency-specific results | ✅ | `test_rounding_same_input_same_output`, `test_currency_specific_rounding`, `test_currency_precision_rounding_deterministic` |
| DTOs are immutable after construction | ✅ | `test_event_envelope_is_frozen`, `test_line_spec_is_frozen`, `test_validation_result_is_frozen` |
| Strategies are pure: same input yields identical output | ✅ | `test_same_event_same_reference_data_same_output`, `test_same_strategy_version_same_output`, `test_same_input_produces_identical_output` |
| ReferenceData is read-only during proposal | ✅ | `test_strategy_cannot_mutate_reference_data`, `test_reference_data_frozen_prevents_direct_mutation` |

### 2. Strategy system

| Item | Status | Test(s) |
|------|--------|---------|
| Missing strategy returns deterministic validation error | ✅ | `test_strategy_unknown_event_type_returns_error`, `test_bookkeeper_requires_strategy` |
| Latest version resolution is correct | ✅ | `test_multiple_versions` |
| Explicit version resolution works | ⚠️ | Partial coverage in `test_strategy_versioning_for_evolution` |
| Duplicate registration is rejected or overwritten deterministically | ⚠️ | `test_strategy_registration_is_additive` (needs explicit duplicate test) |
| Strategy output never mutates EventEnvelope or ReferenceData | ✅ | `test_strategy_cannot_mutate_event_envelope`, `test_strategy_cannot_mutate_reference_data`, `test_strategy_with_exception_does_not_corrupt_inputs` |
| Strategy cannot access persistence or clock | ✅ | Architecture enforced (pure layer has no imports) |

### 3. Bookkeeper behavior

| Item | Status | Test(s) |
|------|--------|---------|
| Transforms EventEnvelope → ProposedJournalEntry deterministically | ✅ | `test_bookkeeper_delegates_to_strategy`, `test_same_event_same_reference_data_same_output` |
| Rejects unknown accounts | ✅ | `test_nonexistent_account_id` |
| Rejects inactive accounts | ⚠️ | Implicit in account validation, needs explicit test |
| Rejects invalid currencies | ✅ | `test_invalid_currency_codes_rejected`, `test_money_rejects_invalid_currency` |
| Enforces balanced entries before persistence | ✅ | `test_balanced_entry_posts`, `test_unbalanced_entry_rejected`, `test_unbalanced_entry_fails_not_silently_corrected` |
| Enforces rounding account usage when imbalance tolerance exceeded | ✅ | `test_rounding_without_rounding_account_fails`, `test_small_imbalance_creates_traceable_rounding_line` |

### 4. Idempotency ✅ FULLY COVERED

| Item | Status | Test(s) |
|------|--------|---------|
| Same event_id produces exactly one journal entry | ✅ | `test_many_post_attempts_one_entry`, `test_100_sequential_posts_same_event_one_entry` |
| Concurrent same-event posting yields one POSTED, rest ALREADY_POSTED | ✅ | `test_10_threads_same_event_exactly_one_wins`, `test_50_threads_same_event_stress`, `test_200_threads_same_event_idempotency` |
| Replay of historical events returns original journal_entry_id | ✅ | `test_retry_after_success_returns_same_entry`, `test_post_same_event_twice_returns_same_result` |
| Payload hash mismatch on same event_id is rejected | ✅ | `test_payload_mismatch_detected`, `test_duplicate_event_different_payload_rejected` |

### 5. Sequence integrity

| Item | Status | Test(s) |
|------|--------|---------|
| Sequences are globally unique | ✅ | `test_100_concurrent_sequence_allocations_unique`, `test_1000_sequence_allocations_concurrent` |
| Sequences are strictly increasing | ✅ | `test_sequence_numbers_strictly_increasing`, `test_sequence_monotonicity_under_concurrency` |
| Rollbacks do not reuse sequence numbers | ✅ | `test_sequence_gaps_not_reused` |
| Concurrent allocation never duplicates | ✅ | `test_100_concurrent_sequence_allocations_unique`, `test_1000_sequence_allocations_concurrent` |
| Cross-entity sequences do not collide | ⚠️ | Journal and audit use separate sequence names, needs explicit test |

### 6. Transaction atomicity

| Item | Status | Test(s) |
|------|--------|---------|
| Partial writes never occur on failure | ✅ | `test_validation_failure_leaves_no_partial_state`, `test_invalid_account_leaves_no_partial_state` |
| JournalEntry and JournalLines commit together | ⚠️ | Implicit in crash tests, needs explicit test |
| AuditEvent commits in same transaction | ✅ | `test_posting_creates_audit_event_in_same_transaction` |
| Failure mid-post leaves no persisted state | ✅ | `test_period_closed_leaves_no_partial_state` |

### 7. Period governance

| Item | Status | Test(s) |
|------|--------|---------|
| period_code is globally unique | ✅ | `test_duplicate_period_code_rejected` |
| Date ranges never overlap | ✅ | `test_overlapping_period_rejected`, `test_period_completely_inside_existing_rejected` |
| Boundary dates resolve deterministically | ✅ | `test_posting_on_period_end_date`, `test_posting_on_period_start_date`, `test_date_resolves_to_single_period` |
| Closed periods reject all postings | ✅ | `test_cannot_post_to_closed_period`, `test_posting_to_closed_period_rejected`, `test_closed_period_rejected_consistently` |
| Close vs post race: close always wins | ⚠️ | **NEEDS TEST** - concurrent close/post race condition |
| Reopen is impossible | ✅ | `test_cannot_reopen_closed_period` |
| Period flags cannot change after close | ✅ | `test_cannot_enable_adjustments_on_closed_period`, `test_cannot_disable_adjustments_on_closed_period` |

### 8. Adjustment policy

| Item | Status | Test(s) |
|------|--------|---------|
| Adjustments allowed only when flag is enabled | ✅ | `test_adjustment_allowed_when_enabled` |
| Regular postings ignore adjustment flag | ✅ | `test_regular_posting_succeeds_regardless_of_adjustment_flag` |
| Adjustments rejected in closed periods | ✅ | `test_adjustment_rejected_in_closed_period_even_if_allowed`, `test_regular_posting_also_rejected_in_closed_period`, `test_adjustment_succeeds_before_close_fails_after` |
| Adjustments replay identically after close | ⚠️ | Implicit via idempotency, needs explicit replay-after-close test |
| Cross-period corrections must forward-adjust, never back-post | ✅ | `test_cannot_back_post_correction_to_closed_period`, `test_forward_adjustment_succeeds_for_correction`, `test_correction_workflow_full_cycle`, `test_no_open_period_available_for_correction` |

### 9. Audit chain integrity ✅ FULLY COVERED

| Item | Status | Test(s) |
|------|--------|---------|
| Hash chain validates end-to-end | ✅ | `test_audit_events_form_valid_chain`, `test_chain_valid_after_postings`, `test_audit_chain_integrity_after_1000_posts` |
| Tampered record fails validation | ✅ | `test_tampered_hash_breaks_chain_validation`, `test_tampered_prev_hash_breaks_chain_validation`, `test_modified_payload_detected` |
| Concurrent posting preserves chain order | ✅ | `test_audit_chain_valid_after_concurrent_posts` |
| Replay regenerates identical hashes | ✅ | `test_audit_event_hash_deterministic`, `test_payload_hash_deterministic` |
| Gaps or reordering are detected | ✅ | `test_first_event_must_have_null_prev_hash`, `test_broken_prev_hash_link_detected` |

### 10. Persistence layer invariants

| Item | Status | Test(s) |
|------|--------|---------|
| JournalEntry records are immutable after commit | ✅ | `test_posted_entry_cannot_be_modified`, `test_posted_entry_cannot_be_deleted`, `test_posted_entry_immutable_across_sessions` |
| JournalLine records are immutable after commit | ✅ | `test_line_cannot_be_modified_after_posting`, `test_line_cannot_be_deleted_after_posting` |
| Event records are immutable after commit | ⚠️ | Implicit via payload_hash check, needs explicit immutability test |
| Foreign key integrity enforced | ✅ | Database constraints (tested implicitly) |
| Database rejects overlapping periods directly | ✅ | `test_overlapping_period_rejected` |

### 11. Concurrency and isolation

| Item | Status | Test(s) |
|------|--------|---------|
| Concurrent distinct events all succeed | ✅ | `test_100_distinct_events_all_post_successfully`, `test_500_concurrent_distinct_events` |
| Concurrent same-event posts deduplicate | ✅ | `test_10_threads_same_event_exactly_one_wins`, `test_200_threads_same_event_idempotency` |
| Concurrent posting preserves balance | ✅ | `test_balance_integrity_under_concurrent_posting` |
| Concurrent posting preserves audit chain | ✅ | `test_audit_chain_valid_after_concurrent_posts`, `test_audit_chain_integrity_after_1000_posts` |
| Concurrent period close blocks posting | ⚠️ | **NEEDS TEST** - race between close and post |
| Concurrent sequence allocation is safe | ✅ | `test_1000_sequence_allocations_concurrent` |

### 12. Error classification

| Item | Status | Test(s) |
|------|--------|---------|
| Validation errors vs system errors are distinct | ✅ | `test_can_identify_error_by_type`, `test_can_identify_error_by_code_attribute` |
| Error codes are stable and documented | ✅ | `test_exception_codes_are_unique`, `test_exception_codes_are_uppercase_snake_case`, `test_all_exceptions_have_code_attribute` |
| Error messages never leak internal state | ⚠️ | **NEEDS TEST** - verify no stack traces/paths in user-facing errors |

### 13. Replay and recovery

| Item | Status | Test(s) |
|------|--------|---------|
| Full ledger rebuild from events matches original balances | ✅ | `test_same_entries_same_trial_balance`, `test_event_order_independent_balance` |
| Replayed journal_entry_ids match originals | ✅ | `test_retry_after_success_returns_same_entry` |
| Replayed sequence numbers remain monotonic | ⚠️ | Implicit in determinism tests, needs explicit replay test |
| Replay ignores current system configuration | ❌ | **MISSING** - need test with different config |

### 14. Security boundaries

| Item | Status | Test(s) |
|------|--------|---------|
| Actor_id is required and validated | ❌ | **MISSING** - need null/invalid actor_id test |
| Producer field is recorded and immutable | ❌ | **MISSING** - need producer immutability test |
| Unauthorized period operations are rejected | ❌ | **MISSING** - need authorization test |
| Event tampering is detected via payload_hash | ✅ | `test_payload_mismatch_detected`, `test_duplicate_event_different_payload_rejected` |

### 15. Determinism and time

| Item | Status | Test(s) |
|------|--------|---------|
| DeterministicClock produces repeatable results | ✅ | `test_deterministic_clock_returns_fixed_time`, `test_deterministic_clock_can_advance`, `test_deterministic_clock_tick` |
| SystemClock is isolated to shell only | ⚠️ | Architecture enforced, needs grep/import test |
| Domain layer never reads wall-clock time | ⚠️ | Architecture enforced, needs grep/import test |

### 16. Schema and migration safety

| Item | Status | Test(s) |
|------|--------|---------|
| Backward-compatible DTO deserialization | ❌ | **MISSING** - need old schema parsing test |
| Older event versions still replay correctly | ❌ | **MISSING** - need version compatibility test |
| Strategy versioning supports historical events | ✅ | `test_strategy_versioning_for_evolution` |

### 17. Performance invariants

| Item | Status | Test(s) |
|------|--------|---------|
| Posting time remains bounded under load | ✅ | `test_30_second_sustained_posting` (P95/P99 latency tracked) |
| Sequence allocation does not serialize entire system | ⚠️ | Implicit in concurrent tests, needs explicit throughput comparison |
| Ledger queries scale with index usage | ✅ | `test_trial_balance_with_5000_journal_entries` |

### 18. Observability

| Item | Status | Test(s) |
|------|--------|---------|
| Every state transition emits an AuditEvent | ✅ | `test_every_posted_entry_has_audit_event`, `test_event_ingestion_creates_audit_event`, `test_posting_creates_audit_event` |
| Failed posts emit failure audit records | ❌ | **MISSING** - need audit for rejection |
| Idempotent hits are auditable | ❌ | **MISSING** - need audit for ALREADY_POSTED |

### 19. Data integrity

| Item | Status | Test(s) |
|------|--------|---------|
| Trial balance always balances at any cutoff date | ✅ | `test_balance_integrity_under_concurrent_posting`, `test_same_entries_same_trial_balance` |
| Account normal balance rules are enforced | ⚠️ | **NEEDS TEST** - verify debit/credit side enforcement |
| Rounding account is the only sink for residuals | ✅ | `test_rounding_without_rounding_account_fails`, `test_rounding_lines_are_explicitly_marked` |

### 20. Contract tests

| Item | Status | Test(s) |
|------|--------|---------|
| Public API schema is stable | ❌ | **MISSING** - need schema snapshot tests |
| Breaking changes are versioned | ❌ | **MISSING** - need version compatibility tests |
| Invalid payloads fail at ingestion layer, not domain layer | ❌ | **MISSING** - need boundary validation test |

---

## Priority Gap List

### High Priority (Core Invariants)

1. ~~**Strategy output never mutates inputs** (2.5)~~ ✅ DONE
2. ~~**Adjustments rejected in closed periods** (8.3)~~ ✅ DONE
3. ~~**Cross-period corrections forward-adjust only** (8.5)~~ ✅ DONE
4. **Actor_id required and validated** (14.1)
5. **Failed posts emit audit records** (18.2)
6. **Concurrent close vs post race** (7.5, 11.5)

### Medium Priority (Robustness)

7. **Inactive account rejection** (3.3)
8. **Cross-entity sequence collision** (5.5)
9. **Error messages don't leak state** (12.3)
10. **Producer field immutability** (14.2)
11. **Idempotent hits auditable** (18.3)
12. **Normal balance enforcement** (19.2)

### Lower Priority (Schema/Migration)

13. **Backward-compatible DTO deserialization** (16.1)
14. **Older event version replay** (16.2)
15. **Public API schema stability** (20.1)
16. **Invalid payloads fail at ingestion** (20.3)

---

## Recent Changes (2026-01-26)

### Added Tests

1. **`tests/domain/test_strategy_purity.py`** (8 tests)
   - `test_strategy_cannot_mutate_event_envelope`
   - `test_strategy_cannot_mutate_reference_data`
   - `test_same_input_produces_identical_output`
   - `test_frozen_dataclass_prevents_direct_mutation`
   - `test_reference_data_frozen_prevents_direct_mutation`
   - `test_strategy_result_does_not_leak_mutable_references`
   - `test_strategy_with_exception_does_not_corrupt_inputs`
   - `test_concurrent_strategy_calls_do_not_interfere`

2. **`tests/period/test_period_rules.py`** (7 new tests)
   - `TestR83AdjustmentsInClosedPeriods`:
     - `test_adjustment_rejected_in_closed_period_even_if_allowed`
     - `test_regular_posting_also_rejected_in_closed_period`
     - `test_adjustment_succeeds_before_close_fails_after`
   - `TestR85CrossPeriodCorrections`:
     - `test_cannot_back_post_correction_to_closed_period`
     - `test_forward_adjustment_succeeds_for_correction`
     - `test_correction_workflow_full_cycle`
     - `test_no_open_period_available_for_correction`

### Bug Fixes

1. **`finance_kernel/domain/dtos.py`** - R2.5 Compliance
   - `EventEnvelope.payload` now deep-frozen with `MappingProxyType`
   - `ReferenceData` dict fields now frozen with `MappingProxyType`
   - Added `_deep_freeze_dict()` helper for nested dict immutability
   - Prevents strategies from mutating their inputs

---

## Recommended Next Steps

1. **Create `tests/security/test_boundaries.py`** for items 14.1-14.3 (actor/producer validation)
2. **Create `tests/audit/test_failure_audit.py`** for items 18.2-18.3 (audit for rejections)
3. **Add concurrent close/post race test** to `tests/concurrency/` for items 7.5, 11.5
4. **Create `tests/schema/test_compatibility.py`** for items 16.1-16.2, 20.1-20.3

**Estimated effort:** 1-2 days to achieve 85%+ coverage

---

## Certification Test Suite Gaps (Addendum A)

The plan.md Addendum A defines a comprehensive certification test suite. This section tracks gaps against that specification.

### Status Summary

| Test Class | Spec Section | Status | Notes |
|------------|--------------|--------|-------|
| A1-A3 Identity/Protocol | 1 | ✅ Covered | `test_event_protocol_violation.py`, `test_idempotency.py` |
| B1-B2 Atomicity/Crash | 2 | ⚠️ Partial | Basic crash tests exist, no full fault injection |
| C1-C2 Ordering/Determinism | 3 | ✅ Covered | `test_r9_sequence_safety.py`, `test_determinism.py` |
| D1-D3 Period Integrity | 4 | ✅ Covered | `test_period_lock.py`, `test_period_rules.py` |
| E1-E3 Multi-currency/Rounding | 5 | ⚠️ Partial | Basic tests, not full permutation coverage |
| F1-F2 Dimension Stability | 6 | ✅ Covered | `test_dimension_integrity.py` |
| G1-G2 Rule Evolution | 7 | ❌ Missing | Version field exists, no upgrade/replay tests |
| H1-H2 Adversarial Input | 8 | ✅ Covered | `test_adversarial.py`, fuzzing tests |
| I1-I2 Audit Survivability | 9 | ✅ Covered | `test_chain_validation.py`, `test_immutability.py` |
| J1-J2 Performance/Scale | 10 | ⚠️ Partial | Stress tests exist, no formal SLO validation |
| K1-K2 Metamorphic Testing | 11 | ❌ Missing | No reversal or split/merge equivalence tests |

### Missing Tests - Quick Wins

These can be implemented with minimal effort:

#### G2: Rule Version Preservation Test (15 min)
**TODO:** Add test to verify `posting_rule_version` is correctly stored and preserved.

```python
# tests/replay/test_rule_version.py
def test_posting_preserves_strategy_version():
    """G2: Verify rule version is stored on journal entry."""
    # Post with a versioned strategy
    # Verify JournalEntry.posting_rule_version matches strategy.version
    pass

def test_replay_uses_original_rule_version():
    """G2: Verify replay uses the stored rule version, not current."""
    # Post entry with version 1
    # Register version 2 of same strategy
    # Verify original entry still has version 1
    pass
```

#### K1: Post + Reverse Equivalence Test (requires reversal implementation)
**TODO:** Implement reversal service first, then add equivalence test.

```python
# tests/metamorphic/test_equivalence.py
def test_post_reverse_returns_to_baseline():
    """K1: Post + Reverse should return ledger to original state."""
    # Get baseline trial balance
    # Post entry
    # Reverse entry
    # Verify trial balance matches baseline
    pass
```

### Missing Tests - Larger Effort

#### B1: Full Crash Injection
**TODO:** Implement fault injection at specific points:
- After draft JournalEntry insert
- After partial JournalLine insert
- After all lines, before status=posted
- After posted, before projection update

**Effort:** 4-6 hours (requires test infrastructure)

#### E1-E3: Full Rounding Permutation Coverage
**TODO:** Add pairwise coverage across:
- Currency pairs
- Rate precision
- Amount magnitude
- Sign combinations

**Effort:** 2-3 hours

#### J1-J2: Performance SLO Validation
**TODO:** Define and validate:
- P95/P99 latency thresholds
- Sustained TPS targets
- 10M line rebuild time

**Effort:** 4-6 hours (requires benchmarking infrastructure)

### Missing Functionality

#### Reversal Service
**TODO:** Implement `ReversalService` to enable K1 metamorphic testing.

Location: `finance_kernel/services/reversal_service.py`

Required methods:
- `reverse_entry(journal_entry_id, reversal_event) -> JournalEntry`
- Should create new entry with negated lines
- Should link via `reversal_of_id`
- Should create REVERSED audit event

**Effort:** 2-4 hours

### Certification Criteria (from Addendum A)

Phase 1 is certified when:
- [ ] All Critical tests pass for 7 consecutive days in CI
- [ ] No Major regressions remain open
- [ ] Trial balance hash stable across 3 full rebuilds
- [ ] Trial balance hash stable across 2 shuffled replays
- [ ] Nightly invariant fuzzing completes with zero violations

**Current Status:** Core functionality complete, certification testing incomplete.

---

## Recent Changes (2026-01-26)

### Documentation Session

1. **Added comprehensive headers to key files:**
   - `finance_kernel/db/immutability.py` - 107 lines explaining defense-in-depth
   - `finance_kernel/exceptions.py` - 155 lines with hierarchy tree and code reference

2. **Refactored triggers for readability:**
   - Split `triggers.py` into 9 SQL files in `db/sql/`
   - Added debugging helpers (`get_installed_triggers`, `get_missing_triggers`)

3. **Added Sindri compatibility:**
   - Added `from_model()` methods to all DTOs for ORM-to-DTO conversion

4. **Documentation accuracy review:**
   - Fixed `strategy_version` → `posting_rule_version` in db/README.md
   - Fixed Event model documentation (id vs event_id)
   - Fixed AuditEvent seq documentation (not PK)
   - Added `from_model()` section to domain/README.md
