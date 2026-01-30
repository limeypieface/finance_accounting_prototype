# Financial Reporting Module — Implementation Plan

**Date:** 2026-01-29
**Status:** Pending approval
**Scope:** GAP-04 from GAP_ANALYSIS.md — Full reporting module + comprehensive tests

---

## Objective

Build `finance_modules/reporting/` — a read-only financial reporting module that generates:

1. **Trial Balance** — formatted with comparative periods
2. **Balance Sheet** — ASC 210 / IAS 1 classified (current/non-current)
3. **Income Statement** — single-step and multi-step formats
4. **Cash Flow Statement** — indirect method (ASC 230 / IAS 7)
5. **Statement of Changes in Equity** — period-over-period movements
6. **Segment Report** — dimension-based filtering

Plus comprehensive tests that post diverse events across all 12 modules and verify that they translate into correct, balanced financial statements.

---

## Architecture

### Key Insight: This Module is Read-Only

Unlike AP/AR/Inventory modules which post journal entries, the reporting module only **reads** the ledger. This means:

- `profiles.py` — minimal (no accounting policies, no posting)
- `workflows.py` — minimal (no state machines)
- `service.py` — does NOT use `ModulePostingService` or `RoleResolver`
- No engine dependencies
- The core work is **pure functions** in `statements.py` transforming trial balance data → financial statements

### Data Sources

- `LedgerSelector.trial_balance(as_of_date, currency)` → `list[TrialBalanceRow]`
- `LedgerSelector.account_balance(account_id, as_of_date, currency)` → `list[AccountBalance]`
- `LedgerSelector.total_debits_credits(as_of_date, currency)` → `tuple[Decimal, Decimal]`
- `LedgerSelector.query(as_of_date, account_id, currency, dimensions)` → `list[LedgerLine]`
- `JournalSelector.get_entries_by_period(start, end, status)` → `list[JournalEntryDTO]`
- Account ORM model for classification metadata (`account_type`, `normal_balance`, `tags`, `code`)

### File Structure

```
finance_modules/reporting/
    __init__.py          # Module exports
    models.py            # Frozen dataclass DTOs for all report types
    config.py            # ReportingConfig (classification rules, display options)
    workflows.py         # Minimal stub (no state machines for reporting)
    profiles.py          # Minimal stub (no posting profiles)
    service.py           # ReportingService (orchestration over selectors)
    statements.py        # CORE: Pure functions transforming TB → statements

tests/reporting/
    __init__.py
    conftest.py                      # Reporting-specific fixtures
    test_pure_statements.py          # Pure function unit tests (NO database)
    test_models.py                   # DTO construction tests
    test_config.py                   # Config validation tests
    test_trial_balance_report.py     # TB integration tests
    test_balance_sheet_report.py     # BS integration tests
    test_income_statement_report.py  # IS integration tests
    test_cash_flow_report.py         # Cash flow integration tests
    test_equity_changes_report.py    # Equity changes integration tests
    test_cross_module_reporting.py   # Full cross-module flow tests
    test_report_invariants.py        # Accounting equation verification
```

---

## Phase 1: Models (`models.py`)

Define all frozen dataclass DTOs. This is the contract every other file depends on.

### Key Types

```python
# Enums
ReportType          # TRIAL_BALANCE, BALANCE_SHEET, INCOME_STATEMENT, CASH_FLOW, EQUITY_CHANGES, SEGMENT
IncomeStatementFormat  # SINGLE_STEP, MULTI_STEP
BalanceSheetFormat     # CLASSIFIED, UNCLASSIFIED

# Common
ReportMetadata      # report_type, entity_name, currency, as_of_date, generated_at, period_start/end, comparative_date

# Trial Balance
TrialBalanceLineItem  # account_id, code, name, account_type, debit_balance, credit_balance, net_balance
TrialBalanceReport    # metadata, lines, total_debits, total_credits, is_balanced, comparative_*

# Balance Sheet
BalanceSheetSection   # label, lines (tuple[TrialBalanceLineItem]), total
BalanceSheetReport    # metadata, format, current/non_current assets/liabilities, equity, totals, is_balanced, comparative

# Income Statement
IncomeStatementSection  # label, lines, total
IncomeStatementReport   # metadata, format, total_revenue, total_expenses, net_income
                        # Multi-step: revenue_section, cogs_section, gross_profit, operating_expense_section,
                        #             operating_income, other_income/expense, income_before_tax, tax_expense
                        # comparative

# Cash Flow
CashFlowSection       # label, lines (tuple[(description, amount)]), total
CashFlowStatementReport  # metadata, net_income, operating_adjustments, working_capital_changes,
                          # net_cash_from_operations, investing/financing activities,
                          # net_change_in_cash, beginning/ending_cash, cash_change_reconciles

# Equity Changes
EquityMovement        # description, amount
EquityChangesReport   # metadata, beginning_equity, movements, ending_equity,
                      # net_income, dividends_declared, other_changes, reconciles

# Segment
SegmentData           # segment_key, segment_value, trial_balance_lines, revenue, expenses, net_income, total_assets
SegmentReport         # metadata, dimension_name, segments, unallocated
```

---

## Phase 2: Config (`config.py`)

### `AccountClassification` Dataclass

Controls how accounts are sorted into report sections using **account code prefixes** (consistent with existing COA: 1xxx=assets, 2xxx=liabilities, 3xxx=equity, 4xxx=revenue, 5xxx-6xxx=expenses):

| Section | Default Prefixes |
|---------|-----------------|
| Current assets | `10xx`-`14xx` |
| Non-current assets | `15xx`-`19xx` |
| Current liabilities | `20xx`-`24xx` |
| Non-current liabilities | `25xx`-`29xx` |
| Equity | `30xx`-`33xx` |
| Revenue | `40xx`-`45xx` |
| COGS | `50xx` |
| Operating expenses | `51xx`-`59xx` |
| Other income/expense | `60xx`-`62xx` |
| Cash accounts | `1000`, `1020`, `1030`, `1040` |

### `ReportingConfig` Dataclass

- `classification: AccountClassification`
- `default_currency: str = "USD"`
- `entity_name: str = "Company"`
- `display_precision: int = 2`
- `include_zero_balances: bool = False`
- `include_inactive: bool = False`
- `with_defaults()` / `from_dict()` factory methods

---

## Phase 3: Pure Functions (`statements.py`) — THE CORE

This is the most important file. All functions are **pure** — no I/O, no database, no clock.

### Bridge Type: `AccountInfo`

```python
@dataclass(frozen=True)
class AccountInfo:
    account_id: UUID
    code: str
    name: str
    account_type: AccountType  # ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE
    normal_balance: NormalBalance  # DEBIT, CREDIT
    tags: tuple[str, ...] = ()
    parent_id: UUID | None = None
```

### Critical Helper: Natural Balance

```python
def compute_natural_balance(debit_total, credit_total, normal_balance) -> Decimal:
    """
    DEBIT-normal (ASSET, EXPENSE): debit_total - credit_total
    CREDIT-normal (LIABILITY, EQUITY, REVENUE): credit_total - debit_total

    Result is positive when account has its expected normal direction.
    """
```

This is the **foundational transformation** — the existing `TrialBalanceRow.balance` returns raw `debit - credit`, but financial statements need natural-balance-adjusted figures.

### Function Signatures

```python
# Helpers
compute_natural_balance(debit_total, credit_total, normal_balance) → Decimal
enrich_trial_balance(rows, accounts, config) → tuple[TrialBalanceLineItem, ...]
compute_net_income_from_tb(rows, accounts) → Decimal

# Report builders (all take TB rows + AccountInfo + config + metadata)
build_trial_balance(rows, accounts, config, metadata, comparative_rows?) → TrialBalanceReport
build_balance_sheet(rows, accounts, config, metadata, comparative_rows?) → BalanceSheetReport
build_income_statement(rows, accounts, config, metadata, format, comparative_rows?) → IncomeStatementReport
build_cash_flow_statement(current_rows, prior_rows, accounts, config, metadata) → CashFlowStatementReport
build_equity_changes(current_rows, prior_rows, accounts, config, metadata) → EquityChangesReport
build_segment_report(rows_by_segment, accounts, config, metadata, dimension_name) → SegmentReport

# Renderer
render_to_dict(report) → dict  # Decimal→str, UUID→str, date→ISO, nested DCs→dicts
```

### Classification Strategy

1. **Primary**: `AccountType` determines which financial statement an account belongs to
   - ASSET, LIABILITY, EQUITY → Balance Sheet
   - REVENUE, EXPENSE → Income Statement
2. **Secondary**: Account code prefix determines sub-section
   - `10xx`-`14xx` → Current Assets; `15xx`+ → Non-Current Assets
   - `50xx` → COGS; `51xx`-`59xx` → Operating Expenses; `60xx`+ → Other

### Cash Flow (Indirect Method)

Requires **two** trial balance snapshots to compute balance changes:

1. Start with net income (from IS computation)
2. Add back non-cash expenses (identified by account tags: `depreciation`, `amortization`)
3. Working capital changes: `prior_balance - current_balance` for current assets/liabilities
   - Increase in AR → cash decrease (negative adjustment)
   - Increase in AP → cash increase (positive adjustment)
4. Investing: changes in non-current assets
5. Financing: changes in non-current liabilities + equity (excluding retained earnings)

---

## Phase 4: Service (`service.py`)

### `ReportingService` Constructor

```python
def __init__(self, session, clock=None, config=None):
    # Unlike other modules: NO role_resolver, NO ModulePostingService
    self._session = session
    self._clock = clock or SystemClock()
    self._config = config or ReportingConfig.with_defaults()
    self._ledger = LedgerSelector(session)
    self._journal = JournalSelector(session)
```

### Internal Method: `_load_accounts()`

Queries `Account` ORM → converts to `dict[UUID, AccountInfo]` frozen dataclasses for pure functions.

### Public API

```python
trial_balance(as_of_date, currency?, comparative_date?) → TrialBalanceReport
balance_sheet(as_of_date, currency?, format?, comparative_date?) → BalanceSheetReport
income_statement(period_start, period_end, currency?, format?, comparative_start?, comparative_end?) → IncomeStatementReport
cash_flow_statement(period_start, period_end, prior_period_end?, currency?) → CashFlowStatementReport
equity_changes(period_start, period_end, prior_period_end?, currency?) → EquityChangesReport
segment_report(as_of_date, dimension_name, currency?) → SegmentReport
to_dict(report) → dict
```

Each method: loads accounts, builds metadata, queries ledger, delegates to pure function, returns DTO.

---

## Phase 5: Stubs (`profiles.py`, `workflows.py`)

Minimal files maintaining the 6-file module convention:

- `profiles.py`: `REPORTING_PROFILES = {}`, `register()` is a no-op
- `workflows.py`: Empty — reporting has no state machines

---

## Phase 6: Exports (`__init__.py`)

Exports: `ReportingService`, all report DTOs, `ReportingConfig`, `ReportMetadata`.

---

## Phase 7: Tests

### Test Categories

#### A. Pure Function Tests (`test_pure_statements.py`) — No Database

~30 test cases covering every pure function with synthetic data:

| Test Class | Key Tests |
|-----------|-----------|
| `TestComputeNaturalBalance` | Debit-normal positive/negative, credit-normal positive/negative, zero, precision |
| `TestEnrichTrialBalance` | All rows enriched, zero-balance filtering, sorting, unknown accounts skipped |
| `TestBuildTrialBalance` | Basic TB, totals correct, is_balanced, comparative, empty ledger |
| `TestClassifyForBalanceSheet` | Assets/liabilities/equity classified, current vs non-current, revenue/expense excluded |
| `TestBuildBalanceSheet` | A=L+E equation, net income in equity, classified format, comparative |
| `TestComputeNetIncome` | Positive/negative/zero, only revenue/expense counted |
| `TestBuildIncomeStatement` | Single-step, multi-step, gross profit, operating income, comparative |
| `TestBuildCashFlowStatement` | Structure, operating/investing/financing, cash reconciliation |
| `TestBuildEquityChanges` | Beginning→ending, net income, dividends |
| `TestRenderToDict` | Decimal→str, UUID→str, date→ISO, nested DCs, None, tuples→lists |

#### B. Integration Tests (With Database)

Each report type gets its own test file (~5 tests each):

- `test_trial_balance_report.py` — Post events, generate TB, verify totals
- `test_balance_sheet_report.py` — Post events, generate BS, verify A=L+E
- `test_income_statement_report.py` — Post events, generate IS, verify NI
- `test_cash_flow_report.py` — Post events in two periods, generate CF, verify reconciliation
- `test_equity_changes_report.py` — Post events, generate equity changes, verify reconciliation

#### C. Cross-Module Flow Tests (`test_cross_module_reporting.py`)

**The heart of the testing requirement.** Posts events from ALL modules and verifies complete reports:

| Test | Events Posted | Report Verified |
|------|--------------|----------------|
| `test_full_business_cycle_trial_balance` | AP, AR, inventory, payroll, cash, GL closing | TB debits = credits |
| `test_full_cycle_balance_sheet` | Same | BS: A = L + E |
| `test_full_cycle_income_statement` | Same | IS: NI = Revenue - Expenses |
| `test_ap_invoice_affects_balance_sheet` | AP invoice | Liabilities increase on BS |
| `test_ar_invoice_affects_income_statement` | AR invoice | Revenue appears on IS |
| `test_inventory_receipt_affects_balance_sheet` | Inventory receipt | Assets increase on BS |
| `test_payroll_affects_income_statement` | Payroll | Salary expense on IS |
| `test_gl_closing_affects_equity` | Year-end close | Retained earnings updated |
| `test_comparative_periods` | Events in two periods | Side-by-side comparison correct |

#### D. Invariant Tests (`test_report_invariants.py`)

Cross-report consistency verification:

| Test | Invariant |
|------|-----------|
| `test_assets_equal_liabilities_plus_equity` | BS equation holds |
| `test_trial_balance_debits_equal_credits` | TB balanced |
| `test_is_net_income_equals_equity_change` | IS NI matches equity statement NI |
| `test_cash_flow_reconciles` | CF ending_cash = beginning + net_change |
| `test_all_reports_consistent` | All reports generated from same data agree |

#### E. Model & Config Tests

- `test_models.py` — DTO construction, immutability, defaults
- `test_config.py` — Config validation, `with_defaults()`, `from_dict()`

---

## Implementation Order

| Step | File | Description | Dependencies |
|------|------|-------------|-------------|
| 1 | `models.py` | All frozen dataclass DTOs | None |
| 2 | `config.py` | `ReportingConfig`, `AccountClassification` | None |
| 3 | `statements.py` | Pure transformation functions | `models.py`, `config.py` |
| 4 | `service.py` | Orchestration service | `statements.py`, kernel selectors |
| 5 | `profiles.py` | Minimal stub | None |
| 6 | `workflows.py` | Minimal stub | None |
| 7 | `__init__.py` | Module exports | All above |
| 8 | `tests/reporting/conftest.py` | Test fixtures | Module + kernel fixtures |
| 9 | `tests/reporting/test_pure_statements.py` | Pure unit tests | `statements.py` |
| 10 | `tests/reporting/test_models.py` | DTO tests | `models.py` |
| 11 | `tests/reporting/test_config.py` | Config tests | `config.py` |
| 12 | `tests/reporting/test_*_report.py` | Per-report integration | Service + fixtures |
| 13 | `tests/reporting/test_cross_module_reporting.py` | Cross-module flow | All modules + service |
| 14 | `tests/reporting/test_report_invariants.py` | Accounting invariants | All above |

---

## Key Design Decisions

1. **`statements.py` as separate pure module** — Follows `finance_kernel/domain/` purity pattern. All transformations independently testable without DB.

2. **`AccountInfo` bridge type** — Pure functions can't import ORM models. Service converts `Account` ORM → `AccountInfo` frozen dataclass.

3. **Natural balance adjustment** — `compute_natural_balance()` is the foundational transformation. Raw `TrialBalanceRow.balance` is `debit - credit`; reporting needs natural-balance-adjusted figures.

4. **Classification by AccountType + code prefix** — Primary classification by `AccountType` enum, sub-classification by account code prefix. Matches existing COA structure (1xxx=assets, 2xxx=liabilities, etc.).

5. **Cash flow uses two TB snapshots** — Indirect method computes balance changes between periods. Consistent with "no stored balances" philosophy.

6. **No `role_resolver` in constructor** — Read-only module doesn't post. Constructor takes `session`, `clock`, `config` only.

7. **Built-in verification flags** — Every report DTO has `is_balanced` / `reconciles` flag for self-verification.

---

## Files Modified/Created

### New Files (15)

```
finance_modules/reporting/__init__.py
finance_modules/reporting/models.py
finance_modules/reporting/config.py
finance_modules/reporting/workflows.py
finance_modules/reporting/profiles.py
finance_modules/reporting/service.py
finance_modules/reporting/statements.py

tests/reporting/__init__.py
tests/reporting/conftest.py
tests/reporting/test_pure_statements.py
tests/reporting/test_models.py
tests/reporting/test_config.py
tests/reporting/test_cross_module_reporting.py
tests/reporting/test_report_invariants.py
tests/reporting/test_balance_sheet_report.py
```

### Modified Files (1)

```
finance_modules/__init__.py   — Add reporting module to register_all_modules()
```

### Existing Files Used (Not Modified)

```
finance_kernel/selectors/ledger_selector.py   — Primary data source
finance_kernel/selectors/journal_selector.py  — Journal detail queries
finance_kernel/models/account.py              — Account types and classification
tests/conftest.py                              — module_accounts, module_role_resolver fixtures
```

---

## Verification

After implementation:

```bash
# Run all reporting tests
python3 -m pytest tests/reporting/ -v --tb=short

# Run full test suite to verify no regressions
python3 -m pytest tests/ -v --tb=short
```

Expected: ~80+ new tests, all passing, zero regressions.
