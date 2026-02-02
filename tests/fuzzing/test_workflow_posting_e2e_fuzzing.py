"""
End-to-end workflow posting fuzzing.

For workflow transitions that have ``posts_entry=True``, we maintain a small
registry mapping (workflow_name, from_state, action) -> (event_type, payload_builder).
Hypothesis draws from this registry and fuzzes amount (and optionally other fields);
we call ModulePostingService.post_event() and assert no crash and a well-formed result.

This exercises the full path: event_type + payload -> ingest -> profile dispatch
-> meaning -> journal write, with fuzzed inputs. No workflow state is persisted
between calls (we only test "post the event that would be posted after this
transition" with varying amounts).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4, uuid5, NAMESPACE_DNS

import pytest

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False
    given = lambda *a, **k: (lambda f: f)
    settings = lambda *a, **k: (lambda f: f)
    st = None

from finance_kernel.services.module_posting_service import ModulePostingStatus


# ---------------------------------------------------------------------------
# Registry: (workflow_name, from_state, action) -> (event_type, payload_builder)
# payload_builder(amount: Decimal, invoice_number: str, ...) -> dict
# ---------------------------------------------------------------------------

def _ap_invoice_payload(amount: Decimal, invoice_number: str, **kwargs: Any) -> dict[str, Any]:
    """Minimal AP invoice (direct expense) payload for ap.invoice_received."""
    return {
        "invoice_number": invoice_number,
        "supplier_code": kwargs.get("supplier_code", "SUP-100"),
        "gross_amount": str(amount),
        "po_number": None,
    }


# Only include transitions we can actually post with minimal payloads.
# AP invoice "match" -> post ap.invoice_received (direct expense).
POSTABLE_TRANSITIONS = [
    {
        "workflow_name": "ap_invoice",
        "from_state": "pending_match",
        "action": "match",
        "event_type": "ap.invoice_received",
        "payload_builder": _ap_invoice_payload,
    },
]


def _get_postable_transition_strategy():
    """Hypothesis strategy: draw one of the registered postable transitions."""
    return st.sampled_from(POSTABLE_TRANSITIONS)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def module_posting_service(session, module_role_resolver, deterministic_clock, register_modules):
    """ModulePostingService with auto_commit=False so fuzzing does not persist between examples."""
    from finance_kernel.services.module_posting_service import ModulePostingService

    return ModulePostingService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
        auto_commit=False,
    )


@pytest.fixture
def effective_date(current_period, deterministic_clock):
    """Effective date for posting (within current open period)."""
    return deterministic_clock.now().date()


# ---------------------------------------------------------------------------
# E2E: draw transition + fuzz amount, post_event never crashes
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestWorkflowPostingE2EFuzzing:
    """Fuzz posting for workflow transitions that post journal entries."""

    @given(
        transition=_get_postable_transition_strategy(),
        amount=st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("999999.99"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
        invoice_number=st.text(
            alphabet=st.characters(whitelist_categories=("Nd", "Ll", "Lu"), max_codepoint=127),
            min_size=1,
            max_size=40,
        ).map(lambda s: f"INV-{s[:32]}" if s else "INV-FUZZ"),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    def test_post_event_for_postable_transition_never_crashes(
        self,
        transition,
        amount,
        invoice_number,
        module_posting_service,
        test_actor_id,
        effective_date,
    ):
        """For each postable transition, post the corresponding event with fuzzed amount; no crash."""
        event_type = transition["event_type"]
        payload_builder = transition["payload_builder"]
        payload = payload_builder(amount, invoice_number=invoice_number)

        result = module_posting_service.post_event(
            event_type=event_type,
            payload=payload,
            effective_date=effective_date,
            actor_id=test_actor_id,
            amount=amount,
        )

        assert result is not None
        assert hasattr(result, "status")
        assert isinstance(result.status, ModulePostingStatus)
        assert hasattr(result, "event_id")
        assert hasattr(result, "is_success")
        assert isinstance(result.is_success, bool)
        # Result must be one of the known statuses (no crash, well-formed)
        known_statuses = (
            ModulePostingStatus.POSTED,
            ModulePostingStatus.ALREADY_POSTED,
            ModulePostingStatus.PERIOD_CLOSED,
            ModulePostingStatus.ADJUSTMENTS_NOT_ALLOWED,
            ModulePostingStatus.INGESTION_FAILED,
            ModulePostingStatus.PROFILE_NOT_FOUND,
            ModulePostingStatus.MEANING_FAILED,
            ModulePostingStatus.GUARD_REJECTED,
            ModulePostingStatus.GUARD_BLOCKED,
            ModulePostingStatus.INTENT_FAILED,
            ModulePostingStatus.POSTING_FAILED,
        )
        assert result.status in known_statuses, f"unexpected status {result.status!r}"

    def test_ap_invoice_posting_idempotent_with_same_event_id(
        self,
        module_posting_service,
        test_actor_id,
        effective_date,
    ):
        """Same event_id + identical payload posted twice yields ALREADY_POSTED (no Hypothesis)."""
        event_type = "ap.invoice_received"
        amount = Decimal("100.00")
        invoice_number = "INV-E2E-IDEM"
        payload = {
            "invoice_number": invoice_number,
            "supplier_code": "SUP-100",
            "gross_amount": "100.00",
            "po_number": None,
        }
        event_id = uuid5(NAMESPACE_DNS, "e2e-fuzz-invoice-idem")

        result1 = module_posting_service.post_event(
            event_type=event_type,
            payload=payload,
            effective_date=effective_date,
            actor_id=test_actor_id,
            amount=amount,
            event_id=event_id,
        )
        assert result1.status == ModulePostingStatus.POSTED
        assert result1.event_id == event_id

        result2 = module_posting_service.post_event(
            event_type=event_type,
            payload=payload,
            effective_date=effective_date,
            actor_id=test_actor_id,
            amount=amount,
            event_id=event_id,
        )
        assert result2.status == ModulePostingStatus.ALREADY_POSTED
        assert result2.event_id == event_id
        assert result2.is_success
