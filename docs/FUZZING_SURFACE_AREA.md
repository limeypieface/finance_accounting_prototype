# Fuzzing & Hypothesis Surface Area

**Purpose:** Map where property-based testing (Hypothesis) and effective fuzzing could be applied beyond current usage, with rationale and effort.

**Current fuzzing:** See `tests/fuzzing/` — Hypothesis in `test_hypothesis_fuzzing.py` (amounts, idempotency, effective_date, domain Money/money_from_str, oversize intents), **`test_workflow_executor_fuzzing.py`** (full WorkflowExecutor across all workflows, state/action/amount/currency/context + approval policies), and explicit boundary/adversarial tests in `test_adversarial.py`.

---

## 1. Pure domain / validation (high value, low effort)

These are **pure functions**: input → output, no I/O. Ideal for Hypothesis.

| Area | Entry points | Current tests | Fuzzing opportunity | Effort |
|------|--------------|---------------|--------------------|--------|
| **Kernel event_validator** | `validate_field_type(value, field_type, path)`, `validate_currencies_in_payload`, `validate_event_type`, `validate_amount`, `validate_payload_against_schema` | `test_event_schema`, `test_pure_layer`, scattered | **@given** value + field_type: assert either valid result or ValidationError; fuzz payload dicts for `validate_event` | Low |
| **Kernel values / Money** | `Money.of(amount, currency)`, `Currency(code)` | `test_hypothesis_fuzzing` (Money), `test_adversarial`, `test_currency` | Already fuzzed (Money); extend to more currencies and edge amounts | Done / Low |
| **Kernel db.types** | `money_from_str(s)`, `validate_currency(s)` | `test_hypothesis_fuzzing`, `test_adversarial` (money_from_str) | Already fuzzed; add `validate_currency` with st.text() | Low |
| **Ingestion validators** | `validate_required_fields`, `validate_field_types`, `validate_currency_codes`, `validate_decimal_precision`, `validate_date_ranges`, `validate_batch_uniqueness` | `tests/ingestion/test_validators.py` (explicit) | **@given** record dict + mappings: assert errors or success; fuzz decimal precision (38,9), date ranges | Low–Medium |
| **Ingestion mapping engine** | `apply_transform(value, transform)`, `coerce_from_string(value, field_type)`, `apply_mapping(raw, mappings)` | `tests/ingestion/test_mapping_engine.py` (explicit) | **@given** string + EventFieldType for coerce; **@given** raw dict + list of FieldMapping for apply_mapping; malformed strings | Low |
| **Config guard_ast** | `validate_guard_expression(expression: str)` | `test_approval_config` (2 tests), guard execution tests | **@given** st.text() or st.from_regex(): assert no crash; valid expressions → []; invalid → GuardASTError list; security: no eval/exec | Medium (security boundary) |
| **Batch schedule** | `parse_cron(expression)`, `matches_cron(spec, dt)`, `should_fire(schedule, as_of)` | `test_schedule_evaluator`, `test_batch_schedule_config` | **@given** cron-like strings: parse_cron either returns CronSpec or raises; **@given** (spec, dt): matches_cron deterministic | Low |
| **Kernel idempotency** | `parse_idempotency_key(key)` | (used internally) | **@given** key strings: parse never crashes; valid format → tuple; invalid → clear error | Low |
| **Policy authority (kernel)** | `validate_ledgers`, `validate_module_action`, `validate_economic_type_posting` | `test_policy_registry` (explicit) | **@given** target_ledgers, action, economic_type: assert allowed or list of errors | Low |

---

## 2. Config parsing / loading (medium value, medium effort)

Parsers take dicts or strings and return structured data or raise. Good for “any valid input” and “malformed input” properties.

| Area | Entry points | Current tests | Fuzzing opportunity | Effort |
|------|--------------|---------------|--------------------|--------|
| **Config loader** | `parse_date`, `parse_scope`, `parse_policy`, `parse_role_binding`, `parse_import_mapping`, `parse_approval_policy`, `parse_batch_schedule` | `test_import_mapping_config`, `test_approval_config`, `test_batch_schedule_config` | **@given** dicts (e.g. st.fixed_dictionaries + st.text()): parse either returns or raises; fuzz required vs optional keys | Medium (many parse_* functions) |
| **Config compiler** | `_check_json_type`, `_check_dispatch_ambiguity`, compilation pipeline | Config tests, compilation tests | **@given** JSON-like values for type check; ambiguous dispatch inputs | Medium |
| **Config validator** | `validate_configuration(config)` | `test_config_validation` | **@given** minimal valid config + mutations: validate returns result; invalid config → errors | Medium |

---

## 3. Engines (pure logic, some context)

Engines are mostly pure; some need small fixtures (e.g. rules, context).

| Area | Entry points | Current tests | Fuzzing opportunity | Effort |
|------|--------------|---------------|--------------------|--------|
| **Approval engine** | `select_rule(amount, context)`, `validate_actor_authority(actor_role, rule)`, `_rule_matches(rule, amount, context)` | `test_approval_engine` (explicit) | **@given** amount (Decimal) + list of rules: select_rule returns rule or None; **@given** actor_role + rule: validate_actor_authority bool; boundary amounts at min/max/auto_approve_below | Low–Medium |
| **Reconciliation checker** | `check_policy_regime`, `check_amount_flow`, `check_temporal_ordering`, `check_chain_completeness`, etc. | Lifecycle recon tests | **@given** graph-like inputs (events, links): checker never crashes; valid inputs → list of errors or empty | Medium |
| **Valuation / allocation / matching** | Engine entry points with (lots, amounts, dates) | Domain/engine tests | **@given** lists of lots/amounts: no crash; invariants (e.g. total consumed ≤ available) | Medium |
| **Billing / tax** | `apply_withholding`, `apply_funding_limit` | Module tests | **@given** amounts and limits: deterministic result or clear error | Low |

---

## 4. Services / orchestration (higher effort, integration-style)

These touch DB or multiple components. Fuzzing is still useful at boundaries (e.g. input validation before DB).

| Area | Entry points | Current tests | Fuzzing opportunity | Effort |
|------|--------------|---------------|--------------------|--------|
| **Period service** | `validate_effective_date`, `validate_adjustment_allowed` | Period tests, posting tests | **@given** (effective_date, period_status, allows_adjustments): validate returns or raises | Low (pure-ish) |
| **Reversal service** | `reverse_in_same_period`, `reverse_in_current_period` (preconditions) | `test_reversal_e2e`, `test_reversal_service`, `test_reversal_queries` | **@given** effective_date boundaries (same period vs current period); entry with N lines | Medium (fixtures) |
| **Approval service** | Request/decision with amount, currency, actor_role | `test_approval_service`, `test_approval_posting_e2e` | **@given** amount at/above/below threshold, currency, role: request → success or known error | Medium |
| **Subledger / contract** | `validate_entry`, `validate_can_charge`, `validate_clin_charge` | Service tests | **@given** entry or charge params: validate never crashes; valid → pass, invalid → list of errors | Medium |

---

## 5. Event / payload boundaries (high value for robustness)

System boundary: untrusted or varied payloads.

| Area | Entry points | Current tests | Fuzzing opportunity | Effort |
|------|--------------|---------------|--------------------|--------|
| **Event payload** | `IngestorService.ingest(..., payload=...)`, `validate_event`, schema validation | `test_adversarial` (unicode), `test_event_schema` | **@given** payload = st.dictionaries(st.text(), st.one_of(st.text(), st.integers(), st.floats(), st.none())): ingest either accepts or rejects with known code; no 500 | Medium |
| **Event type strings** | Where event_type is used (selector, validator) | Various | **@given** event_type = st.text(): validate_event_type returns errors or []; no crash | Low |
| **Unicode / large payloads** | Same as above | `test_adversarial` (unicode) | Extend with st.text(alphabet=st.characters(blacklist_categories=())) and max_size | Low |

---

## 6. Summary matrix

| Category | Candidates | Hypothesis today | Suggested next |
|----------|------------|------------------|----------------|
| **Pure validation (kernel + ingestion)** | event_validator, validators, mapping engine, guard_ast, parse_cron, idempotency_key | Money, money_from_str, posting amounts | event_validator field_type + value; ingestion coerce_from_string + apply_mapping; guard_ast expressions |
| **Config** | parse_* (loader), validate_configuration | None | parse_date, parse_approval_policy (or one parse_*) with st.dicts |
| **Engines** | Approval select_rule/_rule_matches, schedule matches_cron | None | Approval amount boundaries; parse_cron + matches_cron |
| **Services (pure-ish)** | validate_effective_date, validate_adjustment_allowed | None | Period validate_* with dates |
| **Payload / event** | ingest payload, event_type | None | st.dictionaries for payload; st.text() for event_type |

---

## 7. Recommended order of implementation

1. **Kernel `validate_field_type` + EventFieldType** — One Hypothesis test: `@given(value=..., field_type=st.sampled_from(EventFieldType))`; assert ValidationError or success. Fast, pure, high coverage of type boundaries.
2. **Ingestion `coerce_from_string`** — `@given(s=st.text(), field_type=st.sampled_from(...))`; assert CoercionResult and no crash. Complements existing mapping_engine tests.
3. **Guard AST `validate_guard_expression`** — `@given(expression=st.text())`; assert no crash and result is list of GuardASTError or []. Security-sensitive.
4. **Approval engine `_rule_matches` / `select_rule`** — Fuzz amount at boundaries (min_amount, max_amount, auto_approve_below); assert deterministic rule or None.
5. **Batch `parse_cron`** — `@given(expression=st.text())`; parse_cron raises ValueError or returns CronSpec; then `@given(spec, dt)` for matches_cron determinism.
6. **Config `parse_date`** — `@given(value=st.one_of(st.dates(), st.from_regex(r"\d{4}-\d{2}-\d{2}")))`; parse_date returns date or raises.

---

## 8. Workflow executor (done)

**`tests/fuzzing/test_workflow_executor_fuzzing.py`** — Full WorkflowExecutor fuzzing:

- **Broad spectrum:** All registered workflows (AP, AR, Inventory, GL, Procurement, Payroll, etc.), any (current_state, action) — both valid transitions and invalid combinations.
- **Inputs fuzzed:** amount (Decimal or None), currency (USD/EUR/GBP/JPY), actor_role, context (dict with text/number/boolean values).
- **Tests:** (1) `execute_transition` never crashes; result is well-formed TransitionResult; success ⇒ new_state in workflow.states. (2) Same input ⇒ same result (determinism). (3) With real approval policies (US-GAAP), execute_transition never crashes; currency USD to match config.
- **Strategies:** `workflow_state_action` composite (workflow + state + action, valid or random), plus decimals, currency, actor_role, context_dict.

## 8b. Workflow posting E2E (done)

**`tests/fuzzing/test_workflow_posting_e2e_fuzzing.py`** — End-to-end workflow→posting fuzzing:
- **Registry:** `POSTABLE_TRANSITIONS` maps (workflow_name, from_state, action) → (event_type, payload_builder). Currently AP invoice `match` → `ap.invoice_received` (direct expense payload).
- **Never crashes:** Hypothesis draws a postable transition, fuzzes amount and invoice_number, calls `ModulePostingService.post_event()`; asserts no crash and result status is one of the known statuses (POSTED, PERIOD_CLOSED, GUARD_REJECTED, etc.).
- **Idempotency:** Deterministic test (no Hypothesis): same event_id + payload posted twice yields POSTED then ALREADY_POSTED.
- **Fixtures:** `module_posting_service` overridden with `auto_commit=False`; `effective_date` depends on `current_period` so posting uses an open period.

---

## 9. What we are not fuzzing (and why)

- **ORM / DB triggers:** Not pure; covered by integration/adversarial tests (e.g. immutability).
- **Full posting pipeline with random everything:** Partially done (amounts, idempotency, effective_date); adding more dimensions (random event_type, random payload) is possible but heavier and slower.
- **Workflow executor with real DB state:** Better as explicit E2E; Hypothesis can still fuzz amount/role at approval boundary in isolation.
- **Audit chain / hash:** Deterministic; fuzzing would target input to hashing (already covered by payload/event fuzzing if we add it).

---

*Last updated: 2026-02-02*
