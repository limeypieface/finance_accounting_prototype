"""
Tests for Aging Calculator.

Covers:
- Age calculation
- Bucket classification
- Aging report generation
- Custom buckets
- Edge cases and error handling
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_engines.aging import (
    STANDARD_BUCKETS,
    WEEKLY_BUCKETS,
    AgeBucket,
    AgedItem,
    AgingCalculator,
    AgingReport,
)
from finance_kernel.domain.values import Money


class TestAgeCalculation:
    """Tests for age calculation."""

    def setup_method(self):
        self.calculator = AgingCalculator()

    def test_age_from_document_date(self):
        """Calculates age from document date."""
        age = self.calculator.calculate_age(
            document_date=date(2024, 1, 1),
            as_of_date=date(2024, 1, 31),
        )

        assert age == 30

    def test_age_from_due_date(self):
        """Uses due date when provided and use_due_date=True."""
        age = self.calculator.calculate_age(
            document_date=date(2024, 1, 1),
            as_of_date=date(2024, 2, 15),
            due_date=date(2024, 1, 31),  # 30 days from doc, 15 days past due
            use_due_date=True,
        )

        assert age == 15  # Days past due date

    def test_age_ignores_due_date_when_disabled(self):
        """Uses document date when use_due_date=False."""
        age = self.calculator.calculate_age(
            document_date=date(2024, 1, 1),
            as_of_date=date(2024, 2, 15),
            due_date=date(2024, 1, 31),
            use_due_date=False,
        )

        assert age == 45  # Days from document date

    def test_negative_age_not_yet_due(self):
        """Negative age when not yet due."""
        age = self.calculator.calculate_age(
            document_date=date(2024, 1, 1),
            as_of_date=date(2024, 1, 15),
            due_date=date(2024, 1, 31),
            use_due_date=True,
        )

        assert age == -16  # 16 days before due

    def test_zero_age_same_day(self):
        """Zero age when dates match."""
        age = self.calculator.calculate_age(
            document_date=date(2024, 1, 15),
            as_of_date=date(2024, 1, 15),
        )

        assert age == 0


class TestBucketClassification:
    """Tests for bucket classification."""

    def setup_method(self):
        self.calculator = AgingCalculator()

    def test_classify_current(self):
        """Age 0 goes to Current bucket."""
        bucket = self.calculator.classify(0)

        assert bucket.name == "Current"

    def test_classify_1_to_30(self):
        """Ages 1-30 go to 1-30 bucket."""
        bucket = self.calculator.classify(15)

        assert bucket.name == "1-30"

    def test_classify_31_to_60(self):
        """Ages 31-60 go to 31-60 bucket."""
        bucket = self.calculator.classify(45)

        assert bucket.name == "31-60"

    def test_classify_over_90(self):
        """Ages over 90 go to Over 90 bucket."""
        bucket = self.calculator.classify(120)

        assert bucket.name == "Over 90"

    def test_classify_negative_age(self):
        """Negative ages go to Current bucket."""
        bucket = self.calculator.classify(-10)

        assert bucket.name == "Current"

    def test_classify_boundary_30(self):
        """Age 30 goes to 1-30 bucket."""
        bucket = self.calculator.classify(30)

        assert bucket.name == "1-30"

    def test_classify_boundary_31(self):
        """Age 31 goes to 31-60 bucket."""
        bucket = self.calculator.classify(31)

        assert bucket.name == "31-60"

    def test_custom_buckets(self):
        """Uses custom buckets when provided."""
        custom_buckets = (
            AgeBucket("Fresh", 0, 7),
            AgeBucket("Stale", 8, None),
        )

        bucket = self.calculator.classify(10, custom_buckets)

        assert bucket.name == "Stale"


class TestAgeBucket:
    """Tests for AgeBucket value object."""

    def test_bucket_contains(self):
        """contains() correctly identifies ages in bucket."""
        bucket = AgeBucket("31-60", 31, 60)

        assert not bucket.contains(30)
        assert bucket.contains(31)
        assert bucket.contains(45)
        assert bucket.contains(60)
        assert not bucket.contains(61)

    def test_unbounded_bucket(self):
        """Unbounded bucket contains all ages above min."""
        bucket = AgeBucket("Over 90", 91, None)

        assert not bucket.contains(90)
        assert bucket.contains(91)
        assert bucket.contains(1000)
        assert bucket.is_unbounded

    def test_invalid_bucket_raises(self):
        """Raises error for invalid bucket definition."""
        with pytest.raises(ValueError, match="negative"):
            AgeBucket("Invalid", -1, 10)

        with pytest.raises(ValueError, match="less than"):
            AgeBucket("Invalid", 50, 30)

    def test_immutable(self):
        """AgeBucket is immutable."""
        bucket = AgeBucket("Test", 0, 30)

        with pytest.raises(AttributeError):
            bucket.name = "Changed"


class TestAgeItem:
    """Tests for aging individual items."""

    def setup_method(self):
        self.calculator = AgingCalculator()

    def test_age_item(self):
        """Creates aged item from document details."""
        item = self.calculator.age_item(
            document_id="inv-123",
            document_type="invoice",
            document_date=date(2024, 1, 1),
            amount=Money.of("1000.00", "USD"),
            as_of_date=date(2024, 2, 15),
            due_date=date(2024, 1, 31),
            counterparty_id="vendor-1",
        )

        assert item.document_id == "inv-123"
        assert item.age_days == 15  # Days past due
        assert item.bucket.name == "1-30"
        assert item.is_overdue
        assert item.days_past_due == 15

    def test_aged_item_not_overdue(self):
        """AgedItem not overdue when before due date."""
        item = AgedItem(
            document_id="inv-1",
            document_type="invoice",
            document_date=date(2024, 1, 1),
            due_date=date(2024, 2, 1),
            amount=Money.of("100.00", "USD"),
            age_days=-10,  # 10 days before due
            bucket=AgeBucket("Current", 0, 0),
        )

        assert not item.is_overdue
        assert item.days_past_due == 0


class TestAgingReport:
    """Tests for aging report generation."""

    def setup_method(self):
        self.calculator = AgingCalculator()

    def test_generate_report(self):
        """Generates aging report from items."""
        items = [
            self.calculator.age_item(
                document_id="inv-1",
                document_type="invoice",
                document_date=date(2024, 1, 1),
                amount=Money.of("100.00", "USD"),
                as_of_date=date(2024, 2, 15),
                counterparty_id="v-1",
            ),
            self.calculator.age_item(
                document_id="inv-2",
                document_type="invoice",
                document_date=date(2024, 2, 1),
                amount=Money.of("200.00", "USD"),
                as_of_date=date(2024, 2, 15),
                counterparty_id="v-1",
            ),
        ]

        report = self.calculator.generate_report(
            items=items,
            as_of_date=date(2024, 2, 15),
        )

        assert report.item_count == 2
        assert report.total_amount() == Money.of("300.00", "USD")

    def test_report_total_by_bucket(self):
        """Aggregates amounts by bucket."""
        items = [
            AgedItem(
                document_id="inv-1",
                document_type="invoice",
                document_date=date(2024, 1, 1),
                due_date=None,
                amount=Money.of("100.00", "USD"),
                age_days=5,
                bucket=AgeBucket("1-30", 1, 30),
            ),
            AgedItem(
                document_id="inv-2",
                document_type="invoice",
                document_date=date(2024, 1, 1),
                due_date=None,
                amount=Money.of("200.00", "USD"),
                age_days=45,
                bucket=AgeBucket("31-60", 31, 60),
            ),
        ]

        report = AgingReport(
            as_of_date=date(2024, 2, 15),
            buckets=STANDARD_BUCKETS,
            items=tuple(items),
        )

        totals = report.total_by_bucket()

        assert totals["1-30"] == Money.of("100.00", "USD")
        assert totals["31-60"] == Money.of("200.00", "USD")
        assert totals["Current"].is_zero

    def test_report_total_by_counterparty(self):
        """Aggregates amounts by counterparty and bucket."""
        items = [
            AgedItem(
                document_id="inv-1",
                document_type="invoice",
                document_date=date(2024, 1, 1),
                due_date=None,
                amount=Money.of("100.00", "USD"),
                age_days=5,
                bucket=AgeBucket("1-30", 1, 30),
                counterparty_id="v-1",
            ),
            AgedItem(
                document_id="inv-2",
                document_type="invoice",
                document_date=date(2024, 1, 1),
                due_date=None,
                amount=Money.of("200.00", "USD"),
                age_days=5,
                bucket=AgeBucket("1-30", 1, 30),
                counterparty_id="v-2",
            ),
        ]

        report = AgingReport(
            as_of_date=date(2024, 2, 15),
            buckets=STANDARD_BUCKETS,
            items=tuple(items),
        )

        by_counterparty = report.total_by_counterparty()

        assert by_counterparty["v-1"]["1-30"] == Money.of("100.00", "USD")
        assert by_counterparty["v-2"]["1-30"] == Money.of("200.00", "USD")

    def test_report_overdue_items(self):
        """Filters overdue items."""
        items = [
            AgedItem(
                document_id="inv-1",
                document_type="invoice",
                document_date=date(2024, 1, 1),
                due_date=date(2024, 1, 15),
                amount=Money.of("100.00", "USD"),
                age_days=30,  # Overdue
                bucket=AgeBucket("1-30", 1, 30),
            ),
            AgedItem(
                document_id="inv-2",
                document_type="invoice",
                document_date=date(2024, 2, 1),
                due_date=date(2024, 3, 1),
                amount=Money.of("200.00", "USD"),
                age_days=-15,  # Not due yet
                bucket=AgeBucket("Current", 0, 0),
            ),
        ]

        report = AgingReport(
            as_of_date=date(2024, 2, 15),
            buckets=STANDARD_BUCKETS,
            items=tuple(items),
        )

        overdue = report.overdue_items()

        assert len(overdue) == 1
        assert overdue[0].document_id == "inv-1"

    def test_report_items_in_bucket(self):
        """Filters items by bucket."""
        items = [
            AgedItem(
                document_id="inv-1",
                document_type="invoice",
                document_date=date(2024, 1, 1),
                due_date=None,
                amount=Money.of("100.00", "USD"),
                age_days=45,
                bucket=AgeBucket("31-60", 31, 60),
            ),
            AgedItem(
                document_id="inv-2",
                document_type="invoice",
                document_date=date(2024, 2, 1),
                due_date=None,
                amount=Money.of("200.00", "USD"),
                age_days=15,
                bucket=AgeBucket("1-30", 1, 30),
            ),
        ]

        report = AgingReport(
            as_of_date=date(2024, 2, 15),
            buckets=STANDARD_BUCKETS,
            items=tuple(items),
        )

        bucket_items = report.items_in_bucket("31-60")

        assert len(bucket_items) == 1
        assert bucket_items[0].document_id == "inv-1"


class TestGenerateReportFromDocuments:
    """Tests for generating report from raw document data."""

    def setup_method(self):
        self.calculator = AgingCalculator()

    def test_from_documents(self):
        """Generates report from document dictionaries."""
        documents = [
            {
                "document_id": "inv-1",
                "document_type": "invoice",
                "document_date": date(2024, 1, 1),
                "amount": Money.of("100.00", "USD"),
                "due_date": date(2024, 1, 31),
                "counterparty_id": "v-1",
            },
            {
                "document_id": "inv-2",
                "document_type": "invoice",
                "document_date": date(2024, 2, 1),
                "amount": Money.of("200.00", "USD"),
                "counterparty_id": "v-2",
            },
        ]

        report = self.calculator.generate_report_from_documents(
            documents=documents,
            as_of_date=date(2024, 2, 15),
        )

        assert report.item_count == 2
        assert report.total_amount() == Money.of("300.00", "USD")


class TestStandardBuckets:
    """Tests for pre-defined bucket sets."""

    def test_standard_buckets(self):
        """Standard buckets cover 0 to infinity."""
        assert len(STANDARD_BUCKETS) == 5
        assert STANDARD_BUCKETS[0].name == "Current"
        assert STANDARD_BUCKETS[-1].is_unbounded

    def test_weekly_buckets(self):
        """Weekly buckets for short-term analysis."""
        assert len(WEEKLY_BUCKETS) == 6
        assert WEEKLY_BUCKETS[0].name == "Current"
        assert WEEKLY_BUCKETS[1].name == "1-7"
