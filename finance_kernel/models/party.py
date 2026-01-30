"""
Party model for customers, suppliers, and employees.

Party represents external entities that the organization transacts with:
- Customers (AR relationships)
- Suppliers (AP relationships)
- Employees (payroll relationships)
- Intercompany entities

Used by guards for:
- Credit limit checks (customer)
- Frozen status blocking (all types)
- Payment terms validation

Hard invariants:
- Parties referenced by events cannot be deleted
- party_code is immutable once referenced
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
    """Classification of party types."""

    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    EMPLOYEE = "employee"
    INTERCOMPANY = "intercompany"


class PartyStatus(str, Enum):
    """Party lifecycle status."""

    ACTIVE = "active"  # Can transact normally
    FROZEN = "frozen"  # Cannot transact (blocked)
    CLOSED = "closed"  # Historical only, no new transactions


class Party(TrackedBase):
    """
    Party represents external entities: customers, suppliers, employees.

    Used by guards for:
    - Credit limit checks (customer)
    - Frozen status blocking (all types)
    - Payment terms validation
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
        """Check if party is frozen for transactions."""
        return self.status == PartyStatus.FROZEN

    @property
    def can_transact(self) -> bool:
        """Check if party can accept new transactions."""
        return self.is_active and self.status == PartyStatus.ACTIVE

    def __repr__(self) -> str:
        return f"<Party {self.party_code}: {self.name} ({self.party_type.value})>"
