"""
Idempotency tests for the posting engine.

Verifies:
- N retries → one JournalEntry
- Payload mismatch → reject
- Same event always returns same result
"""

import pytest
from decimal import Decimal
from uuid import uuid4

from finance_kernel.services.ingestor_service import IngestorService, IngestStatus
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.domain.strategy_registry import StrategyRegistry


class TestEventIngestionIdempotency:
    """Tests for event ingestion idempotency."""

    def test_duplicate_event_same_payload(
        self,
        session,
        ingestor_service: IngestorService,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that ingesting the same event twice returns duplicate status."""
        event_id = uuid4()
        payload = {"amount": "100.00", "description": "Test"}
        now = deterministic_clock.now()
        today = now.date()

        # First ingestion
        result1 = ingestor_service.ingest(
            event_id=event_id,
            event_type="test.event",
            occurred_at=now,
            effective_date=today,
            actor_id=test_actor_id,
            producer="test",
            payload=payload,
        )

        assert result1.status == IngestStatus.ACCEPTED

        # Second ingestion (duplicate)
        result2 = ingestor_service.ingest(
            event_id=event_id,
            event_type="test.event",
            occurred_at=now,
            effective_date=today,
            actor_id=test_actor_id,
            producer="test",
            payload=payload,
        )

        assert result2.status == IngestStatus.DUPLICATE
        assert result2.event_id == event_id

    def test_duplicate_event_different_payload_rejected(
        self,
        session,
        ingestor_service: IngestorService,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that same event_id with different payload is rejected."""
        event_id = uuid4()
        now = deterministic_clock.now()
        today = now.date()

        # First ingestion
        ingestor_service.ingest(
            event_id=event_id,
            event_type="test.event",
            occurred_at=now,
            effective_date=today,
            actor_id=test_actor_id,
            producer="test",
            payload={"amount": "100.00"},
        )

        # Second ingestion with different payload - should be rejected
        result = ingestor_service.ingest(
            event_id=event_id,
            event_type="test.event",
            occurred_at=now,
            effective_date=today,
            actor_id=test_actor_id,
            producer="test",
            payload={"amount": "200.00"},  # Different amount
        )

        assert result.status == IngestStatus.REJECTED
        assert "mismatch" in result.message.lower()

    def test_many_duplicates_one_event(
        self,
        session,
        ingestor_service: IngestorService,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that 100 duplicate ingestions result in one event."""
        event_id = uuid4()
        payload = {"test": "data"}
        now = deterministic_clock.now()
        today = now.date()

        # Ingest 100 times
        accepted_count = 0
        duplicate_count = 0

        for _ in range(100):
            result = ingestor_service.ingest(
                event_id=event_id,
                event_type="test.event",
                occurred_at=now,
                effective_date=today,
                actor_id=test_actor_id,
                producer="test",
                payload=payload,
            )

            if result.status == IngestStatus.ACCEPTED:
                accepted_count += 1
            elif result.status == IngestStatus.DUPLICATE:
                duplicate_count += 1

        # Should have exactly one accepted, 99 duplicates
        assert accepted_count == 1
        assert duplicate_count == 99


class TestPostingIdempotency:
    """Tests for posting idempotency via PostingOrchestrator."""

    def test_post_same_event_twice_returns_same_result(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that posting the same event twice returns same journal entry."""
        # Register a test strategy for this event type
        from finance_kernel.domain.strategy import BasePostingStrategy
        from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide, ReferenceData
        from finance_kernel.domain.values import Money

        class TestStrategy(BasePostingStrategy):
            def __init__(self, evt_type: str):
                self._event_type = evt_type
                self._version = 1

            @property
            def event_type(self) -> str:
                return self._event_type

            @property
            def version(self) -> int:
                return self._version

            def _compute_line_specs(
                self, event: EventEnvelope, ref: ReferenceData
            ) -> tuple[LineSpec, ...]:
                amount = Decimal(event.payload.get("amount", "100.00"))
                return (
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(amount, "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=LineSide.CREDIT,
                        money=Money.of(amount, "USD"),
                    ),
                )

        StrategyRegistry.register(TestStrategy("test.sale"))

        try:
            event_id = uuid4()
            now = deterministic_clock.now()
            today = now.date()

            # First posting
            result1 = posting_orchestrator.post_event(
                event_id=event_id,
                event_type="test.sale",
                occurred_at=now,
                effective_date=today,
                actor_id=test_actor_id,
                producer="test",
                payload={"amount": "100.00"},
            )
            assert result1.status == PostingStatus.POSTED

            # Second posting
            result2 = posting_orchestrator.post_event(
                event_id=event_id,
                event_type="test.sale",
                occurred_at=now,
                effective_date=today,
                actor_id=test_actor_id,
                producer="test",
                payload={"amount": "100.00"},
            )
            assert result2.status == PostingStatus.ALREADY_POSTED
            assert result2.journal_entry_id == result1.journal_entry_id
        finally:
            StrategyRegistry._strategies.pop("test.sale", None)

    def test_many_post_attempts_one_entry(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that 100 post attempts result in one journal entry."""
        # Register a test strategy for this event type
        from finance_kernel.domain.strategy import BasePostingStrategy
        from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide, ReferenceData
        from finance_kernel.domain.values import Money

        class TestStrategy(BasePostingStrategy):
            def __init__(self, evt_type: str):
                self._event_type = evt_type
                self._version = 1

            @property
            def event_type(self) -> str:
                return self._event_type

            @property
            def version(self) -> int:
                return self._version

            def _compute_line_specs(
                self, event: EventEnvelope, ref: ReferenceData
            ) -> tuple[LineSpec, ...]:
                amount = Decimal(event.payload.get("amount", "100.00"))
                return (
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(amount, "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=LineSide.CREDIT,
                        money=Money.of(amount, "USD"),
                    ),
                )

        StrategyRegistry.register(TestStrategy("test.multi"))

        try:
            event_id = uuid4()
            now = deterministic_clock.now()
            today = now.date()

            posted_count = 0
            already_posted_count = 0
            entry_id = None

            for _ in range(100):
                result = posting_orchestrator.post_event(
                    event_id=event_id,
                    event_type="test.multi",
                    occurred_at=now,
                    effective_date=today,
                    actor_id=test_actor_id,
                    producer="test",
                    payload={"amount": "100.00"},
                )

                if result.status == PostingStatus.POSTED:
                    posted_count += 1
                    entry_id = result.journal_entry_id
                elif result.status == PostingStatus.ALREADY_POSTED:
                    already_posted_count += 1
                    # Verify same entry ID
                    assert result.journal_entry_id == entry_id

            # Should have exactly one posted, 99 already_posted
            assert posted_count == 1
            assert already_posted_count == 99
        finally:
            StrategyRegistry._strategies.pop("test.multi", None)
