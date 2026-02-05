# Workflow Directive: No Generic Workflows

## Rule

**Generic or catch-all workflows are forbidden.**

No module service may call `execute_transition()` with a workflow that represents "everything else" or "just post."

Examples of **forbidden** workflows: `AR_OTHER_WORKFLOW`, `AP_OTHER_WORKFLOW`, `INVENTORY_OTHER_WORKFLOW`, `WIP_OTHER_WORKFLOW`, `CASH_OTHER_WORKFLOW`, `GL_OTHER_WORKFLOW`, or any workflow named `*_OTHER_*` or equivalent catch-all.

This rule applies to **all finance modules**: AR, AP, Inventory, Payroll, GL, Procurement, Assets, Cash, Budget, Tax, WIP, Revenue, Lease.

---

## Rationale

A generic workflow collapses distinct financial lifecycles into a single state machine. This removes:

- **Role separation** — Different actions require different authority. A write-off is not governed like a receipt. A refund is not governed like revenue recognition.
- **Policy enforcement** — Thresholds, timing rules, and approvals become uniform instead of action-specific.
- **Audit meaning** — The system can no longer prove *why* a particular financial fact was allowed to exist—only that it was.

If different economic actions share the same workflow, the ledger loses its ability to demonstrate that **different policies were applied to different kinds of financial risk**.

---

## Required pattern

Every financial action must bind to a **specific lifecycle workflow** that reflects its economic meaning and risk profile.

A workflow is not a technical gate. It is a formal model of **economic authority over time**.

---

## Example: Accounts Receivable (reference model)

### Approved workflows (minimum set)

These must exist and be used explicitly in AR. Other modules must follow the same pattern.

| Workflow | Lifecycle |
|----------|------------|
| `AR_INVOICE_WORKFLOW` | Revenue and receivable creation |
| `AR_RECEIPT_WORKFLOW` | Cash custody and verification |
| `AR_RECEIPT_APPLICATION_WORKFLOW` | Applying cash to open receivables |
| `AR_CREDIT_MEMO_WORKFLOW` | Revenue reversal and customer credit |
| `AR_WRITE_OFF_WORKFLOW` | Bad debt governance |
| `AR_DEFERRED_REVENUE_WORKFLOW` | Cash vs revenue timing |
| `AR_REFUND_WORKFLOW` | Cash outflow control |
| `AR_FINANCE_CHARGE_WORKFLOW` | Penalty and interest policy |

### Mapping rule (AR)

Each service method must call exactly **one** workflow that matches its economic meaning:

| Service method | Required workflow |
|----------------|-------------------|
| `record_invoice` | `AR_INVOICE_WORKFLOW` |
| `record_receipt` | `AR_RECEIPT_WORKFLOW` |
| `record_payment` | `AR_RECEIPT_WORKFLOW` (cash receipt) |
| `apply_payment` / `auto_apply_payment` | `AR_RECEIPT_APPLICATION_WORKFLOW` |
| `record_credit_memo` | `AR_CREDIT_MEMO_WORKFLOW` |
| `record_write_off` | `AR_WRITE_OFF_WORKFLOW` |
| `record_bad_debt_provision` | `AR_WRITE_OFF_WORKFLOW` (bad debt governance) |
| `record_deferred_revenue` / `recognize_deferred_revenue` | `AR_DEFERRED_REVENUE_WORKFLOW` |
| `record_refund` | `AR_REFUND_WORKFLOW` |
| `record_finance_charge` | `AR_FINANCE_CHARGE_WORKFLOW` |

---

## Other modules

Each module must define equivalent **action-specific workflows** (e.g. `AP_INVOICE_WORKFLOW`, `AP_PAYMENT_WORKFLOW`, `INVENTORY_RECEIPT_WORKFLOW`, `INVENTORY_ISSUE_WORKFLOW`, `PAYROLL_DISBURSEMENT_WORKFLOW`, `GL_PERIOD_CLOSE_WORKFLOW`). No `*_OTHER_WORKFLOW` or generic "post" workflow is permitted.

---

## Enforcement

Architecture test: `tests/architecture/test_no_generic_workflow.py` forbids the string `OTHER_WORKFLOW` (and equivalent catch-all names) in any `finance_modules/*/service.py`. Each module must pass this test.

---

## Design intent

> A workflow is a statement of financial authority, not a convenience layer.  
> If two actions do not carry the same economic risk, they must not share the same workflow.  
> The ledger must be able to prove not just *what happened*, but *which rules allowed it to happen*.
