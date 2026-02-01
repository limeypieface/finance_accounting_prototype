"""
Pure financial statement transformation functions.

These functions transform trial balance data and account metadata
into structured financial statements. ZERO I/O. ZERO side effects.

All monetary values are Decimal. All inputs/outputs are frozen dataclasses.

Functions in this module follow the finance_kernel/domain/ purity convention:
- No database access
- No clock access
- No file I/O
- Deterministic: same inputs always produce same outputs
"""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.models.account import AccountType, NormalBalance
from finance_kernel.selectors.ledger_selector import TrialBalanceRow
from finance_modules.reporting.config import ReportingConfig
from finance_modules.reporting.models import (
    BalanceSheetFormat,
    BalanceSheetReport,
    BalanceSheetSection,
    CashFlowLineItem,
    CashFlowSection,
    CashFlowStatementReport,
    EquityChangesReport,
    EquityMovement,
    IncomeStatementFormat,
    IncomeStatementReport,
    IncomeStatementSection,
    ReportMetadata,
    SegmentData,
    SegmentReport,
    TrialBalanceLineItem,
    TrialBalanceReport,
)

# =========================================================================
# Bridge type: account metadata for pure functions
# =========================================================================


@dataclasses.dataclass(frozen=True)
class AccountInfo:
    """
    Snapshot of account metadata needed for classification.

    This is the bridge between the ORM layer (Account model) and
    the pure transformation functions. The service converts Account
    objects to AccountInfo before calling any function here.
    """

    account_id: UUID
    code: str
    name: str
    account_type: AccountType
    normal_balance: NormalBalance
    tags: tuple[str, ...] = ()
    parent_id: UUID | None = None


# =========================================================================
# Helpers
# =========================================================================


def compute_natural_balance(
    debit_total: Decimal,
    credit_total: Decimal,
    normal_balance: NormalBalance,
) -> Decimal:
    """
    Compute balance adjusted for normal balance side.

    DEBIT-normal (ASSET, EXPENSE): balance = debit_total - credit_total
    CREDIT-normal (LIABILITY, EQUITY, REVENUE): balance = credit_total - debit_total

    Result is positive when account has its expected normal direction.
    """
    if normal_balance == NormalBalance.DEBIT:
        return debit_total - credit_total
    return credit_total - debit_total


def enrich_trial_balance(
    rows: list[TrialBalanceRow],
    accounts: dict[UUID, AccountInfo],
    config: ReportingConfig,
) -> tuple[TrialBalanceLineItem, ...]:
    """
    Convert raw TrialBalanceRows to enriched TrialBalanceLineItems.

    Applies natural-balance adjustment and optionally filters zero balances.
    Sorted by account code for consistent presentation.
    """
    items: list[TrialBalanceLineItem] = []
    for row in rows:
        acct = accounts.get(row.account_id)
        if acct is None:
            continue

        natural = compute_natural_balance(
            row.debit_total, row.credit_total, acct.normal_balance,
        )

        if not config.include_zero_balances and natural == Decimal("0"):
            continue

        items.append(
            TrialBalanceLineItem(
                account_id=row.account_id,
                account_code=row.account_code,
                account_name=row.account_name,
                account_type=acct.account_type.value,
                debit_balance=row.debit_total,
                credit_balance=row.credit_total,
                net_balance=natural,
            )
        )

    return tuple(sorted(items, key=lambda x: x.account_code))


def compute_net_income_from_tb(
    rows: list[TrialBalanceRow],
    accounts: dict[UUID, AccountInfo],
) -> Decimal:
    """
    Compute net income from trial balance.

    Net income = sum(REVENUE natural balances) - sum(EXPENSE natural balances)
    Only REVENUE and EXPENSE accounts are considered.
    """
    revenue = Decimal("0")
    expense = Decimal("0")
    for row in rows:
        acct = accounts.get(row.account_id)
        if acct is None:
            continue
        natural = compute_natural_balance(
            row.debit_total, row.credit_total, acct.normal_balance,
        )
        if acct.account_type == AccountType.REVENUE:
            revenue += natural
        elif acct.account_type == AccountType.EXPENSE:
            expense += natural
    return revenue - expense


def _sum_natural_balances(
    items: tuple[TrialBalanceLineItem, ...],
) -> Decimal:
    """Sum the natural balances of a set of line items."""
    return sum((item.net_balance for item in items), Decimal("0"))


def _make_section(
    label: str,
    items: list[TrialBalanceLineItem],
) -> BalanceSheetSection:
    """Create a balance sheet section from items."""
    t = tuple(sorted(items, key=lambda x: x.account_code))
    return BalanceSheetSection(
        label=label,
        lines=t,
        total=_sum_natural_balances(t),
    )


def _make_is_section(
    label: str,
    items: list[TrialBalanceLineItem],
) -> IncomeStatementSection:
    """Create an income statement section from items."""
    t = tuple(sorted(items, key=lambda x: x.account_code))
    return IncomeStatementSection(
        label=label,
        lines=t,
        total=_sum_natural_balances(t),
    )


# =========================================================================
# 1. TRIAL BALANCE
# =========================================================================


def build_trial_balance(
    rows: list[TrialBalanceRow],
    accounts: dict[UUID, AccountInfo],
    config: ReportingConfig,
    metadata: ReportMetadata,
    comparative_rows: list[TrialBalanceRow] | None = None,
) -> TrialBalanceReport:
    """Build a formatted trial balance report with optional comparative period."""
    items = enrich_trial_balance(rows, accounts, config)

    total_debits = sum((item.debit_balance for item in items), Decimal("0"))
    total_credits = sum((item.credit_balance for item in items), Decimal("0"))

    comparative_items = None
    comp_debits = None
    comp_credits = None
    if comparative_rows is not None:
        comparative_items = enrich_trial_balance(comparative_rows, accounts, config)
        comp_debits = sum(
            (item.debit_balance for item in comparative_items), Decimal("0"),
        )
        comp_credits = sum(
            (item.credit_balance for item in comparative_items), Decimal("0"),
        )

    return TrialBalanceReport(
        metadata=metadata,
        lines=items,
        total_debits=total_debits,
        total_credits=total_credits,
        is_balanced=(total_debits == total_credits),
        comparative_lines=comparative_items,
        comparative_total_debits=comp_debits,
        comparative_total_credits=comp_credits,
    )


# =========================================================================
# 2. BALANCE SHEET
# =========================================================================


def classify_for_balance_sheet(
    items: tuple[TrialBalanceLineItem, ...],
    accounts: dict[UUID, AccountInfo],
    config: ReportingConfig,
) -> dict[str, list[TrialBalanceLineItem]]:
    """
    Classify trial balance items into balance sheet sections.

    Primary: AccountType determines BS vs IS
    Secondary: Account code prefix determines current vs non-current

    Returns dict with keys:
        current_assets, non_current_assets,
        current_liabilities, non_current_liabilities,
        equity
    """
    clf = config.classification
    result: dict[str, list[TrialBalanceLineItem]] = {
        "current_assets": [],
        "non_current_assets": [],
        "current_liabilities": [],
        "non_current_liabilities": [],
        "equity": [],
    }

    for item in items:
        acct = accounts.get(item.account_id)
        if acct is None:
            continue

        if acct.account_type == AccountType.ASSET:
            if clf.matches_prefix(item.account_code, clf.non_current_asset_prefixes):
                result["non_current_assets"].append(item)
            else:
                result["current_assets"].append(item)
        elif acct.account_type == AccountType.LIABILITY:
            if clf.matches_prefix(
                item.account_code, clf.non_current_liability_prefixes,
            ):
                result["non_current_liabilities"].append(item)
            else:
                result["current_liabilities"].append(item)
        elif acct.account_type == AccountType.EQUITY:
            result["equity"].append(item)
        # REVENUE and EXPENSE accounts are excluded from the balance sheet

    return result


def build_balance_sheet(
    rows: list[TrialBalanceRow],
    accounts: dict[UUID, AccountInfo],
    config: ReportingConfig,
    metadata: ReportMetadata,
    comparative_rows: list[TrialBalanceRow] | None = None,
) -> BalanceSheetReport:
    """
    Build a classified balance sheet (ASC 210 / IAS 1).

    Classification logic:
    1. Filter TB to ASSET, LIABILITY, EQUITY accounts
    2. Subdivide ASSET into current/non-current by code prefix
    3. Subdivide LIABILITY into current/non-current by code prefix
    4. EQUITY section includes retained earnings
    5. Net income for the current period is added to equity
    6. Verify A = L + E
    """
    # Enrich with natural balances
    items = enrich_trial_balance(rows, accounts, config)

    # Add net income as a synthetic equity line
    net_income = compute_net_income_from_tb(rows, accounts)

    # Classify
    classified = classify_for_balance_sheet(items, accounts, config)

    # Build sections
    current_assets = _make_section("Current Assets", classified["current_assets"])
    non_current_assets = _make_section(
        "Non-Current Assets", classified["non_current_assets"],
    )
    total_assets = current_assets.total + non_current_assets.total

    current_liabilities = _make_section(
        "Current Liabilities", classified["current_liabilities"],
    )
    non_current_liabilities = _make_section(
        "Non-Current Liabilities", classified["non_current_liabilities"],
    )
    total_liabilities = current_liabilities.total + non_current_liabilities.total

    # Equity includes the net income for the period (retained earnings effect)
    equity_items = list(classified["equity"])
    equity_total = _sum_natural_balances(tuple(equity_items)) + net_income
    equity_section = BalanceSheetSection(
        label="Equity",
        lines=tuple(sorted(equity_items, key=lambda x: x.account_code)),
        total=equity_total,
    )

    total_l_and_e = total_liabilities + equity_total

    # Comparative
    comparative = None
    if comparative_rows is not None:
        comparative = build_balance_sheet(
            comparative_rows, accounts, config, metadata,
        )

    return BalanceSheetReport(
        metadata=metadata,
        format=BalanceSheetFormat.CLASSIFIED,
        current_assets=current_assets,
        non_current_assets=non_current_assets,
        total_assets=total_assets,
        current_liabilities=current_liabilities,
        non_current_liabilities=non_current_liabilities,
        total_liabilities=total_liabilities,
        equity=equity_section,
        total_equity=equity_total,
        total_liabilities_and_equity=total_l_and_e,
        is_balanced=(total_assets == total_l_and_e),
        comparative=comparative,
    )


# =========================================================================
# 3. INCOME STATEMENT
# =========================================================================


def classify_for_income_statement(
    items: tuple[TrialBalanceLineItem, ...],
    accounts: dict[UUID, AccountInfo],
    config: ReportingConfig,
) -> dict[str, list[TrialBalanceLineItem]]:
    """
    Classify trial balance items into income statement sections.

    For multi-step: revenue, cogs, operating_expenses, other_income, other_expenses
    For single-step: revenue, expenses
    """
    clf = config.classification
    result: dict[str, list[TrialBalanceLineItem]] = {
        "revenue": [],
        "cogs": [],
        "operating_expenses": [],
        "other_income": [],
        "other_expenses": [],
    }

    for item in items:
        acct = accounts.get(item.account_id)
        if acct is None:
            continue

        if acct.account_type == AccountType.REVENUE:
            # Check if this is "other income" (gains, interest, etc.)
            if clf.matches_prefix(item.account_code, clf.other_income_prefixes):
                result["other_income"].append(item)
            else:
                result["revenue"].append(item)
        elif acct.account_type == AccountType.EXPENSE:
            if clf.matches_prefix(item.account_code, clf.cogs_prefixes):
                result["cogs"].append(item)
            elif clf.matches_prefix(item.account_code, clf.other_expense_prefixes):
                result["other_expenses"].append(item)
            else:
                result["operating_expenses"].append(item)
        # ASSET, LIABILITY, EQUITY excluded from income statement

    return result


def build_income_statement(
    rows: list[TrialBalanceRow],
    accounts: dict[UUID, AccountInfo],
    config: ReportingConfig,
    metadata: ReportMetadata,
    format: IncomeStatementFormat = IncomeStatementFormat.MULTI_STEP,
    comparative_rows: list[TrialBalanceRow] | None = None,
) -> IncomeStatementReport:
    """
    Build income statement in single-step or multi-step format.

    Single-step:
        Total Revenue - Total Expenses = Net Income

    Multi-step:
        Revenue
        - Cost of Goods Sold
        = Gross Profit
        - Operating Expenses
        = Operating Income
        + Other Income
        - Other Expenses
        = Income Before Tax
        (Net Income = Income Before Tax for now; tax_expense is future)
    """
    items = enrich_trial_balance(rows, accounts, config)
    classified = classify_for_income_statement(items, accounts, config)

    revenue_section = _make_is_section("Revenue", classified["revenue"])
    cogs_section = _make_is_section("Cost of Goods Sold", classified["cogs"])
    opex_section = _make_is_section(
        "Operating Expenses", classified["operating_expenses"],
    )
    other_income_section = _make_is_section(
        "Other Income", classified["other_income"],
    )
    other_expense_section = _make_is_section(
        "Other Expenses", classified["other_expenses"],
    )

    total_revenue = revenue_section.total + other_income_section.total
    total_expenses = (
        cogs_section.total + opex_section.total + other_expense_section.total
    )
    net_income = total_revenue - total_expenses

    gross_profit = revenue_section.total - cogs_section.total
    operating_income = gross_profit - opex_section.total
    income_before_tax = (
        operating_income + other_income_section.total - other_expense_section.total
    )

    comparative = None
    if comparative_rows is not None:
        comparative = build_income_statement(
            comparative_rows, accounts, config, metadata, format,
        )

    if format == IncomeStatementFormat.SINGLE_STEP:
        return IncomeStatementReport(
            metadata=metadata,
            format=format,
            total_revenue=total_revenue,
            total_expenses=total_expenses,
            net_income=net_income,
            comparative=comparative,
        )

    return IncomeStatementReport(
        metadata=metadata,
        format=format,
        total_revenue=total_revenue,
        total_expenses=total_expenses,
        net_income=net_income,
        revenue_section=revenue_section,
        cogs_section=cogs_section,
        gross_profit=gross_profit,
        operating_expense_section=opex_section,
        operating_income=operating_income,
        other_income_section=other_income_section,
        other_expense_section=other_expense_section,
        income_before_tax=income_before_tax,
        comparative=comparative,
    )


# =========================================================================
# 4. CASH FLOW STATEMENT (Indirect Method)
# =========================================================================


def _balance_by_account(
    rows: list[TrialBalanceRow],
    accounts: dict[UUID, AccountInfo],
) -> dict[UUID, Decimal]:
    """Compute natural balance per account from TB rows."""
    result: dict[UUID, Decimal] = {}
    for row in rows:
        acct = accounts.get(row.account_id)
        if acct is None:
            continue
        result[row.account_id] = compute_natural_balance(
            row.debit_total, row.credit_total, acct.normal_balance,
        )
    return result


def _sum_balances_for(
    balances: dict[UUID, Decimal],
    accounts: dict[UUID, AccountInfo],
    filter_fn: callable,
) -> Decimal:
    """Sum balances for accounts matching a filter function."""
    total = Decimal("0")
    for acct_id, balance in balances.items():
        acct = accounts.get(acct_id)
        if acct is not None and filter_fn(acct):
            total += balance
    return total


def _cash_balance(
    balances: dict[UUID, Decimal],
    accounts: dict[UUID, AccountInfo],
    config: ReportingConfig,
) -> Decimal:
    """Sum balances of cash/cash-equivalent accounts."""
    clf = config.classification
    total = Decimal("0")
    for acct_id, balance in balances.items():
        acct = accounts.get(acct_id)
        if acct is not None and clf.matches_prefix(
            acct.code, clf.cash_account_prefixes,
        ):
            total += balance
    return total


def build_cash_flow_statement(
    current_rows: list[TrialBalanceRow],
    prior_rows: list[TrialBalanceRow],
    accounts: dict[UUID, AccountInfo],
    config: ReportingConfig,
    metadata: ReportMetadata,
) -> CashFlowStatementReport:
    """
    Build statement of cash flows using indirect method (ASC 230 / IAS 7).

    Requires two periods of trial balance data:
    - current_rows: TB as of period end
    - prior_rows: TB as of prior period end (beginning of current period)

    Steps:
    1. Net income from IS
    2. Add back non-cash expenses (depreciation, amortization)
    3. Working capital changes (current assets/liabilities deltas)
    4. Investing: non-current asset changes
    5. Financing: non-current liability + equity changes (ex retained earnings)
    """
    clf = config.classification
    net_income = compute_net_income_from_tb(current_rows, accounts)

    current_bal = _balance_by_account(current_rows, accounts)
    prior_bal = _balance_by_account(prior_rows, accounts)

    # Helper: change = current - prior (positive means increase)
    def _change(acct_id: UUID) -> Decimal:
        return current_bal.get(acct_id, Decimal("0")) - prior_bal.get(
            acct_id, Decimal("0"),
        )

    # --- Operating adjustments (non-cash items) ---
    op_adj_lines: list[CashFlowLineItem] = []
    for acct_id, acct in accounts.items():
        if acct.account_type != AccountType.EXPENSE:
            continue
        # Depreciation and amortization are non-cash
        is_noncash = any(
            tag in acct.tags
            for tag in ("depreciation", "amortization", "impairment")
        )
        if is_noncash:
            change = _change(acct_id)
            if change != Decimal("0"):
                op_adj_lines.append(
                    CashFlowLineItem(
                        description=f"Add back: {acct.name}",
                        amount=change,  # Positive = add back
                    )
                )

    op_adj_total = sum((line.amount for line in op_adj_lines), Decimal("0"))
    operating_adjustments = CashFlowSection(
        label="Non-Cash Adjustments",
        lines=tuple(op_adj_lines),
        total=op_adj_total,
    )

    # --- Working capital changes ---
    wc_lines: list[CashFlowLineItem] = []
    for acct_id, acct in accounts.items():
        # Skip cash accounts (they are the result, not the input)
        if clf.matches_prefix(acct.code, clf.cash_account_prefixes):
            continue

        change = _change(acct_id)
        if change == Decimal("0"):
            continue

        # Current assets (non-cash): increase = cash outflow (negative)
        if (
            acct.account_type == AccountType.ASSET
            and clf.matches_prefix(acct.code, clf.current_asset_prefixes)
        ):
            wc_lines.append(
                CashFlowLineItem(
                    description=f"Change in {acct.name}",
                    amount=-change,  # Asset increase = cash decrease
                )
            )
        # Current liabilities: increase = cash inflow (positive)
        elif (
            acct.account_type == AccountType.LIABILITY
            and clf.matches_prefix(acct.code, clf.current_liability_prefixes)
        ):
            wc_lines.append(
                CashFlowLineItem(
                    description=f"Change in {acct.name}",
                    amount=change,  # Liability increase = cash increase
                )
            )

    wc_total = sum((line.amount for line in wc_lines), Decimal("0"))
    working_capital_changes = CashFlowSection(
        label="Changes in Working Capital",
        lines=tuple(wc_lines),
        total=wc_total,
    )

    net_cash_from_operations = net_income + op_adj_total + wc_total

    # --- Investing activities (non-current asset changes) ---
    inv_lines: list[CashFlowLineItem] = []
    for acct_id, acct in accounts.items():
        if (
            acct.account_type == AccountType.ASSET
            and clf.matches_prefix(acct.code, clf.non_current_asset_prefixes)
        ):
            change = _change(acct_id)
            if change != Decimal("0"):
                inv_lines.append(
                    CashFlowLineItem(
                        description=f"Change in {acct.name}",
                        amount=-change,  # Asset increase = cash outflow
                    )
                )

    inv_total = sum((line.amount for line in inv_lines), Decimal("0"))
    investing_activities = CashFlowSection(
        label="Investing Activities",
        lines=tuple(inv_lines),
        total=inv_total,
    )

    # --- Financing activities (non-current liabilities + equity changes) ---
    fin_lines: list[CashFlowLineItem] = []
    for acct_id, acct in accounts.items():
        change = _change(acct_id)
        if change == Decimal("0"):
            continue

        is_nc_liability = (
            acct.account_type == AccountType.LIABILITY
            and clf.matches_prefix(acct.code, clf.non_current_liability_prefixes)
        )
        is_equity = acct.account_type == AccountType.EQUITY

        if is_nc_liability or is_equity:
            fin_lines.append(
                CashFlowLineItem(
                    description=f"Change in {acct.name}",
                    amount=change,  # Liability/equity increase = cash inflow
                )
            )

    fin_total = sum((line.amount for line in fin_lines), Decimal("0"))
    financing_activities = CashFlowSection(
        label="Financing Activities",
        lines=tuple(fin_lines),
        total=fin_total,
    )

    net_cash_from_investing = inv_total
    net_cash_from_financing = fin_total

    beginning_cash = _cash_balance(prior_bal, accounts, config)
    ending_cash = _cash_balance(current_bal, accounts, config)
    net_change = (
        net_cash_from_operations + net_cash_from_investing + net_cash_from_financing
    )

    return CashFlowStatementReport(
        metadata=metadata,
        net_income=net_income,
        operating_adjustments=operating_adjustments,
        working_capital_changes=working_capital_changes,
        net_cash_from_operations=net_cash_from_operations,
        investing_activities=investing_activities,
        net_cash_from_investing=net_cash_from_investing,
        financing_activities=financing_activities,
        net_cash_from_financing=net_cash_from_financing,
        net_change_in_cash=net_change,
        beginning_cash=beginning_cash,
        ending_cash=ending_cash,
        cash_change_reconciles=(ending_cash - beginning_cash == net_change),
    )


# =========================================================================
# 5. STATEMENT OF CHANGES IN EQUITY
# =========================================================================


def build_equity_changes(
    current_rows: list[TrialBalanceRow],
    prior_rows: list[TrialBalanceRow],
    accounts: dict[UUID, AccountInfo],
    config: ReportingConfig,
    metadata: ReportMetadata,
) -> EquityChangesReport:
    """
    Build statement of changes in equity.

    Beginning equity (from prior period)
    + Net income
    - Dividends declared
    +/- Other changes in equity accounts
    = Ending equity
    """
    current_bal = _balance_by_account(current_rows, accounts)
    prior_bal = _balance_by_account(prior_rows, accounts)

    # Beginning equity = sum of equity account balances in prior period
    beginning_equity = _sum_balances_for(
        prior_bal, accounts, lambda a: a.account_type == AccountType.EQUITY,
    )

    # Net income
    net_income = compute_net_income_from_tb(current_rows, accounts)

    # Ending equity = sum of equity account balances + net income
    ending_equity_accounts = _sum_balances_for(
        current_bal, accounts, lambda a: a.account_type == AccountType.EQUITY,
    )
    ending_equity = ending_equity_accounts + net_income

    # Dividends: accounts tagged as dividends or with "dividend" in name
    dividends = Decimal("0")
    for acct_id, acct in accounts.items():
        if acct.account_type == AccountType.EQUITY and "dividend" in acct.name.lower():
            change = current_bal.get(acct_id, Decimal("0")) - prior_bal.get(
                acct_id, Decimal("0"),
            )
            dividends += change

    other_changes = ending_equity - beginning_equity - net_income + dividends

    # Build movement list
    movements: list[EquityMovement] = []
    movements.append(EquityMovement(description="Net Income", amount=net_income))
    if dividends != Decimal("0"):
        movements.append(
            EquityMovement(description="Dividends Declared", amount=-dividends),
        )
    if other_changes != Decimal("0"):
        movements.append(
            EquityMovement(description="Other Changes", amount=other_changes),
        )

    total_movements = sum((m.amount for m in movements), Decimal("0"))
    reconciles = (beginning_equity + total_movements) == ending_equity

    return EquityChangesReport(
        metadata=metadata,
        beginning_equity=beginning_equity,
        movements=tuple(movements),
        ending_equity=ending_equity,
        net_income=net_income,
        dividends_declared=dividends,
        other_changes=other_changes,
        reconciles=reconciles,
    )


# =========================================================================
# 6. SEGMENT REPORTING
# =========================================================================


def build_segment_report(
    rows_by_segment: dict[str, list[TrialBalanceRow]],
    accounts: dict[UUID, AccountInfo],
    config: ReportingConfig,
    metadata: ReportMetadata,
    dimension_name: str,
    unallocated_rows: list[TrialBalanceRow] | None = None,
) -> SegmentReport:
    """
    Build dimension-based segment report.

    Each segment contains a subset of the trial balance filtered
    by a specific dimension value.
    """

    def _build_segment(
        key: str, value: str, rows: list[TrialBalanceRow],
    ) -> SegmentData:
        items = enrich_trial_balance(rows, accounts, config)
        revenue = Decimal("0")
        expenses = Decimal("0")
        assets = Decimal("0")
        for item in items:
            acct = accounts.get(item.account_id)
            if acct is None:
                continue
            if acct.account_type == AccountType.REVENUE:
                revenue += item.net_balance
            elif acct.account_type == AccountType.EXPENSE:
                expenses += item.net_balance
            elif acct.account_type == AccountType.ASSET:
                assets += item.net_balance
        return SegmentData(
            segment_key=key,
            segment_value=value,
            trial_balance_lines=items,
            total_revenue=revenue,
            total_expenses=expenses,
            net_income=revenue - expenses,
            total_assets=assets,
        )

    segments = tuple(
        _build_segment(key, key, rows)
        for key, rows in sorted(rows_by_segment.items())
    )

    unallocated = None
    if unallocated_rows:
        unallocated = _build_segment("_unallocated", "Unallocated", unallocated_rows)

    return SegmentReport(
        metadata=metadata,
        dimension_name=dimension_name,
        segments=segments,
        unallocated=unallocated,
    )


# =========================================================================
# 7. RENDERER (dict/JSON output)
# =========================================================================


def render_to_dict(obj: object) -> dict | list | str | int | float | bool | None:
    """
    Convert any report dataclass to a plain dict for JSON serialization.

    Handles:
    - Decimal -> str (preserving precision)
    - UUID -> str
    - date -> ISO format string
    - Enum -> .value
    - Nested frozen dataclasses -> nested dicts
    - Tuples -> lists
    - None preserved
    """
    if obj is None:
        return None
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [render_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): render_to_dict(v) for k, v in obj.items()}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: render_to_dict(getattr(obj, f.name))
            for f in dataclasses.fields(obj)
        }
    if isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)
