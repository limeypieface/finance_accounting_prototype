# Architecture log verification

Confirms which **architecture boundaries** were actually used during each test by **analyzing log output** — no patching. The codebase already emits structured log points; we capture them per test and check that `@pytest.mark.system` tests log the required boundaries.

## Markers (what we look for in logs)

| Marker ID        | Log signal | Where it's emitted |
|------------------|------------|--------------------|
| `config_load`    | `trace_type="FINANCE_CONFIG_TRACE"` | `finance_config.get_active_config()` |
| `db_session`     | `trace_type="FINANCE_DB_SESSION"`    | `finance_kernel.db.engine.get_session()` |
| `journal_write`  | `message="journal_write_started"`   | `JournalWriter.write()` |
| `outcome_recorded` | `message="outcome_recorded"`       | `OutcomeRecorder.record_*()` |
| `persistence`    | (synthetic) `journal_write` OR `outcome_recorded` | — |
| `engine_dispatch`| `message="engine_dispatch_started"` or `trace_type="FINANCE_ENGINE_DISPATCH"` | InterpretationCoordinator |
| `policy_selection` | `trace_type="FINANCE_POLICY_TRACE"` | PolicySelector |

For **`@pytest.mark.system`** tests we require: **config_load**, **db_session**, **persistence**.

## How it works

1. A custom logging handler captures every log record while a test is running and associates it with that test's `nodeid`.
2. After the test (in `pytest_runtest_makereport`), for tests marked `@pytest.mark.system` we compute which markers appeared in the captured records.
3. If any required marker is missing, the test is **failed** with a clear message (or collected for report-only).

## Where logs are

- **The raw log lines** (e.g. `session_created` with `trace_type=FINANCE_DB_SESSION`) go to normal Python logging. The plugin **does not print them**; it only captures them in memory to compute which markers each test hit. To see those lines in the terminal, run pytest with log output enabled, e.g.  
  `pytest tests/ --log-cli-level=INFO` or `pytest tests/ -s` (no capture).
- **The “architecture log” output** (which boundaries were hit) appears in three places:
  1. **When a system test fails the check:** in the test’s failure message (“Missing: … Hit: …”).
  2. **When using report-only and there are failures:** printed at session end (section “ARCHITECTURE LOG: system tests that did not log required boundaries”).
  3. **When you ask for the matrix:**  
     `ARCHITECTURE_LOG_SHOW_MATRIX=1 pytest tests/ -v`  
     At session end you get a **matrix** of every system test and which markers it logged (Hit: … and, if any, Missing: …).

## Usage

- **Enforcement (default):** Run pytest as usual. Any `@pytest.mark.system` test that does not log all three (config_load, db_session, persistence) **fails** with a message listing missing markers and what was hit.
- **Report only:**  
  `ARCHITECTURE_LOG_REPORT_ONLY=1 pytest tests/ -v --tb=no`  
  At session end you get a list of system tests that missed boundaries (exit code 1 if any).
- **Show matrix (see what each system test logged):**  
  `ARCHITECTURE_LOG_SHOW_MATRIX=1 pytest tests/ -v`  
  At session end you get a table: each system test and its Hit (and Missing) markers.
- **Disable:**  
  `ARCHITECTURE_LOG_VERIFY=0 pytest tests/`  
  Log-based verification is turned off.

## Relation to reality detector

- **Reality detector** (tests/reality/): patches boundaries and records "touches"; enforces that system tests touch DB, config, persistence. **Patch-based.**
- **Architecture log**: observes **existing log output**; enforces that system tests produced the expected log markers. **No patching.**

Use both: they give two independent signals that the real architecture was used.

## Adding new markers

1. Ensure the code path emits a log with a stable `message` or `extra["trace_type"]`.
2. Add a predicate in `tests/architecture_log/markers.py` (ARCHITECTURE_MARKERS).
3. If it should be required for system tests, add its id to `REQUIRED_MARKER_IDS_FOR_SYSTEM`.
