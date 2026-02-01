"""
Intercompany Module Service (``finance_modules.intercompany.service``).

Responsibility
--------------
Orchestrates intercompany operations -- IC transfers, elimination entries,
transfer pricing adjustments with markup, IC reconciliation, and
consolidation -- by delegating journal persistence to
``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``IntercompanyService`` is the sole
public entry point for intercompany operations.  It composes the kernel
``ModulePostingService`` and pure calculation methods for markup,
reconciliation differences, and consolidation.

Invariants enforced
-------------------
* R7  -- Each public method owns the transaction boundary
          (``commit`` on success, ``rollback`` on failure or exception).
* R14 -- Event type selection is data-driven; no ``if/switch`` on
          event_type inside the posting path.
* L1  -- Account ROLES in profiles; COA resolution deferred to kernel.
* IC entries must always generate matching pairs across entities.

Failure modes
-------------
* Guard rejection or kernel validation  -> ``ModulePostingResult`` with
  ``is_success == False``; session rolled back.
* Unexpected exception  -> session rolled back, exception re-raised.
* Reconciliation mismatch  -> reported via ``ICReconciliationResult``.

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying entity IDs, transfer amounts, and
reconciliation statuses.  All journal entries feed the kernel audit chain
(R11).  IC eliminations must be fully traceable for consolidation.

Usage::

    service = IntercompanyService(session, role_resolver, clock)
    transaction, result = service.post_ic_transfer(
        from_entity="ENTITY_A", to_entity="ENTITY_B",
        amount=Decimal("10000.00"),
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.logging_config import get_logger
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
)
from finance_modules.intercompany.models import (
    ConsolidationResult,
    EliminationRule,
    ICReconciliationResult,
    ICTransaction,
    IntercompanyAgreement,
)

logger = get_logger(__name__)


class IntercompanyService:
    """
    Orchestrates intercompany accounting operations through kernel.

    Contract
    --------
    * Every posting method returns a tuple of ``(ICTransaction, ModulePostingResult)``;
      callers inspect ``result.is_success`` to determine outcome.
    * Non-posting helpers (``reconcile``, ``consolidate``, etc.) return
      pure domain objects with no side-effects on the journal.

    Guarantees
    ----------
    * Session is committed only on ``result.is_success``; otherwise rolled back.
    * Engine writes and journal writes share a single transaction
      (``ModulePostingService`` runs with ``auto_commit=False``).
    * Clock is injectable for deterministic testing.

    Non-goals
    ---------
    * Does NOT own account-code resolution (delegated to kernel via ROLES).
    * Does NOT enforce fiscal-period locks directly (kernel ``PeriodService``
      handles R12/R13).
    """

    def __init__(self, session, role_resolver, clock: Clock | None = None):
        self._session = session
        self._clock = clock or SystemClock()
        self._poster = ModulePostingService(
            session=session,
            role_resolver=role_resolver,
            clock=self._clock,
            auto_commit=False,
        )

    # =========================================================================
    # Transfers
    # =========================================================================

    def post_ic_transfer(
        self,
        from_entity: str,
        to_entity: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> tuple[ICTransaction, ModulePostingResult]:
        """
        Post an intercompany transfer between two entities.

        Posts via event_type="ic.transfer" using the ICTransferPosted profile.
        Dr INTERCOMPANY_DUE_FROM / Cr INTERCOMPANY_DUE_TO.

        Args:
            from_entity: Source entity identifier.
            to_entity: Destination entity identifier.
            amount: Transfer amount (Decimal, never float).
            effective_date: Accounting effective date.
            actor_id: ID of the actor initiating the transfer.
            currency: ISO 4217 currency code.
            description: Optional description for the transfer.

        Returns:
            Tuple of (ICTransaction, ModulePostingResult).
        """
        try:
            logger.info("ic_transfer_started", extra={
                "from_entity": from_entity,
                "to_entity": to_entity,
                "amount": str(amount),
                "currency": currency,
            })

            result = self._poster.post_event(
                event_type="ic.transfer",
                payload={
                    "from_entity": from_entity,
                    "to_entity": to_entity,
                    "description": description or "",
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ic_transfer_committed", extra={
                    "from_entity": from_entity,
                    "to_entity": to_entity,
                    "status": result.status.value,
                })
            else:
                self._session.rollback()

            transaction = ICTransaction(
                id=uuid4(),
                from_entity=from_entity,
                to_entity=to_entity,
                amount=amount,
                currency=currency,
                transaction_date=effective_date,
                description=description or "",
            )
            return transaction, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Eliminations
    # =========================================================================

    def generate_eliminations(
        self,
        period: str,
        entity_scope: str,
        elimination_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[ICTransaction, ModulePostingResult]:
        """
        Generate intercompany elimination entry for a period.

        Posts via event_type="ic.elimination" using the ICEliminationPosted profile.
        Dr INTERCOMPANY_DUE_TO / Cr INTERCOMPANY_DUE_FROM.

        Args:
            period: Fiscal period identifier (e.g., "2024-Q1").
            entity_scope: Scope of entities for elimination.
            elimination_amount: Total IC balance to eliminate.
            effective_date: Accounting effective date.
            actor_id: ID of the actor initiating the elimination.
            currency: ISO 4217 currency code.

        Returns:
            Tuple of (ICTransaction, ModulePostingResult).
        """
        try:
            logger.info("ic_elimination_started", extra={
                "period": period,
                "entity_scope": entity_scope,
                "amount": str(elimination_amount),
            })

            result = self._poster.post_event(
                event_type="ic.elimination",
                payload={
                    "entity_scope": entity_scope,
                    "period": period,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=elimination_amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ic_elimination_committed", extra={
                    "period": period,
                    "entity_scope": entity_scope,
                })
            else:
                self._session.rollback()

            transaction = ICTransaction(
                id=uuid4(),
                from_entity=entity_scope,
                to_entity=entity_scope,
                amount=elimination_amount,
                currency=currency,
                transaction_date=effective_date,
                description=f"IC elimination: {entity_scope}",
            )
            return transaction, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Reconciliation (Pure)
    # =========================================================================

    def reconcile_ic_balances(
        self,
        entity_a: str,
        entity_b: str,
        period: str,
        entity_a_balance: Decimal,
        entity_b_balance: Decimal,
    ) -> ICReconciliationResult:
        """
        Reconcile intercompany balances between two entities.

        Pure calculation -- no posting, no session interaction.
        Compares balances and determines if they are reconciled within tolerance.

        Args:
            entity_a: First entity identifier.
            entity_b: Second entity identifier.
            period: Fiscal period identifier.
            entity_a_balance: Entity A's recorded IC balance.
            entity_b_balance: Entity B's recorded IC balance.

        Returns:
            ICReconciliationResult with difference and reconciliation status.
        """
        difference = entity_a_balance - entity_b_balance
        is_reconciled = difference == Decimal("0")

        logger.info("ic_reconciliation_completed", extra={
            "entity_a": entity_a,
            "entity_b": entity_b,
            "period": period,
            "entity_a_balance": str(entity_a_balance),
            "entity_b_balance": str(entity_b_balance),
            "difference": str(difference),
            "is_reconciled": is_reconciled,
        })

        return ICReconciliationResult(
            entity_a=entity_a,
            entity_b=entity_b,
            period=period,
            entity_a_balance=entity_a_balance,
            entity_b_balance=entity_b_balance,
            difference=difference,
            is_reconciled=is_reconciled,
        )

    # =========================================================================
    # Consolidation (Pure)
    # =========================================================================

    def consolidate(
        self,
        entities: tuple[str, ...],
        period: str,
        entity_balances: dict[str, tuple[Decimal, Decimal]],
    ) -> ConsolidationResult:
        """
        Consolidate financial data across multiple entities for a period.

        Pure calculation -- no posting, no session interaction.
        Sums debits and credits across entities and calculates
        the elimination amount (sum of IC balances to eliminate).

        Args:
            entities: Tuple of entity identifiers to consolidate.
            period: Fiscal period identifier.
            entity_balances: Dict mapping entity -> (total_debits, total_credits).

        Returns:
            ConsolidationResult with totals and balance status.
        """
        total_debits = Decimal("0")
        total_credits = Decimal("0")
        elimination_amount = Decimal("0")

        for entity in entities:
            if entity in entity_balances:
                debits, credits = entity_balances[entity]
                total_debits += debits
                total_credits += credits
                # IC elimination amount is the lesser of debit/credit IC exposure
                elimination_amount += min(debits, credits)

        is_balanced = total_debits == total_credits

        logger.info("ic_consolidation_completed", extra={
            "entity_count": len(entities),
            "period": period,
            "total_debits": str(total_debits),
            "total_credits": str(total_credits),
            "elimination_amount": str(elimination_amount),
            "is_balanced": is_balanced,
        })

        return ConsolidationResult(
            entities=entities,
            period=period,
            total_debits=total_debits,
            total_credits=total_credits,
            elimination_amount=elimination_amount,
            is_balanced=is_balanced,
        )

    # =========================================================================
    # Balance Query (Pure)
    # =========================================================================

    def get_ic_balance(
        self,
        entity_a: str,
        entity_b: str,
        transactions: list[ICTransaction],
    ) -> Decimal:
        """
        Calculate the net intercompany balance between two entities.

        Pure query -- no session interaction, no posting.
        Sums the net of transactions flowing between the two entities.

        Args:
            entity_a: First entity identifier.
            entity_b: Second entity identifier.
            transactions: List of ICTransaction instances to sum.

        Returns:
            Net balance (positive = entity_a owes entity_b).
        """
        balance = Decimal("0")
        for txn in transactions:
            if txn.from_entity == entity_a and txn.to_entity == entity_b:
                balance += txn.amount
            elif txn.from_entity == entity_b and txn.to_entity == entity_a:
                balance -= txn.amount
        return balance

    # =========================================================================
    # Elimination Report (Pure)
    # =========================================================================

    def get_elimination_report(
        self,
        period: str,
        eliminations: list[ICTransaction],
    ) -> dict:
        """
        Generate an elimination report for a period.

        Pure query -- no session interaction, no posting.

        Args:
            period: Fiscal period identifier.
            eliminations: List of elimination ICTransaction instances.

        Returns:
            Summary dict with period, total_eliminations, count, and entries.
        """
        total = sum(e.amount for e in eliminations)

        entries = [
            {
                "id": str(e.id),
                "from_entity": e.from_entity,
                "to_entity": e.to_entity,
                "amount": str(e.amount),
                "description": e.description,
            }
            for e in eliminations
        ]

        logger.info("ic_elimination_report_generated", extra={
            "period": period,
            "total_eliminations": str(total),
            "count": len(eliminations),
        })

        return {
            "period": period,
            "total_eliminations": total,
            "count": len(eliminations),
            "entries": entries,
        }

    # =========================================================================
    # Transfer Pricing
    # =========================================================================

    def post_transfer_pricing_adjustment(
        self,
        agreement_id: UUID,
        from_entity: str,
        to_entity: str,
        base_amount: Decimal,
        markup_rate: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[ICTransaction, ModulePostingResult]:
        """
        Post a transfer pricing adjustment.

        Calculates markup = base_amount * markup_rate and posts via
        event_type="ic.transfer_pricing" using the ICTransferPricing profile.
        Dr INTERCOMPANY_DUE_FROM / Cr INTERCOMPANY_DUE_TO (with markup amount).

        Args:
            agreement_id: ID of the governing intercompany agreement.
            from_entity: Source entity identifier.
            to_entity: Destination entity identifier.
            base_amount: Base transaction amount before markup.
            markup_rate: Markup rate as a Decimal (e.g., Decimal("0.10") for 10%).
            effective_date: Accounting effective date.
            actor_id: ID of the actor initiating the adjustment.
            currency: ISO 4217 currency code.

        Returns:
            Tuple of (ICTransaction, ModulePostingResult).
        """
        markup = base_amount * markup_rate

        try:
            logger.info("ic_transfer_pricing_started", extra={
                "agreement_id": str(agreement_id),
                "from_entity": from_entity,
                "to_entity": to_entity,
                "base_amount": str(base_amount),
                "markup_rate": str(markup_rate),
                "markup": str(markup),
            })

            result = self._poster.post_event(
                event_type="ic.transfer_pricing",
                payload={
                    "agreement_id": str(agreement_id),
                    "from_entity": from_entity,
                    "to_entity": to_entity,
                    "base_amount": str(base_amount),
                    "markup_rate": str(markup_rate),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=markup,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ic_transfer_pricing_committed", extra={
                    "agreement_id": str(agreement_id),
                    "markup": str(markup),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()

            transaction = ICTransaction(
                id=uuid4(),
                agreement_id=agreement_id,
                from_entity=from_entity,
                to_entity=to_entity,
                amount=markup,
                currency=currency,
                transaction_date=effective_date,
                description=f"Transfer pricing adjustment: {agreement_id}",
            )
            return transaction, result

        except Exception:
            self._session.rollback()
            raise
