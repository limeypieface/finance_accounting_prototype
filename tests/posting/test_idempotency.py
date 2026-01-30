"""
Idempotency tests using the interpretation pipeline.

Verifies:
- Event ingestion: N retries → one Event (IngestorService)
- Posting: N retries with same econ_event_id → one JournalEntry (JournalWriter)
- Payload mismatch → rejection (IngestorService)
- InterpretationOutcome: one outcome per source_event_id (P15)
"""

import pytest
from decimal import Decimal
from uuid import uuid4

from finance_kernel.services.ingestor_service import IngestorService, IngestStatus
from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator
from finance_kernel.services.journal_writer import WriteStatus
from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
    IntentLine,
    LedgerIntent,
)
from finance_kernel.domain.meaning_builder import (
    EconomicEventData,
    MeaningBuilderResult,
)
from tests.conftest import make_source_event


def _make_meaning_and_intent(
    session,
    actor_id,
    clock,
    effective_date,
    source_event_id=None,
    econ_event_id=None,
    amount=Decimal("100.00"),
    currency="USD",
    profile_id="IdempotencyTest",
    create_event_record=True,
):
    """Create a MeaningBuilderResult and AccountingIntent for testing."""
    source_event_id = source_event_id or uuid4()
    econ_event_id = econ_event_id or uuid4()
    # Create source Event record (FK requirement for JournalEntry)
    if create_event_record:
        make_source_event(session, source_event_id, actor_id, clock, effective_date)

    econ_data = EconomicEventData(
        source_event_id=source_event_id,
        economic_type="test.idempotency",
        effective_date=effective_date,
        profile_id=profile_id,
        profile_version=1,
        profile_hash=None,
        quantity=amount,
    )
    meaning_result = MeaningBuilderResult.ok(econ_data)

    intent = AccountingIntent(
        econ_event_id=econ_event_id,
        source_event_id=source_event_id,
        profile_id=profile_id,
        profile_version=1,
        effective_date=effective_date,
        ledger_intents=(
            LedgerIntent(
                ledger_id="GL",
                lines=(
                    IntentLine.debit("CashAsset", amount, currency),
                    IntentLine.credit("SalesRevenue", amount, currency),
                ),
            ),
        ),
        snapshot=AccountingIntentSnapshot(
            coa_version=1,
            dimension_schema_version=1,
        ),
    )

    return meaning_result, intent


class TestEventIngestionIdempotency:
    """Tests for event ingestion idempotency (IngestorService).

    IngestorService is a foundational service used by both old and new
    architectures. These tests remain valid as-is.
    """

    def test_duplicate_event_same_payload(
        self,
        session,
        ingestor_service: IngestorService,
        test_actor_id,
        deterministic_clock,
    ):
        """Ingesting the same event twice returns duplicate status."""
        event_id = uuid4()
        payload = {"amount": "100.00", "description": "Test"}
        now = deterministic_clock.now()
        today = now.date()

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
        """Same event_id with different payload is rejected."""
        event_id = uuid4()
        now = deterministic_clock.now()
        today = now.date()

        ingestor_service.ingest(
            event_id=event_id,
            event_type="test.event",
            occurred_at=now,
            effective_date=today,
            actor_id=test_actor_id,
            producer="test",
            payload={"amount": "100.00"},
        )

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
        """100 duplicate ingestions result in one event."""
        event_id = uuid4()
        payload = {"test": "data"}
        now = deterministic_clock.now()
        today = now.date()

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

        assert accepted_count == 1
        assert duplicate_count == 99


class TestPostingIdempotency:
    """Tests for posting idempotency via InterpretationCoordinator.

    The JournalWriter uses idempotency keys ({econ_event_id}:{ledger_id}:{profile_version})
    to ensure that the same economic event is only posted once per ledger.
    """

    def test_post_same_event_twice_returns_already_exists(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """Posting the same economic event twice returns ALREADY_EXISTS on second attempt."""
        today = deterministic_clock.now().date()
        source_event_id = uuid4()
        econ_event_id = uuid4()

        meaning_result1, intent1 = _make_meaning_and_intent(
            session, test_actor_id, deterministic_clock,
            today,
            source_event_id=source_event_id,
            econ_event_id=econ_event_id,
        )

        # First posting
        result1 = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result1,
            accounting_intent=intent1,
            actor_id=test_actor_id,
        )
        session.flush()
        assert result1.success

        # Second posting with DIFFERENT source_event_id but same econ_event_id
        # This tests JournalWriter idempotency (key is econ_event_id:GL:1)
        source_event_id2 = uuid4()
        meaning_result2, intent2 = _make_meaning_and_intent(
            session, test_actor_id, deterministic_clock,
            today,
            source_event_id=source_event_id2,
            econ_event_id=econ_event_id,
        )

        result2 = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result2,
            accounting_intent=intent2,
            actor_id=test_actor_id,
        )
        session.flush()

        # Second attempt should succeed but with ALREADY_EXISTS status
        assert result2.success
        assert result2.journal_result is not None
        assert result2.journal_result.status == WriteStatus.ALREADY_EXISTS

    def test_outcome_per_source_event_is_unique(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """P15: Each source_event_id gets exactly one InterpretationOutcome.

        Attempting to record a second outcome for the same source_event_id
        should be rejected.
        """
        today = deterministic_clock.now().date()

        # First event posts normally
        meaning_result1, intent1 = _make_meaning_and_intent(session, test_actor_id, deterministic_clock, today)
        result1 = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result1,
            accounting_intent=intent1,
            actor_id=test_actor_id,
        )
        session.flush()
        assert result1.success
        assert result1.outcome is not None

        # Same source_event_id should not get a second outcome
        source_event_id = result1.outcome.source_event_id
        econ_data2 = EconomicEventData(
            source_event_id=source_event_id,
            economic_type="test.idempotency",
            effective_date=today,
            profile_id="IdempotencyTest",
            profile_version=1,
            profile_hash=None,
            quantity=Decimal("100.00"),
        )
        meaning_result2 = MeaningBuilderResult.ok(econ_data2)

        intent2 = AccountingIntent(
            econ_event_id=uuid4(),
            source_event_id=source_event_id,
            profile_id="IdempotencyTest",
            profile_version=1,
            effective_date=today,
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("CashAsset", Decimal("100.00"), "USD"),
                        IntentLine.credit("SalesRevenue", Decimal("100.00"), "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(
                coa_version=1,
                dimension_schema_version=1,
            ),
        )

        # The coordinator should detect P15 violation
        from finance_kernel.services.outcome_recorder import OutcomeAlreadyExistsError
        with pytest.raises(OutcomeAlreadyExistsError):
            interpretation_coordinator.interpret_and_post(
                meaning_result=meaning_result2,
                accounting_intent=intent2,
                actor_id=test_actor_id,
            )
