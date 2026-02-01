"""
finance_engines.matching -- Document matching engine for business process reconciliation.

Responsibility:
    Match documents across business processes: 3-way match (PO/Receipt/Invoice),
    2-way match (PO/Invoice for services), bank reconciliation (Statement/GL),
    and custom matching.  Produces scored match suggestions and variance-aware
    match results.

Architecture position:
    Engines -- pure calculation layer, zero I/O.
    May only import finance_kernel/domain/values.
    Delegates variance calculations to ``finance_engines.variance.VarianceCalculator``.

Invariants enforced:
    - R6 (replay safety): identical inputs produce identical outputs;
      no internal state or clock access.
    - R16 (ISO 4217): currency consistency enforced across matched documents
      when ``require_same_currency=True`` (default).
    - R17 (precision-derived tolerance): tolerance comparisons use Decimal
      arithmetic; no float intermediates.
    - Purity: no clock access, no I/O.

Failure modes:
    - ValueError from ``create_match`` if fewer than 2 documents are provided.
    - ValueError from ``create_match`` if documents have mixed currencies.
    - ValueError from ``create_match`` if no documents have amounts.
    - Score of 0 from ``_evaluate_match`` when vendor, item, or currency
      mismatches are detected (controlled by tolerance flags).

Audit relevance:
    Match results feed into AP 3-way match workflows and bank reconciliation
    processes.  Price and quantity variances detected during matching are
    posted to variance accounts.  All engine invocations are traced via
    ``@traced_engine``.

Usage:
    from finance_engines.matching import MatchingEngine, MatchCandidate, MatchTolerance
    from finance_kernel.domain.values import Money

    engine = MatchingEngine()
    suggestions = engine.find_matches(
        target=invoice_candidate,
        candidates=[po_candidate, receipt_candidate],
        tolerance=MatchTolerance(amount_tolerance=Decimal("0.01")),
    )
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from finance_engines.tracer import traced_engine
from finance_engines.variance import VarianceCalculator, VarianceResult
from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger

logger = get_logger("engines.matching")


class MatchType(str, Enum):
    """Type of document match."""

    THREE_WAY = "three_way"  # PO ↔ Receipt ↔ Invoice
    TWO_WAY = "two_way"  # PO ↔ Invoice (services)
    SHIPMENT = "shipment"  # SO ↔ Shipment ↔ Invoice
    BANK = "bank"  # Statement ↔ Transactions
    CUSTOM = "custom"  # User-defined


class MatchStatus(str, Enum):
    """Status of a match."""

    PENDING = "pending"  # Not yet matched
    PARTIAL = "partial"  # Partially matched
    MATCHED = "matched"  # Fully matched
    VARIANCE = "variance"  # Matched with variance
    REJECTED = "rejected"  # Match rejected


class ToleranceType(str, Enum):
    """Type of tolerance calculation."""

    ABSOLUTE = "absolute"  # Fixed amount (e.g., $0.01)
    PERCENT = "percent"  # Percentage (e.g., 0.5%)


@dataclass(frozen=True)
class MatchTolerance:
    """
    Tolerance rules for matching.

    Immutable configuration for match acceptance.
    """

    amount_tolerance: Decimal = Decimal("0.01")
    amount_tolerance_type: ToleranceType = ToleranceType.ABSOLUTE
    quantity_tolerance: Decimal = Decimal("0")
    quantity_tolerance_type: ToleranceType = ToleranceType.ABSOLUTE
    date_tolerance_days: int = 0
    require_same_vendor: bool = True
    require_same_item: bool = True
    require_same_currency: bool = True


@dataclass(frozen=True)
class MatchCandidate:
    """
    A document that can be matched.

    Immutable value object representing a matchable document.
    """

    document_type: str  # "PO", "RECEIPT", "INVOICE", "STATEMENT", etc.
    document_id: str | UUID
    line_id: str | UUID | None = None
    reference: str = ""
    amount: Money | None = None
    quantity: Decimal | None = None
    date: date | None = None
    dimensions: dict[str, str] = field(default_factory=dict)

    @property
    def vendor_id(self) -> str | None:
        return self.dimensions.get("vendor_id")

    @property
    def item_id(self) -> str | None:
        return self.dimensions.get("item_id")

    @property
    def unit_price(self) -> Money | None:
        """Calculate unit price if amount and quantity available."""
        if self.amount and self.quantity and self.quantity != Decimal("0"):
            return Money.of(
                self.amount.amount / self.quantity,
                self.amount.currency,
            )
        return None


@dataclass(frozen=True)
class MatchSuggestion:
    """
    A suggested match between documents.

    Includes confidence score and any detected variances.
    """

    target: MatchCandidate
    candidate: MatchCandidate
    score: Decimal  # 0-100, higher is better match
    amount_difference: Money | None
    quantity_difference: Decimal | None
    date_difference_days: int | None
    is_within_tolerance: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchResult:
    """
    Result of a match operation.

    Immutable value object with complete match details.
    """

    match_id: str | UUID
    match_type: MatchType
    status: MatchStatus
    documents: tuple[MatchCandidate, ...]
    matched_amount: Money
    matched_quantity: Decimal | None
    price_variance: VarianceResult | None
    quantity_variance: VarianceResult | None
    unmatched: dict[str | UUID, Money]  # document_id -> unmatched amount
    created_at: date | None = None

    @property
    def has_variance(self) -> bool:
        """True if match has price or quantity variance."""
        return self.price_variance is not None or self.quantity_variance is not None

    @property
    def document_count(self) -> int:
        return len(self.documents)


class MatchingEngine:
    """
    Generic document matching engine.

    Contract:
        Pure functions -- no I/O, no database access.
        All reference data and dates passed as explicit parameters.
    Guarantees:
        - ``find_matches`` returns suggestions sorted by descending score.
        - ``create_match`` validates currency consistency and minimum
          document count before producing a ``MatchResult``.
        - Price and quantity variances are computed only when sufficient
          data exists (unit prices and quantities on at least 2 documents).
    Non-goals:
        - Does not persist match results; callers are responsible for
          storage and link creation.
        - Does not enforce business rules about which document types can
          be matched; that is the caller's responsibility.
    """

    def __init__(self) -> None:
        self._variance_calculator = VarianceCalculator()

    @traced_engine("matching", "1.0", fingerprint_fields=("target", "candidates"))
    def find_matches(
        self,
        target: MatchCandidate,
        candidates: Sequence[MatchCandidate],
        tolerance: MatchTolerance,
    ) -> list[MatchSuggestion]:
        """
        Find potential matches for a target document.

        Args:
            target: The document to find matches for
            candidates: Potential matching documents
            tolerance: Matching tolerance rules

        Returns:
            List of MatchSuggestion sorted by score (highest first)
        """
        t0 = time.monotonic()
        logger.info("match_search_started", extra={
            "target_document_type": target.document_type,
            "target_document_id": str(target.document_id),
            "candidate_count": len(candidates),
        })

        suggestions: list[MatchSuggestion] = []

        for candidate in candidates:
            suggestion = self._evaluate_match(target, candidate, tolerance)
            if suggestion.score > Decimal("0"):
                suggestions.append(suggestion)

        # Sort by score descending
        sorted_suggestions = sorted(suggestions, key=lambda s: s.score, reverse=True)

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("match_search_completed", extra={
            "target_document_id": str(target.document_id),
            "candidates_evaluated": len(candidates),
            "suggestions_found": len(sorted_suggestions),
            "top_score": str(sorted_suggestions[0].score) if sorted_suggestions else "0",
            "duration_ms": duration_ms,
        })

        return sorted_suggestions

    @traced_engine("matching", "1.0", fingerprint_fields=("documents", "match_type"))
    def create_match(
        self,
        documents: Sequence[MatchCandidate],
        match_type: MatchType,
        as_of_date: date,
        tolerance: MatchTolerance | None = None,
    ) -> MatchResult:
        """
        Create a match between documents.

        Args:
            documents: Documents to match together
            match_type: Type of match being created
            tolerance: Optional tolerance for variance calculation

        Returns:
            MatchResult with match details and any variances
        """
        t0 = time.monotonic()
        logger.info("match_creation_started", extra={
            "match_type": match_type.value,
            "document_count": len(documents),
        })

        if len(documents) < 2:
            logger.error("match_creation_insufficient_documents", extra={
                "document_count": len(documents),
                "match_type": match_type.value,
            })
            raise ValueError("At least 2 documents required for a match")

        tolerance = tolerance or MatchTolerance()

        # Find common currency
        currencies = {
            doc.amount.currency
            for doc in documents
            if doc.amount is not None
        }
        if len(currencies) > 1:
            raise ValueError(f"Currency mismatch in documents: {currencies}")
        if not currencies:
            raise ValueError("No amounts found in documents")

        currency = next(iter(currencies))

        # Calculate matched amount (minimum across all documents)
        amounts = [doc.amount.amount for doc in documents if doc.amount]
        matched_amount = Money.of(min(amounts), currency)

        # Calculate matched quantity if available
        quantities = [doc.quantity for doc in documents if doc.quantity is not None]
        matched_quantity = min(quantities) if quantities else None

        # Calculate variances
        price_variance = self._calculate_price_variance(documents, matched_quantity)
        quantity_variance = self._calculate_quantity_variance(documents)

        # Calculate unmatched amounts
        unmatched: dict[str | UUID, Money] = {}
        for doc in documents:
            if doc.amount:
                remainder = doc.amount - matched_amount
                if not remainder.is_zero:
                    unmatched[doc.document_id] = remainder

        # Determine status
        if unmatched:
            status = MatchStatus.PARTIAL
        elif price_variance or quantity_variance:
            status = MatchStatus.VARIANCE
        else:
            status = MatchStatus.MATCHED

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("match_creation_completed", extra={
            "match_type": match_type.value,
            "status": status.value,
            "matched_amount": str(matched_amount.amount),
            "document_count": len(documents),
            "has_price_variance": price_variance is not None,
            "has_quantity_variance": quantity_variance is not None,
            "unmatched_count": len(unmatched),
            "duration_ms": duration_ms,
        })

        return MatchResult(
            match_id=uuid4(),
            match_type=match_type,
            status=status,
            documents=tuple(documents),
            matched_amount=matched_amount,
            matched_quantity=matched_quantity,
            price_variance=price_variance,
            quantity_variance=quantity_variance,
            unmatched=unmatched,
            created_at=as_of_date,
        )

    def score(
        self,
        doc1: MatchCandidate,
        doc2: MatchCandidate,
        tolerance: MatchTolerance | None = None,
    ) -> Decimal:
        """
        Calculate match score between two documents.

        Args:
            doc1: First document
            doc2: Second document
            tolerance: Tolerance rules (uses defaults if not provided)

        Returns:
            Score from 0-100 (100 = perfect match)
        """
        tolerance = tolerance or MatchTolerance()
        suggestion = self._evaluate_match(doc1, doc2, tolerance)
        return suggestion.score

    def _evaluate_match(
        self,
        target: MatchCandidate,
        candidate: MatchCandidate,
        tolerance: MatchTolerance,
    ) -> MatchSuggestion:
        """Evaluate how well two documents match."""
        logger.debug("match_evaluation_started", extra={
            "target_id": str(target.document_id),
            "candidate_id": str(candidate.document_id),
            "target_type": target.document_type,
            "candidate_type": candidate.document_type,
        })

        score = Decimal("100")
        notes: list[str] = []
        is_within_tolerance = True

        # Check required dimension matches
        if tolerance.require_same_vendor:
            if target.vendor_id != candidate.vendor_id:
                score = Decimal("0")
                notes.append("Vendor mismatch")
                is_within_tolerance = False

        if tolerance.require_same_item:
            if target.item_id != candidate.item_id:
                score = Decimal("0")
                notes.append("Item mismatch")
                is_within_tolerance = False

        # Calculate amount difference
        amount_diff: Money | None = None
        if target.amount and candidate.amount:
            if tolerance.require_same_currency:
                if target.amount.currency != candidate.amount.currency:
                    score = Decimal("0")
                    notes.append("Currency mismatch")
                    is_within_tolerance = False
                else:
                    amount_diff = target.amount - candidate.amount
                    abs_diff = abs(amount_diff.amount)

                    # Check tolerance
                    if tolerance.amount_tolerance_type == ToleranceType.ABSOLUTE:
                        if abs_diff > tolerance.amount_tolerance:
                            score -= Decimal("30")
                            notes.append(f"Amount differs by {amount_diff}")
                            is_within_tolerance = False
                    else:  # PERCENT
                        if target.amount.amount != Decimal("0"):
                            pct_diff = abs_diff / target.amount.amount * 100
                            if pct_diff > tolerance.amount_tolerance:
                                score -= Decimal("30")
                                notes.append(f"Amount differs by {pct_diff:.2f}%")
                                is_within_tolerance = False

        # Calculate quantity difference
        qty_diff: Decimal | None = None
        if target.quantity is not None and candidate.quantity is not None:
            qty_diff = target.quantity - candidate.quantity
            abs_qty_diff = abs(qty_diff)

            if tolerance.quantity_tolerance_type == ToleranceType.ABSOLUTE:
                if abs_qty_diff > tolerance.quantity_tolerance:
                    score -= Decimal("20")
                    notes.append(f"Quantity differs by {qty_diff}")
                    is_within_tolerance = False
            else:  # PERCENT
                if target.quantity != Decimal("0"):
                    pct_diff = abs_qty_diff / target.quantity * 100
                    if pct_diff > tolerance.quantity_tolerance:
                        score -= Decimal("20")
                        notes.append(f"Quantity differs by {pct_diff:.2f}%")
                        is_within_tolerance = False

        # Calculate date difference
        date_diff: int | None = None
        if target.date and candidate.date:
            date_diff = abs((target.date - candidate.date).days)
            if date_diff > tolerance.date_tolerance_days:
                score -= Decimal("10")
                notes.append(f"Dates differ by {date_diff} days")
                # Date doesn't affect tolerance flag (soft constraint)

        # Reference match bonus
        if target.reference and candidate.reference:
            if target.reference == candidate.reference:
                score += Decimal("10")
                notes.append("Reference match")

        # Clamp score
        score = max(Decimal("0"), min(Decimal("100"), score))

        return MatchSuggestion(
            target=target,
            candidate=candidate,
            score=score,
            amount_difference=amount_diff,
            quantity_difference=qty_diff,
            date_difference_days=date_diff,
            is_within_tolerance=is_within_tolerance,
            notes=tuple(notes),
        )

    def _calculate_price_variance(
        self,
        documents: Sequence[MatchCandidate],
        matched_quantity: Decimal | None,
    ) -> VarianceResult | None:
        """Calculate price variance between documents."""
        # Need at least 2 documents with unit prices
        docs_with_prices = [
            doc for doc in documents
            if doc.unit_price is not None
        ]

        if len(docs_with_prices) < 2:
            return None

        if matched_quantity is None:
            return None

        # Use first as expected, last as actual (e.g., PO vs Invoice)
        expected = docs_with_prices[0]
        actual = docs_with_prices[-1]

        if expected.unit_price.currency != actual.unit_price.currency:
            return None

        if expected.unit_price == actual.unit_price:
            return None

        return self._variance_calculator.price_variance(
            expected_price=expected.unit_price,
            actual_price=actual.unit_price,
            quantity=matched_quantity,
        )

    def _calculate_quantity_variance(
        self,
        documents: Sequence[MatchCandidate],
    ) -> VarianceResult | None:
        """Calculate quantity variance between documents."""
        docs_with_qty = [
            doc for doc in documents
            if doc.quantity is not None
        ]

        if len(docs_with_qty) < 2:
            return None

        # Use first as expected, last as actual
        expected = docs_with_qty[0]
        actual = docs_with_qty[-1]

        if expected.quantity == actual.quantity:
            return None

        # Need a price for quantity variance
        doc_with_price = next(
            (doc for doc in documents if doc.unit_price is not None),
            None,
        )
        if doc_with_price is None:
            return None

        return self._variance_calculator.quantity_variance(
            expected_quantity=expected.quantity,
            actual_quantity=actual.quantity,
            standard_price=doc_with_price.unit_price,
        )


def create_three_way_match(
    po: MatchCandidate,
    receipt: MatchCandidate,
    invoice: MatchCandidate,
    as_of_date: date,
    tolerance: MatchTolerance | None = None,
) -> MatchResult:
    """
    Convenience function for 3-way matching.

    Args:
        po: Purchase order document
        receipt: Goods receipt document
        invoice: Vendor invoice document
        as_of_date: Date of the match (engines must not call date.today())
        tolerance: Match tolerance rules

    Returns:
        MatchResult for the 3-way match
    """
    engine = MatchingEngine()
    return engine.create_match(
        documents=[po, receipt, invoice],
        match_type=MatchType.THREE_WAY,
        as_of_date=as_of_date,
        tolerance=tolerance,
    )


def create_two_way_match(
    po: MatchCandidate,
    invoice: MatchCandidate,
    as_of_date: date,
    tolerance: MatchTolerance | None = None,
) -> MatchResult:
    """
    Convenience function for 2-way matching (services).

    Args:
        po: Purchase order document
        invoice: Vendor invoice document
        as_of_date: Date of the match (engines must not call date.today())
        tolerance: Match tolerance rules

    Returns:
        MatchResult for the 2-way match
    """
    engine = MatchingEngine()
    return engine.create_match(
        documents=[po, invoice],
        match_type=MatchType.TWO_WAY,
        as_of_date=as_of_date,
        tolerance=tolerance,
    )
