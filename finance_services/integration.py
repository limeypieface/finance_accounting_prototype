"""
Integration entrypoint for external systems (e.g. Sindri/Ironflow).

Provides contract validation and a single post_event entrypoint so that
external callers can post economic events without depending on module
internals. The primary interface is event-driven event codes.

Usage (no Sindri code changes; caller in external repo would do):

    from finance_services.integration import (
        validate_contract,
        post_event_from_external,
        IntegrationPostResult,
    )
    from finance_kernel.services.module_posting_service import ModulePostingService

    # Caller builds ModulePostingService (session, role_resolver, clock, ...)
    # then validates and posts:
    validation = validate_contract("ap.invoice_received", payload, schema_version=1)
    if not validation.is_valid:
        return IntegrationPostResult.validation_failed(validation.errors)
    result = post_event_from_external(
        poster=module_posting_service,
        event_type="ap.invoice_received",
        payload=payload,
        effective_date=effective_date,
        actor_id=actor_id,
        amount=amount,
        currency="USD",
        event_id=event_id,  # idempotency key from external system
        producer="sindri",
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from finance_kernel.domain.event_validator import (
    validate_event,
    validate_payload_against_schema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry
from finance_kernel.domain.dtos import ValidationError, ValidationResult
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
)


# Ensure schema definitions are loaded so EventSchemaRegistry is populated
def _ensure_schemas_loaded() -> None:
    """Import schema definitions so they register with EventSchemaRegistry."""
    try:
        import finance_kernel.domain.schemas.definitions  # noqa: F401
    except ImportError:
        pass


@dataclass(frozen=True)
class IntegrationPostResult:
    """Stable result type for external callers.

    status is one of: "posted", "already_posted", "rejected", "validation_failed",
    or any ModulePostingStatus value. When status == "validation_failed",
    errors is non-empty.
    """

    status: str
    event_id: UUID | None = None
    journal_entry_ids: tuple[UUID, ...] = ()
    message: str | None = None
    profile_name: str | None = None
    errors: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @classmethod
    def from_posting_result(cls, r: ModulePostingResult) -> IntegrationPostResult:
        """Map kernel ModulePostingResult to integration result."""
        return cls(
            status=r.status.value,
            event_id=r.event_id,
            journal_entry_ids=r.journal_entry_ids,
            message=r.message,
            profile_name=r.profile_name,
            errors=(),
        )

    @classmethod
    def validation_failed(
        cls,
        validation_errors: tuple[ValidationError, ...],
        event_id: UUID | None = None,
    ) -> IntegrationPostResult:
        """Build result for contract validation failure."""
        errors = tuple(
            {
                "code": e.code,
                "message": e.message,
                "field": e.field,
                "details": e.details,
            }
            for e in validation_errors
        )
        return cls(
            status="validation_failed",
            event_id=event_id,
            message="Contract validation failed",
            errors=errors,
        )

    @property
    def is_success(self) -> bool:
        """True if event was posted or already posted."""
        return self.status in ("posted", "already_posted")

    @property
    def is_validation_failure(self) -> bool:
        """True if failure was due to contract validation (before ingest)."""
        return self.status == "validation_failed"


def validate_contract(
    event_type: str,
    payload: dict[str, Any],
    schema_version: int = 1,
) -> ValidationResult:
    """Validate event_type and payload against the integration contract.

    Runs:
    1. validate_event() — event_type format, schema_version, currencies in payload.
    2. If EventSchemaRegistry has a schema for (event_type, schema_version),
       validate_payload_against_schema() so bad payloads fail fast with clear errors.

    Returns ValidationResult (is_valid, errors). Does not require a session or config.
    """
    result = validate_event(
        event_type=event_type,
        payload=payload,
        schema_version=schema_version,
    )
    if not result.is_valid:
        return result

    _ensure_schemas_loaded()
    if not EventSchemaRegistry.has_schema(event_type, schema_version):
        return ValidationResult.success()

    schema = EventSchemaRegistry.get(event_type, schema_version)
    schema_errors = validate_payload_against_schema(payload, schema)
    if schema_errors:
        return ValidationResult.failure(*schema_errors)
    return ValidationResult.success()


def post_event_from_external(
    poster: ModulePostingService,
    event_type: str,
    payload: dict[str, Any],
    effective_date: date,
    actor_id: UUID,
    amount: Decimal,
    currency: str = "USD",
    event_id: UUID | None = None,
    producer: str = "sindri",
    schema_version: int = 1,
    description: str | None = None,
    **kwargs: Any,
) -> IntegrationPostResult:
    """Validate the contract, then post the event via the given ModulePostingService.

    Caller is responsible for building and owning the ModulePostingService
    (session, role_resolver, clock, party_service, etc.). This function only
    validates and delegates to poster.post_event().

    If validation fails, returns IntegrationPostResult with status="validation_failed"
    and errors populated; no event is ingested. Otherwise returns the mapped
    ModulePostingResult.
    """
    validation = validate_contract(event_type, payload, schema_version)
    if not validation.is_valid:
        return IntegrationPostResult.validation_failed(
            validation.errors,
            event_id=event_id,
        )

    result = poster.post_event(
        event_type=event_type,
        payload=payload,
        effective_date=effective_date,
        actor_id=actor_id,
        amount=amount,
        currency=currency,
        event_id=event_id,
        producer=producer,
        schema_version=schema_version,
        description=description,
        **kwargs,
    )
    return IntegrationPostResult.from_posting_result(result)
