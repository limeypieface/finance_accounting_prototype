"""
Tests for BankReconciliationChecker -- GAP-BRC Phase 1.

Covers all 4 check categories (BR-1 through BR-4) plus the
run_all_checks orchestrator.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_engines.reconciliation.lifecycle_types import CheckSeverity, CheckStatus
from finance_engines.reconciliation.bank_recon_types import (
    BankReconContext,
    BankReconLine,
    BankReconStatement,
)
from finance_engines.reconciliation.bank_checker import BankReconciliationChecker


@pytest.fixture
def checker():
    return BankReconciliationChecker()


def _line(
    status="matched",
    transaction_date=None,
    amount=Decimal("100.00"),
    gl_ids=(),
):
    """Helper to build a BankReconLine."""
    return BankReconLine(
        line_id=uuid4(),
        transaction_date=transaction_date or date(2026, 1, 15),
        amount=amount,
        description="test line",
        status=status,
        matched_journal_line_ids=tuple(gl_ids),
    )


def _stmt(
    bank_account_id=None,
    statement_date=None,
    opening=Decimal("10000"),
    closing=Decimal("10000"),
    lines=(),
):
    """Helper to build a BankReconStatement."""
    return BankReconStatement(
        statement_id=uuid4(),
        bank_account_id=bank_account_id or uuid4(),
        statement_date=statement_date or date(2026, 1, 31),
        opening_balance=opening,
        closing_balance=closing,
        currency="USD",
        lines=lines,
    )


def _ctx(
    bank_account_id=None,
    statements=(),
    recon_status=None,
    recon_variance=None,
    as_of_date=None,
):
    """Helper to build a BankReconContext."""
    return BankReconContext(
        bank_account_id=bank_account_id or uuid4(),
        statements=tuple(statements),
        reconciliation_status=recon_status,
        reconciliation_variance=recon_variance,
        as_of_date=as_of_date or date(2026, 2, 1),
    )


# =============================================================================
# BR-1: Stale Unmatched Lines
# =============================================================================


class TestStaleUnmatched:
    def test_fresh_unmatched_no_finding(self, checker):
        """Unmatched line within threshold produces no finding."""
        line = _line(status="unmatched", transaction_date=date(2026, 1, 20))
        stmt = _stmt(lines=(line,))
        ctx = _ctx(statements=(stmt,), as_of_date=date(2026, 2, 1))
        # 12 days old, threshold is 30
        findings = checker.check_stale_unmatched(context=ctx)
        assert len(findings) == 0

    def test_stale_unmatched_finding(self, checker):
        """Unmatched line beyond threshold produces WARNING."""
        line = _line(status="unmatched", transaction_date=date(2025, 12, 1))
        stmt = _stmt(lines=(line,))
        ctx = _ctx(statements=(stmt,), as_of_date=date(2026, 2, 1))
        # 62 days old
        findings = checker.check_stale_unmatched(context=ctx)
        assert len(findings) == 1
        assert findings[0].code == "STALE_UNMATCHED_LINE"
        assert findings[0].severity == CheckSeverity.WARNING
        assert findings[0].details["days_unmatched"] == 62

    def test_excluded_lines_skipped(self, checker):
        """Excluded lines are not flagged even if old."""
        line = _line(status="excluded", transaction_date=date(2025, 6, 1))
        stmt = _stmt(lines=(line,))
        ctx = _ctx(statements=(stmt,), as_of_date=date(2026, 2, 1))
        findings = checker.check_stale_unmatched(context=ctx)
        assert len(findings) == 0

    def test_matched_lines_skipped(self, checker):
        """Matched lines are not flagged even if old."""
        line = _line(status="matched", transaction_date=date(2025, 6, 1))
        stmt = _stmt(lines=(line,))
        ctx = _ctx(statements=(stmt,), as_of_date=date(2026, 2, 1))
        findings = checker.check_stale_unmatched(context=ctx)
        assert len(findings) == 0

    def test_suggested_lines_skipped(self, checker):
        """Suggested (pending confirmation) lines are not flagged."""
        line = _line(status="suggested", transaction_date=date(2025, 6, 1))
        stmt = _stmt(lines=(line,))
        ctx = _ctx(statements=(stmt,), as_of_date=date(2026, 2, 1))
        findings = checker.check_stale_unmatched(context=ctx)
        assert len(findings) == 0

    def test_boundary_at_threshold(self, checker):
        """Line exactly at threshold produces no finding."""
        line = _line(status="unmatched", transaction_date=date(2026, 1, 2))
        stmt = _stmt(lines=(line,))
        ctx = _ctx(statements=(stmt,), as_of_date=date(2026, 2, 1))
        # Exactly 30 days old, threshold is 30 (> not >=)
        findings = checker.check_stale_unmatched(context=ctx)
        assert len(findings) == 0

    def test_boundary_one_past_threshold(self, checker):
        """Line one day past threshold produces finding."""
        line = _line(status="unmatched", transaction_date=date(2026, 1, 1))
        stmt = _stmt(lines=(line,))
        ctx = _ctx(statements=(stmt,), as_of_date=date(2026, 2, 1))
        # 31 days old
        findings = checker.check_stale_unmatched(context=ctx)
        assert len(findings) == 1

    def test_custom_threshold(self, checker):
        """Custom threshold is respected."""
        line = _line(status="unmatched", transaction_date=date(2026, 1, 20))
        stmt = _stmt(lines=(line,))
        ctx = _ctx(statements=(stmt,), as_of_date=date(2026, 2, 1))
        # 12 days old, custom threshold 10
        findings = checker.check_stale_unmatched(context=ctx, stale_threshold_days=10)
        assert len(findings) == 1

    def test_multiple_stale_across_statements(self, checker):
        """Multiple stale lines across multiple statements are all flagged."""
        l1 = _line(status="unmatched", transaction_date=date(2025, 11, 1))
        l2 = _line(status="unmatched", transaction_date=date(2025, 12, 1))
        s1 = _stmt(statement_date=date(2025, 11, 30), lines=(l1,))
        s2 = _stmt(statement_date=date(2025, 12, 31), lines=(l2,))
        ctx = _ctx(statements=(s1, s2), as_of_date=date(2026, 2, 1))
        findings = checker.check_stale_unmatched(context=ctx)
        assert len(findings) == 2


# =============================================================================
# BR-2: Balance Continuity
# =============================================================================


class TestBalanceContinuity:
    def test_continuous_balances_pass(self, checker):
        """Continuous balances produce no findings."""
        ba = uuid4()
        s1 = _stmt(bank_account_id=ba, statement_date=date(2026, 1, 31),
                    opening=Decimal("10000"), closing=Decimal("12000"))
        s2 = _stmt(bank_account_id=ba, statement_date=date(2026, 2, 28),
                    opening=Decimal("12000"), closing=Decimal("14000"))
        ctx = _ctx(bank_account_id=ba, statements=(s1, s2))
        findings = checker.check_balance_continuity(context=ctx)
        assert len(findings) == 0

    def test_gap_between_statements(self, checker):
        """Balance gap produces ERROR finding."""
        ba = uuid4()
        s1 = _stmt(bank_account_id=ba, statement_date=date(2026, 1, 31),
                    opening=Decimal("10000"), closing=Decimal("12000"))
        s2 = _stmt(bank_account_id=ba, statement_date=date(2026, 2, 28),
                    opening=Decimal("11500"), closing=Decimal("14000"))
        ctx = _ctx(bank_account_id=ba, statements=(s1, s2))
        findings = checker.check_balance_continuity(context=ctx)
        assert len(findings) == 1
        assert findings[0].code == "BALANCE_DISCONTINUITY"
        assert findings[0].severity == CheckSeverity.ERROR
        assert findings[0].details["gap"] == "-500"

    def test_single_statement_trivially_passes(self, checker):
        """Single statement has no pairs to compare."""
        s1 = _stmt(statement_date=date(2026, 1, 31))
        ctx = _ctx(statements=(s1,))
        findings = checker.check_balance_continuity(context=ctx)
        assert len(findings) == 0

    def test_no_statements_trivially_passes(self, checker):
        """No statements at all."""
        ctx = _ctx(statements=())
        findings = checker.check_balance_continuity(context=ctx)
        assert len(findings) == 0

    def test_multiple_gaps(self, checker):
        """Multiple gaps across 3 statements."""
        ba = uuid4()
        s1 = _stmt(bank_account_id=ba, statement_date=date(2026, 1, 31),
                    opening=Decimal("10000"), closing=Decimal("12000"))
        s2 = _stmt(bank_account_id=ba, statement_date=date(2026, 2, 28),
                    opening=Decimal("11000"), closing=Decimal("13000"))
        s3 = _stmt(bank_account_id=ba, statement_date=date(2026, 3, 31),
                    opening=Decimal("14000"), closing=Decimal("15000"))
        ctx = _ctx(bank_account_id=ba, statements=(s1, s2, s3))
        findings = checker.check_balance_continuity(context=ctx)
        assert len(findings) == 2

    def test_sorts_by_date(self, checker):
        """Statements provided out of order are sorted before comparison."""
        ba = uuid4()
        s_feb = _stmt(bank_account_id=ba, statement_date=date(2026, 2, 28),
                      opening=Decimal("12000"), closing=Decimal("14000"))
        s_jan = _stmt(bank_account_id=ba, statement_date=date(2026, 1, 31),
                      opening=Decimal("10000"), closing=Decimal("12000"))
        # Provide Feb before Jan -- should still pass
        ctx = _ctx(bank_account_id=ba, statements=(s_feb, s_jan))
        findings = checker.check_balance_continuity(context=ctx)
        assert len(findings) == 0


# =============================================================================
# BR-3: Duplicate GL Match
# =============================================================================


class TestDuplicateGlMatch:
    def test_unique_matches_pass(self, checker):
        """Each GL line matched to exactly one statement line -- clean."""
        gl1, gl2 = uuid4(), uuid4()
        l1 = _line(status="matched", gl_ids=(gl1,))
        l2 = _line(status="matched", gl_ids=(gl2,))
        stmt = _stmt(lines=(l1, l2))
        ctx = _ctx(statements=(stmt,))
        findings = checker.check_duplicate_gl_matches(context=ctx)
        assert len(findings) == 0

    def test_duplicate_gl_match(self, checker):
        """Same GL line matched to two different statement lines -- ERROR."""
        shared_gl = uuid4()
        l1 = _line(status="matched", gl_ids=(shared_gl,))
        l2 = _line(status="matched", gl_ids=(shared_gl,))
        stmt = _stmt(lines=(l1, l2))
        ctx = _ctx(statements=(stmt,))
        findings = checker.check_duplicate_gl_matches(context=ctx)
        assert len(findings) == 1
        assert findings[0].code == "DUPLICATE_GL_MATCH"
        assert findings[0].severity == CheckSeverity.ERROR
        assert findings[0].details["matched_to_count"] == 2

    def test_duplicate_across_statements(self, checker):
        """Duplicate GL match across different statements."""
        shared_gl = uuid4()
        l1 = _line(status="matched", gl_ids=(shared_gl,))
        l2 = _line(status="matched", gl_ids=(shared_gl,))
        s1 = _stmt(statement_date=date(2026, 1, 31), lines=(l1,))
        s2 = _stmt(statement_date=date(2026, 2, 28), lines=(l2,))
        ctx = _ctx(statements=(s1, s2))
        findings = checker.check_duplicate_gl_matches(context=ctx)
        assert len(findings) == 1

    def test_multiple_gl_per_line_no_dup(self, checker):
        """One statement line matched to multiple GL lines (normal case)."""
        gl1, gl2 = uuid4(), uuid4()
        l1 = _line(status="matched", gl_ids=(gl1, gl2))
        stmt = _stmt(lines=(l1,))
        ctx = _ctx(statements=(stmt,))
        findings = checker.check_duplicate_gl_matches(context=ctx)
        assert len(findings) == 0

    def test_unmatched_lines_have_no_gl_ids(self, checker):
        """Unmatched lines with empty gl_ids don't trigger."""
        l1 = _line(status="unmatched")
        l2 = _line(status="unmatched")
        stmt = _stmt(lines=(l1, l2))
        ctx = _ctx(statements=(stmt,))
        findings = checker.check_duplicate_gl_matches(context=ctx)
        assert len(findings) == 0

    def test_multiple_duplicates(self, checker):
        """Multiple GL IDs each duplicated."""
        gl_a, gl_b = uuid4(), uuid4()
        l1 = _line(status="matched", gl_ids=(gl_a, gl_b))
        l2 = _line(status="matched", gl_ids=(gl_a,))
        l3 = _line(status="matched", gl_ids=(gl_b,))
        stmt = _stmt(lines=(l1, l2, l3))
        ctx = _ctx(statements=(stmt,))
        findings = checker.check_duplicate_gl_matches(context=ctx)
        assert len(findings) == 2  # One per duplicated GL


# =============================================================================
# BR-4: Unexplained Variance
# =============================================================================


class TestUnexplainedVariance:
    def test_completed_zero_variance_pass(self, checker):
        """Completed recon with zero variance -- clean."""
        ctx = _ctx(
            recon_status="completed",
            recon_variance=Decimal("0.00"),
        )
        findings = checker.check_unexplained_variance(context=ctx)
        assert len(findings) == 0

    def test_completed_within_tolerance_pass(self, checker):
        """Completed recon with variance within tolerance -- clean."""
        ctx = _ctx(
            recon_status="completed",
            recon_variance=Decimal("0.005"),
        )
        findings = checker.check_unexplained_variance(context=ctx)
        assert len(findings) == 0

    def test_completed_with_variance_warning(self, checker):
        """Completed recon with variance beyond tolerance -- WARNING."""
        ctx = _ctx(
            recon_status="completed",
            recon_variance=Decimal("5.50"),
        )
        findings = checker.check_unexplained_variance(context=ctx)
        assert len(findings) == 1
        assert findings[0].code == "UNEXPLAINED_VARIANCE"
        assert findings[0].severity == CheckSeverity.WARNING

    def test_completed_negative_variance(self, checker):
        """Negative variance beyond tolerance is also flagged."""
        ctx = _ctx(
            recon_status="completed",
            recon_variance=Decimal("-2.00"),
        )
        findings = checker.check_unexplained_variance(context=ctx)
        assert len(findings) == 1

    def test_draft_recon_skipped(self, checker):
        """Draft reconciliation is not checked."""
        ctx = _ctx(
            recon_status="draft",
            recon_variance=Decimal("1000.00"),
        )
        findings = checker.check_unexplained_variance(context=ctx)
        assert len(findings) == 0

    def test_no_recon_status_skipped(self, checker):
        """No reconciliation status means nothing to check."""
        ctx = _ctx(
            recon_status=None,
            recon_variance=Decimal("500.00"),
        )
        findings = checker.check_unexplained_variance(context=ctx)
        assert len(findings) == 0

    def test_completed_no_variance_value(self, checker):
        """Completed with None variance -- skip gracefully."""
        ctx = _ctx(
            recon_status="completed",
            recon_variance=None,
        )
        findings = checker.check_unexplained_variance(context=ctx)
        assert len(findings) == 0

    def test_custom_tolerance(self, checker):
        """Custom tolerance is respected."""
        ctx = _ctx(
            recon_status="completed",
            recon_variance=Decimal("0.50"),
        )
        # Default tolerance (0.01) would flag this
        findings = checker.check_unexplained_variance(context=ctx)
        assert len(findings) == 1
        # With higher tolerance it passes
        findings = checker.check_unexplained_variance(
            context=ctx, tolerance=Decimal("1.00"),
        )
        assert len(findings) == 0


# =============================================================================
# run_all_checks orchestrator
# =============================================================================


class TestRunAllChecks:
    def test_clean_context(self, checker):
        """Clean context with all matched lines -- PASSED."""
        ba = uuid4()
        gl1 = uuid4()
        l1 = _line(status="matched", gl_ids=(gl1,))
        s1 = _stmt(bank_account_id=ba, statement_date=date(2026, 1, 31),
                    opening=Decimal("10000"), closing=Decimal("10100"),
                    lines=(l1,))
        ctx = _ctx(bank_account_id=ba, statements=(s1,),
                   as_of_date=date(2026, 2, 1))
        result = checker.run_all_checks(context=ctx)
        assert result.status == CheckStatus.PASSED
        assert result.is_clean
        assert result.statements_checked == 1
        assert result.lines_checked == 1
        assert len(result.checks_performed) == 4

    def test_multiple_findings_compound(self, checker):
        """Context with multiple issues produces compound result."""
        ba = uuid4()
        shared_gl = uuid4()
        # Stale unmatched line
        stale_line = _line(status="unmatched", transaction_date=date(2025, 11, 1))
        # Duplicate GL match
        dup1 = _line(status="matched", gl_ids=(shared_gl,))
        dup2 = _line(status="matched", gl_ids=(shared_gl,))

        s1 = _stmt(bank_account_id=ba, statement_date=date(2026, 1, 31),
                    opening=Decimal("10000"), closing=Decimal("12000"),
                    lines=(stale_line, dup1, dup2))
        ctx = _ctx(bank_account_id=ba, statements=(s1,),
                   as_of_date=date(2026, 2, 1))
        result = checker.run_all_checks(context=ctx)
        assert result.status == CheckStatus.FAILED  # ERROR from dup
        assert result.error_count >= 1  # DUPLICATE_GL_MATCH
        assert result.warning_count >= 1  # STALE_UNMATCHED_LINE

    def test_warning_only_status(self, checker):
        """Only warnings (no errors) produces WARNING status."""
        ba = uuid4()
        stale_line = _line(status="unmatched", transaction_date=date(2025, 11, 1))
        s1 = _stmt(bank_account_id=ba, statement_date=date(2026, 1, 31),
                    opening=Decimal("10000"), closing=Decimal("10000"),
                    lines=(stale_line,))
        ctx = _ctx(bank_account_id=ba, statements=(s1,),
                   as_of_date=date(2026, 2, 1))
        result = checker.run_all_checks(context=ctx)
        assert result.status == CheckStatus.WARNING
        assert result.error_count == 0
        assert result.warning_count == 1

    def test_empty_context(self, checker):
        """Empty context (no statements) passes cleanly."""
        ba = uuid4()
        ctx = _ctx(bank_account_id=ba, statements=(),
                   as_of_date=date(2026, 2, 1))
        result = checker.run_all_checks(context=ctx)
        assert result.status == CheckStatus.PASSED
        assert result.is_clean
        assert result.statements_checked == 0
        assert result.lines_checked == 0
