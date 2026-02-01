"""
Tests for Phase 2D Ledger Engine components.

Tests cover:
- AccountingIntent DTO
- JournalWriter with multi-ledger atomicity (P11)
- InterpretationCoordinator with L5 atomicity
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
    IntentLine,
    IntentLineSide,
    LedgerIntent,
    ResolvedIntentLine,
)
from finance_kernel.domain.values import Money

# ============================================================================
# AccountingIntent Tests
# ============================================================================


class TestIntentLine:
    """Tests for IntentLine."""

    def test_create_debit_line(self):
        """Create a debit line."""
        line = IntentLine.debit(
            role="InventoryAsset",
            amount="100.00",
            currency="USD",
            dimensions={"location": "WH-A"},
        )

        assert line.account_role == "InventoryAsset"
        assert line.side == IntentLineSide.DEBIT
        assert line.amount == Decimal("100.00")
        assert line.currency == "USD"
        assert line.dimensions == {"location": "WH-A"}

    def test_create_credit_line(self):
        """Create a credit line."""
        line = IntentLine.credit(
            role="GRNI",
            amount=Decimal("100.00"),
            currency="USD",
        )

        assert line.account_role == "GRNI"
        assert line.side == IntentLineSide.CREDIT
        assert line.amount == Decimal("100.00")

    def test_negative_amount_rejected(self):
        """Negative amounts are rejected."""
        with pytest.raises(ValueError, match="non-negative"):
            IntentLine.debit("Asset", "-10.00", "USD")

    def test_invalid_side_rejected(self):
        """Invalid side is rejected."""
        with pytest.raises(ValueError, match="Invalid side"):
            IntentLine(
                account_role="Asset",
                side="invalid",
                money=Money.of(Decimal("100"), "USD"),
            )


class TestLedgerIntent:
    """Tests for LedgerIntent."""

    def test_create_ledger_intent(self):
        """Create a ledger intent with lines."""
        intent = LedgerIntent(
            ledger_id="GL",
            lines=(
                IntentLine.debit("Asset", "100.00", "USD"),
                IntentLine.credit("Liability", "100.00", "USD"),
            ),
        )

        assert intent.ledger_id == "GL"
        assert len(intent.lines) == 2
        assert intent.is_balanced()

    def test_empty_lines_rejected(self):
        """Empty lines tuple is rejected."""
        with pytest.raises(ValueError, match="at least one line"):
            LedgerIntent(ledger_id="GL", lines=())

    def test_balanced_single_currency(self):
        """Check balance for single currency."""
        intent = LedgerIntent(
            ledger_id="GL",
            lines=(
                IntentLine.debit("Asset", "100.00", "USD"),
                IntentLine.credit("Liability", "100.00", "USD"),
            ),
        )

        assert intent.is_balanced()
        assert intent.is_balanced("USD")
        assert intent.total_debits("USD") == Decimal("100.00")
        assert intent.total_credits("USD") == Decimal("100.00")

    def test_unbalanced_detected(self):
        """Unbalanced intent is detected."""
        intent = LedgerIntent(
            ledger_id="GL",
            lines=(
                IntentLine.debit("Asset", "100.00", "USD"),
                IntentLine.credit("Liability", "50.00", "USD"),
            ),
        )

        assert not intent.is_balanced()
        assert not intent.is_balanced("USD")

    def test_multi_currency_balance(self):
        """Multi-currency balance check."""
        intent = LedgerIntent(
            ledger_id="GL",
            lines=(
                IntentLine.debit("Asset", "100.00", "USD"),
                IntentLine.credit("Liability", "100.00", "USD"),
                IntentLine.debit("Asset", "85.00", "EUR"),
                IntentLine.credit("Liability", "85.00", "EUR"),
            ),
        )

        assert intent.is_balanced()
        assert intent.is_balanced("USD")
        assert intent.is_balanced("EUR")
        assert intent.currencies == frozenset({"USD", "EUR"})


class TestAccountingIntent:
    """Tests for AccountingIntent."""

    def test_create_accounting_intent(self):
        """Create a complete accounting intent."""
        intent = AccountingIntent(
            econ_event_id=uuid4(),
            source_event_id=uuid4(),
            profile_id="TestProfile",
            profile_version=1,
            effective_date=date(2024, 6, 15),
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("Asset", "100.00", "USD"),
                        IntentLine.credit("Liability", "100.00", "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(
                coa_version=1,
                dimension_schema_version=1,
            ),
            description="Test posting",
        )

        assert intent.profile_id == "TestProfile"
        assert intent.ledger_ids == frozenset({"GL"})
        assert intent.all_roles == frozenset({"Asset", "Liability"})
        assert intent.all_balanced()

    def test_empty_ledger_intents_rejected(self):
        """Empty ledger intents rejected."""
        with pytest.raises(ValueError, match="at least one ledger intent"):
            AccountingIntent(
                econ_event_id=uuid4(),
                source_event_id=uuid4(),
                profile_id="Test",
                profile_version=1,
                effective_date=date(2024, 1, 1),
                ledger_intents=(),
                snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
            )

    def test_multi_ledger_intent(self):
        """Intent spanning multiple ledgers."""
        intent = AccountingIntent(
            econ_event_id=uuid4(),
            source_event_id=uuid4(),
            profile_id="TestProfile",
            profile_version=1,
            effective_date=date(2024, 6, 15),
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("InventoryAsset", "100.00", "USD"),
                        IntentLine.credit("GRNI", "100.00", "USD"),
                    ),
                ),
                LedgerIntent(
                    ledger_id="AP",
                    lines=(
                        IntentLine.debit("APControl", "100.00", "USD"),
                        IntentLine.credit("APClearing", "100.00", "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(
                coa_version=1,
                dimension_schema_version=1,
            ),
        )

        assert intent.ledger_ids == frozenset({"GL", "AP"})
        assert intent.all_roles == frozenset(
            {"InventoryAsset", "GRNI", "APControl", "APClearing"}
        )
        assert intent.all_balanced()

    def test_idempotency_key_generation(self):
        """Idempotency key is unique per ledger."""
        econ_event_id = uuid4()
        intent = AccountingIntent(
            econ_event_id=econ_event_id,
            source_event_id=uuid4(),
            profile_id="Test",
            profile_version=2,
            effective_date=date(2024, 1, 1),
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("A", "100", "USD"),
                        IntentLine.credit("B", "100", "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )

        key = intent.idempotency_key("GL")
        assert key == f"{econ_event_id}:GL:2"

    def test_get_ledger_intent(self):
        """Get intent for specific ledger."""
        intent = AccountingIntent(
            econ_event_id=uuid4(),
            source_event_id=uuid4(),
            profile_id="Test",
            profile_version=1,
            effective_date=date(2024, 1, 1),
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("A", "100", "USD"),
                        IntentLine.credit("B", "100", "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )

        gl_intent = intent.get_ledger_intent("GL")
        assert gl_intent is not None
        assert gl_intent.ledger_id == "GL"

        missing = intent.get_ledger_intent("AP")
        assert missing is None


class TestResolvedIntentLine:
    """Tests for ResolvedIntentLine."""

    def test_create_resolved_line(self):
        """Create a resolved intent line."""
        account_id = uuid4()
        line = ResolvedIntentLine(
            account_id=account_id,
            account_code="1100",
            account_role="InventoryAsset",
            side=IntentLineSide.DEBIT,
            money=Money.of(Decimal("100.00"), "USD"),
            dimensions={"location": "WH-A"},
            line_seq=0,
        )

        assert line.account_id == account_id
        assert line.account_code == "1100"
        assert line.account_role == "InventoryAsset"
        assert line.amount == Decimal("100.00")
        assert line.currency == "USD"


# ============================================================================
# JournalWriter Tests (requires database fixtures)
# ============================================================================


class TestJournalWriterUnit:
    """Unit tests for JournalWriter components (no database)."""

    def test_role_resolver_registration(self):
        """RoleResolver can register and resolve bindings."""
        from finance_kernel.services.journal_writer import RoleResolver

        resolver = RoleResolver()
        account_id = uuid4()

        resolver.register_binding("InventoryAsset", account_id, "1100")
        resolved_id, resolved_code = resolver.resolve("InventoryAsset", "GL", 1)

        assert resolved_id == account_id
        assert resolved_code == "1100"

    def test_role_resolver_unknown_role(self):
        """RoleResolver raises for unknown roles."""
        from finance_kernel.services.journal_writer import (
            RoleResolutionError,
            RoleResolver,
        )

        resolver = RoleResolver()

        with pytest.raises(RoleResolutionError) as exc_info:
            resolver.resolve("UnknownRole", "GL", 1)

        assert exc_info.value.role == "UnknownRole"
        assert exc_info.value.ledger_id == "GL"

    def test_write_status_enum(self):
        """WriteStatus enum values."""
        from finance_kernel.services.journal_writer import WriteStatus

        assert WriteStatus.WRITTEN == "written"
        assert WriteStatus.ALREADY_EXISTS == "already_exists"
        assert WriteStatus.ROLE_RESOLUTION_FAILED == "role_resolution_failed"

    def test_journal_write_result_success(self):
        """JournalWriteResult success factory."""
        from finance_kernel.services.journal_writer import (
            JournalWriteResult,
            WrittenEntry,
        )

        entry = WrittenEntry(
            entry_id=uuid4(),
            ledger_id="GL",
            seq=1,
            idempotency_key="test:key:1",
        )
        result = JournalWriteResult.success((entry,))

        assert result.is_success
        assert len(result.entries) == 1
        assert result.entry_ids == (entry.entry_id,)

    def test_journal_write_result_failure(self):
        """JournalWriteResult failure factory."""
        from finance_kernel.services.journal_writer import JournalWriteResult

        result = JournalWriteResult.role_resolution_failed(
            ("MissingRole",), "Cannot resolve role"
        )

        assert not result.is_success
        assert result.error_code == "ROLE_RESOLUTION_FAILED"
        assert result.unresolved_roles == ("MissingRole",)


# ============================================================================
# InterpretationCoordinator Tests (unit level)
# ============================================================================


class TestInterpretationResultUnit:
    """Unit tests for InterpretationResult."""

    def test_posted_result(self):
        """Create a POSTED result."""
        from finance_kernel.services.interpretation_coordinator import (
            InterpretationResult,
        )
        from finance_kernel.services.journal_writer import (
            JournalWriteResult,
            WrittenEntry,
        )

        result = InterpretationResult.posted(
            outcome=None,  # Would be real outcome in integration
            economic_event=None,
            journal_result=JournalWriteResult.success(
                (WrittenEntry(uuid4(), "GL", 1, "key"),)
            ),
        )

        assert result.success

    def test_rejected_result(self):
        """Create a REJECTED result."""
        from finance_kernel.services.interpretation_coordinator import (
            InterpretationResult,
        )

        result = InterpretationResult.rejected(
            outcome=None,
            error_code="INVALID_QUANTITY",
            error_message="Quantity must be positive",
        )

        assert not result.success
        assert result.error_code == "INVALID_QUANTITY"

    def test_blocked_result(self):
        """Create a BLOCKED result."""
        from finance_kernel.services.interpretation_coordinator import (
            InterpretationResult,
        )

        result = InterpretationResult.blocked(
            outcome=None,
            error_code="PENDING_APPROVAL",
            error_message="Waiting for approval",
        )

        assert not result.success
        assert result.error_code == "PENDING_APPROVAL"


# ============================================================================
# Snapshot Tests
# ============================================================================


class TestAccountingIntentSnapshot:
    """Tests for AccountingIntentSnapshot."""

    def test_create_snapshot(self):
        """Create a reference snapshot."""
        snapshot = AccountingIntentSnapshot(
            coa_version=1,
            dimension_schema_version=2,
            rounding_policy_version=1,
            currency_registry_version=3,
            fx_policy_version=None,
        )

        assert snapshot.coa_version == 1
        assert snapshot.dimension_schema_version == 2
        assert snapshot.fx_policy_version is None

    def test_snapshot_default_values(self):
        """Snapshot has sensible defaults."""
        snapshot = AccountingIntentSnapshot(
            coa_version=1,
            dimension_schema_version=1,
        )

        assert snapshot.rounding_policy_version == 1
        assert snapshot.currency_registry_version == 1
