"""Tests for finance_services.integration (external system contract validation and post)."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.dtos import ValidationError, ValidationResult
from finance_services.integration import (
    IntegrationPostResult,
    post_event_from_external,
    validate_contract,
)


class TestValidateContract:
    """Contract validation without DB or session."""

    def test_invalid_event_type_empty(self) -> None:
        r = validate_contract("", {"a": 1}, 1)
        assert not r.is_valid
        assert any(e.code == "INVALID_EVENT_TYPE" for e in r.errors)

    def test_invalid_event_type_not_namespaced(self) -> None:
        r = validate_contract("invoice", {"a": 1}, 1)
        assert not r.is_valid
        assert any(e.code == "INVALID_EVENT_TYPE" for e in r.errors)

    def test_ap_invoice_received_empty_payload_fails_schema(self) -> None:
        r = validate_contract("ap.invoice_received", {}, 1)
        assert not r.is_valid
        assert any(e.code == "MISSING_REQUIRED_FIELD" for e in r.errors)

    def test_ap_invoice_received_valid_minimal_passes_format(self) -> None:
        payload = {
            "invoice_id": str(uuid4()),
            "invoice_number": "INV-001",
            "supplier_party_code": "SUP01",
            "invoice_date": "2026-06-01",
            "due_date": "2026-07-01",
            "gross_amount": "1000.00",
            "net_amount": "1000.00",
            "currency": "USD",
            "org_unit": "default",
        }
        r = validate_contract("ap.invoice_received", payload, 1)
        assert r.is_valid, [e.code for e in r.errors]


class TestIntegrationPostResult:
    def test_validation_failed_has_errors(self) -> None:
        err = IntegrationPostResult.validation_failed(
            (ValidationError(code="MISSING_REQUIRED_FIELD", message="x", field="y"),)
        )
        assert err.is_validation_failure
        assert err.status == "validation_failed"
        assert len(err.errors) == 1
        assert err.errors[0]["code"] == "MISSING_REQUIRED_FIELD"

    def test_from_posting_result_maps_status(self) -> None:
        from finance_kernel.services.module_posting_service import (
            ModulePostingResult,
            ModulePostingStatus,
        )
        mp = ModulePostingResult(status=ModulePostingStatus.POSTED, event_id=uuid4())
        r = IntegrationPostResult.from_posting_result(mp)
        assert r.status == "posted"
        assert r.is_success


class TestPostEventFromExternal:
    """post_event_from_external returns validation_failed when contract invalid."""

    def test_invalid_payload_returns_validation_failed_without_calling_poster(
        self,
    ) -> None:
        from unittest.mock import Mock
        poster = Mock()
        result = post_event_from_external(
            poster=poster,
            event_type="ap.invoice_received",
            payload={},
            effective_date=date(2026, 6, 15),
            actor_id=uuid4(),
            amount=Decimal("100"),
            currency="USD",
        )
        assert result.is_validation_failure
        assert not result.is_success
        poster.post_event.assert_not_called()
