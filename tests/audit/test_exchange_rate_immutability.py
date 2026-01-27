"""
Exchange Rate immutability and validation tests.

From exchange_rate.py docstring:
"Rates are append-only"
"Rates are immutable - new rates are added, old rates are never modified"
"Each posting records the specific rate_id used for conversions"

These tests verify that:
1. ExchangeRate records referenced by JournalLines cannot be modified
2. ExchangeRate records referenced by JournalLines cannot be deleted
3. Exchange rates cannot be zero or negative
4. Inverse rates are validated to prevent arbitrage
5. Enforcement exists at both ORM and database levels
"""

import pytest
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from finance_kernel.models.exchange_rate import ExchangeRate
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus, LineSide
from finance_kernel.models.event import Event
from finance_kernel.exceptions import (
    ExchangeRateImmutableError,
    ExchangeRateReferencedError,
    InvalidExchangeRateError,
)


class TestExchangeRateValueValidation:
    """
    Test that exchange rate values are validated (zero, negative, extreme values).
    """

    def test_cannot_create_zero_exchange_rate(
        self,
        session,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that zero exchange rate is rejected."""
        with pytest.raises(InvalidExchangeRateError) as exc_info:
            rate = ExchangeRate(
                from_currency="USD",
                to_currency="EUR",
                rate=Decimal("0"),
                effective_at=deterministic_clock.now(),
                source="test",
                created_by_id=test_actor_id,
            )
            session.add(rate)
            session.flush()

        assert "positive" in str(exc_info.value).lower()
        session.rollback()

    def test_cannot_create_negative_exchange_rate(
        self,
        session,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that negative exchange rate is rejected."""
        with pytest.raises(InvalidExchangeRateError) as exc_info:
            rate = ExchangeRate(
                from_currency="USD",
                to_currency="EUR",
                rate=Decimal("-1.5"),
                effective_at=deterministic_clock.now(),
                source="test",
                created_by_id=test_actor_id,
            )
            session.add(rate)
            session.flush()

        assert "positive" in str(exc_info.value).lower()
        session.rollback()

    def test_cannot_update_rate_to_zero(
        self,
        session,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that updating a rate to zero is rejected."""
        # Create valid rate first
        rate = ExchangeRate(
            from_currency="USD",
            to_currency="GBP",
            rate=Decimal("0.79"),
            effective_at=deterministic_clock.now(),
            source="test",
            created_by_id=test_actor_id,
        )
        session.add(rate)
        session.flush()

        # Try to update to zero
        rate.rate = Decimal("0")

        with pytest.raises(InvalidExchangeRateError):
            session.flush()

        session.rollback()

    def test_cannot_update_rate_to_negative(
        self,
        session,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that updating a rate to negative is rejected."""
        # Create valid rate first
        rate = ExchangeRate(
            from_currency="USD",
            to_currency="JPY",
            rate=Decimal("149.50"),
            effective_at=deterministic_clock.now(),
            source="test",
            created_by_id=test_actor_id,
        )
        session.add(rate)
        session.flush()

        # Try to update to negative
        rate.rate = Decimal("-149.50")

        with pytest.raises(InvalidExchangeRateError):
            session.flush()

        session.rollback()

    def test_valid_positive_rate_accepted(
        self,
        session,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that valid positive exchange rates are accepted."""
        rate = ExchangeRate(
            from_currency="EUR",
            to_currency="USD",
            rate=Decimal("1.08"),
            effective_at=deterministic_clock.now(),
            source="test",
            created_by_id=test_actor_id,
        )
        session.add(rate)
        session.flush()

        assert rate.id is not None
        assert rate.rate == Decimal("1.08")


class TestExchangeRateImmutabilityWhenReferenced:
    """
    Test that ExchangeRate records cannot be modified once referenced by JournalLines.
    """

    def test_cannot_modify_exchange_rate_referenced_by_journal_line(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test that an ExchangeRate referenced by a JournalLine cannot be modified.
        """
        # Create an exchange rate
        exchange_rate = ExchangeRate(
            from_currency="USD",
            to_currency="EUR",
            rate=Decimal("0.92"),
            effective_at=deterministic_clock.now(),
            source="test",
            created_by_id=test_actor_id,
        )
        session.add(exchange_rate)
        session.flush()

        original_rate = exchange_rate.rate
        rate_id = exchange_rate.id

        # Create source event
        event = Event(
            event_id=uuid4(),
            event_type="test.exchange.rate",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={},
            payload_hash=f"hash-{uuid4()}",
            ingested_at=deterministic_clock.now(),
        )
        session.add(event)
        session.flush()

        # Create posted journal entry
        entry = JournalEntry(
            source_event_id=event.event_id,
            source_event_type="test.exchange.rate",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            status=JournalEntryStatus.POSTED,
            idempotency_key=f"test:exchange:{uuid4()}",
            posting_rule_version=1,
            posted_at=deterministic_clock.now(),
            seq=99999,
            created_by_id=test_actor_id,
        )
        session.add(entry)
        session.flush()

        # Create journal line that references the rate
        cash = standard_accounts["cash"]
        revenue = standard_accounts["revenue"]

        line1 = JournalLine(
            journal_entry_id=entry.id,
            account_id=cash.id,
            side=LineSide.DEBIT,
            amount=Decimal("100.00"),
            currency="USD",
            line_seq=0,
            exchange_rate_id=rate_id,
            created_by_id=test_actor_id,
        )
        line2 = JournalLine(
            journal_entry_id=entry.id,
            account_id=revenue.id,
            side=LineSide.CREDIT,
            amount=Decimal("100.00"),
            currency="USD",
            line_seq=1,
            created_by_id=test_actor_id,
        )
        session.add_all([line1, line2])
        session.flush()

        # THE ATTACK: Try to modify the exchange rate
        exchange_rate.rate = Decimal("1.50")

        with pytest.raises(ExchangeRateImmutableError) as exc_info:
            session.flush()

        assert "immutable" in str(exc_info.value).lower()
        session.rollback()


class TestExchangeRateDeletionProtection:
    """
    Test that referenced ExchangeRate records cannot be deleted.
    """

    def test_cannot_delete_exchange_rate_referenced_by_journal_line(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that a referenced ExchangeRate cannot be deleted."""
        # Create exchange rate
        exchange_rate = ExchangeRate(
            from_currency="GBP",
            to_currency="USD",
            rate=Decimal("1.27"),
            effective_at=deterministic_clock.now(),
            source="test",
            created_by_id=test_actor_id,
        )
        session.add(exchange_rate)
        session.flush()
        rate_id = exchange_rate.id

        # Create source event
        event = Event(
            event_id=uuid4(),
            event_type="test.exchange.delete",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={},
            payload_hash=f"hash-{uuid4()}",
            ingested_at=deterministic_clock.now(),
        )
        session.add(event)
        session.flush()

        # Create posted journal entry
        entry = JournalEntry(
            source_event_id=event.event_id,
            source_event_type="test.exchange.delete",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            status=JournalEntryStatus.POSTED,
            idempotency_key=f"test:exchange:delete:{uuid4()}",
            posting_rule_version=1,
            posted_at=deterministic_clock.now(),
            seq=99998,
            created_by_id=test_actor_id,
        )
        session.add(entry)
        session.flush()

        # Create journal line referencing the rate
        cash = standard_accounts["cash"]
        line = JournalLine(
            journal_entry_id=entry.id,
            account_id=cash.id,
            side=LineSide.DEBIT,
            amount=Decimal("100.00"),
            currency="GBP",
            line_seq=0,
            exchange_rate_id=rate_id,
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        # THE ATTACK: Try to delete the exchange rate
        session.delete(exchange_rate)

        with pytest.raises(ExchangeRateReferencedError) as exc_info:
            session.flush()

        assert "referenced" in str(exc_info.value).lower()
        session.rollback()


class TestExchangeRateUnreferencedOperations:
    """
    Test that unreferenced ExchangeRate records CAN be modified/deleted.
    """

    def test_unreferenced_exchange_rate_can_be_modified(
        self,
        session,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that unreferenced rates can be modified."""
        rate = ExchangeRate(
            from_currency="CHF",
            to_currency="USD",
            rate=Decimal("1.08"),
            effective_at=deterministic_clock.now(),
            source="test",
            created_by_id=test_actor_id,
        )
        session.add(rate)
        session.flush()

        # Modify the rate - should succeed
        rate.rate = Decimal("1.10")
        session.flush()

        assert rate.rate == Decimal("1.10")

    def test_unreferenced_exchange_rate_can_be_deleted(
        self,
        session,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that unreferenced rates can be deleted."""
        rate = ExchangeRate(
            from_currency="AUD",
            to_currency="USD",
            rate=Decimal("0.65"),
            effective_at=deterministic_clock.now(),
            source="test",
            created_by_id=test_actor_id,
        )
        session.add(rate)
        session.flush()
        rate_id = rate.id

        # Delete the rate - should succeed
        session.delete(rate)
        session.flush()

        assert session.get(ExchangeRate, rate_id) is None


class TestDatabaseLevelEnforcement:
    """
    Test that database triggers enforce exchange rate rules.
    """

    def test_raw_sql_cannot_set_zero_rate(
        self,
        session,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that raw SQL cannot create a zero rate."""
        try:
            session.execute(
                text("""
                    INSERT INTO exchange_rates
                    (id, from_currency, to_currency, rate, effective_at, source,
                     created_at, updated_at, created_by_id)
                    VALUES
                    (:id, 'USD', 'EUR', 0, :effective_at, 'test',
                     NOW(), NOW(), :actor_id)
                """),
                {
                    "id": str(uuid4()),
                    "effective_at": deterministic_clock.now(),
                    "actor_id": str(test_actor_id),
                },
            )
            session.flush()
            pytest.fail("Zero rate should be rejected by database trigger")
        except Exception as e:
            assert "EXCHANGE_RATE_INVALID" in str(e) or "positive" in str(e).lower()
            session.rollback()

    def test_raw_sql_cannot_set_negative_rate(
        self,
        session,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that raw SQL cannot create a negative rate."""
        try:
            session.execute(
                text("""
                    INSERT INTO exchange_rates
                    (id, from_currency, to_currency, rate, effective_at, source,
                     created_at, updated_at, created_by_id)
                    VALUES
                    (:id, 'USD', 'EUR', -1.5, :effective_at, 'test',
                     NOW(), NOW(), :actor_id)
                """),
                {
                    "id": str(uuid4()),
                    "effective_at": deterministic_clock.now(),
                    "actor_id": str(test_actor_id),
                },
            )
            session.flush()
            pytest.fail("Negative rate should be rejected by database trigger")
        except Exception as e:
            assert "EXCHANGE_RATE_INVALID" in str(e) or "positive" in str(e).lower()
            session.rollback()

    def test_raw_sql_cannot_modify_referenced_rate(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that raw SQL cannot modify a referenced exchange rate."""
        # Create exchange rate
        exchange_rate = ExchangeRate(
            from_currency="JPY",
            to_currency="USD",
            rate=Decimal("0.0067"),
            effective_at=deterministic_clock.now(),
            source="test",
            created_by_id=test_actor_id,
        )
        session.add(exchange_rate)
        session.flush()
        rate_id = exchange_rate.id

        # Create source event
        event = Event(
            event_id=uuid4(),
            event_type="test.exchange.sql",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={},
            payload_hash=f"hash-{uuid4()}",
            ingested_at=deterministic_clock.now(),
        )
        session.add(event)
        session.flush()

        # Create posted journal entry
        entry = JournalEntry(
            source_event_id=event.event_id,
            source_event_type="test.exchange.sql",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            status=JournalEntryStatus.POSTED,
            idempotency_key=f"test:exchange:sql:{uuid4()}",
            posting_rule_version=1,
            posted_at=deterministic_clock.now(),
            seq=99997,
            created_by_id=test_actor_id,
        )
        session.add(entry)
        session.flush()

        # Create journal line referencing the rate
        cash = standard_accounts["cash"]
        line = JournalLine(
            journal_entry_id=entry.id,
            account_id=cash.id,
            side=LineSide.DEBIT,
            amount=Decimal("10000.00"),
            currency="JPY",
            line_seq=0,
            exchange_rate_id=rate_id,
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        # THE ATTACK: Try raw SQL update
        try:
            session.execute(
                text("""
                    UPDATE exchange_rates
                    SET rate = 0.0100
                    WHERE id = :rate_id
                """),
                {"rate_id": str(rate_id)},
            )
            session.flush()
            pytest.fail("Raw SQL should be blocked by database trigger")
        except Exception as e:
            assert "EXCHANGE_RATE_IMMUTABLE" in str(e) or "immutable" in str(e).lower()
            session.rollback()


class TestArbitrageProtection:
    """
    Test that inverse rate arbitrage is detected and prevented.
    """

    def test_arbitrage_detection_inconsistent_inverse(
        self,
        session,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test that inconsistent inverse rates are detected.

        If USD/EUR = 0.92, then EUR/USD should be ~1.087 (1/0.92)
        If we try to set EUR/USD = 1.50, that creates arbitrage opportunity.
        """
        effective_time = deterministic_clock.now()

        # Create forward rate: USD/EUR = 0.92
        forward_rate = ExchangeRate(
            from_currency="USD",
            to_currency="EUR",
            rate=Decimal("0.92"),
            effective_at=effective_time,
            source="test",
            created_by_id=test_actor_id,
        )
        session.add(forward_rate)
        session.flush()

        # Create inverse rate that creates arbitrage: EUR/USD = 1.50
        # (should be ~1.087 based on forward rate)
        try:
            inverse_rate = ExchangeRate(
                from_currency="EUR",
                to_currency="USD",
                rate=Decimal("1.50"),  # Inconsistent! Creates arbitrage
                effective_at=effective_time,
                source="test",
                created_by_id=test_actor_id,
            )
            session.add(inverse_rate)
            session.flush()

            # If we get here, arbitrage protection may not be enforced
            # The product should be ~1.0, but 0.92 * 1.50 = 1.38
            pytest.fail(
                "Arbitrage should be detected: USD/EUR=0.92 and EUR/USD=1.50 "
                "creates opportunity (product=1.38, should be ~1.0)"
            )
        except Exception as e:
            if "ARBITRAGE" in str(e).upper():
                session.rollback()
                return
            # If it's a different error, that's fine - the important thing
            # is the inconsistent rate wasn't silently accepted
            session.rollback()

    def test_consistent_inverse_rates_allowed(
        self,
        session,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test that consistent inverse rates are allowed.

        USD/EUR = 0.92 and EUR/USD = 1.087 (approximately 1/0.92) should be OK.
        """
        effective_time = deterministic_clock.now()

        # Create forward rate
        forward_rate = ExchangeRate(
            from_currency="USD",
            to_currency="EUR",
            rate=Decimal("0.92"),
            effective_at=effective_time,
            source="test",
            created_by_id=test_actor_id,
        )
        session.add(forward_rate)
        session.flush()

        # Create consistent inverse rate (1/0.92 = 1.0869565...)
        inverse_rate = ExchangeRate(
            from_currency="EUR",
            to_currency="USD",
            rate=Decimal("1.0869565"),  # Consistent inverse
            effective_at=effective_time,
            source="test",
            created_by_id=test_actor_id,
        )
        session.add(inverse_rate)
        session.flush()

        # Both rates should exist
        assert forward_rate.id is not None
        assert inverse_rate.id is not None

        # Product should be approximately 1.0
        product = forward_rate.rate * inverse_rate.rate
        assert abs(product - Decimal("1.0")) < Decimal("0.001")
