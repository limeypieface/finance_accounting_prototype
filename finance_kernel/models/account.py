"""
Module: finance_kernel.models.account
Responsibility: ORM persistence for the Chart of Accounts (COA) -- the target
    of every journal line.
Architecture position: Kernel > Models.  May import from db/base.py only.

Invariants enforced:
    R10 -- Structural fields (account_type, normal_balance) immutable once
           referenced by posted JournalLines.
    L1  -- Role-to-COA resolution requires exactly one active Account per role
           at posting time (enforced by JournalWriter, not this model).
    R5  -- At least one account with the ROUNDING tag must exist per currency
           (enforced at system setup; rounding account lookup at posting time).

Failure modes:
    - AccountNotFoundError when a posting references a non-existent account.
    - AccountInactiveError when a posting targets an inactive account.
    - AccountReferencedError when deletion is attempted on a referenced account.

Audit relevance:
    Account rows define the structure of the general ledger.  Changes to
    account_type or normal_balance after posting would retroactively alter
    the meaning of historical journal lines, so structural fields are locked
    once referenced.
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
    Chart of Accounts entry -- a single node in the general ledger structure.

    Contract:
        Account.code is globally unique (uq_account_code).  Once an account
        is referenced by a posted JournalLine, its account_type and
        normal_balance MUST NOT change (R10).

    Guarantees:
        - code is unique and non-null.
        - account_type is one of ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE.
        - normal_balance is DEBIT or CREDIT, consistent with account_type.

    Non-goals:
        - This model does NOT enforce referential deletion prevention at the
          ORM level; that is handled by FK constraints and service-layer guards.
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
        """Check if this is a rounding account (R5/R22).

        Postconditions: Returns True iff tags list contains "rounding".
        """
        return self.tags is not None and AccountTag.ROUNDING.value in self.tags

    @property
    def is_debit_normal(self) -> bool:
        """Check if account has debit normal balance.

        Postconditions: Returns True iff normal_balance is DEBIT.
        """
        return self.normal_balance == NormalBalance.DEBIT

    @property
    def is_credit_normal(self) -> bool:
        """Check if account has credit normal balance.

        Postconditions: Returns True iff normal_balance is CREDIT.
        """
        return self.normal_balance == NormalBalance.CREDIT

    def has_tag(self, tag: AccountTag | str) -> bool:
        """Check if account has a specific tag.

        Preconditions: tag is an AccountTag enum member or a string.
        Postconditions: Returns True iff tag is present in this account's tags list.
        """
        if self.tags is None:
            return False
        tag_value = tag.value if isinstance(tag, AccountTag) else tag
        return tag_value in self.tags
