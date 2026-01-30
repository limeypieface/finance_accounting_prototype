"""
Reconciliation Domain Objects.

Immutable value objects representing reconciliation states, document matches,
and payment applications. These are pure domain objects with no I/O dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping, Any
from uuid import UUID

from finance_kernel.domain.values import Money
from finance_kernel.domain.economic_link import ArtifactRef, EconomicLink
from finance_kernel.logging_config import get_logger

logger = get_logger("engines.reconciliation.domain")


class ReconciliationStatus(str, Enum):
    """Status of a reconcilable document (invoice, payment, etc.)."""

    OPEN = "open"                    # Nothing applied yet
    PARTIAL = "partial"             # Partially paid/applied
    MATCHED = "matched"             # Fully matched/paid
    OVERAPPLIED = "overapplied"     # More applied than original amount


class MatchType(str, Enum):
    """Types of document matching."""

    TWO_WAY = "two_way"       # Invoice to Payment (simple)
    THREE_WAY = "three_way"   # PO -> Receipt -> Invoice
    BANK = "bank"             # Bank statement to GL transactions


class BankReconciliationStatus(str, Enum):
    """Status of a bank statement line."""

    UNMATCHED = "unmatched"         # No GL match found
    SUGGESTED = "suggested"         # System suggested match, pending approval
    MATCHED = "matched"             # Confirmed match
    EXCLUDED = "excluded"           # Explicitly excluded (bank fees, etc.)


@dataclass(frozen=True, slots=True)
class ReconciliationState:
    """
    Current reconciliation state of a document.

    Derived from EconomicLink relationships via LinkGraphService.get_unconsumed_value().
    This is a view object showing the current state of matching/payment application.
    """

    artifact_ref: ArtifactRef
    status: ReconciliationStatus
    original_amount: Money
    applied_amount: Money
    remaining_amount: Money
    match_references: tuple[ArtifactRef, ...]  # What documents are matched
    last_activity_date: date | None = None

    def __post_init__(self) -> None:
        # Validate amounts are consistent
        expected_remaining = self.original_amount.amount - self.applied_amount.amount
        if abs(expected_remaining - self.remaining_amount.amount) > Decimal("0.01"):
            logger.critical("reconciliation_state_inconsistent_amounts", extra={
                "artifact_ref": str(self.artifact_ref),
                "original_amount": str(self.original_amount.amount),
                "applied_amount": str(self.applied_amount.amount),
                "remaining_amount": str(self.remaining_amount.amount),
                "expected_remaining": str(expected_remaining),
            })
            raise ValueError(
                f"Remaining amount {self.remaining_amount} inconsistent with "
                f"original {self.original_amount} - applied {self.applied_amount}"
            )

    @property
    def is_open(self) -> bool:
        """True if nothing has been applied."""
        return self.status == ReconciliationStatus.OPEN

    @property
    def is_fully_matched(self) -> bool:
        """True if fully paid/matched."""
        return self.status == ReconciliationStatus.MATCHED

    @property
    def application_percentage(self) -> Decimal:
        """Percentage of original amount that has been applied."""
        if self.original_amount.is_zero:
            return Decimal("100") if self.applied_amount.is_zero else Decimal("0")
        return (self.applied_amount.amount / self.original_amount.amount) * 100

    @classmethod
    def from_amounts(
        cls,
        artifact_ref: ArtifactRef,
        original_amount: Money,
        applied_amount: Money,
        match_references: tuple[ArtifactRef, ...] = (),
        last_activity_date: date | None = None,
    ) -> ReconciliationState:
        """Create state from amounts, deriving status automatically."""
        remaining = Money.of(
            original_amount.amount - applied_amount.amount,
            original_amount.currency.code,
        )

        # Derive status
        if applied_amount.is_zero:
            status = ReconciliationStatus.OPEN
        elif remaining.is_zero:
            status = ReconciliationStatus.MATCHED
        elif remaining.is_negative:
            status = ReconciliationStatus.OVERAPPLIED
        else:
            status = ReconciliationStatus.PARTIAL

        return cls(
            artifact_ref=artifact_ref,
            status=status,
            original_amount=original_amount,
            applied_amount=applied_amount,
            remaining_amount=remaining,
            match_references=match_references,
            last_activity_date=last_activity_date,
        )


@dataclass(frozen=True, slots=True)
class PaymentApplication:
    """
    Record of a payment applied to an invoice or other document.

    Created when a payment (or credit memo, etc.) is applied to reduce
    the balance of a receivable/payable.
    """

    application_id: UUID
    source_ref: ArtifactRef      # What is being paid (invoice)
    payment_ref: ArtifactRef     # What is paying (payment, credit memo)
    applied_amount: Money
    applied_date: date
    link_created: EconomicLink | None = None  # The PAID_BY link
    metadata: Mapping[str, Any] | None = None

    @property
    def is_full_payment(self) -> bool:
        """True if this application could be a full payment (needs context to confirm)."""
        return self.applied_amount.is_positive

    @classmethod
    def create(
        cls,
        application_id: UUID,
        source_ref: ArtifactRef,
        payment_ref: ArtifactRef,
        applied_amount: Money,
        applied_date: date,
        link: EconomicLink | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> PaymentApplication:
        """Factory method to create a payment application."""
        if applied_amount.is_negative:
            logger.error("payment_application_negative_amount", extra={
                "source_ref": str(source_ref),
                "payment_ref": str(payment_ref),
                "applied_amount": str(applied_amount.amount),
            })
            raise ValueError(f"Applied amount cannot be negative: {applied_amount}")

        logger.info("payment_application_created", extra={
            "application_id": str(application_id),
            "source_ref": str(source_ref),
            "payment_ref": str(payment_ref),
            "applied_amount": str(applied_amount.amount),
            "applied_date": applied_date.isoformat(),
        })

        return cls(
            application_id=application_id,
            source_ref=source_ref,
            payment_ref=payment_ref,
            applied_amount=applied_amount,
            applied_date=applied_date,
            link_created=link,
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class DocumentMatch:
    """
    A matched set of documents (2-way, 3-way, or bank reconciliation).

    Represents the result of matching documents together, tracking
    what was matched and any variances.
    """

    match_id: UUID
    match_type: MatchType
    documents: tuple[ArtifactRef, ...]  # All documents in the match
    matched_amount: Money
    match_date: date
    variance: Money | None = None  # Price/quantity variance for 3-way match
    variance_account_id: str | None = None  # Where variance was posted
    links_created: tuple[EconomicLink, ...] = ()
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if len(self.documents) < 2:
            logger.error("document_match_insufficient_documents", extra={
                "match_id": str(self.match_id),
                "document_count": len(self.documents),
            })
            raise ValueError("Match requires at least 2 documents")
        if self.match_type == MatchType.THREE_WAY and len(self.documents) != 3:
            logger.error("three_way_match_wrong_document_count", extra={
                "match_id": str(self.match_id),
                "document_count": len(self.documents),
            })
            raise ValueError("Three-way match requires exactly 3 documents")

    @property
    def has_variance(self) -> bool:
        """True if there is a non-zero variance."""
        return self.variance is not None and not self.variance.is_zero

    @property
    def document_count(self) -> int:
        """Number of documents in the match."""
        return len(self.documents)


@dataclass(frozen=True, slots=True)
class ThreeWayMatchResult:
    """
    Result of a 3-way match between PO, Receipt, and Invoice.

    Contains the match details plus any price/quantity variances that
    need to be posted to variance accounts.
    """

    match: DocumentMatch
    po_ref: ArtifactRef
    receipt_ref: ArtifactRef
    invoice_ref: ArtifactRef

    # Quantities
    po_quantity: Decimal
    receipt_quantity: Decimal
    invoice_quantity: Decimal

    # Prices
    po_unit_price: Money
    invoice_unit_price: Money

    # Variances
    quantity_variance: Decimal  # receipt_qty - po_qty
    price_variance: Money       # (invoice_price - po_price) * invoice_qty

    @property
    def has_quantity_variance(self) -> bool:
        """True if quantities don't match."""
        return self.quantity_variance != Decimal("0")

    @property
    def has_price_variance(self) -> bool:
        """True if prices don't match."""
        return not self.price_variance.is_zero

    @property
    def is_clean_match(self) -> bool:
        """True if no variances (perfect match)."""
        return not self.has_quantity_variance and not self.has_price_variance

    @classmethod
    def create(
        cls,
        match: DocumentMatch,
        po_ref: ArtifactRef,
        receipt_ref: ArtifactRef,
        invoice_ref: ArtifactRef,
        po_quantity: Decimal,
        receipt_quantity: Decimal,
        invoice_quantity: Decimal,
        po_unit_price: Money,
        invoice_unit_price: Money,
    ) -> ThreeWayMatchResult:
        """Create a 3-way match result with calculated variances."""
        quantity_variance = receipt_quantity - po_quantity
        price_variance_amount = (
            (invoice_unit_price.amount - po_unit_price.amount) * invoice_quantity
        )
        price_variance = Money.of(price_variance_amount, po_unit_price.currency.code)

        logger.info("three_way_match_created", extra={
            "match_id": str(match.match_id),
            "po_ref": str(po_ref),
            "receipt_ref": str(receipt_ref),
            "invoice_ref": str(invoice_ref),
            "quantity_variance": str(quantity_variance),
            "price_variance": str(price_variance_amount),
            "is_clean_match": quantity_variance == Decimal("0") and price_variance_amount == Decimal("0"),
        })

        return cls(
            match=match,
            po_ref=po_ref,
            receipt_ref=receipt_ref,
            invoice_ref=invoice_ref,
            po_quantity=po_quantity,
            receipt_quantity=receipt_quantity,
            invoice_quantity=invoice_quantity,
            po_unit_price=po_unit_price,
            invoice_unit_price=invoice_unit_price,
            quantity_variance=quantity_variance,
            price_variance=price_variance,
        )


@dataclass(frozen=True, slots=True)
class BankReconciliationLine:
    """
    A bank statement line with its reconciliation status.

    Tracks matching between bank statement transactions and GL entries.
    """

    line_id: UUID
    statement_ref: ArtifactRef      # Bank statement this line belongs to
    transaction_date: date
    description: str
    amount: Money
    status: BankReconciliationStatus
    matched_gl_refs: tuple[ArtifactRef, ...] = ()  # GL entries matched to this line
    suggested_gl_refs: tuple[ArtifactRef, ...] = ()  # System suggestions
    match_confidence: Decimal | None = None  # 0-100 confidence score for suggestions
    links_created: tuple[EconomicLink, ...] = ()
    metadata: Mapping[str, Any] | None = None

    @property
    def is_reconciled(self) -> bool:
        """True if line is fully matched."""
        return self.status == BankReconciliationStatus.MATCHED

    @property
    def needs_attention(self) -> bool:
        """True if line needs user action."""
        return self.status in (
            BankReconciliationStatus.UNMATCHED,
            BankReconciliationStatus.SUGGESTED,
        )

    @property
    def has_suggestions(self) -> bool:
        """True if system has suggested matches."""
        return len(self.suggested_gl_refs) > 0

    @classmethod
    def unmatched(
        cls,
        line_id: UUID,
        statement_ref: ArtifactRef,
        transaction_date: date,
        description: str,
        amount: Money,
        metadata: Mapping[str, Any] | None = None,
    ) -> BankReconciliationLine:
        """Create an unmatched bank line."""
        return cls(
            line_id=line_id,
            statement_ref=statement_ref,
            transaction_date=transaction_date,
            description=description,
            amount=amount,
            status=BankReconciliationStatus.UNMATCHED,
            metadata=metadata,
        )

    def with_suggestion(
        self,
        suggested_refs: tuple[ArtifactRef, ...],
        confidence: Decimal,
    ) -> BankReconciliationLine:
        """Return new line with suggested matches."""
        return BankReconciliationLine(
            line_id=self.line_id,
            statement_ref=self.statement_ref,
            transaction_date=self.transaction_date,
            description=self.description,
            amount=self.amount,
            status=BankReconciliationStatus.SUGGESTED,
            matched_gl_refs=self.matched_gl_refs,
            suggested_gl_refs=suggested_refs,
            match_confidence=confidence,
            links_created=self.links_created,
            metadata=self.metadata,
        )

    def with_confirmed_match(
        self,
        matched_refs: tuple[ArtifactRef, ...],
        links: tuple[EconomicLink, ...],
    ) -> BankReconciliationLine:
        """Return new line with confirmed match."""
        return BankReconciliationLine(
            line_id=self.line_id,
            statement_ref=self.statement_ref,
            transaction_date=self.transaction_date,
            description=self.description,
            amount=self.amount,
            status=BankReconciliationStatus.MATCHED,
            matched_gl_refs=matched_refs,
            suggested_gl_refs=(),
            match_confidence=None,
            links_created=links,
            metadata=self.metadata,
        )
