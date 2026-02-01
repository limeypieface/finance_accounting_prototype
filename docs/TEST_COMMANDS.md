# Test Commands

Comprehensive test commands for the finance kernel project.
**122 test files** across **21 test directories**.

---

## Quick Reference

```bash
# Run ALL tests
pytest

# Run all tests with verbose output
pytest -v

# Run all tests with coverage
pytest --cov=finance_kernel --cov=finance_engines --cov=finance_services --cov=finance_modules --cov-report=term-missing

# Stop on first failure
pytest -x

# Run tests matching a keyword
pytest -k "idempotency"

# Run in parallel (requires pytest-xdist)
pytest -n auto
```

---

## By Category

### Unit Tests (Pure Logic, No DB)

```bash
# Value objects (Money, Currency)
pytest tests/unit/ -v

# Domain layer purity and logic (20 files)
pytest tests/domain/ -v

# Engine calculations - variance, allocation, matching, etc. (12 files)
pytest tests/engines/ -v

# Replay determinism and strategy governance (4 files)
pytest tests/replay/ -v

# Metamorphic equivalences (post+reverse, split/merge)
pytest tests/metamorphic/ -v
```

### Posting Pipeline

```bash
# Core posting (balance, idempotency, period lock)
pytest tests/posting/ -v

# Fiscal period rules
pytest tests/period/ -v

# Multi-currency (FX gain/loss, triangle conversions)
pytest tests/multicurrency/ -v
```

### Security & Immutability

```bash
# Audit trail and immutability - ORM + triggers (9 files)
pytest tests/audit/ -v

# Adversarial attack vectors (10 files)
pytest tests/adversarial/ -v

# SQL injection prevention
pytest tests/security/ -v

# Database-level security (requires PostgreSQL)
pytest tests/database_security/ -v
```

### Concurrency & Crash Safety

```bash
# Race conditions and concurrent posting (5 files)
pytest tests/concurrency/ -v

# Crash recovery and durability
pytest tests/crash/ -v

# Stress tests (high-load, extended timeout recommended)
pytest tests/concurrency/test_stress.py -v --timeout=600
```

### Architecture & Governance

```bash
# Architecture invariants - import boundaries, open/closed, R20 mapping (8 files)
pytest tests/architecture/ -v

# Fuzzing (Hypothesis property-based testing)
pytest tests/fuzzing/ -v
```

### ERP Modules

```bash
# All module tests (31 files)
pytest tests/modules/ -v

# Config validation
pytest tests/modules/test_config_schemas.py tests/modules/test_config_validation.py tests/modules/test_config_fuzzing.py -v

# Domain models
pytest tests/modules/test_model_immutability.py tests/modules/test_model_invariants.py tests/modules/test_boundary_conditions.py -v

# Economic profiles and guards
pytest tests/modules/test_profile_balance.py tests/modules/test_guard_execution.py -v

# Workflows
pytest tests/modules/test_workflow_transitions.py tests/modules/test_workflow_adversarial.py -v

# Gap coverage (blocked party, depreciation, returns, payment terms, etc.)
pytest tests/modules/test_blocked_party.py tests/modules/test_asset_depreciation.py tests/modules/test_returns.py tests/modules/test_payment_terms.py tests/modules/test_invoice_status.py tests/modules/test_cost_center.py tests/modules/test_landed_cost.py tests/modules/test_bank_reconciliation.py tests/modules/test_intercompany.py -v

# Module service integration tests
pytest tests/modules/test_ap_service.py tests/modules/test_ar_service.py tests/modules/test_assets_service.py tests/modules/test_cash_service.py tests/modules/test_contracts_service.py tests/modules/test_expense_service.py tests/modules/test_gl_service.py tests/modules/test_payroll_service.py tests/modules/test_procurement_service.py tests/modules/test_tax_service.py tests/modules/test_wip_service.py tests/modules/test_cross_module_flow.py -v
```

### Integration & Services

```bash
# End-to-end integration tests
pytest tests/integration/ -v

# Service-layer tests (link graph, party, contract)
pytest tests/services/ -v
```

### Demo & Logging

```bash
# Interactive demos (verbose output)
pytest tests/demo/ -v -s

# Logging configuration
pytest tests/test_logging.py -v
```

---

## By Invariant

```bash
# R1-R2: Event immutability & payload hash
pytest tests/audit/test_event_protocol_violation.py -v

# R3: Idempotency
pytest tests/posting/test_idempotency.py tests/concurrency/test_true_concurrency.py -v

# R4: Balance per currency
pytest tests/posting/test_balance.py -v

# R5: Rounding invariants
pytest tests/adversarial/test_rounding_line_abuse.py tests/adversarial/test_rounding_invariant_gaps.py -v

# R6: Replay safety
pytest tests/replay/test_r6_replay_safety.py -v

# R7: Transaction boundaries
pytest tests/domain/test_pure_layer.py tests/crash/test_durability.py -v

# R8: Idempotency locking
pytest tests/posting/test_r8_idempotency_locking.py -v

# R9: Sequence safety
pytest tests/concurrency/test_r9_sequence_safety.py -v

# R10: Posted record immutability
pytest tests/audit/test_immutability.py tests/audit/test_database_attacks.py -v

# R11: Audit chain integrity
pytest tests/audit/test_chain_validation.py -v

# R12-R13: Period enforcement
pytest tests/posting/test_period_lock.py tests/period/test_period_rules.py -v

# R14-R15: Open/closed principle
pytest tests/architecture/test_open_closed.py -v

# R16-R17: Currency and precision
pytest tests/unit/test_currency.py tests/unit/test_money.py -v

# R18-R19: Error handling
pytest tests/architecture/test_error_handling.py -v

# R20: Test class mapping
pytest tests/architecture/test_r20_test_class_mapping.py -v

# R21-R24: Replay determinism and strategy governance
pytest tests/replay/test_rule_version.py tests/domain/test_strategy_purity.py -v

# L1-L5: Economic link invariants
pytest tests/domain/test_economic_link.py tests/services/test_link_graph_service.py -v
```

---

## Smoke Tests

Quick verification before committing:

```bash
# Fast smoke test (~10 seconds)
pytest tests/unit/ tests/posting/test_balance.py tests/audit/test_immutability.py -v --tb=short

# Full security audit
pytest tests/security/ tests/database_security/ tests/adversarial/ -v

# Architecture compliance
pytest tests/architecture/ -v
```

---

## Test Directory Summary

| Directory | Files | Focus |
|-----------|-------|-------|
| `tests/adversarial/` | 10 | Attack vectors, tamper resistance |
| `tests/architecture/` | 8 | Import boundaries, governance, R20 |
| `tests/audit/` | 9 | Immutability, hash chain, triggers |
| `tests/concurrency/` | 5 | Race conditions, stress, sequence |
| `tests/crash/` | 2 | Durability, fault injection |
| `tests/database_security/` | 1 | PostgreSQL-level protection |
| `tests/demo/` | 1 | Interactive demonstrations |
| `tests/domain/` | 20 | Pure logic, schemas, profiles |
| `tests/engines/` | 12 | Calculation engines |
| `tests/fuzzing/` | 2 | Hypothesis property-based |
| `tests/integration/` | 2 | End-to-end pipeline |
| `tests/metamorphic/` | 1 | Mathematical equivalences |
| `tests/modules/` | 31 | ERP module tests |
| `tests/multicurrency/` | 2 | FX, triangle conversions |
| `tests/period/` | 1 | Fiscal period rules |
| `tests/posting/` | 4 | Core posting pipeline |
| `tests/replay/` | 4 | Replay determinism |
| `tests/security/` | 1 | SQL injection prevention |
| `tests/services/` | 3 | Service-layer tests |
| `tests/unit/` | 2 | Value objects |
| **Root** | 1 | Logging |
| **Total** | **122** | |

---

## Markers

```bash
# Skip PostgreSQL-specific tests
pytest -m "not postgres"

# Only PostgreSQL tests
pytest -m postgres

# Only slow tests
pytest -m slow

# Only stress tests (with extended timeout)
pytest -m stress --timeout=600
```
