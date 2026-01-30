"""
AccountingIntent - Contract between Economic and Finance layers.

The AccountingIntent is an immutable DTO emitted by the economic layer that
describes what financial effects should occur. It contains:
- Account roles (not COA accounts) - roles are resolved to accounts by the finance layer
- Amounts and currency
- Dimensions
- Reference snapshot versions

The finance layer receives the intent and either:
1. Atomically posts all required entries (POSTED)
2. Deterministically blocks (BLOCKED) - e.g., missing COA mapping
3. Deterministically rejects (REJECTED) - e.g., closed period

Invariant L1: Every POSTED entry must resolve each role to exactly one COA account.
Invariant L5: No journal rows without a matching POSTED outcome; no POSTED outcome without all journal rows.

Integration with foundational modules:
- ReferenceSnapshot: Can convert from comprehensive snapshot
- PolicyAuthority: Validates economic authority at intent formation
- SubledgerControlContract: Can validate against control contracts
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TYPE_CHECKING
from uuid import UUID

from finance_kernel.domain.values import Money

if TYPE_CHECKING:
    from finance_kernel.domain.reference_snapshot import ReferenceSnapshot as FullSnapshot
    from finance_kernel.domain.policy_authority import PolicyAuthority, ModuleType
    from finance_kernel.domain.subledger_control import SubledgerControlRegistry


class IntentLineSide:
    """Side of an intent line."""

    DEBIT = "debit"
    CREDIT = "credit"


@dataclass(frozen=True)
class IntentLine:
    """
    A single line in an accounting intent.

    Uses account_role (not account_code) because the economic layer
    doesn't know specific COA accounts - only semantic roles.

    Attributes:
        account_role: Semantic role (e.g., "InventoryAsset", "GRNI")
        side: Debit or credit
        money: Amount and currency
        dimensions: Optional dimension values
        memo: Optional line memo
        is_rounding: Whether this is a rounding adjustment line
    """

    account_role: str
    side: str  # "debit" or "credit"
    money: Money
    dimensions: dict[str, str] | None = None
    memo: str | None = None
    is_rounding: bool = False

    @property
    def amount(self) -> Decimal:
        """Get the amount."""
        return self.money.amount

    @property
    def currency(self) -> str:
        """Get the currency code."""
        return self.money.currency.code

    def __post_init__(self) -> None:
        if self.side not in (IntentLineSide.DEBIT, IntentLineSide.CREDIT):
            raise ValueError(f"Invalid side: {self.side}")
        if self.money.amount < Decimal("0"):
            raise ValueError("Amount must be non-negative")

    @classmethod
    def debit(
        cls,
        role: str,
        amount: Decimal | str,
        currency: str,
        dimensions: dict[str, str] | None = None,
        memo: str | None = None,
    ) -> "IntentLine":
        """Create a debit line."""
        if isinstance(amount, str):
            amount = Decimal(amount)
        return cls(
            account_role=role,
            side=IntentLineSide.DEBIT,
            money=Money.of(amount, currency),
            dimensions=dimensions,
            memo=memo,
        )

    @classmethod
    def credit(
        cls,
        role: str,
        amount: Decimal | str,
        currency: str,
        dimensions: dict[str, str] | None = None,
        memo: str | None = None,
    ) -> "IntentLine":
        """Create a credit line."""
        if isinstance(amount, str):
            amount = Decimal(amount)
        return cls(
            account_role=role,
            side=IntentLineSide.CREDIT,
            money=Money.of(amount, currency),
            dimensions=dimensions,
            memo=memo,
        )


@dataclass(frozen=True)
class LedgerIntent:
    """
    Intent for a single ledger.

    A single EconomicEvent may produce intents for multiple ledgers
    (e.g., GL and subledger). Each ledger intent is processed atomically
    with the others.

    Attributes:
        ledger_id: Identifier for the target ledger (e.g., "GL", "AP", "AR")
        lines: The journal lines for this ledger
    """

    ledger_id: str
    lines: tuple[IntentLine, ...]

    def __post_init__(self) -> None:
        if not self.lines:
            raise ValueError("LedgerIntent must have at least one line")

    @property
    def currencies(self) -> frozenset[str]:
        """All currencies in this intent."""
        return frozenset(line.currency for line in self.lines)

    def total_debits(self, currency: str | None = None) -> Decimal:
        """Sum of debit amounts, optionally filtered by currency."""
        return sum(
            (
                line.amount
                for line in self.lines
                if line.side == IntentLineSide.DEBIT
                and (currency is None or line.currency == currency)
            ),
            Decimal("0"),
        )

    def total_credits(self, currency: str | None = None) -> Decimal:
        """Sum of credit amounts, optionally filtered by currency."""
        return sum(
            (
                line.amount
                for line in self.lines
                if line.side == IntentLineSide.CREDIT
                and (currency is None or line.currency == currency)
            ),
            Decimal("0"),
        )

    def is_balanced(self, currency: str | None = None) -> bool:
        """Check if debits equal credits for given currency (or all)."""
        if currency:
            return self.total_debits(currency) == self.total_credits(currency)
        for curr in self.currencies:
            if self.total_debits(curr) != self.total_credits(curr):
                return False
        return True


@dataclass(frozen=True)
class RoleBinding:
    """
    Binding of an account role to a COA account.

    Used by the finance layer to resolve roles to accounts.
    """

    role: str
    account_id: UUID
    account_code: str
    coa_version: int
    effective_from: date
    effective_to: date | None = None


@dataclass(frozen=True)
class AccountingIntentSnapshot:
    """
    Reference data snapshot versions for deterministic replay.

    Invariant L4: Replay using stored snapshots produces identical results.

    Note: This is a lightweight snapshot for backward compatibility.
    For full snapshot functionality, use ReferenceSnapshot from
    finance_kernel.domain.reference_snapshot.
    """

    coa_version: int
    dimension_schema_version: int
    rounding_policy_version: int = 1
    currency_registry_version: int = 1
    fx_policy_version: int | None = None

    # Optional: Reference to full snapshot ID for audit trail
    full_snapshot_id: UUID | None = None

    @classmethod
    def from_full_snapshot(cls, full_snapshot: "FullSnapshot") -> "AccountingIntentSnapshot":
        """
        Create lightweight snapshot from comprehensive ReferenceSnapshot.

        This bridges the new foundational module with existing code.
        """
        return cls(
            coa_version=full_snapshot.coa_version,
            dimension_schema_version=full_snapshot.dimension_schema_version,
            rounding_policy_version=full_snapshot.rounding_policy_version,
            currency_registry_version=full_snapshot.currency_registry_version,
            fx_policy_version=full_snapshot.fx_rates_version,
            full_snapshot_id=full_snapshot.snapshot_id,
        )


@dataclass(frozen=True)
class AccountingIntent:
    """
    The contract between Economic and Finance layers.

    Emitted by the interpretation engine after building economic meaning.
    Contains all information needed for the finance layer to create
    journal entries.

    Invariants:
    - L1: Every role must resolve to exactly one COA account
    - L5: All ledger intents are posted atomically or not at all

    Attributes:
        econ_event_id: The economic event that produced this intent
        source_event_id: The original business event
        profile_id: Profile that interpreted the event
        profile_version: Version of the profile used
        effective_date: Accounting effective date
        ledger_intents: Intents for each affected ledger
        snapshot: Reference data versions for replay
        description: Optional entry description
        trace_id: Optional trace ID for audit
        created_at: When the intent was created
    """

    econ_event_id: UUID
    source_event_id: UUID
    profile_id: str
    profile_version: int
    effective_date: date
    ledger_intents: tuple[LedgerIntent, ...]
    snapshot: AccountingIntentSnapshot
    description: str | None = None
    trace_id: UUID | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.ledger_intents:
            raise ValueError("AccountingIntent must have at least one ledger intent")

    @property
    def ledger_ids(self) -> frozenset[str]:
        """All ledger IDs in this intent."""
        return frozenset(intent.ledger_id for intent in self.ledger_intents)

    @property
    def all_roles(self) -> frozenset[str]:
        """All account roles referenced in this intent."""
        roles = set()
        for ledger_intent in self.ledger_intents:
            for line in ledger_intent.lines:
                roles.add(line.account_role)
        return frozenset(roles)

    def get_ledger_intent(self, ledger_id: str) -> LedgerIntent | None:
        """Get intent for a specific ledger."""
        for intent in self.ledger_intents:
            if intent.ledger_id == ledger_id:
                return intent
        return None

    def idempotency_key(self, ledger_id: str) -> str:
        """
        Generate idempotency key for a ledger posting.

        Format: (econ_event_id, ledger_id, profile_version)
        """
        return f"{self.econ_event_id}:{ledger_id}:{self.profile_version}"

    def all_balanced(self) -> bool:
        """Check if all ledger intents are balanced."""
        return all(intent.is_balanced() for intent in self.ledger_intents)

    def validate_against_policy(
        self,
        policy_registry: "PolicyAuthority",
        module_type: "ModuleType",
        economic_type: str,
    ) -> list[str]:
        """
        Validate this intent against a PolicyAuthority.

        Checks:
        1. Module has authority to post to all target ledgers
        2. Economic type is allowed to post to target ledgers

        Args:
            policy_registry: The policy registry to validate against.
            module_type: The module creating this intent.
            economic_type: The economic type being processed.

        Returns:
            List of error messages (empty if valid).
        """
        errors: list[str] = []

        # Check economic type constraints
        violations = policy_registry.validate_economic_type_posting(
            economic_type=economic_type,
            target_ledgers=self.ledger_ids,
        )
        for v in violations:
            errors.append(v.message)

        return errors

    def validate_subledger_bindings(
        self,
        control_registry: "SubledgerControlRegistry",
    ) -> list[str]:
        """
        Validate that all subledger postings have valid control contracts.

        Args:
            control_registry: The subledger control registry.

        Returns:
            List of warning messages for missing contracts.
        """
        warnings: list[str] = []

        for ledger_intent in self.ledger_intents:
            # Check if this ledger has a subledger contract
            contract = control_registry.get_by_control_account(ledger_intent.ledger_id)
            if contract is None:
                # Not all ledgers are subledgers - only warn for typical subledgers
                if ledger_intent.ledger_id in ("AP", "AR", "INVENTORY", "BANK"):
                    warnings.append(
                        f"No subledger control contract for ledger {ledger_intent.ledger_id}"
                    )

        return warnings


@dataclass(frozen=True)
class IntentResolutionResult:
    """
    Result of resolving an AccountingIntent.

    Contains either successfully resolved lines or error information.
    """

    success: bool
    resolved_lines: tuple["ResolvedIntentLine", ...] | None = None
    error_code: str | None = None
    error_message: str | None = None
    unresolved_roles: tuple[str, ...] | None = None

    @classmethod
    def ok(cls, lines: tuple["ResolvedIntentLine", ...]) -> "IntentResolutionResult":
        """Create a successful result."""
        return cls(success=True, resolved_lines=lines)

    @classmethod
    def fail(
        cls,
        error_code: str,
        error_message: str,
        unresolved_roles: tuple[str, ...] | None = None,
    ) -> "IntentResolutionResult":
        """Create a failure result."""
        return cls(
            success=False,
            error_code=error_code,
            error_message=error_message,
            unresolved_roles=unresolved_roles,
        )


@dataclass(frozen=True)
class ResolvedIntentLine:
    """
    An IntentLine with the account role resolved to a COA account.

    This is the output of role resolution, ready for persistence.
    """

    account_id: UUID
    account_code: str
    account_role: str  # Keep original role for audit
    side: str
    money: Money
    dimensions: dict[str, str] | None = None
    memo: str | None = None
    is_rounding: bool = False
    line_seq: int = 0

    @property
    def amount(self) -> Decimal:
        return self.money.amount

    @property
    def currency(self) -> str:
        return self.money.currency.code
