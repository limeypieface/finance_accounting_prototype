"""
Tests for Phase 3 mandatory guards (G8, G14, G15).

Verifies the three guard mechanisms that prevent circumvention of
the kernel's trust boundary:

G8  — PolicyAuthority in MeaningBuilder
      MeaningBuilder validates economic authority when policy_authority
      is provided and module_type + target_ledgers are specified.

G14 — Actor authorization at posting boundary
      ModulePostingService validates actor_id against PartyService
      before allowing any posting.

G15 — CompilationReceipt for PolicySelector
      PolicySelector.register() validates that the policy was compiled
      when a CompilationReceipt is provided. Mismatched receipts are rejected.
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, PropertyMock, patch
from uuid import uuid4

from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    GuardCondition,
    GuardType,
    LedgerEffect,
    PolicyMeaning,
    PolicyPrecedence,
    PolicyTrigger,
)
from finance_kernel.domain.meaning_builder import (
    MeaningBuilder,
    MeaningBuilderResult,
)
from finance_kernel.domain.policy_selector import (
    CompilationReceipt,
    PolicySelector,
    UncompiledPolicyError,
)


# ============================================================================
# Helpers
# ============================================================================


def _make_policy(
    name: str = "test_policy",
    version: int = 1,
    event_type: str = "test.event",
    economic_type: str = "TestEconomicType",
    effective_from: date | None = None,
    guards: tuple[GuardCondition, ...] = (),
) -> AccountingPolicy:
    """Create a minimal AccountingPolicy for testing."""
    return AccountingPolicy(
        name=name,
        version=version,
        trigger=PolicyTrigger(event_type=event_type),
        meaning=PolicyMeaning(economic_type=economic_type),
        ledger_effects=(
            LedgerEffect(ledger="GL", debit_role="DEBIT", credit_role="CREDIT"),
        ),
        effective_from=effective_from or date(2024, 1, 1),
        guards=guards,
    )


def _make_receipt(
    policy_name: str = "test_policy",
    policy_version: int = 1,
    compiled_hash: str = "sha256:abc123",
    config_fingerprint: str = "fp:xyz789",
) -> CompilationReceipt:
    """Create a CompilationReceipt for testing."""
    return CompilationReceipt(
        policy_name=policy_name,
        policy_version=policy_version,
        compiled_hash=compiled_hash,
        config_fingerprint=config_fingerprint,
    )


# ============================================================================
# G15: CompilationReceipt for PolicySelector
# ============================================================================


class TestCompilationReceipt:
    """Tests for CompilationReceipt and PolicySelector receipt validation."""

    def setup_method(self):
        """Save and clear PolicySelector registry before each test."""
        self._saved_profiles = {k: dict(v) for k, v in PolicySelector._profiles.items()}
        self._saved_by_event = {k: list(v) for k, v in PolicySelector._by_event_type.items()}
        PolicySelector.clear()

    def teardown_method(self):
        """Restore PolicySelector registry after each test."""
        PolicySelector.clear()
        PolicySelector._profiles.update(self._saved_profiles)
        PolicySelector._by_event_type.update(self._saved_by_event)

    def test_receipt_matches_correct_policy(self):
        """Receipt matches when name and version match."""
        policy = _make_policy(name="inventory.receipt", version=2)
        receipt = _make_receipt(
            policy_name="inventory.receipt", policy_version=2,
        )
        assert receipt.matches(policy) is True

    def test_receipt_rejects_name_mismatch(self):
        """Receipt does not match when policy name differs."""
        policy = _make_policy(name="inventory.receipt", version=1)
        receipt = _make_receipt(
            policy_name="ap.invoice", policy_version=1,
        )
        assert receipt.matches(policy) is False

    def test_receipt_rejects_version_mismatch(self):
        """Receipt does not match when policy version differs."""
        policy = _make_policy(name="inventory.receipt", version=2)
        receipt = _make_receipt(
            policy_name="inventory.receipt", policy_version=1,
        )
        assert receipt.matches(policy) is False

    def test_receipt_is_frozen(self):
        """CompilationReceipt is immutable (frozen dataclass)."""
        receipt = _make_receipt()
        with pytest.raises(AttributeError):
            receipt.policy_name = "hacked"  # type: ignore[misc]

    def test_register_with_valid_receipt_succeeds(self):
        """Registration with a matching receipt succeeds."""
        policy = _make_policy(name="valid_policy", version=1)
        receipt = _make_receipt(
            policy_name="valid_policy", policy_version=1,
        )
        PolicySelector.register(policy, compilation_receipt=receipt)
        assert PolicySelector.has_profile("valid_policy", 1)

    def test_register_with_mismatched_receipt_raises(self):
        """Registration with a non-matching receipt raises UncompiledPolicyError."""
        policy = _make_policy(name="real_policy", version=1)
        receipt = _make_receipt(
            policy_name="different_policy", policy_version=1,
        )
        with pytest.raises(UncompiledPolicyError) as exc_info:
            PolicySelector.register(policy, compilation_receipt=receipt)

        assert exc_info.value.policy_name == "real_policy"
        assert exc_info.value.policy_version == 1
        assert "CompilationReceipt" in str(exc_info.value)

    def test_register_without_receipt_succeeds(self):
        """Registration without receipt (legacy mode) still works."""
        policy = _make_policy(name="legacy_policy", version=1)
        PolicySelector.register(policy)
        assert PolicySelector.has_profile("legacy_policy", 1)

    def test_register_with_wrong_version_receipt_raises(self):
        """Receipt with wrong version is rejected even if name matches."""
        policy = _make_policy(name="my_policy", version=3)
        receipt = _make_receipt(
            policy_name="my_policy", policy_version=2,
        )
        with pytest.raises(UncompiledPolicyError):
            PolicySelector.register(policy, compilation_receipt=receipt)

        # Policy should NOT be in the registry
        assert not PolicySelector.has_profile("my_policy", 3)

    def test_uncompiled_policy_error_has_code(self):
        """UncompiledPolicyError carries a machine-readable code."""
        assert UncompiledPolicyError.code == "UNCOMPILED_POLICY"


# ============================================================================
# G8: PolicyAuthority in MeaningBuilder
# ============================================================================


class TestMeaningBuilderPolicyAuthority:
    """Tests for PolicyAuthority validation in MeaningBuilder."""

    def test_build_without_authority_succeeds(self):
        """Building meaning without PolicyAuthority works (backward compat)."""
        builder = MeaningBuilder()
        policy = _make_policy(event_type="test.event")
        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"quantity": 10},
            effective_date=date(2024, 6, 1),
            profile=policy,
        )
        assert result.success is True
        assert result.economic_event is not None

    def test_build_with_authority_but_no_module_type_skips_validation(self):
        """When PolicyAuthority is set but module_type is None, validation is skipped."""
        mock_authority = MagicMock()
        builder = MeaningBuilder(policy_authority=mock_authority)
        policy = _make_policy(event_type="test.event")

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"quantity": 10},
            effective_date=date(2024, 6, 1),
            profile=policy,
            # module_type=None, target_ledgers=None  (omitted)
        )
        assert result.success is True
        # Validation should NOT have been called
        mock_authority.validate_economic_type_posting.assert_not_called()

    def test_build_with_authority_and_module_type_validates(self):
        """When PolicyAuthority, module_type, and target_ledgers are all provided,
        validation is performed."""
        mock_authority = MagicMock()
        mock_authority.validate_economic_type_posting.return_value = []  # No violations
        builder = MeaningBuilder(policy_authority=mock_authority)
        policy = _make_policy(event_type="test.event")

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"quantity": 10},
            effective_date=date(2024, 6, 1),
            profile=policy,
            module_type="INVENTORY",
            target_ledgers=frozenset({"GL", "inventory_subledger"}),
        )
        assert result.success is True
        mock_authority.validate_economic_type_posting.assert_called_once_with(
            economic_type="TestEconomicType",
            target_ledgers=frozenset({"GL", "inventory_subledger"}),
        )

    def test_build_with_authority_violation_fails(self):
        """When PolicyAuthority rejects the economic type, build fails."""
        mock_violation = MagicMock()
        mock_violation.message = "Module not authorized for REVENUE ledger"
        mock_violation.policy_type = "LEDGER_AUTHORITY"

        mock_authority = MagicMock()
        mock_authority.validate_economic_type_posting.return_value = [mock_violation]
        builder = MeaningBuilder(policy_authority=mock_authority)
        policy = _make_policy(event_type="test.event")

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"quantity": 10},
            effective_date=date(2024, 6, 1),
            profile=policy,
            module_type="INVENTORY",
            target_ledgers=frozenset({"GL", "REVENUE"}),
        )
        assert result.success is False
        assert len(result.validation_errors) == 1
        assert result.validation_errors[0].code == "POLICY_VIOLATION"

    def test_build_with_authority_multiple_violations(self):
        """Multiple policy violations are all collected."""
        v1 = MagicMock(message="Violation 1", policy_type="TYPE_A")
        v2 = MagicMock(message="Violation 2", policy_type="TYPE_B")

        mock_authority = MagicMock()
        mock_authority.validate_economic_type_posting.return_value = [v1, v2]
        builder = MeaningBuilder(policy_authority=mock_authority)
        policy = _make_policy(event_type="test.event")

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"quantity": 10},
            effective_date=date(2024, 6, 1),
            profile=policy,
            module_type="INVENTORY",
            target_ledgers=frozenset({"GL"}),
        )
        assert result.success is False
        assert len(result.validation_errors) == 2

    def test_meaning_builder_constructor_accepts_policy_authority(self):
        """Constructor parameter is named policy_authority (not policy_registry)."""
        mock_authority = MagicMock()
        builder = MeaningBuilder(policy_authority=mock_authority)
        # Internal storage still works
        assert builder._policy_registry is mock_authority


# ============================================================================
# G14: Actor authorization at posting boundary
# ============================================================================


class TestActorAuthorizationAtPostingBoundary:
    """Tests for actor validation in ModulePostingService.

    These tests verify that _do_post_event checks actor_id against
    PartyService before proceeding with posting.
    """

    def test_invalid_actor_returns_invalid_actor_status(self):
        """When PartyService raises PartyNotFoundError, status is INVALID_ACTOR."""
        from finance_kernel.exceptions import PartyNotFoundError
        from finance_kernel.services.module_posting_service import (
            ModulePostingService,
            ModulePostingStatus,
        )

        # Build a ModulePostingService with mocked dependencies
        service = ModulePostingService.__new__(ModulePostingService)
        service._session = MagicMock()
        service._clock = MagicMock()
        service._auto_commit = False

        # Mock party service that raises PartyNotFoundError
        mock_party_svc = MagicMock()
        mock_party_svc.get_by_id.side_effect = PartyNotFoundError("unknown_actor")
        service._party_service_ref = mock_party_svc

        # Mock remaining services to prevent actual execution
        service._period_service = MagicMock()
        service._ingestor = MagicMock()
        service._meaning_builder = MagicMock()
        service._coordinator = MagicMock()

        actor_id = uuid4()
        result = service._do_post_event(
            event_id=uuid4(),
            event_type="test.event",
            payload={},
            effective_date=date(2024, 6, 1),
            actor_id=actor_id,
            amount=Decimal("100"),
            currency="USD",
            producer="test",
            occurred_at=MagicMock(),
            schema_version=1,
            is_adjustment=False,
            description=None,
            coa_version=1,
            dimension_schema_version=1,
        )

        assert result.status == ModulePostingStatus.INVALID_ACTOR
        assert str(actor_id) in result.message

    def test_frozen_actor_returns_actor_frozen_status(self):
        """When actor exists but can_transact is False, status is ACTOR_FROZEN."""
        from finance_kernel.services.module_posting_service import (
            ModulePostingService,
            ModulePostingStatus,
        )

        service = ModulePostingService.__new__(ModulePostingService)
        service._session = MagicMock()
        service._clock = MagicMock()
        service._auto_commit = False

        # Mock party service returning a frozen party
        mock_party = MagicMock()
        mock_party.can_transact = False
        mock_party_svc = MagicMock()
        mock_party_svc.get_by_id.return_value = mock_party
        service._party_service_ref = mock_party_svc

        service._period_service = MagicMock()
        service._ingestor = MagicMock()
        service._meaning_builder = MagicMock()
        service._coordinator = MagicMock()

        actor_id = uuid4()
        result = service._do_post_event(
            event_id=uuid4(),
            event_type="test.event",
            payload={},
            effective_date=date(2024, 6, 1),
            actor_id=actor_id,
            amount=Decimal("100"),
            currency="USD",
            producer="test",
            occurred_at=MagicMock(),
            schema_version=1,
            is_adjustment=False,
            description=None,
            coa_version=1,
            dimension_schema_version=1,
        )

        assert result.status == ModulePostingStatus.ACTOR_FROZEN
        assert str(actor_id) in result.message

    def test_valid_actor_proceeds_past_guard(self):
        """When actor is valid and can_transact, posting continues past step 0."""
        from finance_kernel.exceptions import AdjustmentsNotAllowedError
        from finance_kernel.services.module_posting_service import (
            ModulePostingService,
            ModulePostingStatus,
        )

        service = ModulePostingService.__new__(ModulePostingService)
        service._session = MagicMock()
        service._clock = MagicMock()
        service._auto_commit = False

        # Mock party service returning a valid, active party
        mock_party = MagicMock()
        mock_party.can_transact = True
        mock_party_svc = MagicMock()
        mock_party_svc.get_by_id.return_value = mock_party
        service._party_service_ref = mock_party_svc

        # Period service will block — proving we got past the actor check
        service._period_service = MagicMock()
        service._period_service.validate_adjustment_allowed.side_effect = Exception(
            "Period closed"
        )

        service._ingestor = MagicMock()
        service._meaning_builder = MagicMock()
        service._coordinator = MagicMock()

        result = service._do_post_event(
            event_id=uuid4(),
            event_type="test.event",
            payload={},
            effective_date=date(2024, 6, 1),
            actor_id=uuid4(),
            amount=Decimal("100"),
            currency="USD",
            producer="test",
            occurred_at=MagicMock(),
            schema_version=1,
            is_adjustment=False,
            description=None,
            coa_version=1,
            dimension_schema_version=1,
        )

        # Should reach step 1 (period validation) and get PERIOD_CLOSED
        assert result.status == ModulePostingStatus.PERIOD_CLOSED

    def test_no_party_service_skips_actor_validation(self):
        """When _party_service_ref is not set (legacy path), actor check is skipped."""
        from finance_kernel.services.module_posting_service import (
            ModulePostingService,
            ModulePostingStatus,
        )

        service = ModulePostingService.__new__(ModulePostingService)
        service._session = MagicMock()
        service._clock = MagicMock()
        service._auto_commit = False
        # No _party_service_ref attribute set

        # Period service will block to prove we got past step 0
        service._period_service = MagicMock()
        service._period_service.validate_adjustment_allowed.side_effect = Exception(
            "Period closed"
        )

        service._ingestor = MagicMock()
        service._meaning_builder = MagicMock()
        service._coordinator = MagicMock()

        result = service._do_post_event(
            event_id=uuid4(),
            event_type="test.event",
            payload={},
            effective_date=date(2024, 6, 1),
            actor_id=uuid4(),
            amount=Decimal("100"),
            currency="USD",
            producer="test",
            occurred_at=MagicMock(),
            schema_version=1,
            is_adjustment=False,
            description=None,
            coa_version=1,
            dimension_schema_version=1,
        )

        # Should skip actor check and reach period validation
        assert result.status == ModulePostingStatus.PERIOD_CLOSED

    def test_actor_check_runs_before_period_validation(self):
        """Actor validation (step 0) runs before period validation (step 1)."""
        from finance_kernel.exceptions import PartyNotFoundError
        from finance_kernel.services.module_posting_service import (
            ModulePostingService,
            ModulePostingStatus,
        )

        service = ModulePostingService.__new__(ModulePostingService)
        service._session = MagicMock()
        service._clock = MagicMock()
        service._auto_commit = False

        # Both actor check AND period check would fail
        mock_party_svc = MagicMock()
        mock_party_svc.get_by_id.side_effect = PartyNotFoundError("bad_actor")
        service._party_service_ref = mock_party_svc

        service._period_service = MagicMock()
        service._period_service.validate_adjustment_allowed.side_effect = Exception(
            "Period closed"
        )

        service._ingestor = MagicMock()
        service._meaning_builder = MagicMock()
        service._coordinator = MagicMock()

        result = service._do_post_event(
            event_id=uuid4(),
            event_type="test.event",
            payload={},
            effective_date=date(2024, 6, 1),
            actor_id=uuid4(),
            amount=Decimal("100"),
            currency="USD",
            producer="test",
            occurred_at=MagicMock(),
            schema_version=1,
            is_adjustment=False,
            description=None,
            coa_version=1,
            dimension_schema_version=1,
        )

        # Actor check should fire first, period check never reached
        assert result.status == ModulePostingStatus.INVALID_ACTOR
        service._period_service.validate_adjustment_allowed.assert_not_called()


# ============================================================================
# G8 + Guard integration: MeaningBuilder guard evaluation
# ============================================================================


class TestMeaningBuilderGuards:
    """Tests for guard evaluation in MeaningBuilder (part of G8 enforcement)."""

    def test_reject_guard_triggers_on_matching_payload(self):
        """REJECT guard fires when expression matches payload."""
        policy = _make_policy(
            event_type="test.event",
            guards=(
                GuardCondition(
                    guard_type=GuardType.REJECT,
                    expression="payload.quantity <= 0",
                    reason_code="NEGATIVE_QUANTITY",
                    message="Quantity must be positive",
                ),
            ),
        )
        builder = MeaningBuilder()
        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"quantity": -5},
            effective_date=date(2024, 6, 1),
            profile=policy,
        )
        assert result.success is False
        assert result.guard_result is not None
        assert result.guard_result.rejected is True
        assert result.guard_result.reason_code == "NEGATIVE_QUANTITY"

    def test_block_guard_triggers_on_matching_payload(self):
        """BLOCK guard fires when expression matches payload."""
        policy = _make_policy(
            event_type="test.event",
            guards=(
                GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="payload.approved == false",
                    reason_code="NOT_APPROVED",
                    message="Event not approved",
                ),
            ),
        )
        builder = MeaningBuilder()
        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"approved": False, "quantity": 10},
            effective_date=date(2024, 6, 1),
            profile=policy,
        )
        assert result.success is False
        assert result.guard_result is not None
        assert result.guard_result.blocked is True
        assert result.guard_result.reason_code == "NOT_APPROVED"

    def test_guard_passes_when_condition_not_met(self):
        """Guards don't fire when conditions are not met."""
        policy = _make_policy(
            event_type="test.event",
            guards=(
                GuardCondition(
                    guard_type=GuardType.REJECT,
                    expression="payload.quantity <= 0",
                    reason_code="NEGATIVE_QUANTITY",
                ),
            ),
        )
        builder = MeaningBuilder()
        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"quantity": 50},
            effective_date=date(2024, 6, 1),
            profile=policy,
        )
        assert result.success is True
        assert result.guard_result.passed is True

    def test_reject_guard_checked_before_block_guard(self):
        """Guards are evaluated in order: first REJECT, then BLOCK (if listed first)."""
        policy = _make_policy(
            event_type="test.event",
            guards=(
                GuardCondition(
                    guard_type=GuardType.REJECT,
                    expression="payload.quantity <= 0",
                    reason_code="REJECT_NEG",
                ),
                GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="payload.quantity <= 0",
                    reason_code="BLOCK_NEG",
                ),
            ),
        )
        builder = MeaningBuilder()
        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"quantity": -1},
            effective_date=date(2024, 6, 1),
            profile=policy,
        )
        # REJECT should fire first (it's listed first)
        assert result.guard_result.rejected is True
        assert result.guard_result.reason_code == "REJECT_NEG"


# ============================================================================
# Integration: ModulePostingStatus enum completeness
# ============================================================================


class TestModulePostingStatusEnum:
    """Verify that all guard-related statuses exist in the enum."""

    def test_guard_statuses_exist(self):
        from finance_kernel.services.module_posting_service import ModulePostingStatus

        # G14 statuses
        assert ModulePostingStatus.INVALID_ACTOR.value == "invalid_actor"
        assert ModulePostingStatus.ACTOR_FROZEN.value == "actor_frozen"

        # G8 guard statuses (already existed)
        assert ModulePostingStatus.GUARD_REJECTED.value == "guard_rejected"
        assert ModulePostingStatus.GUARD_BLOCKED.value == "guard_blocked"

    def test_guard_statuses_are_not_success(self):
        from finance_kernel.services.module_posting_service import (
            ModulePostingResult,
            ModulePostingStatus,
        )

        for status in [
            ModulePostingStatus.INVALID_ACTOR,
            ModulePostingStatus.ACTOR_FROZEN,
            ModulePostingStatus.GUARD_REJECTED,
            ModulePostingStatus.GUARD_BLOCKED,
        ]:
            result = ModulePostingResult(
                status=status,
                event_id=uuid4(),
            )
            assert result.is_success is False, f"{status} should not be a success status"
