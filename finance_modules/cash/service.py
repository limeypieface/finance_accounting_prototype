"""
Cash Management Service - Orchestrates cash operations via engines + kernel.

Thin glue layer that:
1. Calls ReconciliationManager for bank reconciliation
2. Calls MatchingEngine for bank statement matching
3. Calls LinkGraphService for tracking cash movement links
4. Calls ModulePostingService for journal entry creation

All computation lives in engines. All posting lives in kernel.
This service owns the transaction boundary (R7 compliance).

Usage:
    service = CashService(session, role_resolver, clock)
    result = service.record_receipt(
        receipt_id=uuid4(), amount=Decimal("5000.00"),
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
    ModulePostingStatus,
)
from finance_services.reconciliation_service import ReconciliationManager
from finance_engines.matching import MatchingEngine

logger = get_logger("modules.cash.service")


class CashService:
    """
    Orchestrates cash management operations through engines and kernel.

    Engine composition:
    - ReconciliationManager: bank reconciliation and payment matching
    - MatchingEngine: bank statement line matching
    - LinkGraphService: cash movement link tracking

    Transaction boundary: this service commits on success, rolls back on failure.
    ModulePostingService runs with auto_commit=False so all engine writes
    (links, reconciliation state) and journal writes share a single transaction.
    """

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()

        # Kernel posting (auto_commit=False -- we own the boundary)
        self._poster = ModulePostingService(
            session=session,
            role_resolver=role_resolver,
            clock=self._clock,
            auto_commit=False,
        )

        # Stateful engines (share session for atomicity)
        self._link_graph = LinkGraphService(session)
        self._reconciliation = ReconciliationManager(session, self._link_graph)

        # Stateless engines
        self._matching = MatchingEngine()

    # =========================================================================
    # Receipts
    # =========================================================================

    def record_receipt(
        self,
        receipt_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        bank_account_code: str | None = None,
        payer_name: str | None = None,
        reference: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a cash receipt (deposit).

        Engine: LinkGraphService for tracking the cash receipt artifact.
        Profile: cash.deposit -> CashDeposit
        """
        try:
            logger.info("cash_receipt_started", extra={
                "receipt_id": str(receipt_id),
                "amount": str(amount),
                "bank_account_code": bank_account_code,
            })

            payload: dict[str, Any] = {
                "amount": str(amount),
                "bank_account_code": bank_account_code,
                "payer_name": payer_name,
                "reference": reference,
            }

            result = self._poster.post_event(
                event_type="cash.deposit",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Disbursements
    # =========================================================================

    def record_disbursement(
        self,
        disbursement_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        destination_type: str = "EXPENSE",
        currency: str = "USD",
        bank_account_code: str | None = None,
        payee_name: str | None = None,
        reference: str | None = None,
        cost_center: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a cash disbursement (withdrawal).

        Engine: LinkGraphService for tracking the cash disbursement artifact.
        Profile: cash.withdrawal -> CashWithdrawalExpense / CashWithdrawalSupplier /
                 CashWithdrawalPayroll (where-clause dispatch on destination_type)
        """
        try:
            logger.info("cash_disbursement_started", extra={
                "disbursement_id": str(disbursement_id),
                "amount": str(amount),
                "destination_type": destination_type,
                "bank_account_code": bank_account_code,
            })

            payload: dict[str, Any] = {
                "amount": str(amount),
                "destination_type": destination_type,
                "bank_account_code": bank_account_code,
                "payee_name": payee_name,
                "reference": reference,
                "cost_center": cost_center,
            }

            result = self._poster.post_event(
                event_type="cash.withdrawal",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Bank Fees
    # =========================================================================

    def record_bank_fee(
        self,
        fee_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        bank_account_code: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a bank service charge or fee.

        Profile: cash.bank_fee -> CashBankFee
        """
        try:
            logger.info("cash_bank_fee_started", extra={
                "fee_id": str(fee_id),
                "amount": str(amount),
                "bank_account_code": bank_account_code,
            })

            payload: dict[str, Any] = {
                "amount": str(amount),
                "bank_account_code": bank_account_code,
            }

            result = self._poster.post_event(
                event_type="cash.bank_fee",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Interest Income
    # =========================================================================

    def record_interest_earned(
        self,
        interest_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        bank_account_code: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record interest income on a bank balance.

        Profile: cash.interest_earned -> CashInterestEarned
        """
        try:
            logger.info("cash_interest_earned_started", extra={
                "interest_id": str(interest_id),
                "amount": str(amount),
                "bank_account_code": bank_account_code,
            })

            payload: dict[str, Any] = {
                "amount": str(amount),
                "bank_account_code": bank_account_code,
            }

            result = self._poster.post_event(
                event_type="cash.interest_earned",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Inter-account Transfer
    # =========================================================================

    def record_transfer(
        self,
        transfer_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        from_bank_account_code: str | None = None,
        to_bank_account_code: str | None = None,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record an inter-account bank transfer.

        Profile: cash.transfer -> CashTransfer
        """
        try:
            logger.info("cash_transfer_started", extra={
                "transfer_id": str(transfer_id),
                "amount": str(amount),
                "from_bank_account_code": from_bank_account_code,
                "to_bank_account_code": to_bank_account_code,
            })

            payload: dict[str, Any] = {
                "amount": str(amount),
                "from_bank_account_code": from_bank_account_code,
                "to_bank_account_code": to_bank_account_code,
            }

            result = self._poster.post_event(
                event_type="cash.transfer",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Wire Transfer Out
    # =========================================================================

    def record_wire_transfer_out(
        self,
        wire_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        bank_account_code: str | None = None,
        beneficiary_name: str | None = None,
        reference: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record an outbound wire transfer (uses cash-in-transit account).

        Profile: cash.wire_transfer_out -> CashWireTransferOut
        """
        try:
            logger.info("cash_wire_transfer_out_started", extra={
                "wire_id": str(wire_id),
                "amount": str(amount),
                "bank_account_code": bank_account_code,
            })

            payload: dict[str, Any] = {
                "amount": str(amount),
                "bank_account_code": bank_account_code,
                "beneficiary_name": beneficiary_name,
                "reference": reference,
            }

            result = self._poster.post_event(
                event_type="cash.wire_transfer_out",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Wire Transfer Cleared
    # =========================================================================

    def record_wire_transfer_cleared(
        self,
        wire_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        bank_account_code: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a wire transfer confirmed by receiving bank.

        Profile: cash.wire_transfer_cleared -> CashWireTransferCleared
        """
        try:
            logger.info("cash_wire_transfer_cleared_started", extra={
                "wire_id": str(wire_id),
                "amount": str(amount),
                "bank_account_code": bank_account_code,
            })

            payload: dict[str, Any] = {
                "amount": str(amount),
                "bank_account_code": bank_account_code,
            }

            result = self._poster.post_event(
                event_type="cash.wire_transfer_cleared",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Bank Reconciliation
    # =========================================================================

    def reconcile_bank_statement(
        self,
        statement_id: UUID,
        entries: Sequence[dict[str, Any]],
        effective_date: date,
        actor_id: UUID,
        bank_account_code: str | None = None,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Reconcile a bank statement and post adjustment entries.

        Engine: ReconciliationManager for matching and state derivation.
        Engine: LinkGraphService for MATCHED_WITH link creation.
        Profile: cash.reconciliation -> CashReconciliation

        Each entry in entries is a dict with:
            - line_id: str or UUID
            - amount: str or Decimal (adjustment amount)
            - gl_ref_id: UUID (matched GL transaction, optional)

        The service posts a single reconciliation adjustment for the net
        variance discovered during reconciliation.
        """
        try:
            logger.info("bank_reconciliation_started", extra={
                "statement_id": str(statement_id),
                "entry_count": len(entries),
                "bank_account_code": bank_account_code,
            })

            statement_ref = ArtifactRef(ArtifactType.BANK_STATEMENT, statement_id)

            # Track matched entries via link graph
            net_adjustment = Decimal("0")
            matched_count = 0

            for entry in entries:
                entry_amount = Decimal(str(entry.get("amount", "0")))
                net_adjustment += entry_amount

                gl_ref_id = entry.get("gl_ref_id")
                if gl_ref_id is not None:
                    from finance_kernel.domain.economic_link import (
                        EconomicLink,
                        LinkType,
                    )
                    from datetime import datetime

                    gl_ref = ArtifactRef(ArtifactType.JOURNAL_ENTRY, UUID(str(gl_ref_id)))
                    link = EconomicLink.create(
                        link_id=UUID(str(entry.get("line_id", __import__("uuid").uuid4()))),
                        link_type=LinkType.MATCHED_WITH,
                        parent_ref=statement_ref,
                        child_ref=gl_ref,
                        creating_event_id=statement_id,
                        created_at=datetime.utcnow(),
                        metadata={
                            "amount": str(entry_amount),
                            "currency": currency,
                        },
                    )
                    self._link_graph.establish_link(link, allow_duplicate=True)
                    matched_count += 1

            logger.info("bank_reconciliation_links_created", extra={
                "statement_id": str(statement_id),
                "matched_count": matched_count,
                "net_adjustment": str(net_adjustment),
            })

            # Post reconciliation adjustment if non-zero
            if net_adjustment == Decimal("0"):
                # No adjustment needed -- commit link graph changes only
                self._session.commit()
                return ModulePostingResult(
                    status=ModulePostingStatus.POSTED,
                    event_id=statement_id,
                    message="Bank reconciliation matched; no adjustment required",
                )

            payload: dict[str, Any] = {
                "amount": str(abs(net_adjustment)),
                "statement_id": str(statement_id),
                "bank_account_code": bank_account_code,
                "entry_count": len(entries),
                "matched_count": matched_count,
                "adjustment_direction": "debit" if net_adjustment > 0 else "credit",
            }

            result = self._poster.post_event(
                event_type="cash.reconciliation",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=abs(net_adjustment),
                currency=currency,
                description=description or f"Bank reconciliation adjustment for statement {statement_id}",
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise
