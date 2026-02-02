"""
Tests for bank reconciliation domain types -- GAP-BRC Phase 0.

Covers BankReconLine, BankReconStatement, BankReconContext,
BankReconCheckResult, and factory methods.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_engines.reconciliation.lifecycle_types import (
    CheckSeverity,
    CheckStatus,
    ReconciliationFinding,
)
from finance_engines.reconciliation.bank_recon_types import (
    BankReconCheckResult,
    BankReconContext,
    BankReconLine,
    BankReconStatement,
)


# =============================================================================
# BankReconLine
# =============================================================================


class TestBankReconLine:
    def test_construction(self):
        lid = uuid4()
        line = BankReconLine(
            line_id=lid,
            transaction_date=date(2026, 1, 10),
            amount=Decimal("500.00"),
            description="Wire transfer",
            status="unmatched",
        )
        assert line.line_id == lid
        assert line.status == "unmatched"
        assert line.matched_journal_line_ids == ()

    def test_with_matches(self):
        gl1, gl2 = uuid4(), uuid4()
        line = BankReconLine(
            line_id=uuid4(),
            transaction_date=date(2026, 1, 10),
            amount=Decimal("500.00"),
            description="Payment",
            status="matched",
            matched_journal_line_ids=(gl1, gl2),
        )
        assert len(line.matched_journal_line_ids) == 2

    def test_frozen(self):
        line = BankReconLine(
            line_id=uuid4(),
            transaction_date=date(2026, 1, 10),
            amount=Decimal("500.00"),
            description="test",
            status="unmatched",
        )
        with pytest.raises(AttributeError):
            line.status = "matched"  # type: ignore[misc]


# =============================================================================
# BankReconStatement
# =============================================================================


class TestBankReconStatement:
    def test_construction(self):
        sid = uuid4()
        ba = uuid4()
        stmt = BankReconStatement(
            statement_id=sid,
            bank_account_id=ba,
            statement_date=date(2026, 1, 31),
            opening_balance=Decimal("10000.00"),
            closing_balance=Decimal("12000.00"),
            currency="USD",
        )
        assert stmt.line_count == 0
        assert stmt.unmatched_count == 0
        assert stmt.matched_count == 0

    def test_line_counts(self):
        lines = (
            BankReconLine(uuid4(), date(2026, 1, 5), Decimal("100"), "a", "matched"),
            BankReconLine(uuid4(), date(2026, 1, 10), Decimal("200"), "b", "unmatched"),
            BankReconLine(uuid4(), date(2026, 1, 15), Decimal("300"), "c", "unmatched"),
            BankReconLine(uuid4(), date(2026, 1, 20), Decimal("400"), "d", "excluded"),
        )
        stmt = BankReconStatement(
            statement_id=uuid4(),
            bank_account_id=uuid4(),
            statement_date=date(2026, 1, 31),
            opening_balance=Decimal("10000"),
            closing_balance=Decimal("11000"),
            currency="USD",
            lines=lines,
        )
        assert stmt.line_count == 4
        assert stmt.matched_count == 1
        assert stmt.unmatched_count == 2

    def test_frozen(self):
        stmt = BankReconStatement(
            statement_id=uuid4(),
            bank_account_id=uuid4(),
            statement_date=date(2026, 1, 31),
            opening_balance=Decimal("10000"),
            closing_balance=Decimal("12000"),
            currency="USD",
        )
        with pytest.raises(AttributeError):
            stmt.closing_balance = Decimal("0")  # type: ignore[misc]


# =============================================================================
# BankReconContext
# =============================================================================


class TestBankReconContext:
    def test_empty_context(self):
        ba = uuid4()
        ctx = BankReconContext(
            bank_account_id=ba,
            as_of_date=date(2026, 2, 1),
        )
        assert ctx.statement_count == 0
        assert ctx.total_lines == 0
        assert ctx.reconciliation_status is None

    def test_with_statements(self):
        ba = uuid4()
        line = BankReconLine(uuid4(), date(2026, 1, 10), Decimal("100"), "x", "matched")
        stmt = BankReconStatement(
            uuid4(), ba, date(2026, 1, 31),
            Decimal("10000"), Decimal("10100"), "USD",
            lines=(line,),
        )
        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(stmt,),
            reconciliation_status="completed",
            reconciliation_variance=Decimal("0.00"),
            as_of_date=date(2026, 2, 1),
        )
        assert ctx.statement_count == 1
        assert ctx.total_lines == 1
        assert ctx.reconciliation_status == "completed"


# =============================================================================
# BankReconCheckResult
# =============================================================================


class TestBankReconCheckResult:
    def test_clean_result(self):
        ba = uuid4()
        result = BankReconCheckResult(
            bank_account_id=ba,
            status=CheckStatus.PASSED,
            statements_checked=2,
            lines_checked=10,
        )
        assert result.is_clean
        assert result.error_count == 0
        assert result.warning_count == 0

    def test_from_findings_passed(self):
        ba = uuid4()
        result = BankReconCheckResult.from_findings(
            bank_account_id=ba,
            findings=(),
            statements_checked=1,
            lines_checked=5,
            checks_performed=("BR-1",),
            as_of_date=date(2026, 2, 1),
        )
        assert result.status == CheckStatus.PASSED
        assert result.is_clean

    def test_from_findings_warning(self):
        ba = uuid4()
        findings = (
            ReconciliationFinding(
                code="STALE_UNMATCHED_LINE",
                severity=CheckSeverity.WARNING,
                message="stale line",
            ),
        )
        result = BankReconCheckResult.from_findings(
            bank_account_id=ba,
            findings=findings,
            statements_checked=1,
            lines_checked=5,
            checks_performed=("BR-1",),
            as_of_date=date(2026, 2, 1),
        )
        assert result.status == CheckStatus.WARNING
        assert result.warning_count == 1
        assert result.error_count == 0

    def test_from_findings_failed(self):
        ba = uuid4()
        findings = (
            ReconciliationFinding(
                code="DUPLICATE_GL_MATCH",
                severity=CheckSeverity.ERROR,
                message="dup",
            ),
            ReconciliationFinding(
                code="STALE_UNMATCHED_LINE",
                severity=CheckSeverity.WARNING,
                message="stale",
            ),
        )
        result = BankReconCheckResult.from_findings(
            bank_account_id=ba,
            findings=findings,
            statements_checked=2,
            lines_checked=15,
            checks_performed=("BR-1", "BR-3"),
            as_of_date=date(2026, 2, 1),
        )
        assert result.status == CheckStatus.FAILED
        assert result.error_count == 1
        assert result.warning_count == 1
        assert not result.is_clean
