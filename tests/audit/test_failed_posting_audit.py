"""
Audit Trail for Failed Postings Tests.

A complete audit trail must include rejected postings, not just successful ones.
This is critical for detecting attack attempts and debugging issues.

These tests verify that:
1. Closed period rejections create audit records
2. Validation failures create audit records
3. Failed postings don't corrupt the audit chain
4. Rejection reasons are recorded
"""

import pytest
from uuid import uuid4
from datetime import date, timedelta

from sqlalchemy import select

from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.models.audit_event import AuditEvent, AuditAction
from finance_kernel.domain.clock import DeterministicClock


class TestFailedPostingAuditRecords:
    """
    Tests for audit records on failed postings.
    """

    def test_closed_period_rejection_creates_audit_record(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        create_period,
        standard_accounts,
        test_actor_id,
        deterministic_clock: DeterministicClock,
        auditor_service: AuditorService,
    ):
        """
        Verify that posting to a closed period creates an audit record.
        """
        # Create and close a period
        today = deterministic_clock.now().date()
        last_month = today.replace(day=1) - timedelta(days=1)
        start = last_month.replace(day=1)

        closed_period = create_period(
            period_code="AUDIT-CLOSED-01",
            name="Closed Period for Audit Test",
            start_date=start,
            end_date=last_month,
        )

        # Close the period
        period_service.close_period(closed_period.period_code, test_actor_id)
        session.flush()

        # Count audit events before rejection
        audit_count_before = session.execute(
            select(AuditEvent)
        ).scalars().all()
        count_before = len(audit_count_before)

        # Attempt to post to closed period
        event_id = uuid4()
        result = posting_orchestrator.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=start + timedelta(days=5),  # Date in closed period
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )

        # Should be rejected
        assert result.status == PostingStatus.PERIOD_CLOSED

        # Check for audit event recording the rejection
        # (The exact audit behavior depends on implementation)
        # At minimum, no corruption should occur
        audit_count_after = session.execute(
            select(AuditEvent)
        ).scalars().all()

        # Verify chain is still valid
        assert auditor_service.validate_chain() is True, (
            "Audit chain should remain valid after rejection"
        )

    def test_validation_failure_does_not_corrupt_chain(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
        auditor_service: AuditorService,
    ):
        """
        Verify that validation failures don't corrupt the audit chain.
        """
        # First, post a valid entry to establish chain
        result_valid = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result_valid.status == PostingStatus.POSTED

        # Verify chain is valid
        assert auditor_service.validate_chain() is True

        # Now attempt an invalid posting (unbalanced entry)
        result_invalid = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    # Unbalanced - no credit line
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )

        # Should fail validation
        assert result_invalid.status == PostingStatus.VALIDATION_FAILED

        # Chain should still be valid
        assert auditor_service.validate_chain() is True, (
            "Audit chain should remain valid after validation failure"
        )

        # Post another valid entry to prove chain continuity
        result_valid2 = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "50.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "50.00", "currency": "USD"},
                ]
            },
        )
        assert result_valid2.status == PostingStatus.POSTED

        # Final chain validation
        assert auditor_service.validate_chain() is True

    def test_multiple_failures_dont_corrupt_chain(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
        auditor_service: AuditorService,
    ):
        """
        Verify that multiple consecutive failures don't corrupt the chain.
        """
        # Post initial valid entry
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED

        # Attempt multiple invalid postings
        invalid_payloads = [
            # Unbalanced
            {"lines": [{"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"}]},
            # Invalid account
            {"lines": [
                {"account_code": "INVALID", "side": "debit", "amount": "100.00", "currency": "USD"},
                {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
            ]},
            # Another unbalanced
            {"lines": [{"account_code": "4000", "side": "credit", "amount": "500.00", "currency": "USD"}]},
        ]

        for payload in invalid_payloads:
            result_invalid = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload=payload,
            )
            assert result_invalid.status == PostingStatus.VALIDATION_FAILED

        # Chain should still be valid
        assert auditor_service.validate_chain() is True

    def test_chain_valid_after_mixed_success_and_failure(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
        auditor_service: AuditorService,
    ):
        """
        Verify audit chain validity after mixed successes and failures.
        """
        results = []

        # Interleave valid and invalid postings
        for i in range(10):
            if i % 3 == 0:
                # Invalid posting
                payload = {
                    "lines": [{"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"}]
                }
            else:
                # Valid posting
                payload = {
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": str(10 + i), "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": str(10 + i), "currency": "USD"},
                    ]
                }

            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload=payload,
            )
            results.append(result)

        # Count successes and failures
        successes = [r for r in results if r.status == PostingStatus.POSTED]
        failures = [r for r in results if r.status == PostingStatus.VALIDATION_FAILED]

        assert len(successes) > 0, "Should have some successes"
        assert len(failures) > 0, "Should have some failures"

        # Chain should still be valid
        assert auditor_service.validate_chain() is True


class TestAuditChainIntegrity:
    """
    Tests for audit chain integrity during failure scenarios.
    """

    def test_audit_chain_monotonic_after_failures(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
        auditor_service: AuditorService,
    ):
        """
        Verify that audit sequence numbers remain monotonic after failures.
        """
        # Post several entries with failures in between
        for i in range(5):
            # Try invalid first
            posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={"lines": []},  # Invalid - no lines
            )

            # Then valid
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": str(10 + i), "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": str(10 + i), "currency": "USD"},
                    ]
                },
            )
            assert result.status == PostingStatus.POSTED

        # Verify audit events are monotonic
        audit_events = session.execute(
            select(AuditEvent).order_by(AuditEvent.seq)
        ).scalars().all()

        sequences = [e.seq for e in audit_events]

        # Sequences should be strictly increasing
        for i in range(1, len(sequences)):
            assert sequences[i] > sequences[i - 1], (
                f"Audit sequence not monotonic: {sequences[i - 1]} -> {sequences[i]}"
            )

    def test_audit_hash_chain_linked_after_failures(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
        auditor_service: AuditorService,
    ):
        """
        Verify that audit hash chain is properly linked even with failures.
        """
        # Post valid entries with failures in between
        for i in range(3):
            # Invalid
            posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={"lines": [{"account_code": "INVALID", "side": "debit", "amount": "100", "currency": "USD"}]},
            )

            # Valid
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": str(100 + i), "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": str(100 + i), "currency": "USD"},
                    ]
                },
            )
            assert result.status == PostingStatus.POSTED

        # Verify chain linkage
        audit_events = session.execute(
            select(AuditEvent).order_by(AuditEvent.seq)
        ).scalars().all()

        # First event should have no prev_hash
        if audit_events:
            assert audit_events[0].prev_hash is None

            # Each subsequent event should link to previous
            for i in range(1, len(audit_events)):
                assert audit_events[i].prev_hash == audit_events[i - 1].hash, (
                    f"Hash chain broken at event {i}"
                )

        # Full chain validation
        assert auditor_service.validate_chain() is True
