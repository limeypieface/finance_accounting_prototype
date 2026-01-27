# Testing Philosophy & Approach

## Overview

The finance kernel test suite is designed to **prove invariants**, not just check behavior. Financial systems have non-negotiable guarantees that must hold under all conditions - retries, concurrency, crashes, replays, and adversarial input.

**Testing philosophy**: If an invariant can be violated, it WILL be violated in production. Our tests exist to prove violations are impossible.

---

## Test Categories

### 1. Unit Tests (`tests/unit/`)

Test **pure domain logic** in isolation.

**Characteristics:**
- No database required
- No mocking required (pure functions)
- Fast execution (milliseconds)
- Deterministic (same input = same output)

**What they test:**
- Money arithmetic and currency validation
- DTO immutability
- Value object constraints
- Strategy transformation logic

```python
# Example: Pure domain test
def test_money_prevents_cross_currency_addition():
    usd = Money.of(Decimal("100.00"), "USD")
    eur = Money.of(Decimal("50.00"), "EUR")

    with pytest.raises(CurrencyMismatchError):
        usd + eur  # Should fail - no implicit conversion
```

---

### 2. Posting Tests (`tests/posting/`)

Test **posting invariants** through the orchestrator.

**What they test:**
- Balance validation (debits = credits)
- Idempotency (same event = same result)
- Period lock enforcement
- Account validation

```python
# Example: Idempotency test
def test_same_event_posted_twice_returns_same_entry(posting_orchestrator):
    event_id = uuid4()

    result1 = posting_orchestrator.post_event(event_id=event_id, ...)
    result2 = posting_orchestrator.post_event(event_id=event_id, ...)

    assert result1.journal_entry_id == result2.journal_entry_id
    assert result2.status == PostingStatus.ALREADY_POSTED
```

---

### 3. Audit Tests (`tests/audit/`)

Test **audit trail integrity** and **immutability enforcement**.

**What they test:**
- Hash chain validation
- Tamper detection
- Immutability of posted records
- Database-level trigger protection

```python
# Example: Tamper detection
def test_modified_audit_event_breaks_chain(session, auditor):
    # Post several events to build chain
    for _ in range(10):
        post_event(...)

    # Tamper with middle event (bypassing ORM)
    session.execute(
        text("UPDATE audit_events SET payload_hash = 'tampered' WHERE seq = 5")
    )

    # Chain validation should fail
    with pytest.raises(AuditChainBrokenError):
        auditor.validate_chain()
```

---

### 4. Concurrency Tests (`tests/concurrency/`)

Test **thread safety** and **race condition handling**.

**What they test:**
- Concurrent same-event posting (idempotency under load)
- Concurrent distinct-event posting (no deadlocks)
- Sequence allocation uniqueness
- Audit chain integrity under concurrency

**Test modes:**
- `test_race_safety.py` - Threading-based tests (SQLite compatible)
- `test_true_concurrency.py` - Multi-connection tests (PostgreSQL only)
- `test_stress.py` - Extended load tests

```python
# Example: Concurrent idempotency
def test_200_threads_same_event_one_entry(session, orchestrator):
    event_id = uuid4()
    results = []

    def post_worker():
        result = orchestrator.post_event(event_id=event_id, ...)
        results.append(result)

    threads = [Thread(target=post_worker) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one should be POSTED, rest ALREADY_POSTED
    posted = [r for r in results if r.status == PostingStatus.POSTED]
    assert len(posted) == 1

    # All should have the same journal_entry_id
    entry_ids = {r.journal_entry_id for r in results}
    assert len(entry_ids) == 1
```

---

### 5. Adversarial Tests (`tests/adversarial/`, `tests/fuzzing/`)

Test **attack vectors** and **malicious input handling**.

**What they test:**
- Protocol violations (same event_id, different payload)
- Boundary attacks (period boundaries, numeric limits)
- Injection attempts (SQL, payload manipulation)
- Race condition exploits

```python
# Example: Protocol violation detection
def test_payload_hash_mismatch_rejected(ingestor):
    event_id = uuid4()

    # First submission
    ingestor.ingest(event_id=event_id, payload_hash="HASH_A", ...)

    # Second submission with different payload
    result = ingestor.ingest(event_id=event_id, payload_hash="HASH_B", ...)

    assert result.status == IngestStatus.REJECTED
    assert "PROTOCOL_VIOLATION" in result.message
```

---

### 6. Period Tests (`tests/period/`)

Test **fiscal period governance** (R12, R13).

**What they test:**
- Closed period rejection
- Adjustment policy enforcement
- Period boundary handling
- Period immutability after close

```python
# Example: R13 adjustment policy
def test_adjustment_rejected_when_not_allowed(period_service, orchestrator):
    # Create period without adjustment permission
    period_service.create_period(
        period_code="2024-Q1",
        allows_adjustments=False,
        ...
    )

    # Try to post an adjustment
    result = orchestrator.post_event(
        effective_date=date(2024, 2, 15),
        is_adjustment=True,
        ...
    )

    assert result.status == PostingStatus.ADJUSTMENTS_NOT_ALLOWED
```

---

### 7. Replay Tests (`tests/replay/`)

Test **deterministic replay** capability.

**What they test:**
- Same events produce same journal entries
- Trial balance consistency across rebuilds
- Strategy version handling during replay

```python
# Example: Replay determinism
def test_replay_produces_identical_trial_balance(session):
    # Post 1000 events
    events = post_many_events(1000)
    original_balance = compute_trial_balance()
    original_hash = hash_trial_balance(original_balance)

    # Clear derived data (but keep events)
    clear_journal_entries()

    # Replay all events
    for event in events:
        replay_event(event)

    # Trial balance should match exactly
    replayed_balance = compute_trial_balance()
    replayed_hash = hash_trial_balance(replayed_balance)

    assert original_hash == replayed_hash
```

---

### 8. Crash Tests (`tests/crash/`)

Test **durability and recovery** (R20).

**What they test:**
- Partial write prevention
- Transaction atomicity
- Recovery from mid-operation crashes

```python
# Example: Crash during posting
def test_crash_during_posting_leaves_no_partial_state(session):
    event_id = uuid4()

    # Simulate crash by forcing rollback
    try:
        with session.begin():
            orchestrator.post_event(event_id=event_id, ...)
            raise SimulatedCrash()  # Force rollback
    except SimulatedCrash:
        pass

    # No partial state should exist
    assert session.query(JournalEntry).filter_by(source_event_id=event_id).count() == 0
    assert session.query(JournalLine).count() == 0  # No orphan lines
```

---

### 9. Architecture Tests (`tests/architecture/`)

Test **architectural invariants** and **error handling**.

**What they test:**
- Open/closed principle (strategies are pluggable)
- Error classification and codes
- Domain layer purity (no I/O imports)

```python
# Example: Error code stability
def test_all_exceptions_have_unique_codes():
    codes = set()
    for exc_class in get_all_exception_classes():
        code = exc_class.code
        assert code not in codes, f"Duplicate code: {code}"
        codes.add(code)
```

---

### 10. Domain Tests (`tests/domain/`)

Test **domain layer purity** and **integrity constraints**.

**What they test:**
- Dimension integrity (FK constraints, immutability)
- Reference data validation
- Pure layer isolation

---

### 11. Metamorphic Tests (`tests/metamorphic/`) - TODO

Test **mathematical equivalences** in the ledger.

**Status:** Placeholder tests created, blocked by missing reversal service.

**What they will test (K1-K2 certification):**
- Post + reverse returns ledger to baseline
- Split/merge equivalence preserves totals
- Reversal creates true inverse entries

**Blocking dependency:** `ReversalService` must be implemented first.

---

## Test Infrastructure

### Fixtures (`conftest.py`)

Shared fixtures for all tests:

| Fixture | Purpose |
|---------|---------|
| `session` | Database session (auto-rollback) |
| `deterministic_clock` | Controlled time for determinism |
| `posting_orchestrator` | Pre-configured orchestrator |
| `standard_accounts` | Common account setup |
| `current_period` | Open fiscal period |
| `test_actor_id` | UUID for test actor |

### Markers

```python
@pytest.mark.postgres  # Requires PostgreSQL
@pytest.mark.slow      # Long-running test
@pytest.mark.stress    # High-load stress test
```

**Running filtered tests:**
```bash
# Skip PostgreSQL tests (for SQLite-only runs)
pytest -m "not postgres"

# Only slow tests
pytest -m slow

# Only stress tests with extended timeout
pytest -m stress --timeout=600
```

---

## Testing Principles

### 1. Test Invariants, Not Implementations

```python
# GOOD: Tests the invariant
def test_posted_entry_is_immutable():
    entry = post_event()
    entry.description = "modified"
    with pytest.raises(ImmutabilityError):
        session.flush()

# BAD: Tests implementation detail
def test_immutability_listener_is_registered():
    assert "before_update" in event.listeners_for(JournalEntry)
```

### 2. Deterministic by Design

All tests should produce the same result every time:

```python
# GOOD: Uses deterministic clock
def test_posting_timestamp(deterministic_clock, orchestrator):
    expected_time = deterministic_clock.now()
    result = orchestrator.post_event(...)
    assert result.record.posted_at == expected_time

# BAD: Uses real time (non-deterministic)
def test_posting_timestamp(orchestrator):
    before = datetime.now()
    result = orchestrator.post_event(...)
    after = datetime.now()
    assert before <= result.record.posted_at <= after  # Flaky!
```

### 3. Isolation Between Tests

Each test should be independent:

```python
# GOOD: Uses fixture that auto-rollbacks
def test_posting(session, orchestrator):
    result = orchestrator.post_event(...)
    # Session rolls back after test - no pollution

# BAD: Modifies shared state
def test_posting():
    global_orchestrator.post_event(...)
    # Other tests see this state!
```

### 4. Test the Boundaries

Focus on boundary conditions and edge cases:

```python
# GOOD: Tests period boundaries
def test_posting_on_period_end_date():
    ...

def test_posting_one_day_after_period_end():
    ...

# GOOD: Tests numeric boundaries
def test_maximum_decimal_precision():
    ...

def test_zero_amount_handling():
    ...
```

### 5. Adversarial Mindset

Assume attackers will try to break the system:

```python
# Test protocol violations
def test_resubmit_with_different_payload_rejected():
    ...

# Test race conditions
def test_concurrent_close_and_post():
    ...

# Test injection attempts
def test_sql_injection_in_payload():
    ...
```

---

## Running Tests

### Basic Commands

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=finance_kernel --cov-report=term-missing

# Run specific category
pytest tests/posting/

# Run with verbose output
pytest -v

# Stop on first failure
pytest -x

# Run tests matching pattern
pytest -k "idempotency"
```

### PostgreSQL Tests

```bash
# Start PostgreSQL (if using docker-compose)
docker-compose up -d postgres

# Run all tests including PostgreSQL-specific
pytest

# Run only PostgreSQL tests
pytest -m postgres
```

### Performance Tests

```bash
# Run stress tests with extended timeout
pytest tests/concurrency/test_stress.py --timeout=600

# Run with profiling
pytest tests/concurrency/ --profile
```

---

## Writing New Tests

### Template for Invariant Test

```python
class TestInvariantName:
    """
    Invariant: [State the invariant clearly]

    Why: [Explain why this invariant matters]
    """

    def test_positive_case(self, session, orchestrator, ...):
        """Test that valid operations succeed."""
        # Arrange
        ...
        # Act
        result = orchestrator.do_something(...)
        # Assert
        assert result.is_success

    def test_negative_case(self, session, orchestrator, ...):
        """Test that invalid operations are rejected."""
        # Arrange
        ...
        # Act
        result = orchestrator.do_something_invalid(...)
        # Assert
        assert not result.is_success
        assert result.status == ExpectedStatus

    def test_boundary_case(self, session, orchestrator, ...):
        """Test boundary conditions."""
        ...

    def test_concurrent_case(self, session, orchestrator, ...):
        """Test behavior under concurrency."""
        ...
```

### Checklist for New Tests

- [ ] Test is deterministic (no reliance on real time or random data without seed)
- [ ] Test is isolated (no shared state between tests)
- [ ] Test cleans up after itself (or uses fixtures that auto-rollback)
- [ ] Test has clear docstring explaining what invariant is being tested
- [ ] Test covers both positive and negative cases
- [ ] Test considers boundary conditions
- [ ] Test considers concurrency if applicable

---

## Invariant Test Mapping (R20 Compliance)

Per **R20**, every invariant must have tests in four categories: unit, concurrency, crash, and replay.

### Complete Invariant Coverage Matrix

| Invariant | Name | Primary Test File(s) |
|-----------|------|---------------------|
| **R1** | Event immutability | `audit/test_event_protocol_violation.py` |
| **R2** | Payload hash verification | `audit/test_event_protocol_violation.py` |
| **R3** | Idempotency key uniqueness | `posting/test_idempotency.py`, `concurrency/test_true_concurrency.py` |
| **R4** | Balance per currency | `posting/test_balance.py` |
| **R5** | Rounding line uniqueness | `adversarial/test_rounding_line_abuse.py`, `adversarial/test_rounding_invariant_gaps.py` |
| **R6** | Replay safety | `replay/test_r6_replay_safety.py` |
| **R7** | Transaction boundaries | `domain/test_pure_layer.py`, `crash/test_durability.py` |
| **R8** | Idempotency locking | `posting/test_r8_idempotency_locking.py` |
| **R9** | Sequence safety | `concurrency/test_r9_sequence_safety.py` |
| **R10** | Posted record immutability | `audit/test_immutability.py`, `audit/test_database_attacks.py` |
| **R11** | Audit chain integrity | `audit/test_chain_validation.py`, `audit/test_immutability.py` |
| **R12** | Closed period enforcement | `posting/test_period_lock.py`, `period/test_period_rules.py` |
| **R13** | Adjustment policy | `period/test_period_rules.py`, `audit/test_fiscal_period_immutability.py` |
| **R14** | No central dispatch | `architecture/test_open_closed.py` |
| **R15** | Open/closed compliance | `architecture/test_open_closed.py` |
| **R16** | ISO 4217 enforcement | `unit/test_currency.py` |
| **R17** | Precision-derived tolerance | `unit/test_currency.py`, `unit/test_money.py` |
| **R18** | Deterministic errors | `architecture/test_error_handling.py` |
| **R19** | No silent correction | `architecture/test_error_handling.py` |
| **R20** | Test class mapping | `architecture/test_r20_test_class_mapping.py` |

### Cross-Cutting Test Coverage

These test files provide coverage across multiple invariants:

| Test File | Categories Covered |
|-----------|-------------------|
| `concurrency/test_race_safety.py` | All invariants (race conditions) |
| `crash/test_durability.py` | All invariants (durability) |
| `replay/test_determinism.py` | All invariants (determinism) |
| `concurrency/test_stress.py` | R5, R9, R11 (high load) |
| `adversarial/test_pressure.py` | Multiple adversarial scenarios |
| `audit/test_database_attacks.py` | R10 (raw SQL bypass attempts) |

### Adding Tests for New Invariants

When adding a new invariant, you must:

1. **Document the invariant** in `finance_kernel/README.md`
2. **Add unit tests** proving local correctness
3. **Add concurrency tests** proving race safety
4. **Add crash tests** proving durability
5. **Add replay tests** proving determinism
6. **Update R20 mapping** in `architecture/test_r20_test_class_mapping.py`

```python
# Example: Adding R21 tests
class TestR21NewInvariant:
    """
    R21: [Description of new invariant]

    Tests prove this invariant holds under all conditions.
    """

    def test_r21_unit_correctness(self):
        """Unit test for R21."""
        ...

    def test_r21_concurrent_safety(self):
        """Concurrency test for R21."""
        ...

    def test_r21_crash_recovery(self):
        """Crash test for R21."""
        ...

    def test_r21_replay_determinism(self):
        """Replay test for R21."""
        ...
```
