# Dependency reality detector

Ensures tests marked **`@pytest.mark.system`** actually touch real infrastructure. Verdict is applied in **`pytest_runtest_makereport`** so failures show as clean test failures with a clear message, not as teardown errors.

## Tiers

| Tier     | Marker            | Boundaries required |
|----------|-------------------|----------------------|
| **unit** | `@pytest.mark.unit`   | None (pure logic) |
| **service** | `@pytest.mark.service` | None enforced (may mock DB/config) |
| **system** | `@pytest.mark.system`  | DB + config load + persistence write |

Only **system** tests are checked. Use **system** only for full-pipeline tests. Most posting/inventory tests are **service**-tier (may use real DB but don't need the strict check).

## Instrumented boundaries

- **SQLAlchemy engine.connect** — real DB connection
- **get_active_config** — config load
- **SequenceService.next_value** — sequence allocator (recorded only)
- **OutcomeRecorder.record_posted / record_blocked / record_rejected** — persistence write

## Pass/fail rule (system only)

For any test marked **`@pytest.mark.system`**:

- Must touch **real DB connection**
- Must touch **at least one config load**
- Must touch **at least one persistence write**

Otherwise the test **fails** (in the test report, not teardown) with a message listing missing boundaries and a hint to use `@pytest.mark.service` if the test is service-tier.

## Usage

1. Mark tests that should touch the full stack (DB, config, persistence):

   ```python
   @pytest.mark.system
   def test_inventory_receipt_posts_successfully(self, module_posting_service, ...):
       ...
   ```

2. The detector's conftest is loaded for every test run via `pytest_plugins` in `tests/conftest.py`, so you do not need to include `tests/reality/` in the path.

3. **Verify the detector:** Run the sanity check (un-skip or `-k test_reality_detector_fails`). That test is marked system and touches nothing — the reality check should fail. Integration tests that post via `ModulePostingService` and use config should pass (subject to session-scoped config; see below).

**Note:** Report-only mode: `REALITY_DETECTOR_REPORT_ONLY=1 pytest tests/ -v --tb=no`. Failures are collected and printed at session end; exit code is 1 if any system test missed a boundary.

## Integration tests and session-scoped config

Many integration tests use the session-scoped `test_config` fixture, so config is loaded once for the suite and not again during each test. Those tests will show "Missing: at least one config load" even when they do post. Options: use a function-scoped config fixture for tests that must pass the reality check, or treat the report as informational for those tests.

## Value

Surfaces over-mocked tests, in-memory-only tests, and system tests that don't actually touch DB, config, or persistence.
