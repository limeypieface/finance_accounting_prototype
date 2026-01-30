"""
Phase 9 — Financial Exception Lifecycle Tests.

Tests that prove every failed posting attempt becomes a first-class,
durable, human-actionable financial case. No guard failure, engine failure,
or policy violation is allowed to disappear.

Test classes:
  1. TestOutcomeStateMachine       — Valid and invalid transitions
  2. TestFailedOutcomeCreation     — FAILED outcomes record failure context
  3. TestRetryLifecycle            — FAILED → RETRYING → POSTED/FAILED
  4. TestAbandonLifecycle          — FAILED → ABANDONED (terminal)
  5. TestWorkQueueQueries          — query_failed / query_actionable
  6. TestRetryContract             — Retry preconditions and limits
  7. TestStateTransitionInvariants — VALID_TRANSITIONS truth table
"""

from __future__ import annotations

import pytest
from uuid import uuid4

from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.models.interpretation_outcome import (
    FailureType,
    InterpretationOutcome,
    OutcomeStatus,
    VALID_TRANSITIONS,
)
from finance_kernel.services.outcome_recorder import (
    InvalidOutcomeTransitionError,
    OutcomeAlreadyExistsError,
    OutcomeRecorder,
)
from finance_kernel.services.retry_service import RetryNotAllowedError, RetryService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_recorder(session) -> OutcomeRecorder:
    return OutcomeRecorder(session, DeterministicClock())


def _make_retry_service(session) -> tuple[RetryService, OutcomeRecorder]:
    recorder = _make_recorder(session)
    return RetryService(session, recorder, DeterministicClock()), recorder


def _record_posted(recorder, **kwargs) -> InterpretationOutcome:
    defaults = dict(
        source_event_id=uuid4(),
        profile_id="TestProfile",
        profile_version=1,
        econ_event_id=uuid4(),
        journal_entry_ids=[uuid4()],
    )
    defaults.update(kwargs)
    return recorder.record_posted(**defaults)


def _record_failed(recorder, **kwargs) -> InterpretationOutcome:
    defaults = dict(
        source_event_id=uuid4(),
        profile_id="TestProfile",
        profile_version=1,
        failure_type=FailureType.GUARD,
        failure_message="Guard check failed: stale snapshot",
        reason_code="STALE_SNAPSHOT",
        payload_fingerprint="abc123",
        actor_id=uuid4(),
    )
    defaults.update(kwargs)
    return recorder.record_failed(**defaults)


def _record_blocked(recorder, **kwargs) -> InterpretationOutcome:
    defaults = dict(
        source_event_id=uuid4(),
        profile_id="TestProfile",
        profile_version=1,
        reason_code="PERIOD_CLOSED",
    )
    defaults.update(kwargs)
    return recorder.record_blocked(**defaults)


# ---------------------------------------------------------------------------
# 1. TestOutcomeStateMachine
# ---------------------------------------------------------------------------


class TestOutcomeStateMachine:
    """Valid and invalid transitions follow the state machine."""

    def test_blocked_to_posted(self, session):
        """BLOCKED → POSTED is allowed."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        _record_blocked(recorder, source_event_id=event_id)

        outcome = recorder.transition_to_posted(
            event_id, econ_event_id=uuid4(), journal_entry_ids=[uuid4()],
        )
        assert outcome.status == OutcomeStatus.POSTED

    def test_blocked_to_rejected(self, session):
        """BLOCKED → REJECTED is allowed."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        _record_blocked(recorder, source_event_id=event_id)

        outcome = recorder.transition_to_rejected(
            event_id, reason_code="INVALID_AMOUNT",
        )
        assert outcome.status == OutcomeStatus.REJECTED

    def test_blocked_to_failed(self, session):
        """BLOCKED → FAILED is allowed."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        _record_blocked(recorder, source_event_id=event_id)

        outcome = recorder.transition_to_failed(
            event_id,
            failure_type=FailureType.ENGINE,
            failure_message="Engine timeout",
        )
        assert outcome.status == OutcomeStatus.FAILED

    def test_failed_to_retrying(self, session):
        """FAILED → RETRYING is allowed."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)

        outcome = recorder.transition_to_retrying(event_id)
        assert outcome.status == OutcomeStatus.RETRYING
        assert outcome.retry_count == 1

    def test_failed_to_abandoned(self, session):
        """FAILED → ABANDONED is allowed."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)

        outcome = recorder.transition_to_abandoned(event_id)
        assert outcome.status == OutcomeStatus.ABANDONED

    def test_retrying_to_posted(self, session):
        """RETRYING → POSTED is allowed."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)
        recorder.transition_to_retrying(event_id)

        outcome = recorder.transition_to_posted(
            event_id, econ_event_id=uuid4(), journal_entry_ids=[uuid4()],
        )
        assert outcome.status == OutcomeStatus.POSTED

    def test_retrying_to_failed(self, session):
        """RETRYING → FAILED is allowed."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)
        recorder.transition_to_retrying(event_id)

        outcome = recorder.transition_to_failed(
            event_id,
            failure_type=FailureType.ENGINE,
            failure_message="Engine still failing",
        )
        assert outcome.status == OutcomeStatus.FAILED

    def test_posted_cannot_transition(self, session):
        """POSTED is terminal — no transitions allowed."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        _record_posted(recorder, source_event_id=event_id)

        with pytest.raises(InvalidOutcomeTransitionError):
            recorder.transition_to_rejected(event_id, reason_code="X")

    def test_rejected_cannot_transition(self, session):
        """REJECTED is terminal — no transitions allowed."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        recorder.record_rejected(
            source_event_id=event_id,
            profile_id="P", profile_version=1,
            reason_code="X",
        )

        with pytest.raises(InvalidOutcomeTransitionError):
            recorder.transition_to_posted(event_id, econ_event_id=uuid4(), journal_entry_ids=[])

    def test_abandoned_cannot_transition(self, session):
        """ABANDONED is terminal — no transitions allowed."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)
        recorder.transition_to_abandoned(event_id)

        with pytest.raises(InvalidOutcomeTransitionError):
            recorder.transition_to_retrying(event_id)

    def test_non_posting_cannot_transition(self, session):
        """NON_POSTING is terminal."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        recorder.record_non_posting(
            source_event_id=event_id,
            profile_id="P", profile_version=1,
        )

        with pytest.raises(InvalidOutcomeTransitionError):
            recorder.transition_to_posted(event_id, econ_event_id=uuid4(), journal_entry_ids=[])


# ---------------------------------------------------------------------------
# 2. TestFailedOutcomeCreation
# ---------------------------------------------------------------------------


class TestFailedOutcomeCreation:
    """FAILED outcomes correctly record failure context."""

    def test_failure_type_persisted(self, session):
        """failure_type is stored on the outcome."""
        recorder = _make_recorder(session)
        outcome = _record_failed(recorder, failure_type=FailureType.SNAPSHOT)

        loaded = recorder.get_outcome(outcome.source_event_id)
        assert loaded.failure_type == FailureType.SNAPSHOT.value

    def test_failure_message_persisted(self, session):
        """failure_message is stored on the outcome."""
        recorder = _make_recorder(session)
        outcome = _record_failed(
            recorder, failure_message="Reference snapshot version mismatch",
        )

        loaded = recorder.get_outcome(outcome.source_event_id)
        assert loaded.failure_message == "Reference snapshot version mismatch"

    def test_engine_traces_ref_persisted(self, session):
        """engine_traces_ref links to the trace set."""
        recorder = _make_recorder(session)
        trace_ref = uuid4()
        outcome = _record_failed(recorder, engine_traces_ref=trace_ref)

        loaded = recorder.get_outcome(outcome.source_event_id)
        assert loaded.engine_traces_ref == trace_ref

    def test_payload_fingerprint_persisted(self, session):
        """payload_fingerprint stores the event hash."""
        recorder = _make_recorder(session)
        outcome = _record_failed(recorder, payload_fingerprint="sha256:deadbeef")

        loaded = recorder.get_outcome(outcome.source_event_id)
        assert loaded.payload_fingerprint == "sha256:deadbeef"

    def test_actor_id_persisted(self, session):
        """actor_id is stored on the outcome."""
        recorder = _make_recorder(session)
        actor = uuid4()
        outcome = _record_failed(recorder, actor_id=actor)

        loaded = recorder.get_outcome(outcome.source_event_id)
        assert loaded.actor_id == actor

    def test_retry_count_starts_at_zero(self, session):
        """New FAILED outcomes have retry_count=0."""
        recorder = _make_recorder(session)
        outcome = _record_failed(recorder)
        assert outcome.retry_count == 0

    def test_all_failure_types_accepted(self, session):
        """Every FailureType enum value is accepted."""
        recorder = _make_recorder(session)
        for ft in FailureType:
            event_id = uuid4()
            outcome = _record_failed(
                recorder,
                source_event_id=event_id,
                failure_type=ft,
            )
            assert outcome.failure_type == ft.value

    def test_failed_is_retriable(self, session):
        """FAILED outcome has is_retriable=True."""
        recorder = _make_recorder(session)
        outcome = _record_failed(recorder)
        assert outcome.is_retriable is True

    def test_posted_is_not_retriable(self, session):
        """POSTED outcome has is_retriable=False."""
        recorder = _make_recorder(session)
        outcome = _record_posted(recorder)
        assert outcome.is_retriable is False


# ---------------------------------------------------------------------------
# 3. TestRetryLifecycle
# ---------------------------------------------------------------------------


class TestRetryLifecycle:
    """Full retry lifecycle: FAILED → RETRYING → POSTED or FAILED."""

    def test_retry_to_posted(self, session):
        """FAILED → RETRYING → POSTED."""
        retry_svc, recorder = _make_retry_service(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)

        retry_svc.initiate_retry(event_id)
        outcome = retry_svc.complete_retry_success(
            event_id,
            econ_event_id=uuid4(),
            journal_entry_ids=[uuid4()],
        )

        assert outcome.status == OutcomeStatus.POSTED
        assert outcome.retry_count == 1

    def test_retry_to_failed_again(self, session):
        """FAILED → RETRYING → FAILED."""
        retry_svc, recorder = _make_retry_service(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)

        retry_svc.initiate_retry(event_id)
        outcome = retry_svc.complete_retry_failure(
            event_id,
            failure_type=FailureType.ENGINE,
            failure_message="Still failing",
        )

        assert outcome.status == OutcomeStatus.FAILED
        assert outcome.retry_count == 1

    def test_multiple_retries_increment_count(self, session):
        """Multiple FAILED → RETRYING → FAILED cycles increment retry_count."""
        retry_svc, recorder = _make_retry_service(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)

        for expected_count in range(1, 4):
            retry_svc.initiate_retry(event_id)
            retry_svc.complete_retry_failure(
                event_id,
                failure_type=FailureType.GUARD,
                failure_message=f"Attempt {expected_count}",
            )

        outcome = recorder.get_outcome(event_id)
        assert outcome.retry_count == 3
        assert outcome.status == OutcomeStatus.FAILED

    def test_retry_then_succeed_on_third_attempt(self, session):
        """Multiple failures then success."""
        retry_svc, recorder = _make_retry_service(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)

        # Fail twice
        for _ in range(2):
            retry_svc.initiate_retry(event_id)
            retry_svc.complete_retry_failure(
                event_id,
                failure_type=FailureType.GUARD,
                failure_message="Not yet",
            )

        # Succeed on third
        retry_svc.initiate_retry(event_id)
        outcome = retry_svc.complete_retry_success(
            event_id,
            econ_event_id=uuid4(),
            journal_entry_ids=[uuid4()],
        )

        assert outcome.status == OutcomeStatus.POSTED
        assert outcome.retry_count == 3

    def test_retry_stores_engine_traces(self, session):
        """Successful retry records engine_traces_ref."""
        retry_svc, recorder = _make_retry_service(session)
        event_id = uuid4()
        trace_ref = uuid4()
        _record_failed(recorder, source_event_id=event_id)

        retry_svc.initiate_retry(event_id)
        outcome = retry_svc.complete_retry_success(
            event_id,
            econ_event_id=uuid4(),
            journal_entry_ids=[uuid4()],
            engine_traces_ref=trace_ref,
        )

        assert outcome.engine_traces_ref == trace_ref


# ---------------------------------------------------------------------------
# 4. TestAbandonLifecycle
# ---------------------------------------------------------------------------


class TestAbandonLifecycle:
    """FAILED → ABANDONED is terminal."""

    def test_abandon_sets_status(self, session):
        """abandon() transitions to ABANDONED."""
        retry_svc, recorder = _make_retry_service(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)

        outcome = retry_svc.abandon(event_id)
        assert outcome.status == OutcomeStatus.ABANDONED
        assert outcome.is_terminal is True

    def test_abandoned_stores_reason(self, session):
        """abandon() records the abandonment reason."""
        retry_svc, recorder = _make_retry_service(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)

        outcome = retry_svc.abandon(
            event_id,
            reason_code="CUSTOMER_CANCELLED",
            reason_detail={"cancelled_by": "Jane Doe"},
        )

        assert outcome.reason_code == "CUSTOMER_CANCELLED"
        assert outcome.reason_detail["cancelled_by"] == "Jane Doe"

    def test_abandoned_cannot_retry(self, session):
        """ABANDONED outcome cannot be retried."""
        retry_svc, recorder = _make_retry_service(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)
        retry_svc.abandon(event_id)

        with pytest.raises(RetryNotAllowedError):
            retry_svc.initiate_retry(event_id)

    def test_abandoned_cannot_transition(self, session):
        """ABANDONED is terminal — no transitions via recorder."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)
        recorder.transition_to_abandoned(event_id)

        with pytest.raises(InvalidOutcomeTransitionError):
            recorder.transition_to_retrying(event_id)


# ---------------------------------------------------------------------------
# 5. TestWorkQueueQueries
# ---------------------------------------------------------------------------


class TestWorkQueueQueries:
    """Work queue queries filter by failure_type, profile, actor."""

    def test_query_failed_returns_failed_only(self, session):
        """query_failed() returns only FAILED outcomes."""
        recorder = _make_recorder(session)

        # Create one POSTED, one FAILED
        _record_posted(recorder)
        failed = _record_failed(recorder)

        results = recorder.query_failed()
        assert len(results) == 1
        assert results[0].source_event_id == failed.source_event_id

    def test_query_failed_by_failure_type(self, session):
        """query_failed() filters by failure_type."""
        recorder = _make_recorder(session)

        _record_failed(recorder, failure_type=FailureType.GUARD)
        _record_failed(recorder, failure_type=FailureType.ENGINE)
        _record_failed(recorder, failure_type=FailureType.GUARD)

        guard_only = recorder.query_failed(failure_type=FailureType.GUARD)
        assert len(guard_only) == 2

        engine_only = recorder.query_failed(failure_type=FailureType.ENGINE)
        assert len(engine_only) == 1

    def test_query_failed_by_profile(self, session):
        """query_failed() filters by profile_id."""
        recorder = _make_recorder(session)

        _record_failed(recorder, profile_id="AP_INVOICE")
        _record_failed(recorder, profile_id="AR_INVOICE")
        _record_failed(recorder, profile_id="AP_INVOICE")

        results = recorder.query_failed(profile_id="AP_INVOICE")
        assert len(results) == 2

    def test_query_failed_by_actor(self, session):
        """query_failed() filters by actor_id."""
        recorder = _make_recorder(session)
        actor = uuid4()

        _record_failed(recorder, actor_id=actor)
        _record_failed(recorder, actor_id=uuid4())

        results = recorder.query_failed(actor_id=actor)
        assert len(results) == 1

    def test_query_actionable_returns_failed_and_blocked(self, session):
        """query_actionable() returns FAILED and BLOCKED outcomes."""
        recorder = _make_recorder(session)

        _record_posted(recorder)
        _record_failed(recorder)
        _record_blocked(recorder)
        recorder.record_rejected(
            source_event_id=uuid4(),
            profile_id="P", profile_version=1,
            reason_code="X",
        )

        results = recorder.query_actionable()
        statuses = {r.status for r in results}
        assert statuses == {OutcomeStatus.FAILED, OutcomeStatus.BLOCKED}
        assert len(results) == 2

    def test_query_failed_respects_limit(self, session):
        """query_failed() respects the limit parameter."""
        recorder = _make_recorder(session)
        for _ in range(5):
            _record_failed(recorder)

        results = recorder.query_failed(limit=3)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# 6. TestRetryContract
# ---------------------------------------------------------------------------


class TestRetryContract:
    """Retry preconditions and safety limits."""

    def test_cannot_retry_posted(self, session):
        """POSTED outcomes cannot be retried."""
        retry_svc, recorder = _make_retry_service(session)
        event_id = uuid4()
        _record_posted(recorder, source_event_id=event_id)

        with pytest.raises(RetryNotAllowedError, match="not 'failed'"):
            retry_svc.initiate_retry(event_id)

    def test_cannot_retry_nonexistent(self, session):
        """Non-existent event cannot be retried."""
        retry_svc, _ = _make_retry_service(session)
        with pytest.raises(RetryNotAllowedError, match="No outcome found"):
            retry_svc.initiate_retry(uuid4())

    def test_max_retries_enforced(self, session):
        """Retry count exceeding MAX_RETRIES raises error."""
        retry_svc, recorder = _make_retry_service(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)

        # Manually set retry_count to MAX_RETRIES
        outcome = recorder.get_outcome(event_id)
        outcome.retry_count = RetryService.MAX_RETRIES
        session.flush()

        with pytest.raises(RetryNotAllowedError, match="Maximum retry count"):
            retry_svc.initiate_retry(event_id)

    def test_p15_one_outcome_per_event(self, session):
        """P15: Cannot create two outcomes for the same event."""
        recorder = _make_recorder(session)
        event_id = uuid4()
        _record_failed(recorder, source_event_id=event_id)

        with pytest.raises(OutcomeAlreadyExistsError):
            _record_failed(recorder, source_event_id=event_id)


# ---------------------------------------------------------------------------
# 7. TestStateTransitionInvariants
# ---------------------------------------------------------------------------


class TestStateTransitionInvariants:
    """VALID_TRANSITIONS truth table is complete and correct."""

    def test_all_statuses_have_transition_entry(self):
        """Every OutcomeStatus has an entry in VALID_TRANSITIONS."""
        for status in OutcomeStatus:
            assert status in VALID_TRANSITIONS, (
                f"{status.value} missing from VALID_TRANSITIONS"
            )

    def test_terminal_states_have_empty_transitions(self):
        """Terminal states (POSTED, REJECTED, NON_POSTING, ABANDONED) have no outgoing transitions."""
        terminal = {
            OutcomeStatus.POSTED,
            OutcomeStatus.REJECTED,
            OutcomeStatus.NON_POSTING,
            OutcomeStatus.ABANDONED,
        }
        for status in terminal:
            assert VALID_TRANSITIONS[status] == frozenset(), (
                f"{status.value} should have no outgoing transitions"
            )

    def test_non_terminal_states_have_transitions(self):
        """Non-terminal states have at least one outgoing transition."""
        non_terminal = {
            OutcomeStatus.BLOCKED,
            OutcomeStatus.PROVISIONAL,
            OutcomeStatus.FAILED,
            OutcomeStatus.RETRYING,
        }
        for status in non_terminal:
            assert len(VALID_TRANSITIONS[status]) > 0, (
                f"{status.value} should have outgoing transitions"
            )

    def test_retrying_only_reachable_from_failed(self):
        """RETRYING can only be reached from FAILED."""
        sources = [
            status for status, targets in VALID_TRANSITIONS.items()
            if OutcomeStatus.RETRYING in targets
        ]
        assert sources == [OutcomeStatus.FAILED], (
            f"RETRYING should only be reachable from FAILED, got: {sources}"
        )

    def test_abandoned_only_reachable_from_failed(self):
        """ABANDONED can only be reached from FAILED."""
        sources = [
            status for status, targets in VALID_TRANSITIONS.items()
            if OutcomeStatus.ABANDONED in targets
        ]
        assert sources == [OutcomeStatus.FAILED], (
            f"ABANDONED should only be reachable from FAILED, got: {sources}"
        )

    def test_posted_reachable_from_multiple_states(self):
        """POSTED can be reached from BLOCKED, PROVISIONAL, RETRYING."""
        sources = {
            status for status, targets in VALID_TRANSITIONS.items()
            if OutcomeStatus.POSTED in targets
        }
        assert sources == {
            OutcomeStatus.BLOCKED,
            OutcomeStatus.PROVISIONAL,
            OutcomeStatus.RETRYING,
        }

    def test_validate_transition_method_consistent(self, session):
        """InterpretationOutcome.validate_transition matches VALID_TRANSITIONS."""
        recorder = _make_recorder(session)

        # Create a FAILED outcome
        event_id = uuid4()
        outcome = _record_failed(recorder, source_event_id=event_id)

        # Should allow RETRYING
        outcome.validate_transition(OutcomeStatus.RETRYING)
        # Should allow ABANDONED
        outcome.validate_transition(OutcomeStatus.ABANDONED)
        # Should reject POSTED (not valid from FAILED)
        with pytest.raises(ValueError, match="Invalid transition"):
            outcome.validate_transition(OutcomeStatus.POSTED)
