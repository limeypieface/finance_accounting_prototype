# Sindri ↔ Finance: What It Takes to Work Together

Sindri (Ironflow) and this finance system work together by **events only**: Sindri sends event codes + payloads; finance posts to the ledger. No shared DB; finance stays distinct.

---

## How it works

- **Interface:** Event type (e.g. `ap.invoice_received`) + payload + `effective_date`, `actor_id`, `amount`, `currency`. Optional `event_id` for idempotency (use Sindri document/line ids).
- **Finance owns:** Ledger, policies, posting. It does not import Sindri.
- **Sindri owns:** Operational data (orders, inventory quantities, planning). When a business action happens (PO created, invoice recorded, payment, etc.), Sindri (or an adapter in its process) calls the finance integration and passes the right event type + payload.

---

## What this repo already provides

1. **`finance_services.integration`**
   - **`validate_contract(event_type, payload, schema_version=1)`** — Validates before posting. Returns `ValidationResult`. No session needed.
   - **`post_event_from_external(poster, event_type, payload, effective_date, actor_id, amount, currency, event_id=None, producer="sindri", ...)`** — Validates, then calls `poster.post_event(...)`. Returns **`IntegrationPostResult`** (status, event_id, journal_entry_ids, message, errors). Caller must build and pass a `ModulePostingService` (session, role_resolver, clock, party_service — same as tests/CLI).
   - **`IntegrationPostResult`** — `status`, `event_id`, `journal_entry_ids`, `message`, `errors`; `.is_success`, `.is_validation_failure`.

2. **Event catalog** — `docs/INTEGRATION_EVENT_CATALOG.md`: event types by domain, which have schemas, typical payload keys.

3. **Exports** — `validate_contract`, `post_event_from_external`, `IntegrationPostResult` are on `finance_services`.

---

## What Sindri needs to do

1. **Add an adapter** (e.g. `sindri/integrations/finance` or `sindri/finance_adapter`) that:
   - Depends on this repo as a library (e.g. `pip install -e /path/to/finance-repo`).
   - Loads finance config and builds `ModulePostingService` (session, role_resolver, clock, party_service — see tests/conftest or CLI setup).
   - Exposes something like `post_finance_event(event_type, payload, effective_date, actor_id, amount, currency, event_id=..., producer="sindri")` which calls `validate_contract` then `post_event_from_external`, and returns or raises based on `IntegrationPostResult`.

2. **At each business action** that should hit the ledger (e.g. PO created, invoice recorded, payment, inventory receipt):
   - Build the payload from Sindri models.
   - Call the adapter with the correct **event_type** and **event_id** (Sindri id for idempotency).
   - Handle success vs failure (e.g. roll back or surface to user).

3. **Map parties** — Finance needs a party (customer/supplier) for many events. Map Sindri `company_id` to finance party (e.g. by syncing parties or storing a mapping; finance uses `supplier_party_code` / `customer_party_code` in payloads).

---

## Sindri action → finance event_type

| Sindri action | Finance `event_type` |
|---------------|----------------------|
| PO created / approved (encumbrance) | `procurement.po_encumbered` |
| Supplier invoice recorded | `ap.invoice_received` or `ap.invoice_received_inventory` |
| Payment to supplier | `ap.payment` |
| Customer invoice | `ar.invoice` |
| Customer payment | `ar.payment`, `ar.receipt`, `ar.receipt_applied` |
| Bank deposit / withdrawal | `cash.deposit`, `cash.withdrawal` |
| Inventory receipt | `inventory.receipt` |
| Inventory transfer | `inventory.warehouse_transfer` |
| Inventory adjustment / cycle count | `inventory.adjustment`, `inventory.cycle_count` |
| Intercompany transfer | `ic.transfer` |
| Budget / encumbrance | `budget.entry`, `budget.encumbrance_commit` |

Payload shape per event type: see `docs/INTEGRATION_EVENT_CATALOG.md` and `finance_config/sets/.../policies/*.yaml`. Use Sindri IDs as `event_id` so retries are safe.

---

## Who owns what (no duplication)

- **Sindri:** Orders (PO/SO/MO), inventory quantities and locations, planning/MRP. Operational documents and quantity balance.
- **Finance:** Ledger, journal, AP/AR balances, policies. Money and accounting only.
- **Party/customer/supplier:** Both have a concept. For integration, map Sindri company to finance party (one source of truth or a mapping table); finance uses party codes in payloads.

So: Sindri does not duplicate the ledger; finance does not duplicate the PO document or quantity inventory. They connect only via events.
