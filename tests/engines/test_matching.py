"""
Tests for Matching Engine.

Covers:
- Three-way matching (PO/Receipt/Invoice)
- Two-way matching
- Match scoring
- Tolerance handling
- Variance detection
- Edge cases and error handling
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from finance_kernel.domain.values import Money
from finance_engines.matching import (
    MatchingEngine,
    MatchCandidate,
    MatchResult,
    MatchTolerance,
    MatchStatus,
    MatchType,
    MatchSuggestion,
    ToleranceType,
    create_three_way_match,
    create_two_way_match,
)


class TestMatchCandidateCreation:
    """Tests for MatchCandidate value object."""

    def test_create_basic_candidate(self):
        """Creates a basic match candidate."""
        candidate = MatchCandidate(
            document_type="PO",
            document_id="po-123",
            amount=Money.of("1000.00", "USD"),
            quantity=Decimal("100"),
            date=date(2024, 1, 15),
        )

        assert candidate.document_type == "PO"
        assert candidate.amount == Money.of("1000.00", "USD")

    def test_candidate_unit_price(self):
        """Calculates unit price from amount and quantity."""
        candidate = MatchCandidate(
            document_type="PO",
            document_id="po-123",
            amount=Money.of("1000.00", "USD"),
            quantity=Decimal("100"),
        )

        assert candidate.unit_price == Money.of("10.00", "USD")

    def test_candidate_unit_price_none_when_no_quantity(self):
        """Unit price is None when quantity not available."""
        candidate = MatchCandidate(
            document_type="PO",
            document_id="po-123",
            amount=Money.of("1000.00", "USD"),
        )

        assert candidate.unit_price is None

    def test_candidate_dimensions(self):
        """Accesses vendor and item from dimensions."""
        candidate = MatchCandidate(
            document_type="PO",
            document_id="po-123",
            dimensions={"vendor_id": "v-001", "item_id": "item-abc"},
        )

        assert candidate.vendor_id == "v-001"
        assert candidate.item_id == "item-abc"

    def test_immutable(self):
        """MatchCandidate is immutable."""
        candidate = MatchCandidate(
            document_type="PO",
            document_id="po-123",
        )

        with pytest.raises(AttributeError):
            candidate.document_type = "INVOICE"


class TestFindMatches:
    """Tests for finding match suggestions."""

    def setup_method(self):
        self.engine = MatchingEngine()
        self.tolerance = MatchTolerance()

    def test_find_exact_match(self):
        """Finds exact matches with high score."""
        invoice = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("1000.00", "USD"),
            quantity=Decimal("100"),
            dimensions={"vendor_id": "v-001", "item_id": "item-1"},
        )

        po = MatchCandidate(
            document_type="PO",
            document_id="po-1",
            amount=Money.of("1000.00", "USD"),
            quantity=Decimal("100"),
            dimensions={"vendor_id": "v-001", "item_id": "item-1"},
        )

        suggestions = self.engine.find_matches(invoice, [po], self.tolerance)

        assert len(suggestions) == 1
        assert suggestions[0].score >= Decimal("90")
        assert suggestions[0].is_within_tolerance

    def test_find_match_with_amount_difference(self):
        """Finds matches with amount differences within tolerance."""
        invoice = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("1000.01", "USD"),
            dimensions={"vendor_id": "v-001"},
        )

        po = MatchCandidate(
            document_type="PO",
            document_id="po-1",
            amount=Money.of("1000.00", "USD"),
            dimensions={"vendor_id": "v-001"},
        )

        tolerance = MatchTolerance(
            amount_tolerance=Decimal("0.05"),
            require_same_item=False,
        )

        suggestions = self.engine.find_matches(invoice, [po], tolerance)

        assert len(suggestions) == 1
        assert suggestions[0].is_within_tolerance

    def test_no_match_vendor_mismatch(self):
        """No match when vendors differ and require_same_vendor=True."""
        invoice = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("1000.00", "USD"),
            dimensions={"vendor_id": "v-001"},
        )

        po = MatchCandidate(
            document_type="PO",
            document_id="po-1",
            amount=Money.of("1000.00", "USD"),
            dimensions={"vendor_id": "v-002"},  # Different vendor
        )

        suggestions = self.engine.find_matches(invoice, [po], self.tolerance)

        # Should have zero score or not be within tolerance
        assert len(suggestions) == 0 or suggestions[0].score == Decimal("0")

    def test_find_matches_sorted_by_score(self):
        """Suggestions sorted by score (highest first)."""
        invoice = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("1000.00", "USD"),
            dimensions={"vendor_id": "v-001"},
        )

        candidates = [
            MatchCandidate(
                document_type="PO",
                document_id="po-bad",
                amount=Money.of("500.00", "USD"),  # Very different
                dimensions={"vendor_id": "v-001"},
            ),
            MatchCandidate(
                document_type="PO",
                document_id="po-good",
                amount=Money.of("1000.00", "USD"),  # Exact
                dimensions={"vendor_id": "v-001"},
            ),
        ]

        tolerance = MatchTolerance(
            amount_tolerance=Decimal("0.01"),  # Tight tolerance
            require_same_item=False,
        )

        suggestions = self.engine.find_matches(invoice, candidates, tolerance)

        # Only po-good should be within tolerance
        # po-bad has amount difference > tolerance so it scores 0 or low
        within_tolerance = [s for s in suggestions if s.is_within_tolerance]
        assert len(within_tolerance) == 1
        assert within_tolerance[0].candidate.document_id == "po-good"


class TestCreateMatch:
    """Tests for creating matches."""

    def setup_method(self):
        self.engine = MatchingEngine()

    def test_create_simple_match(self):
        """Creates a match between two documents."""
        po = MatchCandidate(
            document_type="PO",
            document_id="po-1",
            amount=Money.of("1000.00", "USD"),
            quantity=Decimal("100"),
        )

        invoice = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("1000.00", "USD"),
            quantity=Decimal("100"),
        )

        result = self.engine.create_match(
            documents=[po, invoice],
            match_type=MatchType.TWO_WAY,
        )

        assert result.status == MatchStatus.MATCHED
        assert result.matched_amount == Money.of("1000.00", "USD")
        assert result.matched_quantity == Decimal("100")
        assert len(result.documents) == 2

    def test_three_way_match(self):
        """Creates a three-way match."""
        po = MatchCandidate(
            document_type="PO",
            document_id="po-1",
            amount=Money.of("1000.00", "USD"),
            quantity=Decimal("100"),
        )

        receipt = MatchCandidate(
            document_type="RECEIPT",
            document_id="rcpt-1",
            amount=Money.of("1000.00", "USD"),
            quantity=Decimal("100"),
        )

        invoice = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("1000.00", "USD"),
            quantity=Decimal("100"),
        )

        result = self.engine.create_match(
            documents=[po, receipt, invoice],
            match_type=MatchType.THREE_WAY,
        )

        assert result.match_type == MatchType.THREE_WAY
        assert result.document_count == 3
        assert result.status == MatchStatus.MATCHED

    def test_match_with_price_variance(self):
        """Detects price variance in match."""
        po = MatchCandidate(
            document_type="PO",
            document_id="po-1",
            amount=Money.of("1000.00", "USD"),
            quantity=Decimal("100"),
        )

        invoice = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("1050.00", "USD"),  # 5% higher
            quantity=Decimal("100"),
        )

        result = self.engine.create_match(
            documents=[po, invoice],
            match_type=MatchType.TWO_WAY,
        )

        # Status is PARTIAL because amounts differ (unmatched remainder)
        # but variance is still detected
        assert result.status == MatchStatus.PARTIAL
        assert result.has_variance
        assert result.price_variance is not None
        assert result.price_variance.variance == Money.of("50.00", "USD")

    def test_partial_match(self):
        """Creates partial match when amounts differ."""
        po = MatchCandidate(
            document_type="PO",
            document_id="po-1",
            amount=Money.of("1000.00", "USD"),
        )

        invoice = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("800.00", "USD"),  # Partial
        )

        result = self.engine.create_match(
            documents=[po, invoice],
            match_type=MatchType.TWO_WAY,
        )

        assert result.status == MatchStatus.PARTIAL
        assert result.matched_amount == Money.of("800.00", "USD")
        assert result.unmatched["po-1"] == Money.of("200.00", "USD")

    def test_match_requires_two_documents(self):
        """Raises error with fewer than 2 documents."""
        with pytest.raises(ValueError, match="At least 2"):
            self.engine.create_match(
                documents=[MatchCandidate(document_type="PO", document_id="po-1")],
                match_type=MatchType.TWO_WAY,
            )

    def test_currency_mismatch_raises(self):
        """Raises error when currencies don't match."""
        with pytest.raises(ValueError, match="Currency mismatch"):
            self.engine.create_match(
                documents=[
                    MatchCandidate(
                        document_type="PO",
                        document_id="po-1",
                        amount=Money.of("1000.00", "USD"),
                    ),
                    MatchCandidate(
                        document_type="INVOICE",
                        document_id="inv-1",
                        amount=Money.of("1000.00", "EUR"),
                    ),
                ],
                match_type=MatchType.TWO_WAY,
            )


class TestMatchScore:
    """Tests for match scoring."""

    def setup_method(self):
        self.engine = MatchingEngine()

    def test_perfect_score(self):
        """Perfect match gets high score."""
        doc1 = MatchCandidate(
            document_type="PO",
            document_id="po-1",
            amount=Money.of("1000.00", "USD"),
            reference="REF-123",
            dimensions={"vendor_id": "v-001"},
        )

        doc2 = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("1000.00", "USD"),
            reference="REF-123",
            dimensions={"vendor_id": "v-001"},
        )

        tolerance = MatchTolerance(require_same_item=False)
        score = self.engine.score(doc1, doc2, tolerance)

        # Perfect match + reference match bonus
        assert score >= Decimal("100")

    def test_score_reduced_by_amount_diff(self):
        """Score reduced when amounts differ outside tolerance."""
        doc1 = MatchCandidate(
            document_type="PO",
            document_id="po-1",
            amount=Money.of("1000.00", "USD"),
            dimensions={"vendor_id": "v-001"},
        )

        doc2 = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("900.00", "USD"),  # 10% less, outside tolerance
            dimensions={"vendor_id": "v-001"},
        )

        tolerance = MatchTolerance(
            amount_tolerance=Decimal("0.01"),  # Tight tolerance - amount diff exceeds this
            require_same_item=False,
        )

        score = self.engine.score(doc1, doc2, tolerance)

        # Score should be reduced because amount differs beyond tolerance
        assert score < Decimal("100")
        assert score > Decimal("0")


class TestMatchTolerance:
    """Tests for tolerance configuration."""

    def test_default_tolerance(self):
        """Default tolerance values."""
        tolerance = MatchTolerance()

        assert tolerance.amount_tolerance == Decimal("0.01")
        assert tolerance.amount_tolerance_type == ToleranceType.ABSOLUTE
        assert tolerance.require_same_vendor is True
        assert tolerance.require_same_currency is True

    def test_percent_tolerance(self):
        """Percentage-based tolerance."""
        engine = MatchingEngine()

        doc1 = MatchCandidate(
            document_type="PO",
            document_id="po-1",
            amount=Money.of("1000.00", "USD"),
            dimensions={"vendor_id": "v-001"},
        )

        doc2 = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("1004.00", "USD"),  # 0.4% difference
            dimensions={"vendor_id": "v-001"},
        )

        tolerance = MatchTolerance(
            amount_tolerance=Decimal("0.5"),  # 0.5%
            amount_tolerance_type=ToleranceType.PERCENT,
            require_same_item=False,
        )

        suggestions = engine.find_matches(doc1, [doc2], tolerance)

        assert len(suggestions) == 1
        assert suggestions[0].is_within_tolerance


class TestConvenienceFunctions:
    """Tests for convenience matching functions."""

    def test_create_three_way_match_function(self):
        """create_three_way_match convenience function."""
        po = MatchCandidate(
            document_type="PO",
            document_id="po-1",
            amount=Money.of("1000.00", "USD"),
        )
        receipt = MatchCandidate(
            document_type="RECEIPT",
            document_id="rcpt-1",
            amount=Money.of("1000.00", "USD"),
        )
        invoice = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("1000.00", "USD"),
        )

        result = create_three_way_match(po, receipt, invoice)

        assert result.match_type == MatchType.THREE_WAY
        assert result.document_count == 3

    def test_create_two_way_match_function(self):
        """create_two_way_match convenience function."""
        po = MatchCandidate(
            document_type="PO",
            document_id="po-1",
            amount=Money.of("1000.00", "USD"),
        )
        invoice = MatchCandidate(
            document_type="INVOICE",
            document_id="inv-1",
            amount=Money.of("1000.00", "USD"),
        )

        result = create_two_way_match(po, invoice)

        assert result.match_type == MatchType.TWO_WAY
        assert result.document_count == 2


class TestMatchResultProperties:
    """Tests for MatchResult value object."""

    def test_has_variance_property(self):
        """has_variance returns True when variances exist."""
        # This is tested implicitly in other tests
        pass

    def test_immutable(self):
        """MatchResult is immutable."""
        result = MatchResult(
            match_id=uuid4(),
            match_type=MatchType.TWO_WAY,
            status=MatchStatus.MATCHED,
            documents=(),
            matched_amount=Money.of("100.00", "USD"),
            matched_quantity=None,
            price_variance=None,
            quantity_variance=None,
            unmatched={},
        )

        with pytest.raises(AttributeError):
            result.status = MatchStatus.REJECTED
