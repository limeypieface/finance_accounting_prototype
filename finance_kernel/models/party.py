"""
Module: finance_kernel.models.party
Responsibility: ORM persistence for external entities (customers, suppliers,
    employees, intercompany partners) that the organization transacts with.
    Party rows serve as the identity anchor for guard enforcement in the
    posting pipeline.
Architecture position: Kernel > Models.  May import from db/base.py only.
    MUST NOT import from services/, selectors/, domain/, or outer layers.

Invariants enforced:
    R10 -- party_code is immutable once referenced by posted events or journal
           entries.  Changing the code would orphan subledger entries and break
           audit trail linkage.
    Guard enforcement points (not ORM-level, but this model is the data source):
        - Credit limit guard: customer credit_limit vs outstanding AR balance.
        - Frozen status guard: status == FROZEN blocks all new transactions.
        - Payment terms guard: payment_terms_days drives due-date calculation.

Failure modes:
    - IntegrityError on duplicate party_code (uq_party_code constraint).
    - Guard rejection (upstream) when is_frozen returns True or can_transact
      returns False.

Audit relevance:
    Party is the counterparty identity for all financial transactions.
    Subledger entries, AR invoices, AP bills, and payroll runs all reference
    Party.  The credit_limit and status fields are guard inputs that
    determine whether a transaction is admissible.
"""

from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import TrackedBase

if TYPE_CHECKING:
    pass


class PartyType(str, Enum):
    """Classification of party types.

    Contract: Every Party has exactly one PartyType.  The type determines
    which subledger (AR, AP, PAYROLL) and which guards apply.
    """

    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    EMPLOYEE = "employee"
    INTERCOMPANY = "intercompany"


class PartyStatus(str, Enum):
    """Party lifecycle status.

    Contract: Transitions follow ACTIVE -> FROZEN -> CLOSED (one-way seal).
    FROZEN parties cannot transact; CLOSED parties are historical only.
    """

    ACTIVE = "active"  # Can transact normally
    FROZEN = "frozen"  # Cannot transact (blocked)
    CLOSED = "closed"  # Historical only, no new transactions


class Party(TrackedBase):
    """
    External entity that the organization transacts with.

    Contract:
        Each Party has a unique party_code that is immutable once referenced
        by posted financial records.  The party_type determines which
        subledger and guards apply.  Status transitions control transaction
        admissibility.

    Guarantees:
        - party_code is globally unique (uq_party_code constraint).
        - party_type is set at creation and classifies the party permanently.
        - status and is_active together determine transaction admissibility.
        - credit_limit and credit_currency are guard inputs for AR postings.

    Non-goals:
        - This model does NOT enforce credit limits at the ORM level; that
          is the responsibility of the AR module guard.
        - This model does NOT validate payment_terms_days ranges; that is
          enforced by the AP/AR service layer.
    """

    __tablename__ = "parties"

    __table_args__ = (
        UniqueConstraint("party_code", name="uq_party_code"),
        Index("idx_party_type", "party_type"),
        Index("idx_party_status", "status"),
        Index("idx_party_active", "is_active"),
    )

    # Unique business identifier
    party_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # Party classification
    party_type: Mapped[PartyType] = mapped_column(
        String(20),
        nullable=False,
    )

    # Display name
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Lifecycle status
    status: Mapped[PartyStatus] = mapped_column(
        String(20),
        nullable=False,
        default=PartyStatus.ACTIVE,
    )

    # Whether party is active for new transactions
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Financial controls
    credit_limit: Mapped[Decimal | None] = mapped_column(
        nullable=True,
    )

    credit_currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )

    payment_terms_days: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    # Tax identification
    tax_id: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    # Default currency for transactions
    default_currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )

    # External reference (e.g., CRM ID, ERP ID)
    external_ref: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    @property
    def is_frozen(self) -> bool:
        """Check if party is frozen for transactions.

        Postconditions: Returns True iff status is FROZEN.
            Used by guards to block all new transactions for this party.
        """
        return self.status == PartyStatus.FROZEN

    @property
    def can_transact(self) -> bool:
        """Check if party can accept new transactions.

        Postconditions: Returns True iff is_active is True AND status is ACTIVE.
            Both conditions must hold -- a deactivated but ACTIVE-status party
            cannot transact, nor can an active but FROZEN party.
        """
        # INVARIANT: Guard enforcement point -- callers MUST check this
        # before admitting any transaction for this party.
        return self.is_active and self.status == PartyStatus.ACTIVE

    def __repr__(self) -> str:
        return f"<Party {self.party_code}: {self.name} ({self.party_type.value})>"
