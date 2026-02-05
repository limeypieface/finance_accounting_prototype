# Test Commands

How to run tests for the finance kernel project. Use `python3 -m pytest` (or `pytest`) from the project root.

---

## Database isolation (tests vs interactive)

Tests use a **separate database** so they never drop or truncate data you create in interactive or scripts.

| Use case | Database | Notes |
|----------|----------|--------|
| **interactive.py**, run_import.py, seed_data, scripts | `finance_kernel_test` | Your data; only you can reset it (X in interactive). |
| **pytest** | `finance_kernel_pytest` (default) | Tests drop/create/truncate only this DB. |

Create the pytest DB once (same user as interactive):

```bash
createdb -U finance finance_kernel_pytest
```

Override with `DATABASE_URL` if you want pytest to use a different DB (e.g. for CI). Do not point pytest at `finance_kernel_test` if you want to preserve interactive data.

**If many tests fail with `relation "accounts" does not exist` (or "parties", "contracts", "fiscal_periods"):** the test DB has no schema. Fix:

1. **Use the default test DB** — Unset `DATABASE_URL` or set it to the pytest DB:
   ```bash
   export DATABASE_URL="postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_pytest"
   ```
2. **Create the DB if it doesn’t exist:** `createdb -U finance finance_kernel_pytest`
3. **Ensure PostgreSQL is running** (e.g. `brew services start postgresql@15` or `docker-compose up -d db`). Engine suites (reconciliation, correction, valuation, etc.) and any test that uses DB fixtures will fail with `psycopg2.OperationalError: connection refused` if Postgres is not up; spin up the DB first to get a real signal.
4. Re-run pytest; the first run that needs the DB will create all tables. If you see a clear error like *"Schema creation did not create 'accounts'"*, the DB is reachable but table creation failed — check the full traceback for the real cause.

---

## Quick reference

```bash
# Run all tests
python3 -m pytest

# Verbose
python3 -m pytest -v

# Stop on first failure
python3 -m pytest -x

# Run tests matching a keyword
python3 -m pytest -k "idempotency"

# With coverage
python3 -m pytest --cov=finance_kernel --cov=finance_engines --cov=finance_services --cov=finance_modules --cov-report=term-missing

# Parallel (requires pytest-xdist)
python3 -m pytest -n auto
```

---

## See trace output (decision journal)

To see workflow transitions, interpretation, and journal write for posted events:

**Option 1 — Global (all events in selected tests):**

```bash
python3 -m pytest -T -s tests/modules/test_ar_service.py -k test_record_payment_posts
```

Use `-T` (or `--show-trace`) and `-s`. Works for any test that posts.

**Option 2 — Trace a single event after the fact (e.g. after `scripts/interactive.py`):**

```bash
python3 scripts/trace.py --event-id <uuid>
python3 scripts/trace.py --list
```

**Example commands with trace:**

| Scenario              | Command |
|-----------------------|---------|
| AR record invoice     | `python3 -m pytest -T -s tests/modules/test_ar_service.py -k test_record_invoice_posts` |
| AR record payment     | `python3 -m pytest -T -s tests/modules/test_ar_service.py -k test_record_payment_posts` |
| AP record invoice     | `python3 -m pytest -T -s tests/modules/test_ap_service.py -k test_record_invoice_posts` |
| Config-driven posting | `python3 -m pytest -T -s tests/config/test_config_wiring.py -k test_module_posting_service_from_built_orchestrator` |
| Posting balance       | `python3 -m pytest -T -s tests/posting/test_balance.py -k test_balanced_entry_posts` |

---

## Architecture log matrix (what each test touched)

Log-based summary: which parts of the system (config, db, persistence, etc.) each test triggered. No test markers; derived from actual logs.

**Run tests and print the matrix at the end:**

```bash
ARCHITECTURE_LOG_SHOW_MATRIX=1 python3 -m pytest tests/modules/ -v --tb=short
```

Output: a table (test name | parts touched) and a summary (how many touched nothing vs persistence). Use any path instead of `tests/modules/` (e.g. `tests/`, `tests/config/`, or a single file).

**Larger run (modules + config):**

```bash
ARCHITECTURE_LOG_SHOW_MATRIX=1 python3 -m pytest tests/config/ tests/modules/ -v --tb=short
```

Details: `tests/architecture_log/README.md`.

---

## Real-infrastructure convention

Checks that module/integration tests use the real policy registry and DB (no unauthorized mocks of the posting pipeline):

```bash
python3 -m pytest tests/architecture/test_real_infrastructure_convention.py -v
```

---

## Actor required for posting tests (G14)

**Any test that posts to the journal MUST have a valid actor.** Actor validation is mandatory for all POSTED outcomes (G14). Without it, posting returns `REJECTED` ("Actor validation is mandatory for posting; PartyService not configured") or `INVALID_ACTOR`.

- **Root conftest:** Use the `module_posting_service` fixture (it depends on `party_service` and `test_actor_party`). The `test_actor_party` fixture creates a Party for `test_actor_id` so posting succeeds.
- **Module tests (e.g. AP, AR):** Use the module’s service fixture (e.g. `ap_service`), which already depends on `party_service` and `test_actor_party`. Pass `actor_id=test_actor_id` in posting calls.
- **New tests that post:** Depend on `party_service` and `test_actor_party` (or the module service fixture that includes them), and use `test_actor_id` as the `actor_id` in the call. Do not use an arbitrary UUID as `actor_id` unless you also create a Party for that UUID.

---

## Mutation audit

Temporarily break a seam (e.g. journal writer, sequence allocation); tests that should fail must fail. If all pass, the script exits 1.

```bash
# Default: adversarial, posting, integration, architecture, services (~675 tests)
python scripts/run_mutation_audit.py

# Full suite
python scripts/run_mutation_audit.py tests/

# One mutation manually
MUTATION_NAME=JOURNAL_WRITER_RETURN_SUCCESS_WITHOUT_WRITING python3 -m pytest tests/posting/ tests/adversarial/ -v
```

Mutations and rationale: `tests/mutation/README.md`.

---

## Serialization firewall

Validates that values at persistence boundaries (e.g. `decision_log`) are JSON-serializable. Active when running the full suite or any test under `tests/serialization/`.

```bash
# Run firewall tests only
python3 -m pytest tests/serialization/ -v
```

Disable: `SERIALIZATION_FIREWALL=0 python3 -m pytest ...`

---

## Run by category

**Unit / pure logic (no DB):**

```bash
python3 -m pytest tests/unit/ tests/domain/ tests/engines/ tests/replay/ tests/metamorphic/ -v
```

**Posting pipeline:**

```bash
python3 -m pytest tests/posting/ tests/period/ tests/multicurrency/ -v
```

**Security and immutability:**

```bash
python3 -m pytest tests/audit/ tests/adversarial/ tests/security/ tests/database_security/ -v
```

**Concurrency and crash:**

```bash
python3 -m pytest tests/concurrency/ tests/crash/ -v
```

**Architecture and fuzzing:**

```bash
python3 -m pytest tests/architecture/ tests/fuzzing/ -v
```

**ERP modules (all):**

```bash
python3 -m pytest tests/modules/ -v
```

**Integration and services:**

```bash
python3 -m pytest tests/integration/ tests/services/ -v
```

---

## Run by invariant

| Invariant / area        | Command |
|-------------------------|---------|
| R1–R2 Event / payload   | `python3 -m pytest tests/audit/test_event_protocol_violation.py -v` |
| R3 Idempotency          | `python3 -m pytest tests/posting/test_idempotency.py tests/concurrency/test_true_concurrency.py -v` |
| R4 Balance              | `python3 -m pytest tests/posting/test_balance.py -v` |
| R5 Rounding             | `python3 -m pytest tests/adversarial/test_rounding_line_abuse.py tests/adversarial/test_rounding_invariant_gaps.py -v` |
| R6 Replay               | `python3 -m pytest tests/replay/test_r6_replay_safety.py -v` |
| R9 Sequence             | `python3 -m pytest tests/concurrency/test_r9_sequence_safety.py -v` |
| R10 Immutability         | `python3 -m pytest tests/audit/test_immutability.py tests/audit/test_database_attacks.py -v` |
| R11 Audit chain         | `python3 -m pytest tests/audit/test_chain_validation.py -v` |
| R12–R13 Period           | `python3 -m pytest tests/posting/test_period_lock.py tests/period/test_period_rules.py -v` |
| R14–R15 Open/closed      | `python3 -m pytest tests/architecture/test_open_closed.py -v` |
| R16–R17 Currency         | `python3 -m pytest tests/unit/test_currency.py tests/unit/test_money.py -v` |
| L1–L5 Economic links    | `python3 -m pytest tests/domain/test_economic_link.py tests/services/test_link_graph_service.py -v` |

---

## Smoke and markers

**Quick smoke:**

```bash
python3 -m pytest tests/unit/ tests/posting/test_balance.py tests/audit/test_immutability.py -v --tb=short
```

**Markers:**

```bash
python3 -m pytest -m "not postgres"    # Skip PostgreSQL-specific
python3 -m pytest -m postgres         # Only PostgreSQL
python3 -m pytest -m slow             # Slow tests only
```

---

## Test directory summary

| Directory                | Focus |
|--------------------------|--------|
| `tests/unit/`            | Value objects |
| `tests/domain/`          | Pure logic, schemas, profiles |
| `tests/engines/`         | Calculation engines |
| `tests/posting/`         | Core posting pipeline |
| `tests/period/`          | Fiscal period rules |
| `tests/audit/`           | Immutability, hash chain, triggers |
| `tests/adversarial/`     | Attack vectors, tamper resistance |
| `tests/architecture/`    | Import boundaries, governance |
| `tests/concurrency/`     | Race conditions, sequence safety |
| `tests/crash/`           | Durability |
| `tests/integration/`     | End-to-end flows |
| `tests/modules/`         | ERP module tests |
| `tests/services/`       | Service-layer (approval, reversal, etc.) |
| `tests/replay/`          | Replay determinism |
| `tests/multicurrency/`   | FX, conversions |
| `tests/fuzzing/`         | Hypothesis property-based |
| `tests/security/`        | SQL injection prevention |
| `tests/database_security/` | PostgreSQL-level protection |
| `tests/serialization/`   | JSON firewall at persistence |
| `tests/demo/`            | Interactive demos |
