"""
Intercompany Domain Models (``finance_modules.intercompany.models``).

Responsibility
--------------
Frozen dataclass value objects representing the nouns of intercompany
accounting: agreements, transactions, elimination rules, consolidation
results, and reconciliation results.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``IntercompanyService`` and returned to callers.  No dependency on kernel
services, database, or engines.

Invariants enforced
-------------------
* All models are ``frozen=True`` (immutable after construction).
* All monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Construction with invalid enum values raises ``ValueError``.

Audit relevance
---------------
* ``ICTransaction`` records provide a traceable IC transfer log.
* ``EliminationRule`` records document consolidation elimination logic.
* ``ICReconciliationResult`` records support IC balance confirmation.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class IntercompanyAgreement:
    """An agreement governing intercompany transactions between two entities."""

    id: UUID
    entity_a: str
    entity_b: str
    agreement_type: str = "transfer"
    markup_rate: Decimal = Decimal("0")
    currency: str = "USD"
    effective_from: date = date(2024, 1, 1)
    effective_to: date | None = None


@dataclass(frozen=True)
class ICTransaction:
    """A single intercompany transaction between entities."""

    id: UUID
    agreement_id: UUID | None = None
    from_entity: str = ""
    to_entity: str = ""
    amount: Decimal = Decimal("0")
    currency: str = "USD"
    transaction_date: date = date(2024, 1, 1)
    description: str = ""


@dataclass(frozen=True)
class EliminationRule:
    """A rule defining how intercompany balances should be eliminated."""

    id: UUID
    rule_type: str = "balance"
    debit_role: str = "INTERCOMPANY_DUE_TO"
    credit_role: str = "INTERCOMPANY_DUE_FROM"
    description: str = "IC balance elimination"


@dataclass(frozen=True)
class ConsolidationResult:
    """The result of consolidating multiple entities for a period."""

    entities: tuple[str, ...]
    period: str
    total_debits: Decimal
    total_credits: Decimal
    elimination_amount: Decimal
    is_balanced: bool


@dataclass(frozen=True)
class ICReconciliationResult:
    """The result of reconciling intercompany balances between two entities."""

    entity_a: str
    entity_b: str
    period: str
    entity_a_balance: Decimal
    entity_b_balance: Decimal
    difference: Decimal
    is_reconciled: bool
