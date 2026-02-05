# QuickBooks Loader — One-Command Onboarding

**Objective:** A QuickBooks user downloads their XLSX exports; in one command the system converts the data into a format that passes validation, aligns the user's Chart of Accounts with the loaded COA, and constructs Journal and other entries so the upload succeeds. The XLSX structure is fixed (standard QBO export layout), so the flow is repeatable for any QuickBooks user.

**Status:** Phase 1 implemented; Phases 2–5 not yet implemented  
**Last Update:** 2026-02-03

---

## User Story

1. User downloads XLSX from QuickBooks (Account List, Vendor List, Customer List, Journal, etc.) into a known folder.
2. User runs **one command** (e.g. `quickbooks-loader <folder>` or CLI “QBO Load”).
3. System:
   - **CoA alignment first:** Prompts user to map the **input CoA** (from the XLSX Account List) to the **currently loaded COA** in the system. Recommends which existing config COA best matches the input.
   - **After mapping:** Constructs accounts, vendors, customers, and journal entries; assigns key fields (TBD placeholders or account numbers / supplier / customer codes as appropriate); formats dates correctly.
   - **Output:** Staged data that **passes validation** and is ready to promote (or auto-promotes in the same run).

No multi-step “upload then fix errors then promote.” One flow: align CoA → transform → validate → done.

---

## Principles

- **Standard QBO structure:** The XLSX layout is always the same (what we have in the reference folder). One loader handles all such exports.
- **CoA is the anchor:** Input accounts must map to the system’s COA before journal lines can resolve. Mapping is explicit and user-confirmed.
- **Recommend, don’t assume:** System recommends which loaded/config COA best matches the input CoA; user confirms or adjusts.
- **Validation passes:** Dates, required fields, and references are set so the upload passes validation (no “fix 123 rows” after the fact).
- **Single entry point:** One command or CLI flow for “QuickBooks load” from a folder of XLSX files.

---

## Current State (What Exists)

- **scripts/qbo/** — Detect QBO export type, read XLSX/CSV (accounts, vendors, customers, journal), write JSON to `upload/` with `_import_row`.
- **scripts/run_qbo_convert.py** — Converts all XLSX/CSV in a folder to JSON (qbo_accounts_*.json, qbo_vendors_*.json, qbo_journal_*.json, etc.).
- **finance_ingestion** — ImportService (load → stage → validate), PromotionService (promote batch), JSON/CSV adapters, mapping engine, promoters (account, vendor, customer, journal).
- **qbo_json.yaml** — Import mappings for converter JSON (qbo_json_accounts, qbo_json_vendors, qbo_json_customers, qbo_json_journal).
- **CLI Import & Staging (I)** — Upload file, pick mapping, view errors, auto-assign codes, promote.

**Gap:** No CoA mapping step, no “recommend COA” logic, no single command that does convert → CoA align → assign fields → format dates → ensure valid. User today: convert manually, upload, fix errors, promote.

---

## Target Flow (Single Command)

```
User: quickbooks-loader /path/to/qbo/exports
  or: CLI → "QBO Load" → select folder

1. INGEST XLSX
   - Run existing QBO converter on folder (or equivalent): read Account List, Vendor List, Customer List, Journal (and other known QBO exports).
   - Produce in-memory or temp structures (accounts, vendors, customers, journal entries) with normalized keys.

2. COA ALIGNMENT (blocking)
   - Extract input CoA from Account List (account name/code/type).
   - Load current system COA (from config + live accounts, or from a designated “config COA”).
   - RECOMMEND: Compare input CoA to each available “config COA” (e.g. US-GAAP-2026-v1 chart, or named COA sets). Recommend best match (e.g. by name overlap, type distribution, or explicit mapping file).
   - USER MAPS: Present mapping UI (CLI or minimal UI): for each input account, map to a target account in the chosen COA (or “Create new”, “TBD”). Optionally bulk-apply by type or name similarity.
   - Persist mapping: input_account_id/name → target_coa_code (or TBD). This mapping is used when building journal lines and when creating accounts.

3. CONSTRUCT & ASSIGN
   - Accounts: Create/merge from Account List using CoA mapping. Where mapping says “target code”, use it; else “TBD-&lt;slug&gt;” or auto-assign.
   - Vendors / Customers: Assign codes (e.g. V-001, C-001) or leave TBD; ensure required fields present.
   - Journal: For each journal entry, resolve line accounts via CoA mapping (input account → target code). Format dates (normalize to YYYY-MM-DD or system format). Ensure debits/credits balance; drop or fix invalid lines.
   - Output: Staged records (or direct write) that satisfy validation (required fields, date format, account references, balance).

4. VALIDATE & PROMOTE
   - Run validation; if any failures, fix in pipeline (dates, placeholders) or surface minimal “must fix” list.
   - Promote accounts first, then vendors/customers, then journal (dependency order).
   - Option: All in one command (convert → align → construct → validate → promote) or “validate only” for dry-run.

5. DONE
   - User sees: “Loaded N accounts, M vendors, K customers, J journal entries. All promoted.” Or clear list of what failed and one remediation path.
```

---

## Phased Implementation

### Phase 1 — CoA extraction and recommendation (no UI yet) — DONE

- **1.1** From QBO Account List (XLSX or our JSON), extract “input CoA”: list of accounts with name, type, (optional) code. → `scripts/qbo/coa_extract.py` (extract_input_coa, InputCoARecord).
- **1.2** Load “config COA” options: e.g. charts from config sets (US-GAAP-2026-v1, etc.). → `scripts/qbo/coa_config.py` (load_config_coa_options, ConfigCoAOption).
- **1.3** Recommendation engine (pure): given input CoA and a candidate config COA, score match by type coverage. → `scripts/qbo/coa_recommend.py` (score_config_coa, recommend_coa, QBO_ACCOUNT_TYPE_TO_ROLES).
- **1.4** CLI: `python3 scripts/recommend_coa.py --input path/to/qbo_accounts.json` → print recommended config COA and scores. Option `-q` prints only top config_id.

**Deliverable:** Can recommend “which COA best matches this Account List.” Tests: `tests/ingestion/test_qbo_coa_recommend.py` (16 tests).

### Phase 2 — CoA mapping (user aligns input → target)

- **2.1** Persist “CoA mapping” for a load: input account key (name/code) → target account code (in chosen COA) or TBD.
- **2.2** CLI (or minimal UI): for a given input CoA and chosen target COA, show list of input accounts; user can assign target code or TBD. Bulk actions: “match by name”, “match by type”.
- **2.3** Save mapping to file or DB (e.g. `qbo_coa_mapping_<batch>.yaml` or staging table) for use in Phase 3.

**Deliverable:** User can map each QBO account to a system account (or TBD).

### Phase 3 — Construct journal and entities with mapping

- **3.1** Build accounts for staging: from Account List + CoA mapping. If mapped to existing code, treat as “already exists” or “link”; if TBD or new, create with assigned code or TBD placeholder.
- **3.2** Build vendors/customers: assign codes (V-001, C-001) or TBD; ensure required fields; date formatting.
- **3.3** Build journal: resolve each line’s account via CoA mapping (input account → target code). Normalize dates. Validate balance per entry; drop or fix invalid lines. Output journal records that reference only resolved account codes (or TBD with clear rule).
- **3.4** Write to staging (or in-memory) in the format expected by existing import pipeline (e.g. JSON that matches qbo_json_* mappings), so existing validation and promotion apply.

**Deliverable:** From XLSX + CoA mapping, produce staged data that passes validation.

### Phase 4 — One command and UX

- **4.1** Single entry point: `quickbooks-loader <folder>` or CLI “QBO Load” that:
  1. Converts folder (reuse run_qbo_convert).
  2. Runs CoA recommendation; prompts for which config COA to use (default: recommended).
  3. Runs CoA mapping step (Phase 2); user confirms or edits.
  4. Runs construct (Phase 3); writes to staging.
  5. Validates; if ok, promotes in order (accounts → vendors/customers → journal); else reports errors and stops.
- **4.2** Config: “Standard QuickBooks loader” uses fixed XLSX layout (same as reference folder). Document that layout; any QBO export matching it is supported.

**Deliverable:** User runs one command; gets “loaded and promoted” or a clear, minimal fix path.

### Phase 5 — Hardening and docs

- **5.1** Error messages: when validation fails, message must point to CoA mapping or to a specific input row/entry (e.g. “Journal row 42: account X not mapped”).
- **5.2** Docs: “QuickBooks onboarding” — export from QBO, run loader, map CoA once, done.
- **5.3** Tests: E2E test with reference XLSX folder → CoA mapping fixture → validate and promote.

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| CoA before journal | CoA mapping is step 2; journal build uses it | Journal lines must resolve to system accounts; no “fix 123 rows” after the fact. |
| Recommend, then user confirms | System recommends best config COA; user picks and then maps accounts | Avoids wrong auto-match; keeps user in control. |
| Reuse existing ingestion | Constructed data still goes through ImportService / staging / promotion | No duplicate promotion logic; same validation and audit trail. |
| Fixed QBO layout | Loader assumes standard QBO export structure (as in reference folder) | One code path; any QBO user with same export format can use it. |
| TBD placeholders | Allow TBD for accounts/vendors/customers where user defers | Lets user complete mapping later; loader still produces valid structure. |

---

## Out of Scope (For This Plan)

- Supporting arbitrary or non-standard XLSX layouts (other ETL remains generic import + mapping).
- Full GUI for CoA mapping (CLI + optional minimal UI is enough for v1).
- Multi-entity or multi-currency QBO migration in one run (single legal entity, single CoA mapping per run).

---

## Success Criteria

- A QuickBooks user with XLSX exports in the standard layout can:
  1. Run one command (or one CLI flow).
  2. Align their CoA with the system (recommended COA + map accounts).
  3. Get all data converted, validated, and promoted without manual “fix 123 errors” cycles.
- Dates and required fields are set so validation passes.
- Journal lines resolve to system accounts via the CoA mapping; no unresolved account names in promoted data.

---

## References

- **plans/archive/2026-02-04_erp-ingestion-plan.md** — Staging, validation, promotion architecture.
- **scripts/qbo/** — QBO detection and readers; **run_qbo_convert.py** — XLSX → JSON; **coa_extract.py**, **coa_config.py**, **coa_recommend.py** — Phase 1 CoA extraction and recommendation.
- **scripts/recommend_coa.py** — CLI: recommend config COA from QBO accounts JSON.
- **qbo_json.yaml** — Import mappings for converter output.
- **finance_ingestion** — ImportService, PromotionService, promoters (account, vendor, customer, journal).
