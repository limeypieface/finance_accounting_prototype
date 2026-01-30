"""
Aging Calculator - Calculate aging for dated documents.

Used for AP aging, AR aging, and inventory slow-moving analysis.
Pure functions with no I/O.

Usage:
    from finance_engines.aging import AgingCalculator, AgeBucket, AgedItem
    from finance_kernel.domain.values import Money
    from datetime import date

    calculator = AgingCalculator()
    age = calculator.calculate_age(
        document_date=date(2024, 1, 15),
        as_of_date=date(2024, 2, 15),
    )  # Returns 31

    bucket = calculator.classify(age)  # Returns AgeBucket("31-60", 31, 60)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger
from finance_engines.tracer import traced_engine

logger = get_logger("engines.aging")


@dataclass(frozen=True)
class AgeBucket:
    """
    Definition of an aging bucket.

    Immutable value object.
    """

    name: str
    min_days: int
    max_days: int | None  # None = unbounded (e.g., 90+)

    def __post_init__(self) -> None:
        if self.min_days < 0:
            raise ValueError("min_days cannot be negative")
        if self.max_days is not None and self.max_days < self.min_days:
            raise ValueError("max_days cannot be less than min_days")

    def contains(self, age_days: int) -> bool:
        """Check if age falls within this bucket."""
        if age_days < self.min_days:
            return False
        if self.max_days is None:
            return True
        return age_days <= self.max_days

    @property
    def is_unbounded(self) -> bool:
        """True if bucket has no upper limit."""
        return self.max_days is None


# Standard aging buckets used across AP/AR
STANDARD_BUCKETS: tuple[AgeBucket, ...] = (
    AgeBucket("Current", 0, 0),
    AgeBucket("1-30", 1, 30),
    AgeBucket("31-60", 31, 60),
    AgeBucket("61-90", 61, 90),
    AgeBucket("Over 90", 91, None),
)

# Alternative weekly buckets for short-term analysis
WEEKLY_BUCKETS: tuple[AgeBucket, ...] = (
    AgeBucket("Current", 0, 0),
    AgeBucket("1-7", 1, 7),
    AgeBucket("8-14", 8, 14),
    AgeBucket("15-21", 15, 21),
    AgeBucket("22-30", 22, 30),
    AgeBucket("Over 30", 31, None),
)


@dataclass(frozen=True)
class AgedItem:
    """
    An item with its age classification.

    Immutable value object representing a document in an aging report.
    """

    document_id: str | UUID
    document_type: str
    document_date: date
    due_date: date | None
    amount: Money
    age_days: int
    bucket: AgeBucket
    counterparty_id: str | UUID | None = None
    counterparty_name: str | None = None
    reference: str | None = None
    dimensions: dict[str, str] = field(default_factory=dict)

    @property
    def is_overdue(self) -> bool:
        """True if past due date (if due date exists)."""
        if self.due_date is None:
            return False
        return self.age_days > 0

    @property
    def days_past_due(self) -> int:
        """Days past due (0 if not overdue or no due date)."""
        return max(0, self.age_days)


@dataclass(frozen=True)
class AgingReport:
    """
    Complete aging report.

    Immutable value object with aggregation methods.
    """

    as_of_date: date
    buckets: tuple[AgeBucket, ...]
    items: tuple[AgedItem, ...]
    report_type: str = "standard"  # "AP", "AR", "inventory"
    currency: str | None = None

    @property
    def item_count(self) -> int:
        """Total number of items in report."""
        return len(self.items)

    def total_amount(self) -> Money:
        """Sum of all item amounts."""
        if not self.items:
            currency = self.currency or "USD"
            return Money.zero(currency)

        total = self.items[0].amount
        for item in self.items[1:]:
            total = total + item.amount
        return total

    def total_by_bucket(self) -> dict[str, Money]:
        """
        Sum amounts by bucket.

        Returns:
            Dict mapping bucket name to total amount
        """
        if not self.items:
            return {}

        result: dict[str, Money] = {}
        currency = self.items[0].amount.currency

        for bucket in self.buckets:
            bucket_items = [i for i in self.items if i.bucket.name == bucket.name]
            if bucket_items:
                total = bucket_items[0].amount
                for item in bucket_items[1:]:
                    total = total + item.amount
                result[bucket.name] = total
            else:
                result[bucket.name] = Money.zero(currency)

        return result

    def total_by_counterparty(self) -> dict[str | UUID, dict[str, Money]]:
        """
        Sum amounts by counterparty and bucket.

        Returns:
            Dict mapping counterparty_id to dict of bucket name to amount
        """
        if not self.items:
            return {}

        result: dict[str | UUID, dict[str, Money]] = {}
        currency = self.items[0].amount.currency

        for item in self.items:
            if item.counterparty_id is None:
                continue

            if item.counterparty_id not in result:
                result[item.counterparty_id] = {
                    b.name: Money.zero(currency) for b in self.buckets
                }

            current = result[item.counterparty_id][item.bucket.name]
            result[item.counterparty_id][item.bucket.name] = current + item.amount

        return result

    def items_in_bucket(self, bucket_name: str) -> tuple[AgedItem, ...]:
        """Get all items in a specific bucket."""
        return tuple(i for i in self.items if i.bucket.name == bucket_name)

    def items_for_counterparty(
        self,
        counterparty_id: str | UUID,
    ) -> tuple[AgedItem, ...]:
        """Get all items for a specific counterparty."""
        return tuple(i for i in self.items if i.counterparty_id == counterparty_id)

    def overdue_items(self) -> tuple[AgedItem, ...]:
        """Get all overdue items."""
        return tuple(i for i in self.items if i.is_overdue)

    def overdue_amount(self) -> Money:
        """Total amount of overdue items."""
        overdue = self.overdue_items()
        if not overdue:
            currency = self.currency or (self.items[0].amount.currency if self.items else "USD")
            return Money.zero(currency)

        total = overdue[0].amount
        for item in overdue[1:]:
            total = total + item.amount
        return total


class AgingCalculator:
    """
    Calculate aging for any dated documents.

    Pure functions - no I/O, no database access.
    All dates and data passed as parameters.
    """

    DEFAULT_BUCKETS = STANDARD_BUCKETS

    def calculate_age(
        self,
        document_date: date,
        as_of_date: date,
        due_date: date | None = None,
        use_due_date: bool = True,
    ) -> int:
        """
        Calculate age in days.

        Args:
            document_date: Date of the document
            as_of_date: Date to calculate age as of
            due_date: Optional due date (used if use_due_date=True)
            use_due_date: If True and due_date provided, age from due_date

        Returns:
            Age in days (can be negative if not yet due)
        """
        reference_date = document_date
        if use_due_date and due_date is not None:
            reference_date = due_date

        age_days = (as_of_date - reference_date).days
        logger.debug("age_calculated", extra={
            "document_date": document_date.isoformat(),
            "reference_date": reference_date.isoformat(),
            "as_of_date": as_of_date.isoformat(),
            "age_days": age_days,
            "used_due_date": use_due_date and due_date is not None,
        })

        return age_days

    def classify(
        self,
        age_days: int,
        buckets: Sequence[AgeBucket] | None = None,
    ) -> AgeBucket:
        """
        Classify age into a bucket.

        Args:
            age_days: Age in days
            buckets: Buckets to use (defaults to STANDARD_BUCKETS)

        Returns:
            The bucket containing this age

        Raises:
            ValueError: If age doesn't fit any bucket
        """
        if buckets is None:
            buckets = self.DEFAULT_BUCKETS

        # Handle negative ages (not yet due)
        if age_days < 0:
            logger.debug("age_classification_negative", extra={
                "age_days": age_days,
            })
            # Find "Current" or first bucket
            for bucket in buckets:
                if bucket.min_days == 0:
                    return bucket
            return buckets[0]

        for bucket in buckets:
            if bucket.contains(age_days):
                return bucket

        # Should not reach here if buckets are properly defined
        logger.warning("age_classification_no_bucket", extra={
            "age_days": age_days,
            "bucket_count": len(buckets),
        })
        raise ValueError(f"Age {age_days} does not fit any bucket")

    def age_item(
        self,
        document_id: str | UUID,
        document_type: str,
        document_date: date,
        amount: Money,
        as_of_date: date,
        due_date: date | None = None,
        counterparty_id: str | UUID | None = None,
        counterparty_name: str | None = None,
        reference: str | None = None,
        dimensions: dict[str, str] | None = None,
        buckets: Sequence[AgeBucket] | None = None,
        use_due_date: bool = True,
    ) -> AgedItem:
        """
        Create an aged item from document details.

        Convenience method combining calculate_age and classify.

        Args:
            document_id: Unique document identifier
            document_type: Type of document (e.g., "invoice")
            document_date: Date of document
            amount: Document amount
            as_of_date: Date to age as of
            due_date: Optional due date
            counterparty_id: Optional vendor/customer ID
            counterparty_name: Optional vendor/customer name
            reference: Optional reference number
            dimensions: Optional dimension values
            buckets: Buckets to use for classification
            use_due_date: Whether to use due date for aging

        Returns:
            AgedItem with age and bucket classification
        """
        logger.debug("age_item_started", extra={
            "document_id": str(document_id),
            "document_type": document_type,
            "document_date": document_date.isoformat(),
            "amount": str(amount.amount),
        })

        age_days = self.calculate_age(
            document_date=document_date,
            as_of_date=as_of_date,
            due_date=due_date,
            use_due_date=use_due_date,
        )

        bucket = self.classify(age_days, buckets)

        return AgedItem(
            document_id=document_id,
            document_type=document_type,
            document_date=document_date,
            due_date=due_date,
            amount=amount,
            age_days=age_days,
            bucket=bucket,
            counterparty_id=counterparty_id,
            counterparty_name=counterparty_name,
            reference=reference,
            dimensions=dimensions or {},
        )

    @traced_engine("aging", "1.0", fingerprint_fields=("items", "as_of_date", "report_type"))
    def generate_report(
        self,
        items: Sequence[AgedItem],
        as_of_date: date,
        buckets: Sequence[AgeBucket] | None = None,
        report_type: str = "standard",
    ) -> AgingReport:
        """
        Generate complete aging report.

        Args:
            items: Pre-aged items to include in report
            as_of_date: Report date
            buckets: Buckets for the report
            report_type: Type identifier for the report

        Returns:
            AgingReport with all items and aggregations
        """
        if buckets is None:
            buckets = self.DEFAULT_BUCKETS

        currency = items[0].amount.currency.code if items else None

        logger.info("aging_report_generated", extra={
            "as_of_date": as_of_date.isoformat(),
            "report_type": report_type,
            "item_count": len(items),
            "bucket_count": len(buckets),
            "currency": currency,
        })

        return AgingReport(
            as_of_date=as_of_date,
            buckets=tuple(buckets),
            items=tuple(items),
            report_type=report_type,
            currency=currency,
        )

    @traced_engine("aging", "1.0", fingerprint_fields=("documents", "as_of_date", "report_type"))
    def generate_report_from_documents(
        self,
        documents: Sequence[dict],
        as_of_date: date,
        buckets: Sequence[AgeBucket] | None = None,
        report_type: str = "standard",
        use_due_date: bool = True,
    ) -> AgingReport:
        """
        Generate aging report from raw document data.

        Args:
            documents: Sequence of dicts with document details:
                - document_id: str | UUID
                - document_type: str
                - document_date: date
                - amount: Money
                - due_date: date | None (optional)
                - counterparty_id: str | UUID | None (optional)
                - counterparty_name: str | None (optional)
                - reference: str | None (optional)
                - dimensions: dict[str, str] | None (optional)
            as_of_date: Report date
            buckets: Buckets for classification
            report_type: Type identifier for the report
            use_due_date: Whether to use due date for aging

        Returns:
            AgingReport with aged items
        """
        t0 = time.monotonic()
        logger.info("aging_report_from_documents_started", extra={
            "document_count": len(documents),
            "as_of_date": as_of_date.isoformat(),
            "report_type": report_type,
            "use_due_date": use_due_date,
        })

        if buckets is None:
            buckets = self.DEFAULT_BUCKETS

        aged_items: list[AgedItem] = []

        for doc in documents:
            item = self.age_item(
                document_id=doc["document_id"],
                document_type=doc["document_type"],
                document_date=doc["document_date"],
                amount=doc["amount"],
                as_of_date=as_of_date,
                due_date=doc.get("due_date"),
                counterparty_id=doc.get("counterparty_id"),
                counterparty_name=doc.get("counterparty_name"),
                reference=doc.get("reference"),
                dimensions=doc.get("dimensions"),
                buckets=buckets,
                use_due_date=use_due_date,
            )
            aged_items.append(item)

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("aging_report_from_documents_completed", extra={
            "document_count": len(documents),
            "aged_item_count": len(aged_items),
            "duration_ms": duration_ms,
        })

        return self.generate_report(
            items=aged_items,
            as_of_date=as_of_date,
            buckets=buckets,
            report_type=report_type,
        )
