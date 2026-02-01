"""
SQLAlchemy ORM persistence models for period close orchestration.

Responsibility
--------------
Provide database-backed persistence for period close artifacts:
``PeriodCloseRunModel`` tracks each close attempt with correlation ID
and phase progression, and ``CloseCertificateModel`` records the
immutable attestation of a completed period close.

Architecture position
---------------------
**Services layer** -- ORM models consumed by ``PeriodCloseOrchestrator``
for persistence of close runs and certificates.  Inherits from
``TrackedBase`` (kernel db layer).

Invariants enforced
-------------------
* R24 (canonical ledger hash): ``CloseCertificateModel`` records the
  deterministic ledger hash computed at close time.
* R25 (close lock): ``PeriodCloseRunModel`` records the correlation_id
  used for close-lock tracking.
* All monetary fields use ``Decimal`` (Numeric(38,9)) -- NEVER float.
* Enum fields stored as String(50) for readability and portability.
* ``CloseCertificateModel`` is effectively immutable once created
  (represents a signed financial attestation).

Audit relevance
---------------
* ``PeriodCloseRunModel`` provides a full audit trail of every close
  attempt, including status, phases completed, and who started/completed it.
* ``CloseCertificateModel`` is the immutable attestation of period close,
  recording the ledger hash (R24), trial balance snapshot, subledger
  close status, and the certifying actor.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import TrackedBase

# ---------------------------------------------------------------------------
# PeriodCloseRunModel
# ---------------------------------------------------------------------------


class PeriodCloseRunModel(TrackedBase):
    """
    Auditable record of a period close attempt.

    Maps to the ``PeriodCloseRun`` DTO in ``finance_services._close_types``.

    Guarantees:
        - ``correlation_id`` is unique per close run (used for log correlation).
        - Tracks the full lifecycle: IN_PROGRESS -> COMPLETED / FAILED / CANCELLED.
        - Records the actor who initiated and the fiscal year/period.
    """

    __tablename__ = "period_close_runs"

    __table_args__ = (
        UniqueConstraint("correlation_id", name="uq_close_run_correlation"),
        Index("idx_close_run_period", "period_code"),
        Index("idx_close_run_status", "status"),
        Index("idx_close_run_fiscal_year", "fiscal_year"),
        Index("idx_close_run_started_at", "started_at"),
    )

    period_code: Mapped[str] = mapped_column(String(20), nullable=False)
    fiscal_year: Mapped[int]
    is_year_end: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="in_progress")
    current_phase: Mapped[int] = mapped_column(default=0)
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_by: Mapped[UUID]
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ledger_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    certificate_id: Mapped[UUID | None]
    phases_completed: Mapped[int] = mapped_column(default=0)
    phases_skipped: Mapped[int] = mapped_column(default=0)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_dto(self):
        from finance_services._close_types import CloseRunStatus, PeriodCloseRun

        return PeriodCloseRun(
            id=self.id,
            period_code=self.period_code,
            fiscal_year=self.fiscal_year,
            is_year_end=self.is_year_end,
            status=CloseRunStatus(self.status),
            current_phase=self.current_phase,
            correlation_id=self.correlation_id,
            started_at=self.started_at,
            started_by=self.started_by,
            completed_at=self.completed_at,
            ledger_hash=self.ledger_hash,
            certificate_id=self.certificate_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "PeriodCloseRunModel":
        from finance_services._close_types import CloseRunStatus

        return cls(
            id=dto.id,
            period_code=dto.period_code,
            fiscal_year=dto.fiscal_year,
            is_year_end=dto.is_year_end,
            status=dto.status.value if isinstance(dto.status, CloseRunStatus) else dto.status,
            current_phase=dto.current_phase,
            correlation_id=dto.correlation_id,
            started_at=dto.started_at,
            started_by=dto.started_by,
            completed_at=dto.completed_at,
            ledger_hash=dto.ledger_hash,
            certificate_id=dto.certificate_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<PeriodCloseRunModel {self.period_code} [{self.status}] "
            f"corr={self.correlation_id[:8]}...>"
        )


# ---------------------------------------------------------------------------
# CloseCertificateModel
# ---------------------------------------------------------------------------


class CloseCertificateModel(TrackedBase):
    """
    Immutable attestation of a completed period close.

    Maps to the ``CloseCertificate`` DTO in ``finance_services._close_types``.

    This model records the definitive proof that a period was properly
    closed, including the canonical ledger hash (R24), trial balance
    snapshot, and the actors involved.

    Guarantees:
        - ``period_code`` + ``correlation_id`` is unique (one certificate
          per close run).
        - ``ledger_hash`` records the R24 canonical hash at close time.
        - ``trial_balance_debits`` and ``trial_balance_credits`` capture
          the final trial balance.
        - Records subledgers closed, adjustments posted, and closing
          entries posted for complete audit trail.
    """

    __tablename__ = "close_certificates"

    __table_args__ = (
        UniqueConstraint(
            "period_code", "correlation_id",
            name="uq_close_cert_period_correlation",
        ),
        Index("idx_close_cert_period", "period_code"),
        Index("idx_close_cert_closed_at", "closed_at"),
        Index("idx_close_cert_closed_by", "closed_by"),
    )

    period_code: Mapped[str] = mapped_column(String(20), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_by: Mapped[UUID]
    approved_by: Mapped[UUID | None]
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    ledger_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    trial_balance_debits: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    trial_balance_credits: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    subledgers_closed_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    adjustments_posted: Mapped[int] = mapped_column(default=0)
    closing_entries_posted: Mapped[int] = mapped_column(default=0)
    phases_completed: Mapped[int] = mapped_column(default=0)
    phases_skipped: Mapped[int] = mapped_column(default=0)
    audit_event_id: Mapped[UUID | None]

    def to_dto(self):
        import json

        from finance_services._close_types import CloseCertificate

        subledgers = ()
        if self.subledgers_closed_json:
            subledgers = tuple(json.loads(self.subledgers_closed_json))

        return CloseCertificate(
            id=self.id,
            period_code=self.period_code,
            closed_at=self.closed_at,
            closed_by=self.closed_by,
            approved_by=self.approved_by,
            correlation_id=self.correlation_id,
            ledger_hash=self.ledger_hash,
            trial_balance_debits=self.trial_balance_debits,
            trial_balance_credits=self.trial_balance_credits,
            subledgers_closed=subledgers,
            adjustments_posted=self.adjustments_posted,
            closing_entries_posted=self.closing_entries_posted,
            phases_completed=self.phases_completed,
            phases_skipped=self.phases_skipped,
            audit_event_id=self.audit_event_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "CloseCertificateModel":
        import json

        subledgers_json = None
        if dto.subledgers_closed:
            subledgers_json = json.dumps(list(dto.subledgers_closed))

        return cls(
            id=dto.id,
            period_code=dto.period_code,
            closed_at=dto.closed_at,
            closed_by=dto.closed_by,
            approved_by=dto.approved_by,
            correlation_id=dto.correlation_id,
            ledger_hash=dto.ledger_hash,
            trial_balance_debits=dto.trial_balance_debits,
            trial_balance_credits=dto.trial_balance_credits,
            subledgers_closed_json=subledgers_json,
            adjustments_posted=dto.adjustments_posted,
            closing_entries_posted=dto.closing_entries_posted,
            phases_completed=dto.phases_completed,
            phases_skipped=dto.phases_skipped,
            audit_event_id=dto.audit_event_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<CloseCertificateModel {self.period_code} "
            f"hash={self.ledger_hash[:12]}... "
            f"closed_at={self.closed_at}>"
        )
