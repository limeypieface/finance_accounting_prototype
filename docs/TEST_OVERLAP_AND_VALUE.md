# Test Overlap and Value — Audit Guide

**Total collected:** ~5,100 tests (run `pytest tests/ --collect-only -q` for current count).

This doc summarizes where tests live, where overlap is **intentional** vs **redundant**, and how to find tests that may be low-value or safe to trim.

---

## 1. Where the tests live (rough distribution)

| Area | Dir | ~Tests | Purpose |
|------|-----|--------|--------|
| **Modules** | `tests/modules/` | ~2,040 | Config, models, profiles, workflows, **per-module service** (AP/AR/GL/…), **per-module ORM**, gap coverage, helpers |
| **Domain** | `tests/domain/` | ~780 | Pure logic, schemas, policy registry, interpretation, economic link, event scenarios |
| **Engines** | `tests/engines/` | ~640 | Allocation, matching, approval, valuation, tax, billing, bank recon, ICE, lifecycle recon |
| **Services** | `tests/services/` | ~320 | Approval, workflow executor, reversal, period close, party, link graph, contract, subledger pipeline |
| **Architecture** | `tests/architecture/` | ~205 | Import boundaries, kernel boundary, R20 mapping, guards required, no workarounds |
| **Integration** | `tests/integration/` | ~35 | E2E: module posting, reversal, approval+posting, inventory service |
| **Posting** | `tests/posting/` | ~60 | Balance, idempotency, period lock, outcomes |
| **Benchmarks** | `tests/benchmarks/` | ~15 | Throughput, data volume, pipeline stages |
| **Other** | audit, adversarial, batch, config, concurrency, crash, fuzzing, ingestion, etc. | ~1,100+ | Invariants, attacks, scheduling, ingestion, property-based |

So the bulk is **modules** (one service + one ORM file per ERP module, plus shared infra), then **domain** and **engines**.

---

## 2. Intentional overlap (by design)

Some “same surface” is tested in more than one place on purpose:

- **Posting path**
  - **Unit/posting:** `tests/posting/` — balance, idempotency, period lock (kernel contract).
  - **Integration:** `tests/integration/test_module_posting_service.py` — real config, real DB, multiple event types.
  - **Modules:** each `test_*_service.py` has “record_*_posts” style tests that go through the **module API** (workflow + guard + post_event + commit/rollback).
  - **Purpose:** Posting layer is critical; testing it at kernel, integration, and module level gives different guarantees (pure contract vs wired pipeline vs full module flow).

- **Guards and workflows**
  - **Modules:** `test_guard_execution.py`, `test_workflow_transitions.py`, `test_workflow_adversarial.py` — guard behavior, transition rules, naming.
  - **Architecture:** `test_mandatory_guards.py`, `test_workflow_executor_required.py` — “every module uses executor; no bypass.”
  - **Purpose:** Module tests check *what* guards do; architecture tests enforce *that* they are used.

- **Invariants**
  - **Audit / adversarial:** DB immutability, rounding, sequence safety, etc.
  - **Posting / domain:** Balance, idempotency, interpretation rules.
  - **Purpose:** Same invariant can be tested at different layers (domain rule vs DB/attack).

So overlap in “posting works,” “guards run,” and “invariants hold” is **intentional** — different layers, different failure modes.

---

## 3. Potential redundancy (worth auditing)

- **“X posts” tests**
  - Many modules have a test per public method that “posts” (e.g. `test_record_invoice_posts`, `test_record_payment_posts`). They often: create one entity, call service, assert `result.status == POSTED` and sometimes `journal_entry_ids` / balances.
  - **Overlap:** Integration `test_module_posting_service.py` already posts several event types through the kernel. So “does posting succeed for event type X?” is covered in both integration (kernel + config) and module (full service path).
  - **Value:** Module tests add: workflow/guard in the loop, module-specific payloads, and ORM side effects. If a module test only asserts POSTED and does not assert module-specific behavior (e.g. AR balance, allocation, links), it may be **partially redundant** with integration. **Recommendation:** Prefer keeping module tests that assert **module-specific outcomes** (ORM state, returned DTOs, links, balances); consider trimming or merging ones that only assert POSTED and mirror integration coverage.

- **ORM-only tests**
  - Each module has `test_*_orm.py` (e.g. `test_ap_orm.py`, `test_ar_orm.py`). They test CRUD, relationships, and constraints.
  - **Overlap:** Service tests often create the same ORM rows after a successful post. So “ORM row exists after service call” is covered in both ORM tests (direct create) and service tests (create via service).
  - **Value:** ORM tests are useful for edge cases (validation, relationships, constraints) without going through the full posting path. **Recommendation:** Keep ORM tests that stress **constraints, relationships, or invalid data**; treat “create and read back” as complementary to service tests, not redundant.

- **Config / validation**
  - `test_config_schemas.py`, `test_config_validation.py`, `test_config_fuzzing.py` vs config usage inside module/service tests.
  - **Value:** Schema/validation tests are the right place for “invalid config fails.” Module tests that only use valid config don’t duplicate that. **Recommendation:** No need to remove; avoid adding **new** config-edge tests in module tests if they belong in config tests.

- **Engine vs module**
  - Engines are pure; module service tests often call engines and then post. So “engine result is correct” is in `tests/engines/`; “service uses engine and posts” is in `tests/modules/`.
  - **Overlap:** Low; different layers. Only redundant if a module test **reimplements** engine logic in the test instead of asserting on service output.

---

## 4. Tests that are often low-value or safe to trim

- **Purely duplicate assertions**
  - Two tests in the same file (or same module) that do the same call and same assertions. **Action:** Keep one; delete or merge the other.

- **“Smoke” tests that add no new information**
  - e.g. “call method, assert not None” or “assert True” or a single trivial assertion already covered by a stricter test. **Action:** Remove or fold into a more meaningful test.

- **Over-specified, brittle tests**
  - Asserting exact log messages, exact dict keys, or internal implementation details that change often. **Action:** Refactor to assert **observable outcomes** (status, IDs, balances, counts) so they stay useful.

- **Skipped / xfail with no follow-up**
  - Long-term `@pytest.mark.skip` or `xfail` with no ticket or plan to fix. **Action:** Document in a short audit (e.g. `docs/XFAIL_XPASS_AUDIT.md`); either fix, remove, or convert to a proper “known limitation” test.

- **Benchmarks / stress run on every CI**
  - If benchmarks are slow and not required for every commit, run them in a separate job or nightly. **Action:** Don’t delete; gate by job or marker so the main suite stays fast.

---

## 5. How to find overlap and low-value tests

1. **By keyword**
   - Search for tests that only assert `POSTED` / `is_success` and little else:
     ```bash
     grep -rn "ModulePostingStatus.POSTED\|result.is_success" tests/modules/
     ```
   - Compare with integration tests that post the same event types; if a module test adds no extra assertions (no ORM, no links, no module DTOs), flag it for review.

2. **By coverage**
   - Run coverage and see which tests **alone** contribute to covering a given line/branch. Tests that only touch code already fully covered by other tests are candidates for merge or removal:
     ```bash
     pytest tests/ --cov=finance_modules --cov-report=html
     ```

3. **Mutation audit**
   - Use the existing mutation audit to ensure tests are **sensitive** to real bugs. If a test still passes when a mutation is active, it may be redundant or weak:
     ```bash
     python scripts/run_mutation_audit.py
     ```
   - See `tests/mutation/README.md`.

4. **R20 / test class mapping**
   - `docs/TESTING_STRATEGY.md` and architecture tests reference R20 (critical vs important vs architectural). Use that to **prioritize**; don’t drop critical or architectural tests when trimming.

5. **Run time**
   - Identify very slow tests (`pytest --durations=10`). If they’re slow and don’t add unique coverage or risk, consider moving to a separate suite or simplifying.

---

## 6. Recommended next steps

| Step | Action |
|------|--------|
| 1 | Run `pytest tests/ --collect-only -q` and update the table in §1 with exact counts per directory. |
| 2 | List all `test_*_posts` (and similar) in `tests/modules/` and in `tests/integration/`; for each event type, note whether coverage is only “POSTED” or also module-specific outcomes. |
| 3 | Add or update a short **XFAIL/skip audit** (e.g. `docs/XFAIL_XPASS_AUDIT.md`) so skipped/xfail tests have a reason and owner. |
| 4 | Run a **coverage + mutation** pass: keep tests that contribute unique coverage or catch mutations; flag the rest for review. |
| 5 | Prefer **merging or tightening** tests over deleting large swathes; remove only clearly duplicate or trivial tests. |

---

## 7. Summary

- **~5,100 tests** is reasonable for a kernel + engines + services + many ERP modules; the largest chunks are modules (~2k), domain (~780), and engines (~640).
- **Overlap is mostly intentional** (posting at multiple layers, guards in modules + architecture, invariants in domain vs audit).
- **Potential redundancy:** module “X posts” tests that only assert POSTED and duplicate integration coverage; ORM tests that only repeat “create via service.” Focus audit there.
- **Low-value:** duplicate assertions, trivial smoke tests, brittle over-specification, long-term skip/xfail with no plan.
- Use **coverage**, **mutation audit**, and **R20** to decide what to merge or remove; avoid bulk deletion without evidence.

For run commands and categories, see **`docs/TEST_COMMANDS.md`**. For module-only strategy, see **`docs/TESTING_STRATEGY.md`**.
