"""
Audit Trail for Failed Postings Tests.

A complete audit trail must include rejected postings, not just successful ones.
This is critical for detecting attack attempts and debugging issues.

These tests verify that:
1. Successful postings maintain audit chain integrity
2. Multiple consecutive successes don't corrupt the audit chain
3. The audit chain remains valid and monotonically sequenced
4. InterpretationOutcome records are created for all postings
"""

import pytest
from uuid import uuid4
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.models.audit_event import AuditEvent, AuditAction
from finance_kernel.models.journal import JournalEntry
from finance_kernel.domain.clock import DeterministicClock


class TestPostingAuditRecords:
    """
    Tests for audit records on postings via ModulePostingService.

    The posting pipeline records InterpretationOutcome for every event.
    """

    def test_successful_posting_maintains_audit_chain(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
        auditor_service: AuditorService,
    ):
        """
        Verify that a successful posting maintains audit chain integrity.
        """
        # Post a valid entry
        result = post_via_coordinator(
            debit_role="CashAsset",
            credit_role="SalesRevenue",
            amount=Decimal("100.00"),
        )
        assert result.success

        # Verify audit chain is valid
        assert auditor_service.validate_chain() is True, (
            "Audit chain should be valid after successful posting"
        )

    def test_multiple_postings_dont_corrupt_chain(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
        auditor_service: AuditorService,
    ):
        """
        Verify that multiple consecutive postings don't corrupt the audit chain.
        """
        # Post initial valid entry
        result = post_via_coordinator(amount=Decimal("100.00"))
        assert result.success

        # Verify chain is valid
        assert auditor_service.validate_chain() is True

        # Post more entries
        for i in range(5):
            result = post_via_coordinator(
                amount=Decimal(str(50 + i)),
            )
            assert result.success

        # Chain should still be valid
        assert auditor_service.validate_chain() is True

    def test_chain_valid_after_many_postings(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
        auditor_service: AuditorService,
    ):
        """
        Verify audit chain validity after many consecutive postings.
        """
        results = []

        for i in range(10):
            result = post_via_coordinator(
                amount=Decimal(str(10 + i)),
            )
            results.append(result)

        # Count successes
        successes = [r for r in results if r.success]
        assert len(successes) == 10, "All postings should succeed"

        # Chain should be valid
        assert auditor_service.validate_chain() is True

    def test_interpretation_outcome_recorded(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Verify that the posting pipeline records InterpretationOutcome for postings.
        """
        from finance_kernel.models.interpretation_outcome import InterpretationOutcome

        result = post_via_coordinator(
            amount=Decimal("100.00"),
        )
        assert result.success

        # Verify outcome was recorded
        outcomes = session.execute(
            select(InterpretationOutcome)
        ).scalars().all()

        assert len(outcomes) >= 1, "Should have at least one InterpretationOutcome"
        assert any(
            o.status.value == "posted" for o in outcomes
        ), "Should have a POSTED outcome"


class TestAuditChainIntegrity:
    """
    Tests for audit chain integrity under the posting pipeline.
    """

    def test_audit_chain_monotonic_after_postings(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
        auditor_service: AuditorService,
    ):
        """
        Verify that audit sequence numbers remain monotonic after postings.
        """
        # Post several entries
        for i in range(5):
            result = post_via_coordinator(
                amount=Decimal(str(10 + i)),
            )
            assert result.success

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

    def test_audit_hash_chain_linked(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
        auditor_service: AuditorService,
    ):
        """
        Verify that audit hash chain is properly linked.
        """
        # Post valid entries
        for i in range(3):
            result = post_via_coordinator(
                amount=Decimal(str(100 + i)),
            )
            assert result.success

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
