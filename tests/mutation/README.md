# Mutation kill-rate audit

Deliberately break one architectural seam per run. If any test still passes, the build fails and we report the test name and mutation.

## Why these paths? (rationale)

Default paths (~675 tests): **adversarial**, **posting**, **integration**, **architecture**, **services**.

| Path | Why included | Which mutations should kill tests here |
|------|----------------|----------------------------------------|
| **adversarial** | Immutability attacks, journal/period boundary attacks, pressure (sequence, balance). Tests that post or assert DB/ORM invariants. | JOURNAL_WRITER (posting path), DISABLE_IMMUTABILITY_GUARD (immutability tests), BYPASS_SEQUENCE (sequence tests), SKIP_POLICY (post_via_coordinator uses policy). |
| **posting** | Balance, idempotency, period lock, outcomes. Core posting pipeline. | JOURNAL_WRITER (all that assert written state), BYPASS_SEQUENCE (idempotency/sequence), SKIP_POLICY (profile-dependent post). |
| **integration** | Full-stack: ModulePostingService, approval, reversal, inventory. | All five: policy, sequence, writer, immutability (if any), engine dispatch (inventory/valuation). |
| **architecture** | Boundary and convention tests (kernel vs config vs modules, no unauthorized mocks, fixture requirements). | SKIP_POLICY, JOURNAL_WRITER (tests that assert real posting path), BYPASS_SEQUENCE. |
| **services** | Reversal, workflow executor, subledger pipeline, period close, approval. Kernel services that post or depend on sequences/engines. | JOURNAL_WRITER, SKIP_POLICY, BYPASS_SEQUENCE, HARDCODE_ENGINE_DISPATCH (workflow/subledger). |

**Not in default:** **tests/modules** (2000+ tests: AP, AR, inventory, etc.). Module tests are the most impacted by HARDCODE_ENGINE_DISPATCH and SKIP_POLICY (they post through the full pipeline). We exclude them by default so the audit runs in reasonable time (~675 tests × 5 mutations). For maximum kill-rate coverage, run `python scripts/run_mutation_audit.py tests/`.

## What we expect to happen

- **Per mutation run:** Many tests **fail** (they hit the broken seam and assert on real behavior). Some tests **pass** for the right reason (they don’t depend on that seam; e.g. ingestion-only, expect-blocked, no posting in scenario).
- **If all tests pass** under a mutation: the script exits **1** (build fails). That would mean no test in the set is constraining that seam — either the paths are wrong or the tests are fake for that seam.
- **Good outcome:** Each mutation run reports a non-zero number of failures; the script exits 0 after all mutations. That confirms the chosen tests are sensitive to the seams we break.

## Mutations (rotating set)

| Mutation | Seam | Expected: tests that touch this should fail |
|----------|------|--------------------------------------------|
| `SKIP_POLICY_SELECTION` | `PolicySelector.find_for_event` | Always raises `PolicyNotFoundError`. Posting and profile-dependent tests should fail. |
| `BYPASS_SEQUENCE_ALLOCATION` | `SequenceService.next_value` | Always returns 1. Idempotency/sequence tests should fail. |
| `JOURNAL_WRITER_RETURN_SUCCESS_WITHOUT_WRITING` | `JournalWriter.write` | Returns success without persisting. Any test that asserts DB state after post should fail. |
| `DISABLE_IMMUTABILITY_GUARD` | `register_immutability_listeners` | No-op. Adversarial immutability tests should fail. |
| `HARDCODE_ENGINE_DISPATCH_RESULT` | `EngineDispatcher.dispatch` | Returns success without invoking engines. Engine-dependent flows should fail. |

## Usage

- **Run audit script** (all mutations, one after another; default ~675 tests):
  ```bash
  python scripts/run_mutation_audit.py
  ```
  Default paths: `tests/adversarial`, `tests/posting`, `tests/integration`, `tests/architecture`, `tests/services` — focused on tests that should be impacted by the mutations. If any mutation run passes all tests, the script exits 1.

- **Single run with one mutation** (e.g. CI rotating which mutation each run uses):
  ```bash
  MUTATION_NAME=JOURNAL_WRITER_RETURN_SUCCESS_WITHOUT_WRITING python -m pytest tests/adversarial tests/posting tests/integration tests/architecture tests/services
  ```
  If all tests pass, pytest exits with 1 and prints the regression message.

- **Full suite** (all tests, slower):
  ```bash
  python scripts/run_mutation_audit.py tests/
  ```
  Or with specific mutations:
  ```bash
  python scripts/run_mutation_audit.py --mutations SKIP_POLICY_SELECTION,JOURNAL_WRITER_RETURN_SUCCESS_WITHOUT_WRITING tests/
  ```

## Pass/fail rule

If any test **passes** while a mutation is active, the build fails and the test name and mutation are reported. Tests are expected to **fail** when the system is broken at that seam.

## Why do some tests still pass under a mutation?

Tests that pass under a given mutation should do so for the **right reason**: they do not depend on that seam.

Example: under `JOURNAL_WRITER_RETURN_SUCCESS_WITHOUT_WRITING`, the ~69 passing tests (of adversarial + posting) fall into:

- **No full posting path** — Use `OutcomeRecorder` / `RetryService` / `IngestorService` directly with no call to `InterpretationCoordinator.interpret_and_post` or `JournalWriter.write` (e.g. `tests/posting/test_outcomes.py` state machine and retry tests).
- **Ingestion only** — `TestEventIngestionIdempotency` uses `IngestorService.ingest()` only; no journal write.
- **Expect blocked/rejected** — `test_posting_to_closed_period_blocked`, `test_posting_to_nonexistent_period_blocked` expect a blocked outcome; they never reach `record_posted`.
- **No posting in scenario** — `test_unused_account_can_be_deleted` (delete unused account), `test_draft_lines_remain_mutable_documented` (draft only), fiscal period attack tests when the attack is blocked by immutability (so `interpret_and_post` is never called).
- **Other seams** — Sequence/ghost/orphan/constraint tests that don’t require a successful journal write.

So the mutation is working: every test that goes through a successful `interpret_and_post` → `JournalWriter.write` → `record_posted` fails when the writer is broken; the rest correctly pass because they don’t rely on that path.
