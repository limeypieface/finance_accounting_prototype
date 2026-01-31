# Archived Plan: Fix 13 Failing Architecture Tests

## Status: COMPLETE — All 9 Phases Done. 176/176 architecture tests pass.

**Completed:** 2026-01-30
**Result:** 176/176 architecture tests pass. 2998/2998 non-DB tests pass. 0 regressions.

---

## Objective

Fix all 13 failing architecture tests. After auditing each rule against the actual
codebase and architectural review feedback, the approach is split: **fix code** where
the rule is right, **fix tests** where the rule overreaches. No rules are removed.

**Key change in v3:** Phase 4 no longer relaxes the journal model gate. The sealed-kernel
model is non-negotiable — nothing becomes financial history except through kernel
enforcement. Services that need journal data must use kernel-provided selectors and DTOs,
not direct ORM model imports.

**Key change in v4:** Authenticity audit completed. Four additional issues discovered:
1. `correction_service.py` has 4 broken ORM field references (would crash at runtime)
2. `finance_engines/__init__.py` has a boundary violation (imports from `finance_services`)
3. `SubledgerService` is scaffolding (ABC with no implementations) — documented, not actioned
4. `RetryService` is orphaned (never wired into PostingOrchestrator) — documented, not actioned

Issues #1 and #2 are incorporated into existing phases. Issues #3 and #4 are
out-of-scope for this plan (they don't cause test failures) but documented below.

**Key change in v5:** Pipeline audit completed. "Pipeline A" (Bookkeeper → LedgerService)
is **dead code** — no production or test code invokes the full chain. Only Pipeline B
(`ModulePostingService.post_event()` → `InterpretationCoordinator.interpret_and_post()`)
is active. Pipeline A removal is scoped as a **follow-up plan** after the 13 test fixes
are complete.

---

## Authenticity Audit Findings

Audit performed 2026-01-30 to answer: "Is everything wired to real architecture, or do
we have fake functionality pretending to be real?"

### Verdict: System is overwhelmingly real (30,000+ lines of production code)

The active posting pipeline (`ModulePostingService` → `InterpretationCoordinator`
→ `JournalWriter`) is fully wired end-to-end. The sealed kernel is genuine — only
`JournalWriter` (inside `finance_kernel/services/`) creates journal entries in the active
pipeline. `LedgerService` exists but is dead code (see Pipeline A Audit below).
Selectors (`JournalSelector`, `LedgerSelector`) are real and used by the reporting module.

### Issues Found

| # | Issue | Severity | Location | Action |
|---|-------|----------|----------|--------|
| A1 | CorrectionService has 4 broken ORM field references | **CRITICAL** | `finance_services/correction_service.py:432,437,453,458` | Fixed by Phase 4 (selector refactor) |
| A2 | `finance_engines/__init__.py` imports from `finance_services` | **HIGH** | `finance_engines/__init__.py:24` | Fixed in Phase 1 (remove line) |
| A3 | SubledgerService is scaffolding (ABC, zero implementations) | LOW | `finance_services/subledger_service.py` | Out of scope — document only |
| A4 | RetryService is orphaned (not wired into PostingOrchestrator) | LOW | `finance_kernel/services/retry_service.py` | Out of scope — document only |

**A1 Detail — CorrectionService broken fields:**
```
Line 432: JournalLine.entry_id        → ORM field is JournalLine.journal_entry_id
Line 437: line.account_code           → ORM field is line.account_id (UUID, not code)
Line 453: JournalEntry.event_id       → ORM field is JournalEntry.source_event_id
Line 458: JournalLine.entry_id        → same as line 432
```
These would crash with `AttributeError` at runtime. Phase 4's refactor to use
`JournalSelector` DTOs fixes all four simultaneously — the DTOs have the correct
field names and the service never touches ORM models again.

**A2 Detail — Engines boundary violation:**
`finance_engines/__init__.py:24` contains:
```python
from finance_services import ValuationLayer, ReconciliationManager, CorrectionEngine
```
This violates the DAG (engines must not import services). The comment says these
services "moved to finance_services/" — so this line is a stale re-export that
should be removed.

**A3 Detail — SubledgerService scaffolding:**
`finance_services/subledger_service.py` defines an ABC with `post()`, `get_balance()`,
`get_open_items()` but no concrete implementation exists anywhere. This is intentional
scaffolding for future subledger integration. No action needed for architecture tests.

**A4 Detail — RetryService orphaned:**
`finance_kernel/services/retry_service.py` contains real retry logic with tests, but
is never imported or wired into `PostingOrchestrator`. Not blocking any tests.

---

## Pipeline A Audit — DEAD CODE

Audit performed 2026-01-30 after architectural review flagged: "We were supposed to
remove Pipeline A entirely and only use Pipeline B."

### Verdict: Pipeline A is dead code. The only active posting path is the interpretation pipeline.

**Pipeline A** (legacy): `IngestorService → Bookkeeper → LedgerService → COMMIT`
**Active pipeline**: `ModulePostingService.post_event() → InterpretationCoordinator.interpret_and_post() → JournalWriter → OutcomeRecorder → COMMIT`

| Component | Status | Evidence |
|-----------|--------|----------|
| `LedgerService.persist()` | **NEVER CALLED** | Zero callers in production or tests |
| `Bookkeeper.propose()` | **TEST-ONLY** | Called only in pure domain unit tests, never in any posting flow |
| `PostingOrchestrator` | **DI container only** | Creates Pipeline A services but nobody accesses them for posting |
| `ModulePostingService.post_event()` | **ACTIVE** | Used by all 12 modules, both scripts, all integration tests |
| `InterpretationCoordinator` | **ACTIVE** | The real posting orchestrator |
| `JournalWriter` | **ACTIVE** | The only write path to journal entries |

**PostingOrchestrator dead properties** (created but never accessed in posting flow):
- `self.sequence_service` — created, never used (LedgerService would use it, but LedgerService is never called)
- `self.ledger_service` — created, never used
- `self.ingestor` — used, but only by `ModulePostingService`, not as part of Pipeline A

---

## Audit Verdict (13 Architecture Tests)

The architecture tests were written 2026-01-29. The engine dispatch feature was added
2026-01-30. Tests and code have been out of sync since day one.

| Rule | Failing | Verdict | Action |
|------|---------|---------|--------|
| Dependency DAG | 5 tests | **KEEP rule, fix code** | Move `engine_dispatcher`, `invokers`, `posting_orchestrator` to `finance_services/` |
| Engine purity | 2 tests | **KEEP rule, fix code** | Inject clocks into 4 engine files |
| Journal model gate | 2 tests | **KEEP rule, fix code** | Fix imports to use kernel selectors/DTOs (sealed kernel) |
| No raw Decimal amounts | 1 test | **RELAX rule** | Test is too crude (text matching). Apply only to dataclass fields, not method params |
| Strategy naming | 1 test | **RELAX rule** | Exclude Enum subclasses from `*Strategy` check |
| Kernel → engines | 1 test | **KEEP rule, fix code** | Same as DAG fix (move engine_dispatcher) |
| Exception hierarchy | 1 test | **KEEP rule, fix code** | Config exceptions extend `FinanceKernelError` |
| Config centralization | 1 test | **RELAX rule** | Exclude `scripts/` and `tests/` from the scan |
| R20 test mapping | 1 test | **RELAX rule** | Map to real files; tiered model for coverage categories |

**6 KEEP (fix code) + 3 RELAX (fix tests) = 13 tests fixed**

---

## Phases (all DONE)

### Phase 1: Move cross-layer files to `finance_services/` (FIX CODE) ✅ DONE
**Fixes tests:** #1, #5, #6, #11
**Also fixes:** Authenticity issue A2 (engines → services boundary violation)

Moved `engine_dispatcher.py`, `invokers.py`, `posting_orchestrator.py` to `finance_services/`.
Removed stale re-export from `finance_engines/__init__.py`. Updated all import statements.

### Phase 2: Fix engine impurity via clock injection (FIX CODE) ✅ DONE
**Fixes tests:** #2, #3

Injected explicit date/datetime parameters into `correction/unwind.py`, `matching.py`,
`tax.py`, `subledger.py`. Emptied `KNOWN_VIOLATIONS` set.

### Phase 3: Fix exception hierarchy (FIX CODE) ✅ DONE
**Fixes test:** #12

Changed `CompilationFailedError`, `AssemblyError`, `IntegrityCheckError` to extend
`FinanceKernelError` with machine-readable `code` attributes.

### Phase 4: Seal the journal model gate (FIX CODE) ✅ DONE
**Fixes tests:** #7, #8
**Also fixes:** Authenticity issue A1 (4 broken ORM field references in correction_service.py)

Refactored `reporting/service.py` to import `LineSide` from domain, not ORM models.
Refactored `correction_service.py` to use `JournalSelector` DTOs instead of ORM queries.
Enhanced `JournalSelector` with `get_entries_by_event()` and `account_code` on `JournalLineDTO`.

### Phase 5: Relax primitive reuse rules (FIX TESTS) ✅ DONE
**Fixes tests:** #9, #10

Added frozen-dataclass detection to skip immutable DTOs. Expanded threshold field detection.
Excluded Enum subclasses from `*Strategy` naming check.

### Phase 6: Relax config centralization (FIX TEST) ✅ DONE
**Fixes test:** #4

Excluded `scripts/` and `tests/` directories from config centralization scan.

### Phase 7: Restructure R20 test mapping (FIX TEST) ✅ DONE
**Fixes test:** #13

Complete rewrite with tiered model: Tier 1 (critical: R3,R4,R9,R10,R11), Tier 2
(important: R1,R2,R5-R8,R12,R13), Tier 3 (architectural: R14-R19). Maps to real files only.

### Phase 8: Update all documentation ✅ DONE

Updated CLAUDE.md (single pipeline, services table, clock injection, R18/R20),
`finance_engines/__init__.py` (purity note), `finance_kernel/services/README.md` and
`finance_kernel/README.md` (import path fixes), `module_posting_service.py` (removed
Pipeline B language).

### Phase 9: Verify + archive ✅ DONE

176/176 architecture tests pass. 2998 total tests pass (127 DB-dependent module integration
tests fail — pre-existing, not our change). 11 skipped.

---

## Decisions Made

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Move PostingOrchestrator to `finance_services/`, don't delete | Actively used as composition root |
| 2 | Inject clocks into engines (proper fix) | Engines must be deterministic for replay |
| 3 | String annotations instead of TYPE_CHECKING imports | Preserves strict boundary tests |
| 4 | Config exceptions extend `FinanceKernelError` | Config already depends on kernel (bridges.py); note: shared base would be cleaner |
| 5 | **KEEP journal gate absolute** (revised from v2) | Sealed kernel is non-negotiable; services use selectors/DTOs, not ORM models |
| 6 | Enhance `JournalSelector` + `JournalLineDTO` for correction needs | Add `get_entries_by_event()` (plural) and `account_code` to DTO |
| 7 | Relax Decimal check to dataclass fields only | Method params with separate `currency` arg are fine |
| 8 | Exclude Enum subclasses from Strategy check | `UnwindStrategy(str, Enum)` is not a posting strategy |
| 9 | Exclude `scripts/` and `tests/` from config centralization | Build tools and tests need internal access |
| 10 | Tiered R20 model instead of blanket 4-category mandate | Architectural invariants don't need crash tests |
| 11 | Remove `finance_engines/__init__.py:24` stale re-export | Engines must not import services (DAG violation discovered in authenticity audit) |
| 12 | Document SubledgerService as scaffolding, don't action | ABC with no implementations — intentional future placeholder, not blocking tests |
| 13 | Document RetryService as orphaned, don't action | Real code but not wired — out of scope for architecture test fixes |
| 14 | Pipeline A removal is a follow-up plan, not part of this plan | Fix 13 tests first (focused scope), then remove dead Pipeline A code in separate plan |
| 15 | Documentation update is mandatory, not optional (Phase 8) | Stale docs caused Pipeline A confusion; every structural change must be reflected in CLAUDE.md and docstrings |

---

## Revision History

| Version | Date | Change |
|---------|------|--------|
| v1 | 2026-01-30 | Initial analysis: fix all code to make tests pass |
| v2 | 2026-01-30 | Audit: split into 5 KEEP + 4 RELAX. Proposed relaxing journal gate for services. |
| v3 | 2026-01-30 | **Architectural review rejected Phase 4 relaxation.** Journal gate stays absolute. Services must use kernel selectors/DTOs. Changed to 6 KEEP + 3 RELAX. |
| v4 | 2026-01-30 | **Authenticity audit completed.** Found 4 issues: correction_service broken fields (A1), engines/__init__.py boundary violation (A2), SubledgerService scaffolding (A3), RetryService orphaned (A4). A1 fixed by Phase 4, A2 fixed by Phase 1. A3/A4 documented, not actioned. |
| v5 | 2026-01-30 | **Pipeline audit completed.** Pipeline A (Bookkeeper → LedgerService) is dead code — never invoked. Only one active pipeline. Removal scoped as follow-up. Added Phase 8 (documentation update) as mandatory. |
| v6 | 2026-01-30 | **Subledger follow-up plan appended.** Full audit of subledger system completed. Documented what exists, what's missing, and the phases to make it fully functional. |
| v7 | 2026-01-30 | **Subledger architectural review.** Reviewed SL-Phases 1–10 against architecture changes from Phases 1–9. Found 8 issues (2 critical, 1 high, 2 medium, 3 low). All resolved. |
