"""
Tests for Phase 4 runtime enforcement points (G9, G10, G11, G12).

Verifies that domain guard primitives are wired into the runtime:

G9  — Subledger control reconciliation at posting time
      JournalWriter checks SubledgerControlContract.enforce_on_post
      when a SubledgerControlRegistry is configured.

G10 — Reference snapshot freshness validation
      JournalWriter validates snapshot integrity via
      ReferenceSnapshotService when configured.

G11 — Link graph cycle detection
      LinkGraphService._detect_cycle() blocks cycles for all
      ACYCLIC_LINK_TYPES. (Already implemented — regression tests.)

G12 — Correction period lock enforcement
      CorrectionEngine._check_can_unwind() rejects corrections
      for artifacts in closed fiscal periods.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, PropertyMock, call, patch
from uuid import UUID, uuid4

import pytest

from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
    IntentLine,
    IntentLineSide,
    LedgerIntent,
)
from finance_kernel.domain.clock import Clock
from finance_kernel.domain.economic_link import (
    ArtifactRef,
    ArtifactType,
    EconomicLink,
    LinkType,
)
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import (
    ClosedPeriodError,
    PeriodNotFoundError,
    StaleReferenceSnapshotError,
    SubledgerReconciliationError,
)

# ============================================================================
# Helpers
# ============================================================================


def _make_intent(
    ledger_ids: tuple[str, ...] = ("GL",),
    snapshot_id: UUID | None = None,
) -> AccountingIntent:
    """Create a minimal AccountingIntent for testing."""
    econ_id = uuid4()
    source_id = uuid4()

    ledger_intents = []
    for ledger_id in ledger_ids:
        ledger_intents.append(
            LedgerIntent(
                ledger_id=ledger_id,
                lines=(
                    IntentLine(
                        account_role="DEBIT_ROLE",
                        side=IntentLineSide.DEBIT,
                        money=Money(Decimal("100.00"), "USD"),
                    ),
                    IntentLine(
                        account_role="CREDIT_ROLE",
                        side=IntentLineSide.CREDIT,
                        money=Money(Decimal("100.00"), "USD"),
                    ),
                ),
            )
        )

    return AccountingIntent(
        econ_event_id=econ_id,
        source_event_id=source_id,
        profile_id="test_profile",
        profile_version=1,
        effective_date=date(2024, 6, 1),
        ledger_intents=tuple(ledger_intents),
        snapshot=AccountingIntentSnapshot(
            coa_version=1,
            dimension_schema_version=1,
            rounding_policy_version=1,
            currency_registry_version=1,
            full_snapshot_id=snapshot_id,
        ),
    )


# ============================================================================
# G9: Subledger control reconciliation
# ============================================================================


class TestSubledgerControlWiring:
    """Tests for subledger control registry wiring in JournalWriter."""

    def test_journal_writer_accepts_subledger_registry(self):
        """JournalWriter constructor accepts subledger_control_registry param."""
        from finance_kernel.services.journal_writer import JournalWriter, RoleResolver

        mock_session = MagicMock()
        resolver = RoleResolver()
        mock_registry = MagicMock()

        writer = JournalWriter(
            session=mock_session,
            role_resolver=resolver,
            subledger_control_registry=mock_registry,
        )
        assert writer._subledger_control_registry is mock_registry

    def test_journal_writer_without_registry_is_backward_compatible(self):
        """JournalWriter works without subledger registry (legacy mode)."""
        from finance_kernel.services.journal_writer import JournalWriter, RoleResolver

        mock_session = MagicMock()
        resolver = RoleResolver()

        writer = JournalWriter(
            session=mock_session,
            role_resolver=resolver,
        )
        assert writer._subledger_control_registry is None

    def test_validate_subledger_controls_called_when_registry_set(self):
        """When registry is set, _validate_subledger_controls is called during write."""
        from finance_kernel.services.journal_writer import JournalWriter, RoleResolver

        mock_registry = MagicMock()
        mock_contract = MagicMock()
        mock_contract.enforce_on_post = True
        mock_contract.subledger_type = MagicMock(value="AP")
        mock_registry.get.return_value = mock_contract

        writer = JournalWriter.__new__(JournalWriter)
        writer._session = MagicMock()
        writer._role_resolver = RoleResolver()
        writer._clock = MagicMock()
        writer._auditor = None
        writer._subledger_control_registry = mock_registry
        writer._snapshot_service = None
        writer._sequence_service = MagicMock()

        # Set up the writer to reach the subledger check
        # This requires a full write() call which needs many mocks.
        # Instead, test the method directly.
        intent = _make_intent(ledger_ids=("AP",))
        writer._validate_subledger_controls(intent)

        mock_registry.get.assert_called_once_with("AP")

    def test_subledger_check_skips_when_no_contract(self):
        """When no contract exists for a ledger, check is skipped."""
        from finance_kernel.services.journal_writer import JournalWriter, RoleResolver

        mock_registry = MagicMock()
        mock_registry.get.return_value = None

        writer = JournalWriter.__new__(JournalWriter)
        writer._subledger_control_registry = mock_registry

        intent = _make_intent(ledger_ids=("GL",))
        # Should not raise
        writer._validate_subledger_controls(intent)

    def test_subledger_check_skips_when_enforce_on_post_false(self):
        """When contract.enforce_on_post is False, check is skipped."""
        from finance_kernel.services.journal_writer import JournalWriter, RoleResolver

        mock_registry = MagicMock()
        mock_contract = MagicMock()
        mock_contract.enforce_on_post = False
        mock_registry.get.return_value = mock_contract

        writer = JournalWriter.__new__(JournalWriter)
        writer._subledger_control_registry = mock_registry

        intent = _make_intent(ledger_ids=("GL",))
        # Should not raise
        writer._validate_subledger_controls(intent)


# ============================================================================
# G10: Reference snapshot freshness validation
# ============================================================================


class TestSnapshotFreshnessWiring:
    """Tests for snapshot freshness validation in JournalWriter."""

    def test_journal_writer_accepts_snapshot_service(self):
        """JournalWriter constructor accepts snapshot_service param."""
        from finance_kernel.services.journal_writer import JournalWriter, RoleResolver

        mock_session = MagicMock()
        resolver = RoleResolver()
        mock_snapshot_svc = MagicMock()

        writer = JournalWriter(
            session=mock_session,
            role_resolver=resolver,
            snapshot_service=mock_snapshot_svc,
        )
        assert writer._snapshot_service is mock_snapshot_svc

    def test_snapshot_freshness_passes_when_valid(self):
        """When snapshot is still valid, write proceeds normally."""
        from finance_kernel.services.journal_writer import JournalWriter

        mock_snapshot_svc = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot_svc.get.return_value = mock_snapshot
        mock_validation = MagicMock()
        mock_validation.is_valid = True
        mock_snapshot_svc.validate_integrity.return_value = mock_validation

        writer = JournalWriter.__new__(JournalWriter)
        writer._snapshot_service = mock_snapshot_svc

        snapshot_id = uuid4()
        intent = _make_intent(snapshot_id=snapshot_id)

        # Should not raise
        writer._validate_snapshot_freshness(intent)

        mock_snapshot_svc.get.assert_called_once_with(snapshot_id)
        mock_snapshot_svc.validate_integrity.assert_called_once_with(mock_snapshot)

    def test_snapshot_freshness_raises_when_stale(self):
        """When snapshot components have changed, raises StaleReferenceSnapshotError."""
        from finance_kernel.services.journal_writer import JournalWriter

        mock_snapshot_svc = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot_svc.get.return_value = mock_snapshot

        mock_error = MagicMock()
        mock_error.component_type = "COA"
        mock_validation = MagicMock()
        mock_validation.is_valid = False
        mock_validation.errors = [mock_error]
        mock_snapshot_svc.validate_integrity.return_value = mock_validation

        writer = JournalWriter.__new__(JournalWriter)
        writer._snapshot_service = mock_snapshot_svc

        intent = _make_intent(snapshot_id=uuid4())

        with pytest.raises(StaleReferenceSnapshotError) as exc_info:
            writer._validate_snapshot_freshness(intent)

        assert "COA" in exc_info.value.stale_components

    def test_snapshot_freshness_skipped_when_no_snapshot_id(self):
        """When intent has no full_snapshot_id, freshness check is skipped."""
        from finance_kernel.services.journal_writer import JournalWriter

        mock_snapshot_svc = MagicMock()

        writer = JournalWriter.__new__(JournalWriter)
        writer._snapshot_service = mock_snapshot_svc

        intent = _make_intent(snapshot_id=None)

        # Should not raise and should not call get()
        writer._validate_snapshot_freshness(intent)
        mock_snapshot_svc.get.assert_not_called()

    def test_snapshot_freshness_skipped_when_no_service(self):
        """When no snapshot_service is configured, freshness check is skipped."""
        from finance_kernel.services.journal_writer import JournalWriter

        writer = JournalWriter.__new__(JournalWriter)
        writer._snapshot_service = None

        intent = _make_intent(snapshot_id=uuid4())

        # Should not raise
        writer._validate_snapshot_freshness(intent)

    def test_snapshot_not_found_is_graceful(self):
        """When snapshot_id can't be retrieved, check is skipped gracefully."""
        from finance_kernel.services.journal_writer import JournalWriter

        mock_snapshot_svc = MagicMock()
        mock_snapshot_svc.get.return_value = None

        writer = JournalWriter.__new__(JournalWriter)
        writer._snapshot_service = mock_snapshot_svc

        intent = _make_intent(snapshot_id=uuid4())

        # Should not raise
        writer._validate_snapshot_freshness(intent)
        mock_snapshot_svc.validate_integrity.assert_not_called()


# ============================================================================
# G11: Link graph cycle detection (regression tests)
# ============================================================================


class TestLinkGraphCycleDetection:
    """Regression tests for L3 acyclic enforcement.

    Cycle detection is already implemented in LinkGraphService._detect_cycle().
    These tests verify the architectural contract.
    """

    def test_acyclic_link_types_defined(self):
        """ACYCLIC_LINK_TYPES covers the expected set of link types."""
        from finance_kernel.services.link_graph_service import LinkGraphService

        expected = frozenset({
            LinkType.FULFILLED_BY,
            LinkType.SOURCED_FROM,
            LinkType.DERIVED_FROM,
            LinkType.CONSUMED_BY,
            LinkType.CORRECTED_BY,
        })
        assert LinkGraphService.ACYCLIC_LINK_TYPES == expected

    def test_non_acyclic_types_not_included(self):
        """Non-directional link types are not in ACYCLIC_LINK_TYPES."""
        from finance_kernel.services.link_graph_service import LinkGraphService

        assert LinkType.MATCHED_WITH not in LinkGraphService.ACYCLIC_LINK_TYPES
        assert LinkType.ADJUSTED_BY not in LinkGraphService.ACYCLIC_LINK_TYPES

    def test_link_cycle_error_exists(self):
        """LinkCycleError exception is defined with correct code."""
        from finance_kernel.exceptions import LinkCycleError

        assert LinkCycleError.code == "LINK_CYCLE"

    def test_detect_cycle_method_exists(self):
        """LinkGraphService has _detect_cycle method."""
        from finance_kernel.services.link_graph_service import LinkGraphService

        assert hasattr(LinkGraphService, "_detect_cycle")


# ============================================================================
# G12: Correction period lock enforcement
# ============================================================================


class TestCorrectionPeriodLockEnforcement:
    """Tests for period lock checking in CorrectionEngine."""

    def test_correction_engine_accepts_period_service(self):
        """CorrectionEngine constructor accepts period_service param."""
        from finance_services.correction_service import CorrectionEngine

        mock_session = MagicMock()
        mock_link_graph = MagicMock()
        mock_period_svc = MagicMock()

        engine = CorrectionEngine(
            session=mock_session,
            link_graph=mock_link_graph,
            period_service=mock_period_svc,
        )
        assert engine._period_service is mock_period_svc

    def test_closed_period_blocks_correction(self):
        """Artifact in closed period is blocked from correction."""
        from finance_services.correction_service import CorrectionEngine

        mock_session = MagicMock()
        mock_link_graph = MagicMock()
        mock_link_graph.find_correction.return_value = None
        mock_link_graph.find_reversal.return_value = None

        mock_period_svc = MagicMock()
        mock_period_svc.validate_effective_date.side_effect = ClosedPeriodError(
            "FY2024-Q1", "2024-03-15"
        )

        engine = CorrectionEngine(
            session=mock_session,
            link_graph=mock_link_graph,
            period_service=mock_period_svc,
        )

        # Mock _get_effective_date to return a date
        engine._get_effective_date = MagicMock(return_value=date(2024, 3, 15))

        artifact_ref = ArtifactRef(
            artifact_type=ArtifactType.JOURNAL_ENTRY,
            artifact_id=str(uuid4()),
        )

        can_unwind, reason = engine._check_can_unwind(artifact_ref)

        assert can_unwind is False
        assert "FY2024-Q1" in reason
        assert "closed" in reason.lower()

    def test_open_period_allows_correction(self):
        """Artifact in open period can be corrected."""
        from finance_services.correction_service import CorrectionEngine

        mock_session = MagicMock()
        mock_link_graph = MagicMock()
        mock_link_graph.find_correction.return_value = None
        mock_link_graph.find_reversal.return_value = None

        mock_period_svc = MagicMock()
        mock_period_svc.validate_effective_date.return_value = None  # No error

        engine = CorrectionEngine(
            session=mock_session,
            link_graph=mock_link_graph,
            period_service=mock_period_svc,
        )

        engine._get_effective_date = MagicMock(return_value=date(2024, 6, 15))

        artifact_ref = ArtifactRef(
            artifact_type=ArtifactType.JOURNAL_ENTRY,
            artifact_id=str(uuid4()),
        )

        can_unwind, reason = engine._check_can_unwind(artifact_ref)

        assert can_unwind is True
        assert reason is None

    def test_no_period_service_skips_check(self):
        """Without period_service, period check is skipped (legacy mode)."""
        from finance_services.correction_service import CorrectionEngine

        mock_session = MagicMock()
        mock_link_graph = MagicMock()
        mock_link_graph.find_correction.return_value = None
        mock_link_graph.find_reversal.return_value = None

        engine = CorrectionEngine(
            session=mock_session,
            link_graph=mock_link_graph,
            period_service=None,
        )

        artifact_ref = ArtifactRef(
            artifact_type=ArtifactType.JOURNAL_ENTRY,
            artifact_id=str(uuid4()),
        )

        can_unwind, reason = engine._check_can_unwind(artifact_ref)

        assert can_unwind is True
        assert reason is None

    def test_period_not_found_allows_correction(self):
        """When no period exists for the date, correction is allowed."""
        from finance_services.correction_service import CorrectionEngine

        mock_session = MagicMock()
        mock_link_graph = MagicMock()
        mock_link_graph.find_correction.return_value = None
        mock_link_graph.find_reversal.return_value = None

        mock_period_svc = MagicMock()
        mock_period_svc.validate_effective_date.side_effect = PeriodNotFoundError(
            "2024-06-15"
        )

        engine = CorrectionEngine(
            session=mock_session,
            link_graph=mock_link_graph,
            period_service=mock_period_svc,
        )

        engine._get_effective_date = MagicMock(return_value=date(2024, 6, 15))

        artifact_ref = ArtifactRef(
            artifact_type=ArtifactType.JOURNAL_ENTRY,
            artifact_id=str(uuid4()),
        )

        can_unwind, reason = engine._check_can_unwind(artifact_ref)

        assert can_unwind is True
        assert reason is None

    def test_already_corrected_checked_before_period(self):
        """Already-corrected check runs before period check."""
        from finance_services.correction_service import CorrectionEngine

        mock_session = MagicMock()
        mock_link_graph = MagicMock()

        # Already corrected
        mock_correction = MagicMock()
        mock_correction.child_ref = "CORRECTION_DOC"
        mock_link_graph.find_correction.return_value = mock_correction

        mock_period_svc = MagicMock()

        engine = CorrectionEngine(
            session=mock_session,
            link_graph=mock_link_graph,
            period_service=mock_period_svc,
        )

        artifact_ref = ArtifactRef(
            artifact_type=ArtifactType.JOURNAL_ENTRY,
            artifact_id=str(uuid4()),
        )

        can_unwind, reason = engine._check_can_unwind(artifact_ref)

        assert can_unwind is False
        assert "Already corrected" in reason
        # Period check should NOT have been called
        mock_period_svc.validate_effective_date.assert_not_called()

    def test_no_effective_date_skips_period_check(self):
        """When effective date cannot be determined, period check is skipped."""
        from finance_services.correction_service import CorrectionEngine

        mock_session = MagicMock()
        mock_link_graph = MagicMock()
        mock_link_graph.find_correction.return_value = None
        mock_link_graph.find_reversal.return_value = None

        mock_period_svc = MagicMock()

        engine = CorrectionEngine(
            session=mock_session,
            link_graph=mock_link_graph,
            period_service=mock_period_svc,
        )

        engine._get_effective_date = MagicMock(return_value=None)

        artifact_ref = ArtifactRef(
            artifact_type=ArtifactType.PURCHASE_ORDER,
            artifact_id=str(uuid4()),
        )

        can_unwind, reason = engine._check_can_unwind(artifact_ref)

        assert can_unwind is True
        assert reason is None
        mock_period_svc.validate_effective_date.assert_not_called()


# ============================================================================
# Exception type tests
# ============================================================================


class TestEnforcementExceptions:
    """Verify enforcement exceptions exist and have correct codes."""

    def test_stale_snapshot_error_code(self):
        assert StaleReferenceSnapshotError.code == "STALE_REFERENCE_SNAPSHOT"

    def test_stale_snapshot_error_fields(self):
        err = StaleReferenceSnapshotError(
            entry_id="test-entry",
            stale_components=["COA", "FX_RATES"],
        )
        assert err.entry_id == "test-entry"
        assert err.stale_components == ["COA", "FX_RATES"]
        assert "COA" in str(err)
        assert "FX_RATES" in str(err)

    def test_subledger_reconciliation_error_code(self):
        assert SubledgerReconciliationError.code == "SUBLEDGER_RECONCILIATION_FAILED"

    def test_subledger_reconciliation_error_fields(self):
        err = SubledgerReconciliationError(
            ledger_id="AP",
            violations=["Balance drift exceeds tolerance"],
        )
        assert err.ledger_id == "AP"
        assert err.violations == ["Balance drift exceeds tolerance"]
        assert "AP" in str(err)
