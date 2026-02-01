"""
Cash Management ORM Models (``finance_modules.cash.orm``).

Responsibility
--------------
SQLAlchemy persistence models for cash management -- bank accounts,
transactions, statements, statement lines, reconciliations, and
reconciliation matches.  Maps frozen domain dataclasses from ``models.py``
to database tables.

Architecture position
---------------------
**Modules layer** -- persistence.  Imports from ``finance_kernel.db.base``
and sibling ``models.py``.  MUST NOT be imported by ``finance_kernel``.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import String, ForeignKey, Index, UniqueConstraint, Boolean, Date
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase


# ---------------------------------------------------------------------------
# BankAccountModel
# ---------------------------------------------------------------------------

class BankAccountModel(TrackedBase):
    """
    ORM model for ``BankAccount`` -- a bank account managed by the
    organization.

    Table: ``cash_bank_accounts``
    """

    __tablename__ = "cash_bank_accounts"

    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    institution: Mapped[str] = mapped_column(String(200))
    account_number_masked: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    gl_account_code: Mapped[str] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(default=True)

    # Relationships (children)
    transactions: Mapped[list["BankTransactionModel"]] = relationship(
        back_populates="bank_account", cascade="all, delete-orphan",
    )
    reconciliations: Mapped[list["ReconciliationModel"]] = relationship(
        back_populates="bank_account", cascade="all, delete-orphan",
    )
    statements: Mapped[list["BankStatementModel"]] = relationship(
        back_populates="bank_account", cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("code", name="uq_cash_bank_accounts_code"),
        Index("idx_cash_bank_accounts_institution", "institution"),
        Index("idx_cash_bank_accounts_gl_account_code", "gl_account_code"),
    )

    def to_dto(self):
        from finance_modules.cash.models import BankAccount
        return BankAccount(
            id=self.id,
            code=self.code,
            name=self.name,
            institution=self.institution,
            account_number_masked=self.account_number_masked,
            currency=self.currency,
            gl_account_code=self.gl_account_code,
            is_active=self.is_active,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "BankAccountModel":
        return cls(
            id=dto.id,
            code=dto.code,
            name=dto.name,
            institution=dto.institution,
            account_number_masked=dto.account_number_masked,
            currency=dto.currency,
            gl_account_code=dto.gl_account_code,
            is_active=dto.is_active,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<BankAccountModel(id={self.id!r}, code={self.code!r}, "
            f"institution={self.institution!r})>"
        )


# ---------------------------------------------------------------------------
# BankTransactionModel
# ---------------------------------------------------------------------------

class BankTransactionModel(TrackedBase):
    """
    ORM model for ``BankTransaction`` -- a single transaction from a bank
    statement or feed.

    Table: ``cash_bank_transactions``
    """

    __tablename__ = "cash_bank_transactions"

    bank_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("cash_bank_accounts.id"),
    )
    transaction_date: Mapped[date]
    amount: Mapped[Decimal]
    transaction_type: Mapped[str] = mapped_column(String(50))
    reference: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(500), default="")
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reconciled: Mapped[bool] = mapped_column(default=False)
    matched_journal_line_id: Mapped[UUID | None]

    # Relationships
    bank_account: Mapped["BankAccountModel"] = relationship(
        back_populates="transactions",
    )

    __table_args__ = (
        Index("idx_cash_bank_transactions_bank_account_id", "bank_account_id"),
        Index("idx_cash_bank_transactions_transaction_date", "transaction_date"),
        Index("idx_cash_bank_transactions_external_id", "external_id"),
        Index(
            "idx_cash_bank_transactions_matched_journal_line_id",
            "matched_journal_line_id",
        ),
    )

    def to_dto(self):
        from finance_modules.cash.models import BankTransaction, TransactionType
        return BankTransaction(
            id=self.id,
            bank_account_id=self.bank_account_id,
            transaction_date=self.transaction_date,
            amount=self.amount,
            transaction_type=TransactionType(self.transaction_type),
            reference=self.reference,
            description=self.description,
            external_id=self.external_id,
            reconciled=self.reconciled,
            matched_journal_line_id=self.matched_journal_line_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "BankTransactionModel":
        return cls(
            id=dto.id,
            bank_account_id=dto.bank_account_id,
            transaction_date=dto.transaction_date,
            amount=dto.amount,
            transaction_type=dto.transaction_type.value,
            reference=dto.reference,
            description=dto.description,
            external_id=dto.external_id,
            reconciled=dto.reconciled,
            matched_journal_line_id=dto.matched_journal_line_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<BankTransactionModel(id={self.id!r}, "
            f"type={self.transaction_type!r}, amount={self.amount!r})>"
        )


# ---------------------------------------------------------------------------
# ReconciliationModel
# ---------------------------------------------------------------------------

class ReconciliationModel(TrackedBase):
    """
    ORM model for ``Reconciliation`` -- a bank reconciliation for a specific
    account and statement period.

    Table: ``cash_reconciliations``
    """

    __tablename__ = "cash_reconciliations"

    bank_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("cash_bank_accounts.id"),
    )
    statement_date: Mapped[date]
    statement_balance: Mapped[Decimal]
    book_balance: Mapped[Decimal]
    adjusted_book_balance: Mapped[Decimal | None]
    variance: Mapped[Decimal | None]
    status: Mapped[str] = mapped_column(String(50), default="draft")
    completed_by_id: Mapped[UUID | None]
    completed_at: Mapped[date | None]

    # Relationships
    bank_account: Mapped["BankAccountModel"] = relationship(
        back_populates="reconciliations",
    )

    __table_args__ = (
        Index("idx_cash_reconciliations_bank_account_id", "bank_account_id"),
        Index("idx_cash_reconciliations_statement_date", "statement_date"),
        Index("idx_cash_reconciliations_status", "status"),
    )

    def to_dto(self):
        from finance_modules.cash.models import Reconciliation, ReconciliationStatus
        return Reconciliation(
            id=self.id,
            bank_account_id=self.bank_account_id,
            statement_date=self.statement_date,
            statement_balance=self.statement_balance,
            book_balance=self.book_balance,
            adjusted_book_balance=self.adjusted_book_balance,
            variance=self.variance,
            status=ReconciliationStatus(self.status),
            completed_by_id=self.completed_by_id,
            completed_at=self.completed_at,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ReconciliationModel":
        return cls(
            id=dto.id,
            bank_account_id=dto.bank_account_id,
            statement_date=dto.statement_date,
            statement_balance=dto.statement_balance,
            book_balance=dto.book_balance,
            adjusted_book_balance=dto.adjusted_book_balance,
            variance=dto.variance,
            status=dto.status.value,
            completed_by_id=dto.completed_by_id,
            completed_at=dto.completed_at,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ReconciliationModel(id={self.id!r}, "
            f"bank_account_id={self.bank_account_id!r}, "
            f"status={self.status!r})>"
        )


# ---------------------------------------------------------------------------
# BankStatementModel
# ---------------------------------------------------------------------------

class BankStatementModel(TrackedBase):
    """
    ORM model for ``BankStatement`` -- a parsed bank statement.

    Table: ``cash_bank_statements``
    """

    __tablename__ = "cash_bank_statements"

    bank_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("cash_bank_accounts.id"),
    )
    statement_date: Mapped[date]
    opening_balance: Mapped[Decimal]
    closing_balance: Mapped[Decimal]
    line_count: Mapped[int]
    format: Mapped[str] = mapped_column(String(50), default="MT940")
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Relationships (children)
    lines: Mapped[list["BankStatementLineModel"]] = relationship(
        back_populates="statement", cascade="all, delete-orphan",
    )

    # Relationships (parent)
    bank_account: Mapped["BankAccountModel"] = relationship(
        back_populates="statements",
    )

    __table_args__ = (
        Index("idx_cash_bank_statements_bank_account_id", "bank_account_id"),
        Index("idx_cash_bank_statements_statement_date", "statement_date"),
    )

    def to_dto(self):
        from finance_modules.cash.models import BankStatement
        return BankStatement(
            id=self.id,
            bank_account_id=self.bank_account_id,
            statement_date=self.statement_date,
            opening_balance=self.opening_balance,
            closing_balance=self.closing_balance,
            line_count=self.line_count,
            format=self.format,
            currency=self.currency,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "BankStatementModel":
        return cls(
            id=dto.id,
            bank_account_id=dto.bank_account_id,
            statement_date=dto.statement_date,
            opening_balance=dto.opening_balance,
            closing_balance=dto.closing_balance,
            line_count=dto.line_count,
            format=dto.format,
            currency=dto.currency,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<BankStatementModel(id={self.id!r}, "
            f"bank_account_id={self.bank_account_id!r}, "
            f"date={self.statement_date!r})>"
        )


# ---------------------------------------------------------------------------
# BankStatementLineModel
# ---------------------------------------------------------------------------

class BankStatementLineModel(TrackedBase):
    """
    ORM model for ``BankStatementLine`` -- a single line from a parsed
    bank statement.

    Table: ``cash_bank_statement_lines``
    """

    __tablename__ = "cash_bank_statement_lines"

    statement_id: Mapped[UUID] = mapped_column(
        ForeignKey("cash_bank_statements.id"),
    )
    transaction_date: Mapped[date]
    amount: Mapped[Decimal]
    reference: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(500), default="")
    transaction_type: Mapped[str] = mapped_column(String(50), default="UNKNOWN")

    # Relationships
    statement: Mapped["BankStatementModel"] = relationship(
        back_populates="lines",
    )
    matches: Mapped[list["ReconciliationMatchModel"]] = relationship(
        back_populates="statement_line", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_cash_bank_statement_lines_statement_id", "statement_id"),
        Index(
            "idx_cash_bank_statement_lines_transaction_date",
            "transaction_date",
        ),
    )

    def to_dto(self):
        from finance_modules.cash.models import BankStatementLine
        return BankStatementLine(
            id=self.id,
            statement_id=self.statement_id,
            transaction_date=self.transaction_date,
            amount=self.amount,
            reference=self.reference,
            description=self.description,
            transaction_type=self.transaction_type,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "BankStatementLineModel":
        return cls(
            id=dto.id,
            statement_id=dto.statement_id,
            transaction_date=dto.transaction_date,
            amount=dto.amount,
            reference=dto.reference,
            description=dto.description,
            transaction_type=dto.transaction_type,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<BankStatementLineModel(id={self.id!r}, "
            f"statement_id={self.statement_id!r}, amount={self.amount!r})>"
        )


# ---------------------------------------------------------------------------
# ReconciliationMatchModel
# ---------------------------------------------------------------------------

class ReconciliationMatchModel(TrackedBase):
    """
    ORM model for ``ReconciliationMatch`` -- a match between a bank
    statement line and a book entry.

    Table: ``cash_reconciliation_matches``
    """

    __tablename__ = "cash_reconciliation_matches"

    statement_line_id: Mapped[UUID] = mapped_column(
        ForeignKey("cash_bank_statement_lines.id"),
    )
    journal_line_id: Mapped[UUID | None]
    match_confidence: Mapped[Decimal] = mapped_column(default=Decimal("1.0"))
    match_method: Mapped[str] = mapped_column(String(50), default="manual")

    # Relationships
    statement_line: Mapped["BankStatementLineModel"] = relationship(
        back_populates="matches",
    )

    __table_args__ = (
        Index(
            "idx_cash_reconciliation_matches_statement_line_id",
            "statement_line_id",
        ),
        Index(
            "idx_cash_reconciliation_matches_journal_line_id",
            "journal_line_id",
        ),
    )

    def to_dto(self):
        from finance_modules.cash.models import ReconciliationMatch
        return ReconciliationMatch(
            id=self.id,
            statement_line_id=self.statement_line_id,
            journal_line_id=self.journal_line_id,
            match_confidence=self.match_confidence,
            match_method=self.match_method,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ReconciliationMatchModel":
        return cls(
            id=dto.id,
            statement_line_id=dto.statement_line_id,
            journal_line_id=dto.journal_line_id,
            match_confidence=dto.match_confidence,
            match_method=dto.match_method,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ReconciliationMatchModel(id={self.id!r}, "
            f"method={self.match_method!r}, "
            f"confidence={self.match_confidence!r})>"
        )
