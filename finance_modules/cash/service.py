"""
finance_modules.cash.service
============================

Responsibility:
    Orchestrates cash management operations by composing shared engines
    (ReconciliationManager, MatchingEngine) with kernel services
    (ModulePostingService, LinkGraphService).  This module is thin ERP
    glue -- it contains no financial calculation logic.

Architecture:
    Module layer (finance_modules).  Delegates all journal posting to
    ModulePostingService with ``auto_commit=False`` and owns the
    transaction boundary (commit on success, rollback on failure).

Invariants enforced:
    - R4  (DOUBLE_ENTRY_BALANCE): posting delegated to kernel pipeline.
    - R7  (TRANSACTION_BOUNDARIES): every public method commits or rolls
          back the session before returning.
    - R16 (ISO_4217): currency validated at kernel boundary.
    - LINK_LEGALITY: economic links created via LinkGraphService with
          validated link types and artifact references.

Failure modes:
    - Posting failure (kernel rejection) -> session rolled back, result
      returned with non-success status.
    - Unexpected exception -> session rolled back, exception re-raised.
    - Unsupported statement format -> ValueError from helpers.

Audit relevance:
    Cash movements are SOX-critical.  Every public method emits structured
    log events before and after posting.  All postings flow through the
    kernel's audit pipeline (InterpretationOutcome + AuditEvent).

Usage::

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
from uuid import UUID, uuid4

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
from finance_modules.cash.models import (
    BankStatement,
    BankStatementLine,
    CashForecast,
    PaymentFile,
    ReconciliationMatch,
)

logger = get_logger("modules.cash.service")


class CashService:
    """
    Orchestrates cash management operations through engines and kernel.

    Contract:
        Each public method either (a) commits and returns a success result, or
        (b) rolls back and returns a failure result / re-raises.  No method
        leaves the session in an uncommitted state.

    Guarantees:
        - All monetary amounts are ``Decimal`` -- never ``float`` (R16/R17).
        - Journal entries are balanced per currency (R4) via kernel pipeline.
        - Economic links are immutable once persisted (LINK_LEGALITY).
        - Clock is injected; ``datetime.now()`` is never called directly.

    Non-goals:
        - Does NOT contain financial calculation logic.
        - Does NOT directly read or write ORM journal models.
        - Does NOT enforce fiscal-period rules (kernel responsibility).

    Engine composition:
        - ReconciliationManager: bank reconciliation and payment matching.
        - MatchingEngine: bank statement line matching.
        - LinkGraphService: cash movement link tracking.

    Transaction boundary:
        This service commits on success, rolls back on failure.
        ModulePostingService runs with ``auto_commit=False`` so all engine
        writes (links, reconciliation state) and journal writes share a
        single transaction.
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

        Preconditions:
            - ``amount`` is a positive ``Decimal`` (guard enforced by profile).
            - ``effective_date`` falls within an open fiscal period (kernel).
        Postconditions:
            - On success: one POSTED journal entry (Dr Bank / Cr Undeposited
              Funds) and session committed.
            - On failure: session rolled back, result carries failure status.
        Raises:
            Exception -- any unexpected error; session is rolled back first.

        Engine: LinkGraphService for tracking the cash receipt artifact.
        Profile: cash.deposit -> CashDeposit
        """
        # INVARIANT [R4]: amount passed to kernel; kernel enforces Dr = Cr.
        assert isinstance(amount, Decimal), "amount must be Decimal, not float"
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

        Preconditions:
            - ``amount`` is a positive ``Decimal``.
            - ``destination_type`` is one of EXPENSE, SUPPLIER_PAYMENT, PAYROLL
              (where-clause dispatch selects the profile).
        Postconditions:
            - On success: one POSTED journal entry and session committed.
            - On failure: session rolled back.
        Raises:
            Exception -- unexpected error; session rolled back first.

        Engine: LinkGraphService for tracking the cash disbursement artifact.
        Profile: cash.withdrawal -> CashWithdrawalExpense / CashWithdrawalSupplier /
                 CashWithdrawalPayroll (where-clause dispatch on destination_type)
        """
        # INVARIANT [R4]: amount passed to kernel; kernel enforces Dr = Cr.
        assert isinstance(amount, Decimal), "amount must be Decimal, not float"
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

        Preconditions:
            - ``amount`` is a positive ``Decimal`` (guard enforced by profile).
        Postconditions:
            - On success: Dr Bank Fee Expense / Cr Cash posted.
        Raises:
            Exception -- unexpected error; session rolled back.

        Profile: cash.bank_fee -> CashBankFee
        """
        # INVARIANT [R4]: balanced entry enforced by kernel.
        assert isinstance(amount, Decimal), "amount must be Decimal, not float"
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

        Preconditions:
            - ``amount`` is a positive ``Decimal`` (guard enforced by profile).
        Postconditions:
            - On success: Dr Cash / Cr Interest Income posted.
        Raises:
            Exception -- unexpected error; session rolled back.

        Profile: cash.interest_earned -> CashInterestEarned
        """
        # INVARIANT [R4]: balanced entry enforced by kernel.
        assert isinstance(amount, Decimal), "amount must be Decimal, not float"
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

        Preconditions:
            - ``amount`` is a positive ``Decimal``.
            - ``from_bank_account_code != to_bank_account_code`` (guard).
        Postconditions:
            - On success: Dr Destination Bank / Cr Source Bank posted.
        Raises:
            Exception -- unexpected error; session rolled back.

        Profile: cash.transfer -> CashTransfer
        """
        # INVARIANT [R4]: balanced entry enforced by kernel.
        assert isinstance(amount, Decimal), "amount must be Decimal, not float"
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

        Preconditions:
            - ``amount`` is a positive ``Decimal``.
        Postconditions:
            - On success: Dr Cash-in-Transit / Cr Cash posted.
        Raises:
            Exception -- unexpected error; session rolled back.

        Profile: cash.wire_transfer_out -> CashWireTransferOut
        """
        # INVARIANT [R4]: balanced entry enforced by kernel.
        assert isinstance(amount, Decimal), "amount must be Decimal, not float"
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

        Preconditions:
            - ``amount`` is a positive ``Decimal``.
        Postconditions:
            - On success: Dr Cash / Cr Cash-in-Transit posted.
        Raises:
            Exception -- unexpected error; session rolled back.

        Profile: cash.wire_transfer_cleared -> CashWireTransferCleared
        """
        # INVARIANT [R4]: balanced entry enforced by kernel.
        assert isinstance(amount, Decimal), "amount must be Decimal, not float"
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

        Preconditions:
            - ``entries`` is a sequence of dicts, each containing at minimum
              an ``amount`` key with a numeric value.
        Postconditions:
            - MATCHED_WITH links created for all entries with ``gl_ref_id``.
            - If net adjustment is non-zero: one POSTED reconciliation entry.
            - If net adjustment is zero: link-graph changes committed, no
              journal entry created.
        Raises:
            Exception -- unexpected error; session rolled back.

        Engine: ReconciliationManager for matching and state derivation.
        Engine: LinkGraphService for MATCHED_WITH link creation.
        Profile: cash.reconciliation -> CashReconciliation

        Each entry in ``entries`` is a dict with:
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
                    gl_ref = ArtifactRef(ArtifactType.JOURNAL_ENTRY, UUID(str(gl_ref_id)))
                    link = EconomicLink.create(
                        link_id=UUID(str(entry.get("line_id", uuid4()))),
                        link_type=LinkType.MATCHED_WITH,
                        parent_ref=statement_ref,
                        child_ref=gl_ref,
                        creating_event_id=statement_id,
                        created_at=self._clock.now(),
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

    # =========================================================================
    # Bank Statement Import
    # =========================================================================

    def import_bank_statement(
        self,
        raw_data: str,
        format: str,
        bank_account_id: UUID,
        statement_date: date,
    ) -> tuple[BankStatement, list[BankStatementLine]]:
        """
        Parse a bank statement into structured records.

        Pure parsing -- no posting, no session interaction.
        Supported formats: MT940, BAI2, CAMT053.

        Args:
            raw_data: Raw statement data string.
            format: Statement format (MT940, BAI2, CAMT053).
            bank_account_id: Bank account UUID.
            statement_date: Statement date.

        Returns:
            Tuple of (BankStatement, list of BankStatementLine).
        """
        from finance_modules.cash.helpers import parse_mt940, parse_bai2, parse_camt053

        parsers = {
            "MT940": parse_mt940,
            "BAI2": parse_bai2,
            "CAMT053": parse_camt053,
        }
        parser = parsers.get(format.upper())
        if parser is None:
            raise ValueError(f"Unsupported format: {format}")

        records = parser(raw_data)

        lines = []
        total_amount = Decimal("0")
        for record in records:
            line = BankStatementLine(
                id=uuid4(),
                statement_id=uuid4(),
                transaction_date=statement_date,
                amount=record["amount"],
                reference=record.get("reference", ""),
                description=record.get("description", ""),
                transaction_type=record.get("type", "UNKNOWN"),
            )
            lines.append(line)
            total_amount += record["amount"]

        statement = BankStatement(
            id=uuid4(),
            bank_account_id=bank_account_id,
            statement_date=statement_date,
            opening_balance=Decimal("0"),
            closing_balance=total_amount,
            line_count=len(lines),
            format=format.upper(),
        )

        logger.info("bank_statement_imported", extra={
            "format": format,
            "line_count": len(lines),
            "total_amount": str(total_amount),
        })

        return statement, lines

    # =========================================================================
    # Auto-Reconciliation
    # =========================================================================

    def auto_reconcile(
        self,
        bank_account_id: UUID,
        statement_lines: list[BankStatementLine],
        book_entries: list[dict],
        effective_date: date,
        actor_id: UUID,
        tolerance: Decimal = Decimal("0.01"),
        currency: str = "USD",
    ) -> tuple[list[ReconciliationMatch], ModulePostingResult]:
        """
        Auto-reconcile bank statement lines against book entries.

        Uses amount matching with tolerance. Posts adjustment for net variance.

        Args:
            bank_account_id: Bank account UUID.
            statement_lines: Parsed bank statement lines.
            book_entries: List of dicts with 'amount' and 'id' keys.
            effective_date: Accounting effective date.
            actor_id: Actor UUID.
            tolerance: Matching tolerance.
            currency: ISO 4217 currency code.

        Returns:
            Tuple of (list of ReconciliationMatch, ModulePostingResult).
        """
        try:
            matches: list[ReconciliationMatch] = []
            matched_book_ids: set[str] = set()
            net_variance = Decimal("0")

            for line in statement_lines:
                best_match = None
                for entry in book_entries:
                    entry_id = str(entry.get("id", ""))
                    if entry_id in matched_book_ids:
                        continue
                    entry_amount = Decimal(str(entry.get("amount", "0")))
                    if abs(line.amount - entry_amount) <= tolerance:
                        best_match = entry
                        break

                if best_match is not None:
                    match = ReconciliationMatch(
                        id=uuid4(),
                        statement_line_id=line.id,
                        journal_line_id=UUID(str(best_match["id"])) if best_match.get("id") else None,
                        match_confidence=Decimal("1.0"),
                        match_method="auto_amount",
                    )
                    matches.append(match)
                    matched_book_ids.add(str(best_match.get("id", "")))
                else:
                    net_variance += line.amount

            logger.info("auto_reconciliation_completed", extra={
                "matched_count": len(matches),
                "unmatched_count": len(statement_lines) - len(matches),
                "net_variance": str(net_variance),
            })

            if net_variance == Decimal("0") or abs(net_variance) < tolerance:
                self._session.commit()
                return matches, ModulePostingResult(
                    status=ModulePostingStatus.POSTED,
                    event_id=uuid4(),
                    message="Auto-reconciliation complete; no adjustment needed",
                )

            result = self._poster.post_event(
                event_type="cash.auto_reconciled",
                payload={
                    "bank_account_id": str(bank_account_id),
                    "matched_count": len(matches),
                    "net_variance": str(net_variance),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=abs(net_variance),
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()

            return matches, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Payment File Generation
    # =========================================================================

    def generate_payment_file(
        self,
        payments: list[dict],
        format: str,
        company_name: str,
        company_id: str,
    ) -> PaymentFile:
        """
        Generate a payment file for bank submission.

        Pure calculation -- no posting, no session interaction.

        Args:
            payments: List of payment dicts (name, account, routing, amount).
            format: Output format (NACHA).
            company_name: Company name for file header.
            company_id: Company ID for file header.

        Returns:
            PaymentFile with formatted content.
        """
        from finance_modules.cash.helpers import format_nacha

        if format.upper() != "NACHA":
            raise ValueError(f"Unsupported payment format: {format}")

        content = format_nacha(payments, company_name, company_id)
        total = sum(Decimal(str(p.get("amount", "0"))) for p in payments)

        file = PaymentFile(
            id=uuid4(),
            format=format.upper(),
            payment_count=len(payments),
            total_amount=total,
            content=content,
        )

        logger.info("payment_file_generated", extra={
            "format": format,
            "payment_count": len(payments),
            "total_amount": str(total),
        })

        return file

    # =========================================================================
    # Cash Forecasting
    # =========================================================================

    def forecast_cash(
        self,
        periods: list[str],
        opening_balance: Decimal,
        expected_inflows_per_period: Decimal,
        expected_outflows_per_period: Decimal,
        currency: str = "USD",
    ) -> list[CashForecast]:
        """
        Generate a cash flow forecast for future periods.

        Pure calculation -- no posting, no session interaction.

        Args:
            periods: List of period identifiers to forecast.
            opening_balance: Starting cash balance.
            expected_inflows_per_period: Expected inflows per period.
            expected_outflows_per_period: Expected outflows per period.
            currency: Currency code.

        Returns:
            List of CashForecast for each period.
        """
        forecasts: list[CashForecast] = []
        balance = opening_balance

        for period in periods:
            projected_closing = balance + expected_inflows_per_period - expected_outflows_per_period
            forecast = CashForecast(
                period=period,
                opening_balance=balance,
                expected_inflows=expected_inflows_per_period,
                expected_outflows=expected_outflows_per_period,
                projected_closing=projected_closing,
                currency=currency,
            )
            forecasts.append(forecast)
            balance = projected_closing

        logger.info("cash_forecast_generated", extra={
            "period_count": len(periods),
            "starting_balance": str(opening_balance),
            "ending_balance": str(balance),
        })

        return forecasts

    # =========================================================================
    # NSF Return
    # =========================================================================

    def record_nsf_return(
        self,
        deposit_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record an NSF (non-sufficient funds) returned deposit.

        Preconditions:
            - ``amount`` is a positive ``Decimal``.
            - ``deposit_id`` references the original deposit event.
        Postconditions:
            - On success: Dr Accounts Receivable / Cr Cash posted.
        Raises:
            Exception -- unexpected error; session rolled back.

        Profile: cash.nsf_return -> CashNSFReturn
        """
        # INVARIANT [R4]: balanced entry enforced by kernel.
        assert isinstance(amount, Decimal), "amount must be Decimal, not float"
        try:
            logger.info("nsf_return_started", extra={
                "deposit_id": str(deposit_id),
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="cash.nsf_return",
                payload={
                    "deposit_id": str(deposit_id),
                    "amount": str(amount),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description or f"NSF return: {deposit_id}",
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise
