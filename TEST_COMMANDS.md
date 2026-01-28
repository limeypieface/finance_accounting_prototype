# Finance Kernel Test Commands

Copy and paste these commands into your terminal. All commands assume you're in the project root directory.

```bash
cd /Users/andrewdimitruk/Documents/Project\ -\ finance\ and\ accounting\ V1
```

---

## Quick Reference

| Command | What it runs |
|---------|--------------|
| `pytest` | All 970 tests |
| `pytest -x` | All tests, stop on first failure |
| `pytest -v` | Verbose output |
| `pytest --tb=short` | Shorter tracebacks |
| `pytest -k "keyword"` | Tests matching keyword |

---

## 1. RUN ALL TESTS

```bash
# Run everything (970 tests, ~2-3 minutes)
pytest

# Run everything with verbose output
pytest -v

# Run everything, stop on first failure
pytest -x

# Run with coverage report
pytest --cov=finance_kernel --cov-report=term-missing
```

---

## 2. BY CATEGORY

### Unit Tests (Core Primitives)
**Tests Money and Currency value objects - the foundation of all financial calculations.**

```bash
# All unit tests
pytest tests/unit/

# Money arithmetic, rounding, precision
pytest tests/unit/test_money.py

# Currency validation and codes
pytest tests/unit/test_currency.py
```

### Domain Tests (Business Logic)
**Tests domain models, strategies, and core accounting rules.**

```bash
# All domain tests
pytest tests/domain/

# Pure functional layer (no side effects)
pytest tests/domain/test_pure_layer.py

# Posting strategy purity (R22 - strategies must be pure functions)
pytest tests/domain/test_strategy_purity.py

# Dimension integrity (cost centers, departments, etc.)
pytest tests/domain/test_dimension_integrity.py

# Economic profile and event interpretation
pytest tests/domain/test_economic_profile.py
pytest tests/domain/test_interpretation.py
pytest tests/domain/test_interpretation_invariants.py

# Subledger control accounts
pytest tests/domain/test_subledger_control.py

# Event schema validation
pytest tests/domain/test_event_schema.py

# Ledger engine operations
pytest tests/domain/test_ledger_engine.py

# Policy registry
pytest tests/domain/test_policy_registry.py

# Reference data snapshots
pytest tests/domain/test_reference_snapshot.py

# Real-world scenario: inventory receipt
pytest tests/domain/test_real_world_inventory_receipt.py

# EconomicLink domain primitive (artifact relationships)
pytest tests/domain/test_economic_link.py
```

### Service Tests (Link Graph)
**Tests LinkGraphService - graph traversal, cycle detection, unconsumed value.**

```bash
# All service tests
pytest tests/services/

# EconomicLink graph operations
pytest tests/services/test_link_graph_service.py
```

### Posting Tests (Journal Entry Creation)
**Tests the posting pipeline - balance enforcement, idempotency, period locks.**

```bash
# All posting tests
pytest tests/posting/

# R12: Debits must equal credits
pytest tests/posting/test_balance.py

# R8: Idempotency (same event = same result)
pytest tests/posting/test_idempotency.py
pytest tests/posting/test_r8_idempotency_locking.py

# Period locks (can't post to closed periods)
pytest tests/posting/test_period_lock.py
```

### Audit Tests (Immutability & Chain Integrity)
**Tests R10 compliance - posted records are immutable, audit trail is unbreakable.**

```bash
# All audit tests
pytest tests/audit/

# R10: Posted journal entries cannot be modified
pytest tests/audit/test_immutability.py

# Account structural immutability
pytest tests/audit/test_account_immutability.py

# Fiscal period immutability
pytest tests/audit/test_fiscal_period_immutability.py

# Exchange rate immutability
pytest tests/audit/test_exchange_rate_immutability.py

# Audit chain hash validation
pytest tests/audit/test_chain_validation.py

# Event protocol violations
pytest tests/audit/test_event_protocol_violation.py

# Database-level attack prevention
pytest tests/audit/test_database_attacks.py

# Failed posting audit trails
pytest tests/audit/test_failed_posting_audit.py
```

### Replay Tests (Determinism & Reproducibility)
**Tests R6 compliance - replaying events produces identical results.**

```bash
# All replay tests
pytest tests/replay/

# Deterministic replay (same input = same output)
pytest tests/replay/test_determinism.py

# R6 replay safety
pytest tests/replay/test_r6_replay_safety.py

# Rule version tracking (R21-R24)
pytest tests/replay/test_rule_version.py

# Schema backward compatibility
pytest tests/replay/test_schema_backward_compat.py
```

### Concurrency Tests (Race Conditions & Thread Safety)
**Tests R9 compliance - concurrent operations are safe.**

```bash
# All concurrency tests
pytest tests/concurrency/

# R9 sequence number safety
pytest tests/concurrency/test_r9_sequence_safety.py

# Race condition prevention
pytest tests/concurrency/test_race_safety.py

# True multi-threaded concurrency
pytest tests/concurrency/test_true_concurrency.py

# Period close race conditions
pytest tests/concurrency/test_period_close_race.py

# Stress testing under load
pytest tests/concurrency/test_stress.py
```

### Adversarial Tests (Attack Vectors)
**Tests that attempt to break invariants through malicious inputs.**

```bash
# All adversarial tests
pytest tests/adversarial/

# Prevent deletion of accounts in use
pytest tests/adversarial/test_account_deletion_protection.py

# Account hierarchy integrity
pytest tests/adversarial/test_account_hierarchy_integrity.py

# Fiscal period boundary attacks
pytest tests/adversarial/test_fiscal_period_boundary_attack.py

# Journal line modification attempts
pytest tests/adversarial/test_journal_line_modification.py

# Orchestrator attack vectors
pytest tests/adversarial/test_orchestrator_attack_vectors.py

# System pressure testing
pytest tests/adversarial/test_pressure.py

# Producer field immutability
pytest tests/adversarial/test_producer_immutability.py

# Rounding account deletion protection
pytest tests/adversarial/test_rounding_account_deletion.py

# Rounding invariant gap attacks
pytest tests/adversarial/test_rounding_invariant_gaps.py

# Rounding line abuse attempts
pytest tests/adversarial/test_rounding_line_abuse.py
```

### Database Security Tests (PostgreSQL-Level Protection)
**Tests database triggers and constraints that protect against raw SQL attacks.**

```bash
# All database security tests (REQUIRES POSTGRESQL)
pytest tests/database_security/

# Transaction isolation, constraint bypass, rollback safety
pytest tests/database_security/test_database_invariants.py
```

### Security Tests (SQL Injection)
**Tests for SQL injection vulnerabilities in the codebase.**

```bash
# All security tests
pytest tests/security/

# SQL injection prevention
pytest tests/security/test_sql_injection.py
```

### Crash Recovery Tests (Durability)
**Tests ACID properties and recovery from failures.**

```bash
# All crash tests
pytest tests/crash/

# Durability guarantees
pytest tests/crash/test_durability.py

# Fault injection scenarios
pytest tests/crash/test_fault_injection.py
```

### Engines Tests (Specialized Accounting Engines)
**Tests for AP/AR aging, allocations, matching, tax, variance.**

```bash
# All engine tests
pytest tests/engines/

# AP/AR aging calculations
pytest tests/engines/test_aging.py

# Cost allocation engine
pytest tests/engines/test_allocation.py

# Transaction matching (payments to invoices)
pytest tests/engines/test_matching.py

# Subledger operations
pytest tests/engines/test_subledger.py

# Tax calculations
pytest tests/engines/test_tax.py

# Variance analysis
pytest tests/engines/test_variance.py
```

### Multi-Currency Tests
**Tests currency conversion and triangular arbitrage prevention.**

```bash
# Multi-currency tests
pytest tests/multicurrency/

# Triangle conversion consistency (USD->EUR->GBP = USD->GBP)
pytest tests/multicurrency/test_triangle_conversions.py
```

### Period Tests
**Tests fiscal period rules and boundaries.**

```bash
# Period rule tests
pytest tests/period/test_period_rules.py
```

### Metamorphic Tests
**Tests that verify equivalent operations produce equivalent results.**

```bash
# Equivalence testing
pytest tests/metamorphic/test_equivalence.py
```

### Fuzzing Tests
**Property-based testing with random inputs.**

```bash
# Adversarial fuzzing
pytest tests/fuzzing/test_adversarial.py

# NOTE: test_hypothesis_fuzzing.py has a dependency issue
```

### Architecture Tests
**Tests that enforce architectural rules and coding standards.**

```bash
# All architecture tests
pytest tests/architecture/

# Actor validation (all operations require actor_id)
pytest tests/architecture/test_actor_validation.py

# Error handling patterns
pytest tests/architecture/test_error_handling.py

# Open/closed principle compliance
pytest tests/architecture/test_open_closed.py

# Primitive reuse (no duplicate value objects)
pytest tests/architecture/test_primitive_reuse.py

# R20 test class mapping verification
pytest tests/architecture/test_r20_test_class_mapping.py
```

---

## 3. BY INVARIANT RULE

### R6 - Replay Determinism
```bash
pytest tests/replay/ -v
```

### R8 - Idempotency
```bash
pytest tests/posting/test_idempotency.py tests/posting/test_r8_idempotency_locking.py -v
```

### R9 - Sequence Safety
```bash
pytest tests/concurrency/test_r9_sequence_safety.py -v
```

### R10 - Immutability
```bash
pytest tests/audit/test_immutability.py tests/audit/test_account_immutability.py tests/audit/test_fiscal_period_immutability.py tests/audit/test_exchange_rate_immutability.py -v
```

### R12 - Balance Enforcement
```bash
pytest tests/posting/test_balance.py tests/database_security/test_database_invariants.py::TestConstraintBypass -v
```

### R21-R24 - Strategy Governance
```bash
pytest tests/replay/test_rule_version.py tests/domain/test_strategy_purity.py -v
```

---

## 4. POSTGRESQL-SPECIFIC TESTS

These tests require a running PostgreSQL database with triggers installed.

```bash
# All PostgreSQL tests
pytest tests/database_security/ tests/concurrency/ -v

# Database-level invariant enforcement
pytest tests/database_security/test_database_invariants.py -v

# Specific test classes:
pytest tests/database_security/test_database_invariants.py::TestTransactionIsolation -v
pytest tests/database_security/test_database_invariants.py::TestConstraintBypass -v
pytest tests/database_security/test_database_invariants.py::TestRollbackSafety -v
pytest tests/database_security/test_database_invariants.py::TestAuditAtomicity -v
pytest tests/database_security/test_database_invariants.py::TestConcurrentTriggerRaces -v
```

---

## 5. QUICK SMOKE TESTS

Fast subset to verify basic functionality:

```bash
# Core primitives only (~5 seconds)
pytest tests/unit/ -v

# Balance + Idempotency (~10 seconds)
pytest tests/posting/test_balance.py tests/posting/test_idempotency.py -v

# Critical invariants (~30 seconds)
pytest tests/unit/ tests/posting/test_balance.py tests/audit/test_immutability.py tests/replay/test_determinism.py -v
```

---

## 6. DEBUGGING OPTIONS

```bash
# Show print statements
pytest -s

# Show local variables on failure
pytest -l

# Drop into debugger on failure
pytest --pdb

# Run only failed tests from last run
pytest --lf

# Run failed tests first, then others
pytest --ff

# Stop after N failures
pytest --maxfail=3

# Show slowest 10 tests
pytest --durations=10
```

---

## 7. MARKERS AND FILTERING

```bash
# Run tests matching a keyword
pytest -k "immutability"
pytest -k "balance"
pytest -k "concurrent"
pytest -k "injection"

# Exclude certain tests
pytest -k "not slow"
pytest -k "not postgres"

# Run specific test by full path
pytest tests/audit/test_immutability.py::TestPostedJournalEntryImmutability::test_cannot_modify_posted_entry
```

---

## 8. DEMO TESTS (Learning & Verification)

Interactive demonstrations with verbose output to understand how the system works.

**Requires PostgreSQL** - these tests run against the real database with triggers installed.

```bash
# All demo tests (use -s to see output!)
pytest tests/demo/ -v -s

# Full AP workflow demonstration
# Shows: PO → Receipts → Invoices → Payment/CreditMemo graph traversal
pytest tests/demo/test_economic_link_demo.py::TestEconomicLinkDemo -v -s

# Cycle detection demonstration (L3 invariant)
# Shows: A→B→C chain, then C→A rejected as cycle
pytest tests/demo/test_economic_link_demo.py::TestCycleDetectionDemo -v -s

# Max children enforcement (single reversal rule)
# Shows: Entry→Reversal1 allowed, Entry→Reversal2 rejected
pytest tests/demo/test_economic_link_demo.py::TestMaxChildrenDemo -v -s
```

**Note:** The `-s` flag is required to see the verbose output that explains each step.

---

## Test Count Summary

| Category | Tests | Description |
|----------|-------|-------------|
| unit | ~50 | Money, Currency primitives |
| domain | ~150 | Business logic, strategies |
| posting | ~80 | Journal entry creation |
| audit | ~120 | Immutability, audit trail |
| replay | ~60 | Determinism, reproducibility |
| concurrency | ~100 | Thread safety, race conditions |
| adversarial | ~150 | Attack vector prevention |
| database_security | ~26 | PostgreSQL trigger tests |
| security | ~50 | SQL injection prevention |
| crash | ~40 | ACID, durability |
| engines | ~60 | Specialized engines |
| architecture | ~40 | Code standards |
| demo | 3 | Interactive learning demos |
| other | ~44 | Fuzzing, metamorphic, period |
| **TOTAL** | **~973** | |

---

## Recommended Test Runs

### Before Committing
```bash
pytest tests/unit/ tests/posting/test_balance.py tests/audit/test_immutability.py -v --tb=short
```

### Before PR
```bash
pytest --tb=short
```

### Full Validation
```bash
pytest -v --tb=short --durations=20
```

### Security Audit
```bash
pytest tests/security/ tests/database_security/ tests/adversarial/ -v
```
