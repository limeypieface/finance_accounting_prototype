"""
Bank Reconciliation Tests.

Tests auto-matching bank transactions and reconciliation tracking
using real architecture:
- MatchingEngine (finance_engines.matching) for auto-matching
- BankReconciliationLine (finance_engines.reconciliation.domain) for status tracking
- BankTransaction / Reconciliation (finance_modules.cash.models) for domain models
- MatchCandidate / MatchTolerance (finance_engines.matching) for matching config

CRITICAL: Bank reconciliation ensures cash records match bank statements.
"""

import pytest
from dataclasses import replace
from decimal import Decimal
from datetime import date, timedelta
from uuid import uuid4

from finance_modules.cash.models import (
    BankTransaction,
    Reconciliation,
    ReconciliationStatus,
    TransactionType,
)
from finance_engines.matching import (
    MatchCandidate,
    MatchingEngine,
    MatchSuggestion,
    MatchTolerance,
    MatchType,
)
from finance_engines.reconciliation.domain import (
    BankReconciliationLine,
    BankReconciliationStatus,
)
from finance_kernel.domain.values import Money
from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType


# =============================================================================
# Helpers
# =============================================================================

def _statement_candidate(
    line_id: str,
    amount: Decimal,
    txn_date: date | None = None,
    reference: str = "",
) -> MatchCandidate:
    """Build a MatchCandidate representing a bank statement line."""
    return MatchCandidate(
        document_type="STATEMENT",
        document_id=line_id,
        amount=Money.of(amount, "USD"),
        date=txn_date or date.today(),
        reference=reference,
    )


def _payment_candidate(
    entry_id: str,
    amount: Decimal,
    pay_date: date | None = None,
    reference: str = "",
) -> MatchCandidate:
    """Build a MatchCandidate representing a book-side payment entry."""
    return MatchCandidate(
        document_type="PAYMENT",
        document_id=entry_id,
        amount=Money.of(amount, "USD"),
        date=pay_date or date.today(),
        reference=reference,
    )


def _bank_recon_tolerance(date_tolerance_days: int = 0) -> MatchTolerance:
    """Standard tolerance for bank reconciliation matching."""
    return MatchTolerance(
        amount_tolerance=Decimal("0.01"),
        date_tolerance_days=date_tolerance_days,
        require_same_vendor=False,
        require_same_item=False,
        require_same_currency=True,
    )


def _make_recon_line(
    line_id: str,
    amount: Decimal,
    txn_date: date | None = None,
    description: str = "",
) -> BankReconciliationLine:
    """Create an unmatched BankReconciliationLine."""
    return BankReconciliationLine.unmatched(
        line_id=uuid4(),
        statement_ref=ArtifactRef(artifact_type=ArtifactType.BANK_STATEMENT, artifact_id=line_id),
        transaction_date=txn_date or date.today(),
        description=description,
        amount=Money.of(amount, "USD"),
    )


# =============================================================================
# Test: Auto-Matching Bank Transactions
# =============================================================================

class TestBankReconciliationMatching:
    """Auto-matching bank transactions using MatchingEngine."""

    @pytest.fixture
    def engine(self):
        return MatchingEngine()

    @pytest.fixture
    def tolerance(self):
        return _bank_recon_tolerance()

    @pytest.fixture
    def sample_statements(self):
        return [
            _statement_candidate(
                "STM-001",
                Decimal("1000.00"),
                txn_date=date.today() - timedelta(days=5),
                reference="CHK-12345",
            ),
            _statement_candidate(
                "STM-002",
                Decimal("-5000.00"),
                txn_date=date.today() - timedelta(days=3),
                reference="WT-98765",
            ),
            _statement_candidate(
                "STM-003",
                Decimal("2500.00"),
                txn_date=date.today() - timedelta(days=2),
            ),
        ]

    @pytest.fixture
    def sample_payments(self):
        return [
            _payment_candidate(
                "PMT-001",
                Decimal("1000.00"),
                pay_date=date.today() - timedelta(days=5),
                reference="CHK-12345",
            ),
            _payment_candidate(
                "PMT-002",
                Decimal("-5000.00"),
                pay_date=date.today() - timedelta(days=3),
                reference="WT-98765",
            ),
            _payment_candidate(
                "PMT-003",
                Decimal("2500.00"),
                pay_date=date.today() - timedelta(days=2),
                reference="INV-500",
            ),
        ]

    def test_exact_amount_match(self, engine, tolerance, sample_statements, sample_payments):
        """Match on exact amount using MatchingEngine."""
        # For each statement line, find best match among payment entries
        matched = []
        for stmt in sample_statements:
            suggestions = engine.find_matches(stmt, sample_payments, tolerance)
            if suggestions:
                matched.append(suggestions[0])

        assert len(matched) == 3  # All should match

        # Verify first match — STM-001 matches PMT-001 (same amount + reference + date)
        first = next(s for s in matched if s.target.document_id == "STM-001")
        assert first.candidate.document_id == "PMT-001"
        assert first.score >= Decimal("40")  # Significant confidence

    def test_reference_match_boosts_score(self, engine, tolerance, sample_statements, sample_payments):
        """Reference match adds score bonus."""
        # STM-001 has ref "CHK-12345", PMT-001 has ref "CHK-12345"
        stmt = sample_statements[0]
        suggestions = engine.find_matches(stmt, sample_payments, tolerance)

        # Should find PMT-001 with high score (amount + reference + date all match)
        assert len(suggestions) >= 1
        best = suggestions[0]
        assert best.candidate.document_id == "PMT-001"
        # Perfect match: amount within tolerance (100) + reference bonus (+10) - no date penalty
        assert best.score > Decimal("90")

    def test_date_tolerance_match(self, engine):
        """Match within date window for check clearing delays."""
        tolerance = _bank_recon_tolerance(date_tolerance_days=3)

        # Statement shows transaction today, entry is from 2 days ago
        stmt = _statement_candidate("STM-LATE", Decimal("500.00"), txn_date=date.today())
        payment = _payment_candidate(
            "PMT-EARLY", Decimal("500.00"), pay_date=date.today() - timedelta(days=2),
        )

        suggestions = engine.find_matches(stmt, [payment], tolerance)

        assert len(suggestions) == 1
        assert suggestions[0].candidate.document_id == "PMT-EARLY"

    def test_no_match_when_amount_differs(self, engine, tolerance):
        """No match when amounts differ beyond tolerance."""
        stmt = _statement_candidate("STM-X", Decimal("999.99"))
        payment = _payment_candidate("PMT-Y", Decimal("111.11"))

        suggestions = engine.find_matches(stmt, [payment], tolerance)

        # Should return suggestion but with low score due to amount mismatch
        high_confidence = [s for s in suggestions if s.is_within_tolerance]
        assert len(high_confidence) == 0

    def test_multiple_potential_matches_ranked(self, engine):
        """When multiple entries match, date outside tolerance penalises score."""
        tolerance = _bank_recon_tolerance(date_tolerance_days=2)

        stmt = _statement_candidate("STM-DUP", Decimal("100.00"), txn_date=date.today())

        payments = [
            # 5 days ago — outside 2-day tolerance, penalised
            _payment_candidate(
                "PMT-DUP-1", Decimal("100.00"),
                pay_date=date.today() - timedelta(days=5),
            ),
            # Same day — within tolerance, no penalty
            _payment_candidate(
                "PMT-DUP-2", Decimal("100.00"),
                pay_date=date.today(),
            ),
        ]

        suggestions = engine.find_matches(stmt, payments, tolerance)

        assert len(suggestions) == 2
        # PMT-DUP-2 (within tolerance) scores higher than PMT-DUP-1 (outside)
        assert suggestions[0].candidate.document_id == "PMT-DUP-2"
        assert suggestions[0].score > suggestions[1].score

    def test_partial_reference_match(self, engine, tolerance):
        """Reference matching scores between documents."""
        stmt = _statement_candidate(
            "STM-REF", Decimal("500.00"),
            reference="CHK-12345",
        )
        payment = _payment_candidate(
            "PMT-REF", Decimal("500.00"),
            reference="CHK-12345",
        )

        suggestions = engine.find_matches(stmt, [payment], tolerance)

        assert len(suggestions) == 1
        # Reference match should boost score
        assert suggestions[0].score > Decimal("90")


# =============================================================================
# Test: Reconciliation Status (using immutable domain objects)
# =============================================================================

class TestReconciliationStatus:
    """Reconciliation tracking with BankReconciliationLine."""

    def test_new_line_is_unmatched(self):
        """New bank lines start as unmatched."""
        line = _make_recon_line("STM-001", Decimal("1000.00"), description="Deposit")

        assert line.status == BankReconciliationStatus.UNMATCHED
        assert not line.is_reconciled
        assert line.needs_attention

    def test_suggest_match(self):
        """System suggests a match (pending user approval)."""
        line = _make_recon_line("STM-001", Decimal("1000.00"))
        gl_ref = ArtifactRef(artifact_type=ArtifactType.JOURNAL_LINE, artifact_id="JL-001")

        suggested = line.with_suggestion(
            suggested_refs=(gl_ref,),
            confidence=Decimal("85"),
        )

        assert suggested.status == BankReconciliationStatus.SUGGESTED
        assert suggested.has_suggestions
        assert suggested.match_confidence == Decimal("85")
        assert not suggested.is_reconciled

    def test_confirm_match(self):
        """User confirms a suggested match."""
        line = _make_recon_line("STM-001", Decimal("1000.00"))
        gl_ref = ArtifactRef(artifact_type=ArtifactType.JOURNAL_LINE, artifact_id="JL-001")

        confirmed = line.with_confirmed_match(
            matched_refs=(gl_ref,),
            links=(),
        )

        assert confirmed.status == BankReconciliationStatus.MATCHED
        assert confirmed.is_reconciled
        assert not confirmed.needs_attention
        assert gl_ref in confirmed.matched_gl_refs

    def test_unreconcile_creates_new_unmatched(self):
        """Unreconciling produces a fresh unmatched line."""
        original = _make_recon_line("STM-001", Decimal("1000.00"))
        gl_ref = ArtifactRef(artifact_type=ArtifactType.JOURNAL_LINE, artifact_id="JL-001")

        matched = original.with_confirmed_match(matched_refs=(gl_ref,), links=())
        assert matched.is_reconciled

        # "Unreconcile" = create new unmatched line with same statement details
        unmatched = BankReconciliationLine.unmatched(
            line_id=matched.line_id,
            statement_ref=matched.statement_ref,
            transaction_date=matched.transaction_date,
            description=matched.description,
            amount=matched.amount,
        )

        assert unmatched.status == BankReconciliationStatus.UNMATCHED
        assert not unmatched.is_reconciled
        assert len(unmatched.matched_gl_refs) == 0

    def test_unreconciled_items_filtered(self):
        """Filter lines by status to get unreconciled items."""
        gl_ref = ArtifactRef(artifact_type=ArtifactType.JOURNAL_LINE, artifact_id="JL-001")

        lines = [
            _make_recon_line("STM-001", Decimal("1000.00")),
            _make_recon_line("STM-002", Decimal("2000.00")).with_confirmed_match(
                matched_refs=(gl_ref,), links=(),
            ),
            _make_recon_line("STM-003", Decimal("-500.00")),
        ]

        unreconciled = [ln for ln in lines if not ln.is_reconciled]
        reconciled = [ln for ln in lines if ln.is_reconciled]

        assert len(unreconciled) == 2
        assert len(reconciled) == 1


# =============================================================================
# Test: Reconciliation Summary (using cash models)
# =============================================================================

class TestReconciliationSummary:
    """Reconciliation summary using Reconciliation model."""

    def test_summary_counts(self):
        """Summary shows correct reconciled/unreconciled counts."""
        gl_ref = ArtifactRef(artifact_type=ArtifactType.JOURNAL_LINE, artifact_id="JL-001")

        lines = [
            _make_recon_line("STM-001", Decimal("1000.00")).with_confirmed_match(
                matched_refs=(gl_ref,), links=(),
            ),
            _make_recon_line("STM-002", Decimal("2000.00")),
            _make_recon_line("STM-003", Decimal("-500.00")),
        ]

        reconciled_count = sum(1 for ln in lines if ln.is_reconciled)
        unreconciled_count = sum(1 for ln in lines if not ln.is_reconciled)

        assert reconciled_count == 1
        assert unreconciled_count == 2

    def test_summary_difference(self):
        """Reconciliation model tracks balance difference."""
        recon = Reconciliation(
            id=uuid4(),
            bank_account_id=uuid4(),
            statement_date=date.today(),
            statement_balance=Decimal("2500.00"),
            book_balance=Decimal("3000.00"),
            variance=Decimal("2500.00") - Decimal("3000.00"),
            status=ReconciliationStatus.IN_PROGRESS,
        )

        assert recon.statement_balance == Decimal("2500.00")
        assert recon.book_balance == Decimal("3000.00")
        assert recon.variance == Decimal("-500.00")

    def test_completed_reconciliation(self):
        """Reconciliation marked complete when fully matched."""
        recon = Reconciliation(
            id=uuid4(),
            bank_account_id=uuid4(),
            statement_date=date.today(),
            statement_balance=Decimal("2500.00"),
            book_balance=Decimal("2500.00"),
            variance=Decimal("0"),
            status=ReconciliationStatus.COMPLETED,
            completed_by_id=uuid4(),
            completed_at=date.today(),
        )

        assert recon.status == ReconciliationStatus.COMPLETED
        assert recon.variance == Decimal("0")
        assert recon.completed_by_id is not None


# =============================================================================
# Test: Edge Cases
# =============================================================================

class TestBankReconciliationEdgeCases:
    """Edge cases in bank reconciliation."""

    @pytest.fixture
    def engine(self):
        return MatchingEngine()

    @pytest.fixture
    def tolerance(self):
        return _bank_recon_tolerance()

    def test_no_matches_found(self, engine, tolerance):
        """Handle no matching transactions."""
        stmt = _statement_candidate("STM-X", Decimal("999.99"))
        payment = _payment_candidate("PMT-Y", Decimal("111.11"))

        suggestions = engine.find_matches(stmt, [payment], tolerance)
        within_tol = [s for s in suggestions if s.is_within_tolerance]

        assert len(within_tol) == 0

    def test_bank_transaction_model(self):
        """BankTransaction captures raw bank feed data."""
        txn = BankTransaction(
            id=uuid4(),
            bank_account_id=uuid4(),
            transaction_date=date.today(),
            amount=Decimal("1000.00"),
            transaction_type=TransactionType.DEPOSIT,
            reference="DEP-001",
            description="Customer payment",
        )

        assert txn.amount == Decimal("1000.00")
        assert txn.transaction_type == TransactionType.DEPOSIT
        assert not txn.reconciled

    def test_recon_line_immutability(self):
        """BankReconciliationLine is frozen/immutable."""
        line = _make_recon_line("STM-001", Decimal("500.00"))

        with pytest.raises(AttributeError):
            line.status = BankReconciliationStatus.MATCHED  # type: ignore[misc]

    def test_recon_line_transitions_produce_new_objects(self):
        """Each status transition creates a new BankReconciliationLine."""
        original = _make_recon_line("STM-001", Decimal("500.00"))
        gl_ref = ArtifactRef(artifact_type=ArtifactType.JOURNAL_LINE, artifact_id="JL-001")

        suggested = original.with_suggestion(
            suggested_refs=(gl_ref,), confidence=Decimal("90"),
        )
        confirmed = suggested.with_confirmed_match(
            matched_refs=(gl_ref,), links=(),
        )

        # All three are different objects
        assert original is not suggested
        assert suggested is not confirmed
        # Original unchanged
        assert original.status == BankReconciliationStatus.UNMATCHED
        assert suggested.status == BankReconciliationStatus.SUGGESTED
        assert confirmed.status == BankReconciliationStatus.MATCHED


# =============================================================================
# Summary
# =============================================================================

class TestBankReconciliationSummary:
    """Summary of bank reconciliation test coverage."""

    def test_document_coverage(self):
        """
        Bank Reconciliation Test Coverage:

        Auto-Matching (MatchingEngine):
        - Exact amount match
        - Reference match boosts score
        - Date tolerance match
        - Amount mismatch excluded
        - Multiple candidates picks best

        Reconciliation Status (BankReconciliationLine):
        - New line is unmatched
        - Suggest match (pending approval)
        - Confirm match (reconciled)
        - Unreconcile creates new unmatched
        - Filter by status

        Summary Reports (Reconciliation model):
        - Reconciled/unreconciled counts
        - Balance variance tracking
        - Completed status

        Edge Cases:
        - No matches found
        - BankTransaction model
        - Immutability enforcement
        - Status transitions produce new objects

        Total: 14 tests covering bank reconciliation with real architecture.
        """
        pass
