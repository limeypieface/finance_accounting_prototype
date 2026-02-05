# Guard Wiring Plan — All Modules

## Objective

Wire workflow guard enforcement across all 13 unwired module services, following the
AP reference implementation. Today only AP calls `WorkflowExecutor.execute_transition()`
at guarded transition points. All other modules declare guards in `workflows.py` but
never evaluate them at runtime.

**Scope:** 13 modules, ~45 guarded transitions, ~35 new guard evaluators, 1 helper module.

---

## Current State

### Already Wired (AP)
- `finance_modules/ap/service.py` — accepts `workflow_executor: WorkflowExecutor | None = None`
- Wires 5 guarded transition points: match_invoice_to_po, approve_invoice, submit_payment, approve_payment, record_payment

### Guard Evaluators Already Registered (16)
In `finance_services/workflow_executor.py :: default_guard_executor()`:
```
match_within_tolerance, payment_approved, approval_threshold_met, sufficient_funds,
budget_available, approval_complete, po_sent, fully_received, stock_available,
qc_passed, calculation_complete, approval_obtained, all_subledgers_closed,
no_pending_transactions, trial_balance_balanced, all_lines_received
```

### Local Type Duplication
All 13 non-AP modules define their own local `Guard`, `Transition`, `Workflow` dataclasses
in `workflows.py`. These lack `requires_approval`, `approval_policy`, and `terminal_states`
fields present in the canonical types (`finance_kernel.domain.workflow`). The `WorkflowExecutor`
uses Protocol-based structural typing, so local types work for basic guard evaluation, but
cannot support approval gating.

---

## Phase 0: Foundation

### 0A — Migrate Workflow Types to Canonical Imports (13 files)

For each of the 13 `workflows.py` files, remove local `Guard`/`Transition`/`Workflow`
definitions and replace with canonical imports:

```python
from finance_kernel.domain.workflow import Guard, Transition, Workflow
```

**Files:**
- `finance_modules/ar/workflows.py`
- `finance_modules/gl/workflows.py`
- `finance_modules/inventory/workflows.py`
- `finance_modules/payroll/workflows.py`
- `finance_modules/expense/workflows.py`
- `finance_modules/procurement/workflows.py`
- `finance_modules/budget/workflows.py`
- `finance_modules/cash/workflows.py`
- `finance_modules/revenue/workflows.py`
- `finance_modules/lease/workflows.py`
- `finance_modules/wip/workflows.py`
- `finance_modules/tax/workflows.py`
- `finance_modules/assets/workflows.py`

**Risk:** Low. Canonical types are structurally identical for the fields currently used.
Tests should pass unchanged. Run full suite after this step.

### 0B — Create Guard Helper Module (1 new file)

**New file:** `finance_modules/_guard_helpers.py`

Extract the repeated AP pattern into shared helpers:

```python
def check_transition_result(transition_result: TransitionResult) -> ModulePostingResult | None:
    """Convert a failed TransitionResult into a ModulePostingResult.

    Returns None if transition succeeded (caller should proceed).
    Returns GUARD_BLOCKED or GUARD_REJECTED ModulePostingResult if failed.
    """

def make_bypass_result(message: str = "No workflow executor; transition not enforced") -> ModulePostingResult:
    """Return a POSTED result for when workflow_executor is None."""
```

### 0C — Register Missing Guard Evaluators (~35 new)

**File:** `finance_services/workflow_executor.py`

Add evaluator functions and registrations. Two patterns:

**Boolean-passthrough** — for guards where the service pre-computes the result:
```python
ex.register("daily_recording_valid", lambda ctx: bool(_get_attr(ctx, "daily_recording_valid")))
```

**Computed** — for guards that evaluate conditions from context values:
```python
def _variance_within_tolerance(context: Any) -> bool:
    variance = _get_attr(context, "reconciliation_variance")
    tolerance = _get_attr(context, "tolerance")
    ...
    return abs(variance_d) <= tolerance_d
```

**Missing evaluators by module:**

| Module | Guard Name | Pattern |
|--------|-----------|---------|
| **AR** | `credit_check_passed` | Computed: `balance + amount <= credit_limit` |
| | `balance_zero` | Computed: `balance_due == 0` |
| | `write_off_approved` | Passthrough (approval engine handles) |
| **GL** | `adjustments_posted` | Passthrough: `adjustments_posted` |
| | `year_end_entries_posted` | Passthrough: `year_end_entries_posted` |
| **Inventory** | `qc_failed` | Computed: `qc_status == "failed"` |
| **Payroll** | `all_timecards_approved` | Passthrough: `all_timecards_approved` |
| | `daily_recording_valid` | Passthrough: `daily_recording_valid` (D1) |
| | `no_concurrent_overlap` | Passthrough: `no_concurrent_overlap` (D4) |
| | `total_time_balanced` | Passthrough: `total_time_balanced` (D3) |
| | `supervisor_approved` | Passthrough (approval engine handles) (D2) |
| | `reversal_exists` | Passthrough: `reversal_exists` (D5) |
| **Expense** | `receipts_attached` | Passthrough: `receipts_attached` |
| | `within_policy` | Passthrough: `within_policy` |
| | `approval_authority` | Passthrough (approval engine handles) |
| | `pre_travel_valid` | Passthrough: `pre_travel_valid` (D6) |
| | `travel_auth_authority` | Passthrough (approval engine handles) (D6) |
| **Procurement** | `vendor_approved` | Passthrough: `vendor_approved` |
| | `fully_invoiced` | Passthrough: `fully_invoiced` |
| **Budget** | `approved_by_authority` | Passthrough (approval engine handles) |
| **Cash** | `variance_within_tolerance` | Computed: `abs(variance) <= tolerance` |
| | `all_items_matched` | Computed: `unmatched_count == 0` |
| **Revenue** | `contract_approved` | Passthrough: `contract_approved` |
| | `obligations_identified` | Passthrough: `obligations_identified` |
| | `price_determined` | Passthrough: `price_determined` |
| **Lease** | `classification_complete` | Passthrough: `classification_complete` |
| **WIP** | `materials_available` | Passthrough: `materials_available` |
| | `all_operations_complete` | Computed: `pending_operations == 0` |
| | `variance_calculated` | Passthrough: `variance_calculated` |
| **Tax** | `period_closed` | Passthrough: `period_closed` |
| | `reconciled` | Passthrough: `reconciled` |
| | `reviewed` | Passthrough: `reviewed` |
| **Assets** | `in_service_date_set` | Computed: `in_service_date is not None` |
| | `fully_depreciated` | Computed: `net_book_value <= salvage_value` |
| | `disposal_approved` | Passthrough (approval engine handles) |

**Run full test suite after Phase 0.**

---

## Phase 1: Simple Modules (6 modules, 13 guards, 17 transitions)

Wire guard enforcement in modules with 1-3 guards and straightforward service methods.

### Pattern (same for all modules)

For each service:
1. Add `workflow_executor: WorkflowExecutor | None = None` to `__init__` signature
2. Store as `self._workflow_executor = workflow_executor`
3. At each guarded transition point, add:

```python
# Guard: {workflow} {action} ({from} -> {to})
if self._workflow_executor is not None:
    transition_result = self._workflow_executor.execute_transition(
        workflow=WORKFLOW_CONSTANT,
        entity_type="entity_type",
        entity_id=entity_id,
        current_state="current_state",
        action="action_name",
        actor_id=actor_id,
        actor_role="",
        amount=amount,
        currency=currency,
        context={...},  # guard-specific context values
    )
    guard_result = check_transition_result(transition_result)
    if guard_result is not None:
        return guard_result
```

### 1A — Cash (2 guards, 2 transitions)

**File:** `finance_modules/cash/service.py`

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| `reconcile_bank_statement` (submit path) | submit | `ALL_ITEMS_MATCHED` | `unmatched_count` |
| `reconcile_bank_statement` (approve path) | approve | `VARIANCE_WITHIN_TOLERANCE` | `reconciliation_variance`, `tolerance` |

### 1B — Budget (1 guard, 1 transition)

**File:** `finance_modules/budget/service.py`

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| `approve_budget` | approve | `APPROVED_BY_AUTHORITY` | `approved` (approval engine) |

### 1C — Lease (1 guard, 1 transition)

**File:** `finance_modules/lease/service.py`

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| `commence_lease` | commence | `CLASSIFICATION_COMPLETE` | `classification_complete` |

### 1D — Tax (3 guards, 3 transitions)

**File:** `finance_modules/tax/service.py`

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| `calculate_tax_return` | calculate | `PERIOD_CLOSED` | `period_closed` |
| `review_tax_return` | review | `RECONCILED` | `reconciled` |
| `file_tax_return` | file | `REVIEWED` | `reviewed` |

### 1E — WIP (3 guards, 3 transitions)

**File:** `finance_modules/wip/service.py`

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| `release_work_order` | release | `MATERIALS_AVAILABLE` | `materials_available` |
| `complete_work_order` | complete | `ALL_OPERATIONS_COMPLETE` | `pending_operations` |
| `close_work_order` | close | `VARIANCE_CALCULATED` | `variance_calculated` |

### 1F — Assets (3 guards, 5 transitions)

**File:** `finance_modules/assets/service.py`

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| `record_asset_acquisition` or `place_in_service` | place_in_service | `IN_SERVICE_DATE_SET` | `in_service_date` |
| depreciation completion path | complete_depreciation | `FULLY_DEPRECIATED` | `net_book_value`, `salvage_value` |
| `record_disposal` | dispose | `DISPOSAL_APPROVED` | `disposal_approved` (approval engine) |
| `record_scrap` | dispose | `DISPOSAL_APPROVED` | `disposal_approved` |
| `record_impairment` (if disposal path) | dispose | `DISPOSAL_APPROVED` | `disposal_approved` |

**Run full test suite after Phase 1.**

---

## Phase 2: Medium Complexity (4 modules, 13 guards, 14 transitions)

### 2A — AR (3 guards, 7 transitions)

**File:** `finance_modules/ar/service.py`

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| `record_invoice` | issue | `CREDIT_CHECK_PASSED` | `customer_balance`, `credit_limit`, `invoice_amount` |
| `apply_payment` (to paid) | apply_payment | `BALANCE_ZERO` | `balance_due` |
| `record_write_off` | write_off | `WRITE_OFF_APPROVED` | `write_off_approved` |

Note: `apply_payment` transitions from multiple states (issued, delivered, partially_paid)
to `paid`. Guard checks against the current invoice state.

### 2B — GL (4 guards, 3 transitions)

**File:** `finance_modules/gl/service.py`

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| `begin_period_close` (or inline) | begin_close | `ALL_SUBLEDGERS_CLOSED` | `ap_closed`, `ar_closed`, `inventory_closed`, `payroll_closed` |
| `close_period` (or inline) | close | `TRIAL_BALANCE_BALANCED` | same + `pending_transaction_count` |
| `lock_period` (or inline) | lock | `YEAR_END_ENTRIES_POSTED` | `year_end_entries_posted` |

Note: `ADJUSTMENTS_POSTED` guard is declared but may not have a separate transition.
Wire where applicable.

### 2C — Inventory (3 guards, 3 transitions)

**File:** `finance_modules/inventory/service.py`

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| QC acceptance path | pass_qc | `QC_PASSED` | `qc_status` |
| QC rejection path | fail_qc | `QC_FAILED` | `qc_status` |
| `issue_sale` / `issue_production` | pick | `STOCK_AVAILABLE` | `requested_quantity`, `available_quantity` |

### 2D — Revenue (3 guards, 3 transitions)

**File:** `finance_modules/revenue/service.py`

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| `identify_contract_obligations` | identify_obligations | `CONTRACT_APPROVED` | `contract_approved` |
| `determine_contract_price` | determine_price | `OBLIGATIONS_IDENTIFIED` | `obligations_identified` |
| `allocate_contract_price` | allocate_price | `PRICE_DETERMINED` | `price_determined` |

**Run full test suite after Phase 2.**

---

## Phase 3: High Complexity / DCAA (3 modules, 18 guards, 16 transitions)

### 3A — Payroll (8 guards, 7 transitions)

**File:** `finance_modules/payroll/service.py`

**Payroll Run Workflow:**

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| payroll run calculate | calculate | `ALL_TIMECARDS_APPROVED` | `all_timecards_approved` |
| payroll run finish | finish_calculation | `CALCULATION_COMPLETE` | `has_calculation_errors` |
| payroll run approve | approve | `APPROVAL_OBTAINED` | `approval_status` |

**Timesheet Workflow (DCAA):**

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| `submit_timesheet` | submit | `DAILY_RECORDING_VALID` | `daily_recording_valid` (pre-computed from D1 engine) |
| `submit_timesheet` (2nd transition) | route_for_approval | `NO_CONCURRENT_OVERLAP` | `no_concurrent_overlap` (pre-computed from D4 engine) |
| `approve_timesheet` | approve | `SUPERVISOR_APPROVED` | `supervisor_approved` (approval engine handles) |
| `correct_timesheet_entry` | initiate_correction | `REVERSAL_EXISTS` | `reversal_exists` (pre-computed from D5 engine) |

**DCAA guard pattern:** Service methods already validate D1/D3/D4/D5 via engine calls.
Guard check uses pre-computed boolean results:

```python
# In submit_timesheet, after existing D1 validation:
if self._workflow_executor is not None:
    transition_result = self._workflow_executor.execute_transition(
        workflow=TIMESHEET_WORKFLOW,
        entity_type="timesheet",
        entity_id=submission.submission_id,
        current_state="draft",
        action="submit",
        actor_id=actor_id,
        actor_role="",
        context={"daily_recording_valid": all_entries_valid},
    )
    guard_result = check_transition_result(transition_result)
    if guard_result is not None:
        return guard_result
```

### 3B — Expense (5 guards, 4 transitions)

**File:** `finance_modules/expense/service.py`

**Expense Report Workflow:**

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| existing expense report submit | submit | `RECEIPTS_ATTACHED` | `receipts_attached` |
| existing expense report approve | approve | `APPROVAL_AUTHORITY` | approval engine handles |

**Travel Authorization Workflow (DCAA):**

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| `submit_travel_authorization` | submit | `PRE_TRAVEL_VALID` | `pre_travel_valid` (D6 pre-computed) |
| `approve_travel_authorization` | approve | `TRAVEL_AUTH_AUTHORITY` | approval engine handles |

### 3C — Procurement (5 guards, 5 transitions)

**File:** `finance_modules/procurement/service.py`

| Service Method | Action | Guard | Context |
|---------------|--------|-------|---------|
| `approve_requisition` | approve | `BUDGET_AVAILABLE` | `requisition_amount`, `available_budget` |
| `approve_po` | approve | `APPROVAL_COMPLETE` | approval engine handles |
| `send_po` | send | `VENDOR_APPROVED` | `vendor_approved` |
| `receive_goods` | receive | `ALL_LINES_RECEIVED` | `quantity_ordered`, `quantity_received` |
| three-way match | match_invoice | `FULLY_INVOICED` | `fully_invoiced` |

**Run full test suite after Phase 3.**

---

## Phase 4: Tests

### 4A — Guard Evaluator Tests

**File:** `tests/modules/test_guard_execution.py` (extend existing)

Add tests for all ~35 new evaluators. Pattern per evaluator:
- `test_{guard_name}_passes` — context satisfies guard, returns True
- `test_{guard_name}_fails` — context violates guard, returns False
- `test_{guard_name}_missing_context` — missing keys, returns False

**~105 new evaluator tests.**

### 4B — Service Guard Wiring Tests

For each module, add guard wiring tests to the existing test file. Pattern:

```python
class TestGuardWiring:
    def test_no_executor_passthrough(self):
        """When workflow_executor=None, transitions not enforced."""

    def test_guard_rejects_on_failure(self):
        """When guard fails, returns GUARD_REJECTED."""

    def test_guard_allows_on_success(self):
        """When guard passes, operation proceeds."""
```

**Files to modify (add guard wiring test classes):**

| Test File | Module | Tests |
|-----------|--------|-------|
| `tests/modules/test_ar_service.py` | AR | ~6 |
| `tests/modules/test_gl_service.py` | GL | ~6 |
| `tests/modules/test_cash_service.py` | Cash | ~4 |
| `tests/modules/test_assets_service.py` | Assets | ~6 |
| `tests/modules/test_wip_service.py` | WIP | ~6 |
| `tests/modules/test_tax_service.py` | Tax | ~6 |
| `tests/modules/test_payroll_service.py` | Payroll | ~14 |
| `tests/modules/test_contracts_service.py` | Procurement | ~10 |
| + expense, budget, lease, revenue, inventory | | ~20 |

**~78 new service wiring tests.**

**Total new tests: ~183**

---

## Phase 5: Documentation

Update `docs/archive/GUARD_WIRING_GAP.md`:
- Mark all modules as **Yes** in "Guards wired in service?" column
- Add implementation date
- Add reference to test files

---

## File Change Summary

| Category | Files | Count |
|----------|-------|-------|
| **New** | `finance_modules/_guard_helpers.py` | 1 |
| **Modified — Workflow migrations** | 13 `workflows.py` files | 13 |
| **Modified — Evaluator registration** | `finance_services/workflow_executor.py` | 1 |
| **Modified — Service wiring** | 13 `service.py` files | 13 |
| **Modified — Tests** | ~14 test files | 14 |
| **Modified — Docs** | `docs/archive/GUARD_WIRING_GAP.md` | 1 |
| **Total** | | **~43** |

---

## Key Design Decisions

1. **Helper extraction over repetition.** The `check_transition_result()` helper
   eliminates the 4-way branching duplicated across AP's 5 wired methods. All 13 new
   modules use the helper instead of copying the if/else chain.

2. **Boolean-passthrough for DCAA guards.** Service methods that already validate
   D1-D9 pre-compute boolean results and pass them in context. The guard evaluator
   reads the boolean. This avoids duplicating validation logic in the evaluator.

3. **Canonical type migration is a prerequisite.** Migrating to `finance_kernel.domain.workflow`
   types enables `requires_approval` and `approval_policy` on transitions when needed later,
   and eliminates 13 copies of identical dataclass definitions.

4. **All constructors remain backward-compatible.** `workflow_executor=None` is the default,
   so all existing callers and tests continue to work. Guards are only enforced when an
   executor is provided.

5. **Guard evaluators default to False for missing context.** If the service fails to
   provide expected context values, the guard rejects (fail-closed). This matches the
   AP pattern where `_match_within_tolerance` returns False for missing values.

---

## Verification

After each phase:
```bash
python3 -m pytest tests/ -v --tb=short
```

After all phases:
- All ~183 new tests pass
- All pre-existing tests still pass
- No module transitions execute without guard checks when executor is provided
- `docs/archive/GUARD_WIRING_GAP.md` shows all modules wired
