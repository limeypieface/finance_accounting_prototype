"""Integration tests for config-driven policies and controls.

Verifies that policies (guards, trigger, meaning) and controls (controls.yaml)
from YAML are actually enforced when using an orchestrator built from config.

Full-pipeline: build_posting_orchestrator (get_active_config) + session + posting.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

# Service-tier: build_posting_orchestrator + session fixture; no get_session() or real persistence.
pytestmark = pytest.mark.service

from finance_kernel.services.module_posting_service import (
    ModulePostingStatus,
    ModulePostingService,
)
from finance_services.posting_orchestrator import build_posting_orchestrator


class TestBuildFromConfig:
    """build_posting_orchestrator loads config and wires policy_source + control_rules."""

    def test_build_posting_orchestrator_returns_orchestrator(self, session):
        """build_posting_orchestrator(legal_entity, as_of_date) returns a working orchestrator."""
        orchestrator = build_posting_orchestrator(
            session=session,
            legal_entity="*",
            as_of_date=date(2026, 1, 1),
        )
        assert orchestrator is not None
        assert hasattr(orchestrator, "policy_source")
        assert hasattr(orchestrator, "control_rules")
        assert hasattr(orchestrator, "workflow_executor")
        assert hasattr(orchestrator, "journal_writer")

    def test_module_posting_service_from_built_orchestrator_uses_pack(
        self, session, current_period, test_actor_id
    ):
        """ModulePostingService from build_posting_orchestrator uses pack (policy_source + control_rules)."""
        orchestrator = build_posting_orchestrator(
            session=session,
            legal_entity="*",
            as_of_date=date(2026, 1, 1),
        )
        service = ModulePostingService.from_orchestrator(orchestrator, auto_commit=True)
        # Post a valid AP invoice (positive amount) — should succeed or fail for other reasons, not control
        result = service.post_event(
            event_type="ap.invoice_received",
            payload={
                "invoice_id": str(uuid4()),
                "vendor_id": "00000000-0000-4000-a000-000000000001",
                "gross_amount": "100.00",
                "amount": "100.00",
                "po_number": None,
            },
            effective_date=current_period.start_date,
            actor_id=test_actor_id,
            amount=Decimal("100.00"),
            currency="USD",
        )
        # Either posted or failed for actor/period/ingestion/profile; control (amount > 0) should pass
        assert result.status in (
            ModulePostingStatus.POSTED,
            ModulePostingStatus.ALREADY_POSTED,
            ModulePostingStatus.PROFILE_NOT_FOUND,
            ModulePostingStatus.PERIOD_CLOSED,
            ModulePostingStatus.INGESTION_FAILED,
            ModulePostingStatus.INVALID_ACTOR,
        )


class TestConfigDrivenControl:
    """Controls from controls.yaml are enforced when pack has controls."""

    def test_control_rules_wired_and_evaluate_zero_amount(
        self, session
    ):
        """Control rules from pack evaluate correctly; payload.amount <= 0 triggers reject."""
        from finance_config import get_active_config
        from finance_config.bridges import controls_from_compiled
        from finance_kernel.domain.control import evaluate_controls

        pack = get_active_config(legal_entity="*", as_of_date=date(2026, 1, 1))
        if not pack.controls:
            pytest.skip("This config set has no controls")
        rules = controls_from_compiled(pack.controls)
        # positive_amount_required: expression payload.amount <= 0, applies_to '*'
        result = evaluate_controls(
            payload={"amount": 0, "gross_amount": "0"},
            event_type="ap.invoice_received",
            rules=rules,
        )
        assert not result.passed
        assert result.rejected
        assert result.reason_code
        assert "amount" in (result.message or "").lower() or result.reason_code
