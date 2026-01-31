# Completed Plan: Enrich Auditor Trace with Full Provenance

## Status: COMPLETE

## Objective
Make the interactive trace display production-audit quality by:
1. Persisting role-binding provenance in the decision journal (Option 2)
2. Surfacing all existing-but-hidden data in the trace display

## What Was Done

### Phase 1: Persist Binding Provenance in Decision Journal (DONE)

- **1a.** Added `BindingRecord` NamedTuple to `journal_writer.py` with full provenance fields
  (account_name, account_type, normal_balance, effective_from/to, config_id, config_version)
- **1b.** Updated `bridges.py` to pass all binding metadata to RoleResolver
- **1c.** Enriched `role_resolved` log event — provenance flows automatically into
  `InterpretationOutcome.decision_log` via LogCapture

### Phase 2: Enrich Trace Display (DONE)

Rewrote `show_trace()` in `scripts/interactive.py` with 9 sections:
1. **ORIGIN EVENT** — full payload display + source document extraction
2. **POSTING CONTEXT** — actor (Party query), period (FiscalPeriod query), config identity
3. **JOURNAL ENTRIES** — account name/type/normal_balance, idempotency_key, dimensions
4. **INTERPRETATION OUTCOME** — profile_hash, reason_code
5. **DECISION JOURNAL** — enhanced role_resolved with binding provenance
6. **ECONOMIC LINKS** — lifecycle_links from TraceBundle
7. **REPRODUCIBILITY** — R21 snapshot versions
8. **INTEGRITY** — hash/balance/chain verification
9. **MISSING FACTS** — explicitly declared gaps

### Interactive Scenarios Added
- 22 total scenarios (10 simple + 7 engine + 5 module)
- "A" command to post all at once
- Config-based COA from YAML (153 accounts)

## Files Modified

| File | Change |
|------|--------|
| `finance_kernel/services/journal_writer.py` | BindingRecord, extended RoleResolver, enriched role_resolved log |
| `finance_config/bridges.py` | Pass binding metadata to RoleResolver |
| `scripts/interactive.py` | 22 scenarios + 9-section enriched trace display |

## Test Results
- 22/22 interactive scenarios post successfully
- 27/28 existing tests pass (1 pre-existing failure in test_pressure.py — UUID type mismatch, unrelated)

## Completed: 2026-01-30
