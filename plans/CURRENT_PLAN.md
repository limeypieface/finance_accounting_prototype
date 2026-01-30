# Current Plan

## Status: Active

---

## Backlog

### 1. PostingOrchestrator Dependency Injection Refactor

**Priority:** Medium
**Status:** Not started — waiting for current test runs to complete

**Problem:**
`PostingOrchestrator.__init__` hard-wires its sub-services internally:

```python
self._auditor = AuditorService(session, clock)
self._ingestor = IngestorService(session, clock, self._auditor)
self._ledger = LedgerService(session, clock, self._auditor)
```

This means:
- Sub-services cannot be injected (violates explicit-dependency principle)
- Replacing `orchestrator._auditor` after construction has no effect — `_ingestor` and `_ledger` still hold references to the original auditor
- `test_crash_during_audit_event_creation_rolls_back` in `tests/crash/test_fault_injection.py` (line 106) is broken because of this: it patches `orchestrator._auditor` but `LedgerService.persist()` calls `record_posting` on its own internal auditor reference

**Fix:**
Refactor `PostingOrchestrator.__init__` to accept optional pre-built services:

```python
def __init__(self, session, clock=None, auto_commit=True,
             auditor=None, ingestor=None, ledger=None,
             period_service=None, bookkeeper=None, reference_loader=None):
    self._clock = clock or SystemClock()
    self._auditor = auditor or AuditorService(session, self._clock)
    self._ingestor = ingestor or IngestorService(session, self._clock, self._auditor)
    self._ledger = ledger or LedgerService(session, self._clock, self._auditor)
    self._period_service = period_service or PeriodService(session, self._clock)
    self._bookkeeper = bookkeeper or Bookkeeper()
    self._reference_loader = reference_loader or ReferenceDataLoader(session)
```

**Affected files:**
- `finance_kernel/services/posting_orchestrator.py` — refactor constructor
- `tests/crash/test_fault_injection.py` — fix `test_crash_during_audit_event_creation_rolls_back` to inject the patched auditor via constructor instead of monkey-patching
- `tests/conftest.py` — update `posting_orchestrator` fixture if needed

**Validation:**
- All existing tests pass unchanged (defaults preserve current behavior)
- The broken fault injection test passes with injected auditor
- No production code changes behavior (only constructor signature widens)
