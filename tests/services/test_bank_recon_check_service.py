"""
Tests for BankReconciliationCheckService -- GAP-BRC Phase 2.

Covers service-level orchestration: clock injection, threshold configuration,
default date substitution, batch checks, and end-to-end through the service.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.clock import DeterministicClock
from finance_engines.reconciliation.lifecycle_types import CheckSeverity, CheckStatus
from finance_engines.reconciliation.bank_recon_types import (
    BankReconContext,
    BankReconLine,
    BankReconStatement,
)
from finance_services.bank_reconciliation_check_service import (
    BankReconciliationCheckService,
)


@pytest.fixture
def clock():
    return DeterministicClock(datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def service(clock):
    return BankReconciliationCheckService(clock=clock)


def _line(status="matched", transaction_date=None, amount=Decimal("100"), gl_ids=()):
    return BankReconLine(
        line_id=uuid4(),
        transaction_date=transaction_date or date(2026, 1, 15),
        amount=amount,
        description="test",
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
    return BankReconStatement(
        statement_id=uuid4(),
        bank_account_id=bank_account_id or uuid4(),
        statement_date=statement_date or date(2026, 1, 31),
        opening_balance=opening,
        closing_balance=closing,
        currency="USD",
        lines=lines,
    )


class TestCheckSingle:
    def test_clean_context_passes(self, service):
        """All matched lines, no issues -> PASSED."""
        ba = uuid4()
        gl = uuid4()
        line = _line(status="matched", gl_ids=(gl,))
        stmt = _stmt(bank_account_id=ba, lines=(line,))
        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(stmt,),
            as_of_date=date(2026, 2, 1),
        )
        result = service.check(ctx)
        assert result.status == CheckStatus.PASSED
        assert result.is_clean

    def test_stale_unmatched_detected(self, service):
        """Stale unmatched line detected as WARNING."""
        ba = uuid4()
        line = _line(status="unmatched", transaction_date=date(2025, 12, 1))
        stmt = _stmt(bank_account_id=ba, lines=(line,))
        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(stmt,),
            as_of_date=date(2026, 2, 1),
        )
        result = service.check(ctx)
        assert result.status == CheckStatus.WARNING
        assert any(f.code == "STALE_UNMATCHED_LINE" for f in result.findings)

    def test_duplicate_gl_detected(self, service):
        """Duplicate GL match detected as ERROR -> FAILED."""
        ba = uuid4()
        shared_gl = uuid4()
        l1 = _line(status="matched", gl_ids=(shared_gl,))
        l2 = _line(status="matched", gl_ids=(shared_gl,))
        stmt = _stmt(bank_account_id=ba, lines=(l1, l2))
        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(stmt,),
            as_of_date=date(2026, 2, 1),
        )
        result = service.check(ctx)
        assert result.status == CheckStatus.FAILED
        assert any(f.code == "DUPLICATE_GL_MATCH" for f in result.findings)

    def test_empty_context_passes(self, service):
        """Empty context (no statements) -> PASSED."""
        ba = uuid4()
        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(),
            as_of_date=date(2026, 2, 1),
        )
        result = service.check(ctx)
        assert result.status == CheckStatus.PASSED
        assert result.statements_checked == 0

    def test_default_date_uses_clock(self, service):
        """Context with default as_of_date gets clock date substituted."""
        ba = uuid4()
        # Line from 60 days before clock date (2026-02-01)
        line = _line(status="unmatched", transaction_date=date(2025, 12, 3))
        stmt = _stmt(bank_account_id=ba, lines=(line,))
        # Use default as_of_date (2000-01-01)
        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(stmt,),
        )
        result = service.check(ctx)
        # Clock is 2026-02-01, line is from 2025-12-03 = 60 days old > 30
        assert result.status == CheckStatus.WARNING
        assert result.as_of_date == date(2026, 2, 1)

    def test_custom_stale_threshold(self, clock):
        """Custom stale threshold is respected."""
        svc = BankReconciliationCheckService(
            clock=clock, stale_threshold_days=7,
        )
        ba = uuid4()
        line = _line(status="unmatched", transaction_date=date(2026, 1, 20))
        stmt = _stmt(bank_account_id=ba, lines=(line,))
        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(stmt,),
            as_of_date=date(2026, 2, 1),
        )
        # 12 days old, threshold is 7
        result = svc.check(ctx)
        assert any(f.code == "STALE_UNMATCHED_LINE" for f in result.findings)

    def test_custom_variance_tolerance(self, clock):
        """Custom variance tolerance is respected."""
        svc = BankReconciliationCheckService(
            clock=clock, variance_tolerance=Decimal("10.00"),
        )
        ba = uuid4()
        ctx = BankReconContext(
            bank_account_id=ba,
            statements=(),
            reconciliation_status="completed",
            reconciliation_variance=Decimal("5.00"),
            as_of_date=date(2026, 2, 1),
        )
        # Variance 5.00 < tolerance 10.00 -> pass
        result = svc.check(ctx)
        assert not any(f.code == "UNEXPLAINED_VARIANCE" for f in result.findings)


class TestCheckMultiple:
    def test_batch_check(self, service):
        """Multiple contexts checked in batch."""
        ba1, ba2 = uuid4(), uuid4()
        ctx1 = BankReconContext(
            bank_account_id=ba1,
            statements=(),
            as_of_date=date(2026, 2, 1),
        )
        ctx2 = BankReconContext(
            bank_account_id=ba2,
            statements=(),
            as_of_date=date(2026, 2, 1),
        )
        results = service.check_multiple([ctx1, ctx2])
        assert len(results) == 2
        assert results[0].bank_account_id == ba1
        assert results[1].bank_account_id == ba2

    def test_batch_mixed_results(self, service):
        """Batch with one clean and one problematic context."""
        ba_clean = uuid4()
        ba_bad = uuid4()
        shared_gl = uuid4()

        ctx_clean = BankReconContext(
            bank_account_id=ba_clean,
            statements=(),
            as_of_date=date(2026, 2, 1),
        )
        l1 = _line(status="matched", gl_ids=(shared_gl,))
        l2 = _line(status="matched", gl_ids=(shared_gl,))
        stmt = _stmt(bank_account_id=ba_bad, lines=(l1, l2))
        ctx_bad = BankReconContext(
            bank_account_id=ba_bad,
            statements=(stmt,),
            as_of_date=date(2026, 2, 1),
        )

        results = service.check_multiple([ctx_clean, ctx_bad])
        assert results[0].status == CheckStatus.PASSED
        assert results[1].status == CheckStatus.FAILED

    def test_empty_batch(self, service):
        """Empty batch returns empty list."""
        results = service.check_multiple([])
        assert results == []
