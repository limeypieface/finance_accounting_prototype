# Serialization firewall

Enforces a hard boundary between runtime objects and persisted artifacts. If any test stores or emits a non-JSON-safe object at a persistence/export point, the test fails immediately with the field path.

## Mechanism

- At persistence points (e.g. `OutcomeRecorder.record_posted(..., decision_log=...)`), the value is wrapped with `json.dumps` then `json.loads`.
- If serialization fails (e.g. a `Currency` or `Money` object in `decision_log`), `SerializationFirewallError` is raised with the field path.

## Persistence points covered

- **InterpretationOutcome.decision_log** — validated when `OutcomeRecorder.record_posted` (and related methods) are called with `decision_log=...`.

## Enabling

The firewall is installed when the serialization conftest is loaded (e.g. `pytest tests/` or `pytest tests/serialization/`) and `SERIALIZATION_FIREWALL` is not `0`. Default is enabled (`SERIALIZATION_FIREWALL=1`). Set `SERIALIZATION_FIREWALL=0` to disable. The firewall is not loaded via `pytest_plugins` (to avoid double-registration when running `tests/serialization/`), so when you run only other dirs (e.g. `pytest tests/integration/`) the firewall is not active unless you include `tests/serialization/` in the path.

## Value

Prevents domain objects leaking into audit logs, environment-specific serialization crashes, and in-memory artifacts masquerading as stored records.
