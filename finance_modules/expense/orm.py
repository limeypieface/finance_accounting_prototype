"""
SQLAlchemy ORM persistence models for the Expense module.

Responsibility
--------------
Provide database-backed persistence for expense domain entities: expense
reports, expense lines, expense policies, and reimbursements.  Transient
computation DTOs (``PolicyViolation``, ``MileageRate``, ``PerDiemRate``)
are not persisted here.

Architecture position
---------------------
**Modules layer** -- ORM models consumed by ``ExpenseService`` for
persistence.  Inherits from ``TrackedBase`` (kernel db layer).

Invariants enforced
-------------------
* All monetary fields use ``Decimal`` (Numeric(38,9)) -- NEVER float.
* Enum fields stored as String(50) for readability and portability.
* ``ExpenseLineModel`` belongs to exactly one ``ExpenseReportModel``.
* ``ReimbursementModel`` links back to an expense report.

Audit relevance
---------------
* ``ExpenseReportModel`` lifecycle (draft -> submitted -> approved -> paid)
  is fully auditable via status field.
* ``ExpensePolicyModel`` records the policy rules governing each category.
* ``ReimbursementModel`` records employee reimbursement payments.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase


# ---------------------------------------------------------------------------
# ExpenseReportModel
# ---------------------------------------------------------------------------


class ExpenseReportModel(TrackedBase):
    """
    An employee expense report.

    Maps to the ``ExpenseReport`` DTO in ``finance_modules.expense.models``.

    Guarantees:
        - ``report_number`` is unique across all reports.
        - ``status`` follows the lifecycle:
          draft -> submitted -> pending_approval -> approved -> processing -> paid.
        - ``employee_id`` references a Party (employee).
    """

    __tablename__ = "expense_reports"

    __table_args__ = (
        UniqueConstraint("report_number", name="uq_expense_report_number"),
        Index("idx_expense_report_employee", "employee_id"),
        Index("idx_expense_report_status", "status"),
        Index("idx_expense_report_date", "report_date"),
    )

    report_number: Mapped[str] = mapped_column(String(50), nullable=False)
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("parties.id"), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    submitted_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_by: Mapped[UUID | None]
    paid_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    project_id: Mapped[UUID | None]
    department_id: Mapped[UUID | None]

    # Relationships
    lines: Mapped[list["ExpenseLineModel"]] = relationship(
        "ExpenseLineModel",
        back_populates="report",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.expense.models import ExpenseReport, ReportStatus

        return ExpenseReport(
            id=self.id,
            report_number=self.report_number,
            employee_id=self.employee_id,
            report_date=self.report_date,
            purpose=self.purpose,
            total_amount=self.total_amount,
            currency=self.currency,
            status=ReportStatus(self.status),
            submitted_date=self.submitted_date,
            approved_date=self.approved_date,
            approved_by=self.approved_by,
            paid_date=self.paid_date,
            project_id=self.project_id,
            department_id=self.department_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ExpenseReportModel":
        from finance_modules.expense.models import ReportStatus

        return cls(
            id=dto.id,
            report_number=dto.report_number,
            employee_id=dto.employee_id,
            report_date=dto.report_date,
            purpose=dto.purpose,
            total_amount=dto.total_amount,
            currency=dto.currency,
            status=dto.status.value if isinstance(dto.status, ReportStatus) else dto.status,
            submitted_date=dto.submitted_date,
            approved_date=dto.approved_date,
            approved_by=dto.approved_by,
            paid_date=dto.paid_date,
            project_id=dto.project_id,
            department_id=dto.department_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<ExpenseReportModel {self.report_number} [{self.status}] {self.total_amount}>"


# ---------------------------------------------------------------------------
# ExpenseLineModel
# ---------------------------------------------------------------------------


class ExpenseLineModel(TrackedBase):
    """
    A single expense item on an expense report.

    Maps to the ``ExpenseLine`` DTO in ``finance_modules.expense.models``.

    Guarantees:
        - Belongs to exactly one ``ExpenseReportModel``.
        - (report_id, line_number) is unique.
    """

    __tablename__ = "expense_lines"

    __table_args__ = (
        UniqueConstraint("report_id", "line_number", name="uq_expense_line_number"),
        Index("idx_expense_line_report", "report_id"),
        Index("idx_expense_line_category", "category"),
        Index("idx_expense_line_date", "expense_date"),
    )

    report_id: Mapped[UUID] = mapped_column(ForeignKey("expense_reports.id"), nullable=False)
    line_number: Mapped[int]
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(50), nullable=False)
    receipt_attached: Mapped[bool] = mapped_column(default=False)
    billable: Mapped[bool] = mapped_column(default=False)
    gl_account_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    project_id: Mapped[UUID | None]
    card_transaction_id: Mapped[UUID | None]
    violation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Parent relationship
    report: Mapped["ExpenseReportModel"] = relationship(
        "ExpenseReportModel",
        back_populates="lines",
    )

    def to_dto(self):
        from finance_modules.expense.models import (
            ExpenseCategory,
            ExpenseLine,
            PaymentMethod,
        )

        return ExpenseLine(
            id=self.id,
            report_id=self.report_id,
            line_number=self.line_number,
            expense_date=self.expense_date,
            category=ExpenseCategory(self.category),
            description=self.description,
            amount=self.amount,
            currency=self.currency,
            payment_method=PaymentMethod(self.payment_method),
            receipt_attached=self.receipt_attached,
            billable=self.billable,
            gl_account_code=self.gl_account_code,
            project_id=self.project_id,
            card_transaction_id=self.card_transaction_id,
            violation_notes=self.violation_notes,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ExpenseLineModel":
        from finance_modules.expense.models import ExpenseCategory, PaymentMethod

        return cls(
            id=dto.id,
            report_id=dto.report_id,
            line_number=dto.line_number,
            expense_date=dto.expense_date,
            category=dto.category.value if isinstance(dto.category, ExpenseCategory) else dto.category,
            description=dto.description,
            amount=dto.amount,
            currency=dto.currency,
            payment_method=dto.payment_method.value if isinstance(dto.payment_method, PaymentMethod) else dto.payment_method,
            receipt_attached=dto.receipt_attached,
            billable=dto.billable,
            gl_account_code=dto.gl_account_code,
            project_id=dto.project_id,
            card_transaction_id=dto.card_transaction_id,
            violation_notes=dto.violation_notes,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<ExpenseLineModel #{self.line_number} {self.category} {self.amount}>"


# ---------------------------------------------------------------------------
# ExpensePolicyModel
# ---------------------------------------------------------------------------


class ExpensePolicyModel(TrackedBase):
    """
    Category-level expense policy rules.

    Maps to the ``ExpensePolicy`` DTO in ``finance_modules.expense.models``.

    Guarantees:
        - ``category`` is unique across all policies.
        - Limit fields are optional (None means no limit).
    """

    __tablename__ = "expense_policies"

    __table_args__ = (
        UniqueConstraint("category", name="uq_expense_policy_category"),
    )

    category: Mapped[str] = mapped_column(String(50), nullable=False)
    daily_limit: Mapped[Decimal | None]
    per_transaction_limit: Mapped[Decimal | None]
    requires_receipt_above: Mapped[Decimal | None]
    requires_justification: Mapped[bool] = mapped_column(default=False)

    def to_dto(self):
        from finance_modules.expense.models import ExpensePolicy

        return ExpensePolicy(
            category=self.category,
            daily_limit=self.daily_limit,
            per_transaction_limit=self.per_transaction_limit,
            requires_receipt_above=self.requires_receipt_above,
            requires_justification=self.requires_justification,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ExpensePolicyModel":
        return cls(
            category=dto.category,
            daily_limit=dto.daily_limit,
            per_transaction_limit=dto.per_transaction_limit,
            requires_receipt_above=dto.requires_receipt_above,
            requires_justification=dto.requires_justification,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<ExpensePolicyModel {self.category}>"


# ---------------------------------------------------------------------------
# ReimbursementModel
# ---------------------------------------------------------------------------


class ReimbursementModel(TrackedBase):
    """
    An employee reimbursement payment for an approved expense report.

    Guarantees:
        - Links to the expense report that was reimbursed.
        - Records the employee, amount, and payment date.
    """

    __tablename__ = "expense_reimbursements"

    __table_args__ = (
        Index("idx_reimbursement_report", "report_id"),
        Index("idx_reimbursement_employee", "employee_id"),
        Index("idx_reimbursement_date", "payment_date"),
    )

    report_id: Mapped[UUID] = mapped_column(ForeignKey("expense_reports.id"), nullable=False)
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("parties.id"), nullable=False)
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(50), nullable=False, default="direct_deposit")
    payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    def to_dto(self) -> dict:
        return {
            "id": self.id,
            "report_id": self.report_id,
            "employee_id": self.employee_id,
            "amount": self.amount,
            "currency": self.currency,
            "payment_date": self.payment_date,
            "payment_method": self.payment_method,
            "payment_reference": self.payment_reference,
        }

    @classmethod
    def from_dto(cls, dto: dict, created_by_id: UUID) -> "ReimbursementModel":
        return cls(
            id=dto.get("id"),
            report_id=dto["report_id"],
            employee_id=dto["employee_id"],
            amount=dto["amount"],
            currency=dto.get("currency", "USD"),
            payment_date=dto["payment_date"],
            payment_method=dto.get("payment_method", "direct_deposit"),
            payment_reference=dto.get("payment_reference"),
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<ReimbursementModel report={self.report_id} {self.amount}>"
