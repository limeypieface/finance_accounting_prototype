# Repeated Code Patterns Across Modules — Consolidation Analysis

This document catalogs repeated patterns in `finance_modules/*/service.py` and recommends where to consolidate via shared helpers, a small “posting coordinator,” or existing services/engines.

---

## 1. Guard failure + commit/rollback (already partially consolidated)

### Pattern

In almost every `record_*` / `post_*` method you see:

```python
transition_result = self._workflow_executor.execute_transition(...)
if not transition_result.success:
    status = (
        ModulePostingStatus.GUARD_BLOCKED
        if transition_result.approval_required
        else ModulePostingStatus.GUARD_REJECTED
    )
    return ModulePostingResult(status=status)  # sometimes with entity placeholder

# ... build payload, post_event ...

if result.is_success:
    self._session.commit()
else:
    self._session.rollback()
return result
```

### Existing consolidation

- **`finance_modules/_posting_helpers.py`** already provides:
  - **`guard_failure_result(transition_result, event_id=None)`** — returns `None` if success, else `ModulePostingResult(GUARD_BLOCKED | GUARD_REJECTED)`.
  - **`commit_or_rollback(session, result)`** — commit on success, rollback otherwise.

### Who uses it today

| Module       | Uses helpers |
|-------------|----------------|
| cash        | ✅ guard_failure_result, commit_or_rollback |
| assets      | ✅ guard_failure_result, commit_or_rollback |
| gl          | ✅ guard_failure_result, commit_or_rollback |
| ap          | ✅ guard_failure_result, commit_or_rollback |
| ar          | ✅ guard_failure_result, commit_or_rollback |
| expense     | ✅ guard_failure_result, commit_or_rollback |
| project     | ✅ guard_failure_result, commit_or_rollback |
| credit_loss | ✅ guard_failure_result, commit_or_rollback |
| inventory   | ✅ guard_failure_result, commit_or_rollback |
| payroll     | ✅ guard_failure_result, commit_or_rollback |
| revenue     | ✅ guard_failure_result (require_decimal) |

### Who still inlines the same logic

These modules still repeat the 6–8 line guard block and/or inline commit/rollback:

- **budget** — inline guard + inline commit/rollback (~6 methods)
- **contracts** — inline guard + inline commit/rollback (~12 methods)
- **lease** — inline guard + inline commit/rollback (~6 methods)
- **procurement** — inline guard + inline commit/rollback (~10 methods)
- **tax** — inline guard + inline commit/rollback (~7 methods)
- **wip** — inline guard + inline commit/rollback (~10 methods)
- **intercompany** — inline guard + inline commit/rollback (~3 methods)

### Recommendation

- **Consolidate:** Migrate all of the above modules to use `guard_failure_result` and `commit_or_rollback` from `finance_modules._posting_helpers`. No new service or engine — just adopt the existing helpers everywhere. This removes hundreds of duplicated lines and keeps behavior identical.

---

## 2. Full “workflow → post → commit/rollback” flow

### Pattern

Many methods share this structure:

1. `try:`
2. Optional validation (e.g. `require_decimal(amount)`)
3. Optional logging (`logger.info("..._started", extra={...})`)
4. `transition_result = self._workflow_executor.execute_transition(workflow=..., entity_type=..., entity_id=..., current_state="draft", action="post", actor_id=..., amount=..., currency=..., context=...)`
5. If guard failed → return failure result (or entity + failure result)
6. Build `payload` dict (method-specific)
7. `result = self._poster.post_event(event_type=..., payload=..., effective_date=..., actor_id=..., amount=..., currency=..., description=...)`
8. Optional: on success, create/update ORM rows and `self._session.add(...)`
9. `commit_or_rollback(self._session, result)` (or inline commit/rollback)
10. `return result` (or entity + result)
11. `except Exception: self._session.rollback(); raise`

Steps 4–5 are already covered by `guard_failure_result`. Steps 7–9 are covered by `commit_or_rollback` after `post_event`. What remains method-specific: payload building, optional ORM side effects, and return shape (result only vs tuple(entity, result)).

### Recommendation

- **Optional further consolidation:** Add a thin helper that runs “execute_transition + guard check” and returns either `(None, None)` (proceed) or `(failure_result, None)` so callers can do:

  ```python
  failure = guard_failure_result(transition_result)
  if failure is not None:
      return failure  # or return entity_placeholder, failure
  payload = {...}  # method-specific
  result = self._poster.post_event(...)
  if result.is_success and orm_thing:
      self._session.add(orm_thing)
  commit_or_rollback(self._session, result)
  return result
  ```

  You already have that with `guard_failure_result`; no new abstraction is strictly necessary. A “post with workflow” coordinator that took a payload builder callable could reduce one more level of repetition but would be more invasive. **Recommendation:** first finish migrating all modules to the existing helpers; then decide if a higher-level “workflow + post” helper is worth it.

---

## 3. Try/except rollback and re-raise

### Pattern

Many methods wrap the body in:

```python
try:
    # ... workflow, post_event, commit_or_rollback ...
except Exception:
    self._session.rollback()
    raise
```

### Recommendation

- **Optional:** A context manager or decorator that ensures “on exception, rollback then re-raise” would remove repeated `except: rollback; raise` blocks. This is a small win and must not change transaction semantics (e.g. only one session, no nested transactions). Can be added to `_posting_helpers` or a small `finance_modules._transaction_helpers` if desired.

---

## 4. Module-specific “helpers” and “calculations”

### Current layout

- **cash/helpers.py** — MT940/BAI2/CAMT parsing, NACHA/ACH formatting (cash-specific).
- **inventory/helpers.py** — ABC classification, ROP, EOQ (inventory-specific).
- **assets/helpers.py** — Depreciation (straight-line, DDB, SOYD, UOP), impairment (asset-specific).
- **lease/calculations.py** — PV of payments, amortization schedule, lease classification (lease-specific).
- **credit_loss/calculations.py** — ECL loss rate, PD/LGD, vintage curves (credit-loss-specific).
- **expense/helpers.py**, **payroll/helpers.py**, **revenue/helpers.py**, **tax/helpers.py** — module-specific pure logic.

### Recommendation

- **Do not consolidate** these into a single shared “engine” by domain mix. They are intentionally module-scoped and domain-specific.
- **Possible future engine:** A shared **time-value / present-value** utility could be used by lease, revenue, and any other module that does discounting. That would be a small, pure `finance_engines` (or kernel domain) helper used by lease/revenue, not a merger of lease and credit_loss logic.

---

## 5. Summary: what to do

| Action | Where | Effect |
|--------|--------|--------|
| **Use existing helpers everywhere** | budget, expense, project, intercompany, payroll, credit_loss, inventory, contracts, lease, tax, wip, procurement, revenue | Replace inline “if not transition_result.success: status = GUARD_*; return ModulePostingResult(status=status)” with `failure = guard_failure_result(transition_result); if failure is not None: return failure` (or return entity + failure). Replace “if result.is_success: commit else rollback” with `commit_or_rollback(self._session, result)`. |
| **Optional: transaction boundary helper** | `_posting_helpers` or new `_transaction_helpers` | Context manager or decorator for “on exception, session.rollback(); raise” to remove repeated try/except blocks. |
| **Optional: shared PV/discounting** | New small helper in `finance_engines` or kernel | Only if lease, revenue, and others can share one present-value/discounting API. |
| **Do not merge** | Module-specific helpers (cash, inventory, assets, lease, credit_loss, etc.) | Keep domain-specific logic in their modules. |

---

## 6. Quick reference: using the existing helpers

```python
from finance_modules._posting_helpers import commit_or_rollback, guard_failure_result

# In each record_* method, after execute_transition:
transition_result = self._workflow_executor.execute_transition(...)
failure = guard_failure_result(transition_result)
if failure is not None:
    return failure  # or: return placeholder_entity, failure

# ... build payload ...
result = self._poster.post_event(...)

# Optional: add ORM rows only on success
if result.is_success:
    self._session.add(some_orm_object)

commit_or_rollback(self._session, result)
return result
```

No new service or engine is required for the main duplication: it’s already solved in `_posting_helpers`; the remaining work is to adopt those helpers in every module that still inlines the same logic.

---

## 7. Remaining LOC refinement opportunities (by impact)

Based on the current codebase, the **largest remaining opportunities** to reduce lines without changing behavior:

| Priority | Refinement | Where | Est. LOC saved | Effort |
|----------|------------|--------|----------------|--------|
| **1** | **`run_workflow_guard` helper** | `_posting_helpers.py` + all modules | **~1,200–1,400** | Medium |
| **2** | **Finish guard + commit_or_rollback migration** | budget, contracts, lease, procurement, tax, wip, intercompany | **~400–500** | Low |
| **3** | **Transaction-boundary context manager** | `_posting_helpers` or `_transaction_helpers` + all service methods | **~350–400** | Low |

### 1. `run_workflow_guard` (biggest win) — **implemented**

**Done:** `finance_modules._posting_helpers.run_workflow_guard(executor, workflow, entity_type, entity_id, *, current_state="draft", action="post", actor_id, actor_role="", amount=None, currency="USD", context=None, approval_request_id=None, outcome_sink=None, event_id=None)` runs `execute_transition` then `guard_failure_result` and returns `Optional[ModulePostingResult]`. **AR** and **cash** services are fully migrated; other modules can be migrated the same way.

**Pattern:** Replace the 10–15 line block with e.g. `failure = run_workflow_guard(self._workflow_executor, FOO_WORKFLOW, "entity_type", entity_id, actor_id=actor_id, amount=amount, currency=currency, context={}); if failure is not None: return failure` (or `return [], failure` for methods that return a tuple). Optional args: `current_state`, `action`, `context`, `outcome_sink`, `approval_request_id`.

### 2. Finish guard + commit_or_rollback migration

**Modules still inlining:** budget, contracts, lease, procurement, tax, wip, intercompany (~54 guard blocks + commit/rollback). Same refactor already done for ar, ap, gl, cash, assets, expense, payroll, project, credit_loss, inventory. **Est. ~400–500 lines** saved, low risk.

### 3. Transaction-boundary context manager

**Pattern today:** Most posting methods use `try: ... except Exception: self._session.rollback(); raise`.

**Refinement:** Context manager `rollback_on_exception(session)` that on exception rolls back and re-raises. Use `with rollback_on_exception(self._session):` and drop the explicit try/except. **~200 methods** × 2–3 lines ⇒ **~350–400 lines** saved. Must preserve semantics (single session, no nested transactions).

**Summary:** Largest single opportunity is **`run_workflow_guard`** (~1.2k–1.4k LOC). Next: **guard + commit_or_rollback** in the seven remaining modules (~400–500 LOC), then **rollback-on-exception** CM (~350–400 LOC).
