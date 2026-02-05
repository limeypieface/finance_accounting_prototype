# Integration Event Catalog

Event types and payload contract for external systems (e.g. Sindri/Ironflow) that post events into the finance kernel via `finance_services.integration.post_event_from_external`.

**Idempotency:** Pass `event_id` (e.g. external system’s document or line id) so retries are safe. Same `event_id` + same payload ⇒ same outcome (R3).

**Contract validation:** Use `validate_contract(event_type, payload, schema_version=1)` before posting to fail fast on invalid payloads. When a schema is registered for the event type, payload is validated against it.

---

## Event types (by domain)

Event types are namespaced `module.action`. Policy and payload shape are defined in `finance_config/sets/<config_id>/policies/*.yaml` and, when present, in `finance_kernel/domain/schemas/definitions/`.

### Accounts Payable (AP)

| event_type | Schema (v1) | Typical payload keys |
|------------|-------------|------------------------|
| `ap.invoice_received` | ✅ AP_INVOICE_RECEIVED_V1 | invoice_id, invoice_number, supplier_party_code, invoice_date, due_date, gross_amount, net_amount, currency |
| `ap.invoice_received_inventory` | — | As per policy where-clause |
| `ap.payment` | ✅ AP_PAYMENT_V1 | payment_id, invoice_ids or supplier_party_code, amount, currency |
| `ap.payment_with_discount` | — | |
| `ap.accrual_recorded` | — | |
| `ap.prepayment_recorded` | — | |

### Accounts Receivable (AR)

| event_type | Schema (v1) | Typical payload keys |
|------------|-------------|------------------------|
| `ar.invoice` | ✅ AR_INVOICE_ISSUED_V1 | invoice_id, customer_party_code, amount, currency |
| `ar.payment` | ✅ AR_PAYMENT_RECEIVED_V1 | payment_id, receipt_id, amount, currency |
| `ar.receipt` | — | |
| `ar.credit_memo` | ✅ AR_CREDIT_MEMO_V1 | |
| `ar.write_off` | — | |

### Cash / Bank

| event_type | Schema (v1) | Typical payload keys |
|------------|-------------|------------------------|
| `cash.deposit` | ✅ BANK_DEPOSIT_V1 | amount, bank_account_code, reference |
| `cash.withdrawal` | ✅ BANK_WITHDRAWAL_V1 | amount, destination_type, bank_account_code |
| `cash.transfer` | ✅ BANK_TRANSFER_V1 | amount, from_account, to_account |
| `cash.bank_fee` | — | |
| `cash.interest_earned` | — | |
| `cash.nsf_return` | — | |

### Inventory

| event_type | Schema (v1) | Typical payload keys |
|------------|-------------|------------------------|
| `inventory.receipt` | ✅ INVENTORY_RECEIPT_V1 | receipt_id, item_id or item_code, quantity, location_id, unit_cost |
| `inventory.issue` | ✅ INVENTORY_ISSUE_V1 | issue_id, item_id, quantity, location_id |
| `inventory.adjustment` | ✅ INVENTORY_ADJUSTMENT_V1 | |
| `inventory.warehouse_transfer` | — | from_location, to_location, item_id, quantity |
| `inventory.cycle_count` | — | |

### Procurement

| event_type | Schema (v1) | Typical payload keys |
|------------|-------------|------------------------|
| `procurement.po_encumbered` | — | po_id, supplier_party_code, lines (amounts), period |
| `procurement.commitment_recorded` | — | |
| `procurement.receipt_matched` | — | |

### Contracts / Billing

| event_type | Schema (v1) | Typical payload keys |
|------------|-------------|------------------------|
| `contract.cost_incurred` | ✅ CONTRACT_COST_INCURRED_V1 | contract_id, amount, cost_type, ... |
| `contract.billing_provisional` | ✅ CONTRACT_BILLING_PROVISIONAL_V1 | |
| `contract.fee_accrual` | ✅ CONTRACT_FEE_ACCRUAL_V1 | |
| `contract.funding_action` | — | |

### Other domains

- **Budget:** `budget.entry`, `budget.encumbrance_commit`, `budget.transfer`, ...
- **GL:** `gl.recurring_entry`, `gl.intercompany_transfer`, `gl.retained_earnings_roll`, `fx.unrealized_gain`, ...
- **Intercompany:** `ic.transfer`
- **Project:** `project.cost_recorded`, `project.billing_tm`, `project.billing_milestone`
- **Payroll / Timesheet:** See `finance_kernel/domain/schemas/definitions/payroll.py` and `dcaa.py` for DCAA-related schemas

Policies (and where-clauses) are in `finance_config/sets/<config_id>/policies/*.yaml`. Payload shape for event types **without** a registered schema is defined by the policy’s meaning and ledger effects; use the kernel’s policy YAML and existing module services as reference.

---

## How to use (this repo only; no Sindri changes)

1. **Validate before post:**  
   `validation = validate_contract(event_type, payload, schema_version=1)`  
   If `not validation.is_valid`, return or surface `validation.errors` to the caller.

2. **Post event:**  
   Build `ModulePostingService` (session, role_resolver, clock, party_service, etc.) using your existing config and orchestrator pattern, then:  
   `result = post_event_from_external(poster, event_type=..., payload=..., effective_date=..., actor_id=..., amount=..., currency=..., event_id=..., producer="sindri")`

3. **Interpret result:**  
   `result.is_success` → posted or already posted.  
   `result.is_validation_failure` → contract validation failed; use `result.errors`.  
   Otherwise `result.status` is the kernel status (e.g. `rejected`, `profile_not_found`, `guard_rejected`).

See `finance_services.integration` and `docs/SINDRI_INTEGRATION_ANALYSIS.md` for full context.
