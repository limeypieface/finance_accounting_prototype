"""
Chart of Accounts model.

Defines the Account entity with:
- Account types (asset, liability, equity, revenue, expense)
- Normal balance (debit or credit)
- Tags for categorization (direct, indirect, unallowable, billable, rounding)

Hard invariants:
- Accounts referenced by posted lines cannot be deleted
- account_id is immutable
- type and normal_balance are immutable once referenced
- At least one rounding account must exist per currency or ledger
"""

from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, Index, String, UniqueConstraint
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase, UUIDString

if TYPE_CHECKING:
    from finance_kernel.models.journal import JournalLine


class AccountType(str, Enum):
    """Types of accounts in the chart of accounts."""

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


class NormalBalance(str, Enum):
    """Normal balance side for an account."""

    DEBIT = "debit"
    CREDIT = "credit"


# Standard account tags
class AccountTag(str, Enum):
    """Standard tags for account categorization."""

    DIRECT = "direct"  # Direct costs
    INDIRECT = "indirect"  # Indirect costs
    UNALLOWABLE = "unallowable"  # Unallowable costs (gov contracting)
    BILLABLE = "billable"  # Billable to customers
    ROUNDING = "rounding"  # Rounding difference account
    SUSPENSE = "suspense"  # Suspense/clearing account
    RETAINED_EARNINGS = "retained_earnings"  # Retained earnings
    INTERCOMPANY = "intercompany"  # Intercompany transactions


class Account(TrackedBase):
    """
    Chart of Accounts entry.

    Represents a single account in the general ledger structure.
    """

    __tablename__ = "accounts"

    __table_args__ = (
        UniqueConstraint("code", name="uq_account_code"),
        Index("idx_account_type", "account_type"),
        Index("idx_account_active", "is_active"),
    )

    # Account identifier (human-readable code)
    code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # Account name/description
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Account type determines financial statement placement
    account_type: Mapped[AccountType] = mapped_column(
        String(20),
        nullable=False,
    )

    # Normal balance determines debit/credit behavior
    normal_balance: Mapped[NormalBalance] = mapped_column(
        String(10),
        nullable=False,
    )

    # Whether the account is active for new postings
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Tags for categorization (stored as JSON array)
    tags: Mapped[list[str] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Parent account for hierarchical chart of accounts
    parent_id: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        nullable=True,
    )

    # Currency restriction (null = multi-currency allowed)
    currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )

    # Relationships
    journal_lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="account",
        lazy="dynamic",
    )

    def __repr__(self) -> str:
        return f"<Account {self.code}: {self.name}>"

    @property
    def is_rounding_account(self) -> bool:
        """Check if this is a rounding account."""
        return self.tags is not None and AccountTag.ROUNDING.value in self.tags

    @property
    def is_debit_normal(self) -> bool:
        """Check if account has debit normal balance."""
        return self.normal_balance == NormalBalance.DEBIT

    @property
    def is_credit_normal(self) -> bool:
        """Check if account has credit normal balance."""
        return self.normal_balance == NormalBalance.CREDIT

    def has_tag(self, tag: AccountTag | str) -> bool:
        """Check if account has a specific tag."""
        if self.tags is None:
            return False
        tag_value = tag.value if isinstance(tag, AccountTag) else tag
        return tag_value in self.tags
