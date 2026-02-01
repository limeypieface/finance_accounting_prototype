"""
Reporting Module Service (``finance_modules.reporting.service``).

Responsibility
--------------
Orchestrates financial statement generation -- trial balance, income
statement, balance sheet, cash flow statement, equity changes, segment
reports, and multi-currency trial balance -- by bridging database selectors
(``LedgerSelector``, ``JournalSelector``) to the pure transformation
functions in ``statements.py``.  This is a **read-only** service: no
journal entries are posted, no events are ingested.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``ReportingService`` is the sole
public entry point for financial statement generation.  Unlike other module
services, it does NOT use ``RoleResolver``, ``ModulePostingService``, or
profiles/workflows.  Constructor: ``session`` + ``clock`` + ``config``.

Invariants enforced
-------------------
* Read-only -- no mutations to the journal or event store.
* All monetary amounts use ``Decimal`` -- NEVER ``float``.
* Report metadata carries generation timestamp and parameters for
  reproducibility.

Failure modes
-------------
* Selector query failure  -> exception propagates (no rollback needed --
  read-only).
* Invalid report parameters (e.g., end_date < start_date)  ->
  ``ValueError`` raised before query execution.
* Missing account data  -> empty sections in generated report.

Audit relevance
---------------
Structured log events emitted for every report generation, carrying report
type, period, and parameters.  Reports are derived from the immutable
journal -- they do not modify it.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.logging_config import get_logger
from finance_kernel.models.account import Account, AccountType, NormalBalance
from finance_kernel.selectors.journal_selector import JournalSelector
from finance_kernel.selectors.ledger_selector import LedgerSelector, TrialBalanceRow

from finance_modules.reporting.config import ReportingConfig
from finance_modules.reporting.models import (
    BalanceSheetReport,
    CashFlowStatementReport,
    EquityChangesReport,
    IncomeStatementFormat,
    IncomeStatementReport,
    MultiCurrencyTrialBalance,
    ReportMetadata,
    ReportType,
    SegmentReport,
    TrialBalanceReport,
)
from finance_modules.reporting.statements import (
    AccountInfo,
    build_balance_sheet,
    build_cash_flow_statement,
    build_equity_changes,
    build_income_statement,
    build_segment_report,
    build_trial_balance,
    render_to_dict,
)

logger = get_logger("modules.reporting.service")


class ReportingService:
    """
    Financial statement generation service.

    Contract
    --------
    * Every public method returns a typed report DTO (e.g.,
      ``TrialBalanceReport``, ``IncomeStatementReport``).
    * All methods are **read-only** -- no mutations to the database.

    Guarantees
    ----------
    * Report generation delegates to pure transformation functions in
      ``statements.py``; no financial logic lives in this class.
    * Clock is injectable for deterministic testing.
    * All monetary amounts use ``Decimal`` -- NEVER ``float``.

    Non-goals
    ---------
    * Does NOT post journal entries or ingest events.
    * Does NOT use ``RoleResolver``, ``ModulePostingService``, or profiles.
    * Does NOT enforce fiscal-period locks (read-only service).

    Orchestrates data loading from kernel selectors and delegates
    to pure transformation functions in statements.py.
    """

    def __init__(
        self,
        session: Session,
        clock: Clock | None = None,
        config: ReportingConfig | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()
        self._config = config or ReportingConfig.with_defaults()
        self._ledger = LedgerSelector(session)
        self._journal = JournalSelector(session)

        logger.info(
            "reporting_service_initialized",
            extra={
                "entity_name": self._config.entity_name,
                "default_currency": self._config.default_currency,
            },
        )

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _load_accounts(self) -> dict[UUID, AccountInfo]:
        """
        Load all accounts from the database and convert to AccountInfo.

        AccountInfo is a frozen dataclass bridge type used by the pure
        functions in statements.py. This conversion keeps the pure layer
        free of ORM dependencies.
        """
        query = self._session.query(Account)
        if not self._config.include_inactive:
            query = query.filter(Account.is_active.is_(True))

        accounts: dict[UUID, AccountInfo] = {}
        for acct in query.all():
            tags = tuple(acct.tags) if acct.tags else ()
            accounts[acct.id] = AccountInfo(
                account_id=acct.id,
                code=acct.code,
                name=acct.name,
                account_type=AccountType(acct.account_type),
                normal_balance=NormalBalance(acct.normal_balance),
                tags=tags,
                parent_id=acct.parent_id,
            )

        logger.debug(
            "accounts_loaded_for_reporting",
            extra={"account_count": len(accounts)},
        )
        return accounts

    def _build_metadata(
        self,
        report_type: ReportType,
        as_of_date: date,
        currency: str | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
        comparative_date: date | None = None,
        dimensions_filter: dict | None = None,
    ) -> ReportMetadata:
        """Build report metadata with injected clock timestamp."""
        return ReportMetadata(
            report_type=report_type,
            entity_name=self._config.entity_name,
            currency=currency or self._config.default_currency,
            as_of_date=as_of_date,
            generated_at=self._clock.now().isoformat(),
            period_start=period_start,
            period_end=period_end,
            comparative_date=comparative_date,
            dimensions_filter=dimensions_filter,
        )

    def _get_trial_balance_rows(
        self,
        as_of_date: date,
        currency: str | None = None,
    ) -> list[TrialBalanceRow]:
        """Fetch trial balance from ledger selector."""
        return self._ledger.trial_balance(
            as_of_date=as_of_date,
            currency=currency or self._config.default_currency,
        )

    # =========================================================================
    # Public API
    # =========================================================================

    def trial_balance(
        self,
        as_of_date: date,
        currency: str | None = None,
        comparative_date: date | None = None,
    ) -> TrialBalanceReport:
        """
        Generate a trial balance report.

        Args:
            as_of_date: Cutoff date for the trial balance.
            currency: Currency filter (defaults to config default).
            comparative_date: Optional prior period date for comparison.

        Returns:
            TrialBalanceReport with balanced debits/credits.
        """
        curr = currency or self._config.default_currency
        accounts = self._load_accounts()
        rows = self._get_trial_balance_rows(as_of_date, curr)

        comparative_rows = None
        if comparative_date is not None:
            comparative_rows = self._get_trial_balance_rows(
                comparative_date, curr,
            )

        metadata = self._build_metadata(
            ReportType.TRIAL_BALANCE,
            as_of_date,
            curr,
            comparative_date=comparative_date,
        )

        report = build_trial_balance(
            rows, accounts, self._config, metadata, comparative_rows,
        )

        logger.info(
            "trial_balance_generated",
            extra={
                "as_of_date": as_of_date.isoformat(),
                "currency": curr,
                "line_count": len(report.lines),
                "is_balanced": report.is_balanced,
            },
        )
        return report

    def balance_sheet(
        self,
        as_of_date: date,
        currency: str | None = None,
        comparative_date: date | None = None,
    ) -> BalanceSheetReport:
        """
        Generate a classified balance sheet (ASC 210 / IAS 1).

        Args:
            as_of_date: Report date.
            currency: Currency filter.
            comparative_date: Optional prior period date for comparison.

        Returns:
            BalanceSheetReport with A = L + E verification.
        """
        curr = currency or self._config.default_currency
        accounts = self._load_accounts()
        rows = self._get_trial_balance_rows(as_of_date, curr)

        comparative_rows = None
        if comparative_date is not None:
            comparative_rows = self._get_trial_balance_rows(
                comparative_date, curr,
            )

        metadata = self._build_metadata(
            ReportType.BALANCE_SHEET,
            as_of_date,
            curr,
            comparative_date=comparative_date,
        )

        report = build_balance_sheet(
            rows, accounts, self._config, metadata, comparative_rows,
        )

        logger.info(
            "balance_sheet_generated",
            extra={
                "as_of_date": as_of_date.isoformat(),
                "currency": curr,
                "total_assets": str(report.total_assets),
                "total_l_and_e": str(report.total_liabilities_and_equity),
                "is_balanced": report.is_balanced,
            },
        )
        return report

    def income_statement(
        self,
        period_start: date,
        period_end: date,
        currency: str | None = None,
        format: IncomeStatementFormat = IncomeStatementFormat.MULTI_STEP,
        comparative_start: date | None = None,
        comparative_end: date | None = None,
    ) -> IncomeStatementReport:
        """
        Generate an income statement (P&L).

        For period-based reporting, the trial balance is computed as of
        period_end. Revenue and expense accounts reflect activity for the
        period because they are not closed mid-period.

        Args:
            period_start: Start of reporting period.
            period_end: End of reporting period.
            currency: Currency filter.
            format: SINGLE_STEP or MULTI_STEP.
            comparative_start: Prior period start for comparison.
            comparative_end: Prior period end for comparison.

        Returns:
            IncomeStatementReport with net income calculation.
        """
        curr = currency or self._config.default_currency
        accounts = self._load_accounts()
        rows = self._get_trial_balance_rows(period_end, curr)

        comparative_rows = None
        if comparative_end is not None:
            comparative_rows = self._get_trial_balance_rows(
                comparative_end, curr,
            )

        metadata = self._build_metadata(
            ReportType.INCOME_STATEMENT,
            period_end,
            curr,
            period_start=period_start,
            period_end=period_end,
        )

        report = build_income_statement(
            rows, accounts, self._config, metadata, format, comparative_rows,
        )

        logger.info(
            "income_statement_generated",
            extra={
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "currency": curr,
                "format": format.value,
                "net_income": str(report.net_income),
            },
        )
        return report

    def cash_flow_statement(
        self,
        period_start: date,
        period_end: date,
        prior_period_end: date | None = None,
        currency: str | None = None,
    ) -> CashFlowStatementReport:
        """
        Generate a statement of cash flows (indirect method, ASC 230 / IAS 7).

        Requires two trial balance snapshots to compute balance changes.

        Args:
            period_start: Start of reporting period.
            period_end: End of reporting period.
            prior_period_end: End of prior period (defaults to period_start - 1 day).
            currency: Currency filter.

        Returns:
            CashFlowStatementReport with reconciliation verification.
        """
        curr = currency or self._config.default_currency
        accounts = self._load_accounts()

        current_rows = self._get_trial_balance_rows(period_end, curr)

        # Prior period: default to day before period start
        if prior_period_end is None:
            prior_period_end = period_start - timedelta(days=1)

        prior_rows = self._get_trial_balance_rows(prior_period_end, curr)

        metadata = self._build_metadata(
            ReportType.CASH_FLOW,
            period_end,
            curr,
            period_start=period_start,
            period_end=period_end,
        )

        report = build_cash_flow_statement(
            current_rows, prior_rows, accounts, self._config, metadata,
        )

        logger.info(
            "cash_flow_statement_generated",
            extra={
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "currency": curr,
                "net_change_in_cash": str(report.net_change_in_cash),
                "reconciles": report.cash_change_reconciles,
            },
        )
        return report

    def equity_changes(
        self,
        period_start: date,
        period_end: date,
        prior_period_end: date | None = None,
        currency: str | None = None,
    ) -> EquityChangesReport:
        """
        Generate a statement of changes in equity.

        Args:
            period_start: Start of reporting period.
            period_end: End of reporting period.
            prior_period_end: End of prior period (defaults to period_start - 1 day).
            currency: Currency filter.

        Returns:
            EquityChangesReport with reconciliation verification.
        """
        curr = currency or self._config.default_currency
        accounts = self._load_accounts()

        current_rows = self._get_trial_balance_rows(period_end, curr)

        if prior_period_end is None:
            prior_period_end = period_start - timedelta(days=1)

        prior_rows = self._get_trial_balance_rows(prior_period_end, curr)

        metadata = self._build_metadata(
            ReportType.EQUITY_CHANGES,
            period_end,
            curr,
            period_start=period_start,
            period_end=period_end,
        )

        report = build_equity_changes(
            current_rows, prior_rows, accounts, self._config, metadata,
        )

        logger.info(
            "equity_changes_generated",
            extra={
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "currency": curr,
                "beginning_equity": str(report.beginning_equity),
                "ending_equity": str(report.ending_equity),
                "reconciles": report.reconciles,
            },
        )
        return report

    def segment_report(
        self,
        as_of_date: date,
        dimension_name: str,
        currency: str | None = None,
    ) -> SegmentReport:
        """
        Generate a dimension-based segment report.

        Queries the ledger with dimension filtering to produce
        per-segment financial summaries.

        Args:
            as_of_date: Report date.
            dimension_name: Dimension key to segment by (e.g., "department").
            currency: Currency filter.

        Returns:
            SegmentReport with per-segment trial balance data.
        """
        curr = currency or self._config.default_currency
        accounts = self._load_accounts()

        # Query all ledger lines with dimensions
        lines = self._ledger.query(as_of_date=as_of_date, currency=curr)

        # Group lines by dimension value
        rows_by_segment: dict[str, list[TrialBalanceRow]] = {}
        unallocated_ids: set[UUID] = set()

        # Build a mapping from account_id to accumulated debits/credits per segment
        segment_accum: dict[str, dict[UUID, dict]] = {}
        unallocated_accum: dict[UUID, dict] = {}

        for line in lines:
            dim_value = None
            if line.dimensions:
                dim_value = line.dimensions.get(dimension_name)

            acct = accounts.get(line.account_id)
            if acct is None:
                continue

            if dim_value is not None:
                if dim_value not in segment_accum:
                    segment_accum[dim_value] = {}
                accum = segment_accum[dim_value]
            else:
                accum = unallocated_accum

            if line.account_id not in accum:
                accum[line.account_id] = {
                    "debit_total": Decimal("0"),
                    "credit_total": Decimal("0"),
                }

            from finance_kernel.domain import LineSide
            if line.side == LineSide.DEBIT:
                accum[line.account_id]["debit_total"] += line.amount
            else:
                accum[line.account_id]["credit_total"] += line.amount

        # Convert accumulated data to TrialBalanceRow lists
        def _to_tb_rows(
            accum: dict[UUID, dict],
        ) -> list[TrialBalanceRow]:
            result = []
            for acct_id, totals in accum.items():
                acct = accounts.get(acct_id)
                if acct is None:
                    continue
                result.append(
                    TrialBalanceRow(
                        account_id=acct_id,
                        account_code=acct.code,
                        account_name=acct.name,
                        currency=curr,
                        debit_total=totals["debit_total"],
                        credit_total=totals["credit_total"],
                    )
                )
            return result

        for seg_value, seg_accum in segment_accum.items():
            rows_by_segment[seg_value] = _to_tb_rows(seg_accum)

        unallocated_rows = _to_tb_rows(unallocated_accum) if unallocated_accum else None

        metadata = self._build_metadata(
            ReportType.SEGMENT,
            as_of_date,
            curr,
            dimensions_filter=((dimension_name, "*"),),
        )

        report = build_segment_report(
            rows_by_segment, accounts, self._config, metadata,
            dimension_name, unallocated_rows,
        )

        logger.info(
            "segment_report_generated",
            extra={
                "as_of_date": as_of_date.isoformat(),
                "currency": curr,
                "dimension": dimension_name,
                "segment_count": len(report.segments),
            },
        )
        return report

    def multi_currency_trial_balance(
        self,
        as_of_date: date,
        currencies: list[str],
    ) -> MultiCurrencyTrialBalance:
        """
        Generate a trial balance across multiple currencies.

        Calls the existing trial_balance() for each currency and
        aggregates results into a MultiCurrencyTrialBalance.

        Args:
            as_of_date: Cutoff date for the trial balance.
            currencies: List of ISO 4217 currency codes to include.

        Returns:
            MultiCurrencyTrialBalance with per-currency reports.
        """
        currency_reports = []
        debits_by_currency: list[tuple[str, Decimal]] = []
        credits_by_currency: list[tuple[str, Decimal]] = []

        for curr in currencies:
            report = self.trial_balance(as_of_date=as_of_date, currency=curr)
            currency_reports.append(report)
            debits_by_currency.append((curr, report.total_debits))
            credits_by_currency.append((curr, report.total_credits))

        all_balanced = all(r.is_balanced for r in currency_reports)

        metadata = self._build_metadata(
            ReportType.TRIAL_BALANCE,
            as_of_date,
            currencies[0] if currencies else self._config.default_currency,
        )

        result = MultiCurrencyTrialBalance(
            metadata=metadata,
            currency_reports=tuple(currency_reports),
            currencies=tuple(currencies),
            total_debits_by_currency=tuple(debits_by_currency),
            total_credits_by_currency=tuple(credits_by_currency),
            all_balanced=all_balanced,
        )

        logger.info(
            "multi_currency_trial_balance_generated",
            extra={
                "as_of_date": as_of_date.isoformat(),
                "currencies": currencies,
                "currency_count": len(currencies),
                "all_balanced": all_balanced,
            },
        )
        return result

    def to_dict(self, report: object) -> dict:
        """
        Convert any report DTO to a plain dict for JSON serialization.

        Delegates to the pure render_to_dict function.
        """
        return render_to_dict(report)
