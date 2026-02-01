"""
Module: finance_modules.revenue.models
Responsibility:
    Frozen domain DTOs for the ASC 606 revenue recognition lifecycle.
    The nouns: contracts, performance obligations, transaction prices,
    SSP allocations, recognition schedules, and contract modifications.

Architecture:
    finance_modules layer -- pure data definitions with ZERO I/O.
    All models are frozen dataclasses (immutable after construction).
    All monetary fields use Decimal -- NEVER float.

    These DTOs flow through the service layer and are never persisted
    directly; the kernel JournalEntry/JournalLine models carry the
    financial truth.

Invariants:
    - All dataclasses are frozen (immutable).
    - Monetary amounts use Decimal (R16 precision).
    - Enum values are string-backed for serialization safety.

Failure modes:
    - FrozenInstanceError on attempted mutation.
    - ValueError on invalid enum construction.

Audit relevance:
    - Immutable DTOs ensure no in-flight mutation of financial data.
    - Enum-based status and method fields prevent invalid state
      representation in audit logs.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.revenue.models")


class ContractStatus(Enum):
    """
    Revenue contract lifecycle states.

    Contract:
        Enum members correspond to workflow states in workflows.py.
        Values are lowercase strings for JSON serialization.
    """
    IDENTIFIED = "identified"
    ACTIVE = "active"
    MODIFIED = "modified"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class RecognitionMethod(Enum):
    """
    Revenue recognition methods for performance obligations.

    Contract:
        Each member maps to a distinct event_type in profiles.py:
        - POINT_IN_TIME  -> revenue.recognize_point_in_time
        - OVER_TIME_INPUT  -> revenue.recognize_over_time_input
        - OVER_TIME_OUTPUT -> revenue.recognize_over_time_output
    """
    POINT_IN_TIME = "point_in_time"
    OVER_TIME_INPUT = "over_time_input"
    OVER_TIME_OUTPUT = "over_time_output"


class ModificationType(Enum):
    """
    Contract modification treatment per ASC 606-10-25-12.

    Contract:
        Determined by helpers.assess_modification_type() based on
        whether modification adds distinct goods, reflects SSP, and
        whether remaining goods are distinct.
    """
    SEPARATE_CONTRACT = "separate_contract"
    CUMULATIVE_CATCH_UP = "cumulative_catch_up"
    PROSPECTIVE = "prospective"
    TERMINATION = "termination"


@dataclass(frozen=True)
class RevenueContract:
    """
    An identified revenue contract (ASC 606 Step 1).

    Contract:
        Frozen dataclass -- immutable after construction.
        All monetary fields are Decimal (never float).
    Guarantees:
        - ``total_consideration`` and ``variable_consideration`` are Decimal.
        - ``currency`` defaults to "USD" (ISO 4217).
    Non-goals:
        - Not persisted to database; kernel JournalEntry is the source of
          financial truth.
    """
    id: UUID
    customer_id: UUID
    contract_number: str
    start_date: date
    end_date: date | None = None
    total_consideration: Decimal = Decimal("0")
    variable_consideration: Decimal = Decimal("0")
    status: ContractStatus = ContractStatus.IDENTIFIED
    currency: str = "USD"


@dataclass(frozen=True)
class PerformanceObligation:
    """
    An identified performance obligation (ASC 606 Step 2).

    Contract:
        Frozen dataclass -- immutable after construction.
        Each obligation belongs to exactly one contract (``contract_id``).
    Guarantees:
        - ``standalone_selling_price`` and ``allocated_price`` are Decimal.
        - ``recognition_method`` is a valid RecognitionMethod enum.
    Non-goals:
        - Not persisted; used as in-memory DTO for calculation and posting.
    """
    id: UUID
    contract_id: UUID
    description: str
    is_distinct: bool = True
    standalone_selling_price: Decimal = Decimal("0")
    allocated_price: Decimal = Decimal("0")
    recognition_method: RecognitionMethod = RecognitionMethod.POINT_IN_TIME
    satisfied: bool = False
    satisfaction_date: date | None = None


@dataclass(frozen=True)
class TransactionPrice:
    """
    Determined transaction price (ASC 606 Step 3).

    Contract:
        Frozen dataclass -- immutable after construction.
    Guarantees:
        - ``total_transaction_price`` = base + variable + financing +
          noncash - consideration_payable.
        - All monetary fields are Decimal (never float).
    """
    id: UUID
    contract_id: UUID
    base_price: Decimal
    variable_consideration: Decimal = Decimal("0")
    constraint_applied: bool = False
    financing_component: Decimal = Decimal("0")
    noncash_consideration: Decimal = Decimal("0")
    consideration_payable: Decimal = Decimal("0")
    total_transaction_price: Decimal = Decimal("0")


@dataclass(frozen=True)
class SSPAllocation:
    """
    SSP allocation result (ASC 606 Step 4).

    Contract:
        Frozen dataclass -- immutable after construction.
    Guarantees:
        - ``allocation_percentage`` is Decimal in range (0, 1].
        - ``allocated_amount`` is Decimal produced by AllocationEngine.
    """
    id: UUID
    contract_id: UUID
    obligation_id: UUID
    standalone_selling_price: Decimal
    allocated_amount: Decimal
    allocation_percentage: Decimal


@dataclass(frozen=True)
class RecognitionSchedule:
    """
    Revenue recognition schedule entry.

    Contract:
        Frozen dataclass -- immutable after construction.
        Each entry represents one period's recognition amount for one
        obligation within a contract.
    """
    id: UUID
    contract_id: UUID
    obligation_id: UUID
    period: str
    amount: Decimal
    recognized: bool = False
    recognized_date: date | None = None


@dataclass(frozen=True)
class ContractModification:
    """
    A contract modification record (ASC 606-10-25-12).

    Contract:
        Frozen dataclass -- immutable after construction.
        Modification type is determined by helpers.assess_modification_type().
    Guarantees:
        - ``price_change`` is Decimal (may be negative for reductions).
        - ``modification_type`` is a valid ModificationType enum.
    """
    id: UUID
    contract_id: UUID
    modification_date: date
    modification_type: ModificationType
    description: str
    price_change: Decimal = Decimal("0")
    scope_change: str | None = None
    actor_id: UUID | None = None
