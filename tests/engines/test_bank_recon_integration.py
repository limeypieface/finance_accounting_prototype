"""
Integration tests for bank reconciliation checker -- GAP-BRC Phase 3.

End-to-end scenarios testing the full checker with realistic bank
reconciliation data, plus verification that types are importable from
the reconciliation package.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_engines.reconciliation import (
    BankReconCheckResult,
    BankReconContext,
    BankReconLine,
    BankReconStatement,
    BankReconciliationChecker,
    CheckStatus,
)


@pytest.fixture
def checker():
    return BankReconciliationChecker()


def _line(status="matched", txn_date=None, amount=Decimal("100"), gl_ids=()):
    return BankReconLine(
        line_id=uuid4(),
        transaction_date=txn_date or date(2026, 1, 15),
        amount=amount,
        description="test",
        status=status,
        matched_journal_line_ids=tuple(gl_ids),
    )


def _stmt(ba_id, stmt_date, opening, closing, lines=()):
    return BankReconStatement(
        statement_id=uuid4(),
        bank_account_id=ba_id,
        statement_date=stmt_date,
        opening_balance=opening,
        closing_balance=closing,
        currency="USD",
        lines=lines,
    )


class TestCleanBankAccount:
    """Fully reconciled bank account should produce clean results."""

    def test_all_matched_single_statement(self, checker):
        ba = uuid4()
        gl1, gl2, gl3 = uuid4(), uuid4(), uuid4()
        lines = (
            _line("matched", date(2026, 1, 5), Decimal("1500.00"), (gl1,)),
            _line("matched", date(2026, 1, 12), Decimal("-200.00"), (gl2,)),
            _line("matched", date(2026, 1, 20), Decimal("700.00"), (gl3,)),
        )
        stmt = _stmt(ba, date(2026, 1, 31), Decimal("10000"), Decimal("12000"), lines)
        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(stmt,),
            reconciliation_status="completed",
            reconciliation_variance=Decimal("0.00"),
            as_of_date=date(2026, 2, 1),
        )
        result = checker.run_all_checks(context=ctx)
        assert result.status == CheckStatus.PASSED
        assert result.is_clean
        assert result.statements_checked == 1
        assert result.lines_checked == 3

    def test_all_matched_continuous_statements(self, checker):
        """Three monthly statements with continuous balances and all matched."""
        ba = uuid4()
        s1 = _stmt(ba, date(2026, 1, 31), Decimal("10000"), Decimal("12000"),
                    (_line("matched", date(2026, 1, 15), gl_ids=(uuid4(),)),))
        s2 = _stmt(ba, date(2026, 2, 28), Decimal("12000"), Decimal("14000"),
                    (_line("matched", date(2026, 2, 10), gl_ids=(uuid4(),)),))
        s3 = _stmt(ba, date(2026, 3, 31), Decimal("14000"), Decimal("15500"),
                    (_line("matched", date(2026, 3, 20), gl_ids=(uuid4(),)),))
        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(s1, s2, s3),
            reconciliation_status="completed",
            reconciliation_variance=Decimal("0.00"),
            as_of_date=date(2026, 4, 1),
        )
        result = checker.run_all_checks(context=ctx)
        assert result.status == CheckStatus.PASSED
        assert result.is_clean


class TestCompoundFindings:
    """Multiple issues in one bank account."""

    def test_stale_plus_duplicate_plus_gap(self, checker):
        """Stale unmatched, duplicate GL match, AND balance discontinuity."""
        ba = uuid4()
        shared_gl = uuid4()

        # Statement 1: Jan -- stale unmatched line + duplicate GL
        jan_lines = (
            _line("unmatched", date(2025, 11, 1), Decimal("500")),  # stale
            _line("matched", date(2026, 1, 10), gl_ids=(shared_gl,)),
        )
        s_jan = _stmt(ba, date(2026, 1, 31), Decimal("10000"), Decimal("11000"),
                      jan_lines)

        # Statement 2: Feb -- balance gap from Jan + another dup GL ref
        feb_lines = (
            _line("matched", date(2026, 2, 5), gl_ids=(shared_gl,)),  # dup!
        )
        s_feb = _stmt(ba, date(2026, 2, 28), Decimal("11500"), Decimal("12000"),
                      feb_lines)  # gap: Jan closing 11000 != Feb opening 11500

        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(s_jan, s_feb),
            as_of_date=date(2026, 3, 1),
        )
        result = checker.run_all_checks(context=ctx)
        assert result.status == CheckStatus.FAILED

        codes = {f.code for f in result.findings}
        assert "STALE_UNMATCHED_LINE" in codes
        assert "DUPLICATE_GL_MATCH" in codes
        assert "BALANCE_DISCONTINUITY" in codes

    def test_variance_on_completed_with_stale_lines(self, checker):
        """Completed recon with variance AND stale unmatched lines."""
        ba = uuid4()
        stale = _line("unmatched", date(2025, 10, 1), Decimal("300"))
        stmt = _stmt(ba, date(2026, 1, 31), Decimal("10000"), Decimal("10300"),
                     (stale,))
        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(stmt,),
            reconciliation_status="completed",
            reconciliation_variance=Decimal("15.50"),
            as_of_date=date(2026, 2, 1),
        )
        result = checker.run_all_checks(context=ctx)
        # Both stale and variance are warnings
        assert result.status == CheckStatus.WARNING
        codes = {f.code for f in result.findings}
        assert "STALE_UNMATCHED_LINE" in codes
        assert "UNEXPLAINED_VARIANCE" in codes


class TestRealisticScenario:
    """Real-world-like scenarios."""

    def test_monthly_statements_mid_month_discontinuity(self, checker):
        """3 months of statements where month 2 has corrupted opening balance."""
        ba = uuid4()
        s1 = _stmt(ba, date(2026, 1, 31), Decimal("50000"), Decimal("52000"),
                    (_line("matched", date(2026, 1, 15), gl_ids=(uuid4(),)),))
        # Month 2: opening 51000 instead of 52000
        s2 = _stmt(ba, date(2026, 2, 28), Decimal("51000"), Decimal("53000"),
                    (_line("matched", date(2026, 2, 10), gl_ids=(uuid4(),)),))
        # Month 3: continuous from month 2
        s3 = _stmt(ba, date(2026, 3, 31), Decimal("53000"), Decimal("55000"),
                    (_line("matched", date(2026, 3, 20), gl_ids=(uuid4(),)),))

        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(s1, s2, s3),
            as_of_date=date(2026, 4, 1),
        )
        result = checker.run_all_checks(context=ctx)
        assert result.status == CheckStatus.FAILED
        # Only one gap: between s1 and s2
        gap_findings = [f for f in result.findings if f.code == "BALANCE_DISCONTINUITY"]
        assert len(gap_findings) == 1

    def test_all_excluded_lines_pass(self, checker):
        """Bank account where all unreconcilable lines are properly excluded."""
        ba = uuid4()
        lines = (
            _line("matched", date(2026, 1, 5), gl_ids=(uuid4(),)),
            _line("excluded", date(2025, 6, 1)),  # old but excluded
            _line("matched", date(2026, 1, 20), gl_ids=(uuid4(),)),
        )
        stmt = _stmt(ba, date(2026, 1, 31), Decimal("10000"), Decimal("10500"), lines)
        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(stmt,),
            as_of_date=date(2026, 2, 1),
        )
        result = checker.run_all_checks(context=ctx)
        assert result.status == CheckStatus.PASSED


class TestPackageImports:
    """Verify bank recon types are importable from the package."""

    def test_types_available(self):
        from finance_engines.reconciliation import (
            BankReconCheckResult,
            BankReconContext,
            BankReconLine,
            BankReconStatement,
            BankReconciliationChecker,
        )
        assert BankReconLine is not None
        assert BankReconStatement is not None
        assert BankReconContext is not None
        assert BankReconCheckResult is not None
        assert BankReconciliationChecker is not None
