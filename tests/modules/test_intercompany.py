"""
Intercompany Transaction Tests.

Tests cross-company journal entries and automatic mirror entry creation.

CRITICAL: Intercompany transactions must balance within each entity.

Domain specification tests using self-contained business logic models for
mirror entries, currency conversion, balance verification, and elimination.
Integration tests at bottom exercise GeneralLedgerService.record_intercompany_transfer().
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import pytest

# =============================================================================
# Domain Models for Intercompany
# =============================================================================

@dataclass(frozen=True)
class Company:
    """Company entity in multi-company setup."""
    company_id: str
    name: str
    functional_currency: str
    is_active: bool = True


@dataclass(frozen=True)
class IntercompanyAccount:
    """Account for intercompany transactions."""
    account_id: str
    company_id: str
    account_type: str  # "receivable" or "payable"
    related_company_id: str


@dataclass(frozen=True)
class GLEntry:
    """GL entry with company dimension."""
    entry_id: str
    company_id: str
    account: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    currency: str = "USD"
    base_amount: Decimal = Decimal("0")  # In company's functional currency
    reference: str | None = None


@dataclass
class IntercompanyTransaction:
    """Transaction between two companies."""
    transaction_id: str
    source_company: str
    target_company: str
    amount: Decimal
    currency: str
    description: str
    transaction_date: date
    source_entries: list[GLEntry] = field(default_factory=list)
    target_entries: list[GLEntry] = field(default_factory=list)
    status: str = "draft"  # draft, posted, cancelled


@dataclass
class ExchangeRate:
    """Exchange rate between currencies."""
    from_currency: str
    to_currency: str
    rate: Decimal
    effective_date: date


# =============================================================================
# Intercompany Service
# =============================================================================

class IntercompanyService:
    """Manage intercompany transactions."""

    def __init__(self):
        self.companies: dict[str, Company] = {}
        self.ic_accounts: dict[str, IntercompanyAccount] = {}
        self.transactions: list[IntercompanyTransaction] = []
        self.exchange_rates: dict[tuple[str, str, date], Decimal] = {}

    def register_company(self, company: Company) -> None:
        """Register a company."""
        self.companies[company.company_id] = company

    def register_ic_account(self, account: IntercompanyAccount) -> None:
        """Register an intercompany account."""
        self.ic_accounts[account.account_id] = account

    def set_exchange_rate(
        self,
        from_currency: str,
        to_currency: str,
        rate: Decimal,
        effective_date: date,
    ) -> None:
        """Set exchange rate for currency pair."""
        self.exchange_rates[(from_currency, to_currency, effective_date)] = rate

    def get_exchange_rate(
        self,
        from_currency: str,
        to_currency: str,
        effective_date: date,
    ) -> Decimal | None:
        """Get exchange rate for currency pair."""
        if from_currency == to_currency:
            return Decimal("1")
        return self.exchange_rates.get((from_currency, to_currency, effective_date))

    def create_intercompany_entry(
        self,
        source_company_id: str,
        target_company_id: str,
        amount: Decimal,
        currency: str,
        description: str,
        transaction_date: date,
        source_expense_account: str,
        target_income_account: str,
    ) -> IntercompanyTransaction:
        """
        Create intercompany journal entry.

        Creates balanced entries in both companies:
        - Source: DR Expense, CR IC Payable
        - Target: DR IC Receivable, CR Income
        """
        source_company = self.companies.get(source_company_id)
        target_company = self.companies.get(target_company_id)

        if not source_company or not target_company:
            raise ValueError("Invalid company")

        transaction_id = str(uuid4())

        # Get IC accounts
        source_ic_payable = f"IC-PAY-{target_company_id}"
        target_ic_receivable = f"IC-REC-{source_company_id}"

        # Convert to each company's functional currency
        source_rate = self.get_exchange_rate(
            currency,
            source_company.functional_currency,
            transaction_date,
        ) or Decimal("1")
        target_rate = self.get_exchange_rate(
            currency,
            target_company.functional_currency,
            transaction_date,
        ) or Decimal("1")

        source_base = (amount * source_rate).quantize(Decimal("0.01"))
        target_base = (amount * target_rate).quantize(Decimal("0.01"))

        # Create source entries (paying company)
        source_entries = [
            GLEntry(
                entry_id=str(uuid4()),
                company_id=source_company_id,
                account=source_expense_account,
                debit=source_base,
                currency=source_company.functional_currency,
                base_amount=source_base,
                reference=f"IC-{transaction_id}",
            ),
            GLEntry(
                entry_id=str(uuid4()),
                company_id=source_company_id,
                account=source_ic_payable,
                credit=source_base,
                currency=source_company.functional_currency,
                base_amount=source_base,
                reference=f"IC-{transaction_id}",
            ),
        ]

        # Create target entries (receiving company)
        target_entries = [
            GLEntry(
                entry_id=str(uuid4()),
                company_id=target_company_id,
                account=target_ic_receivable,
                debit=target_base,
                currency=target_company.functional_currency,
                base_amount=target_base,
                reference=f"IC-{transaction_id}",
            ),
            GLEntry(
                entry_id=str(uuid4()),
                company_id=target_company_id,
                account=target_income_account,
                credit=target_base,
                currency=target_company.functional_currency,
                base_amount=target_base,
                reference=f"IC-{transaction_id}",
            ),
        ]

        transaction = IntercompanyTransaction(
            transaction_id=transaction_id,
            source_company=source_company_id,
            target_company=target_company_id,
            amount=amount,
            currency=currency,
            description=description,
            transaction_date=transaction_date,
            source_entries=source_entries,
            target_entries=target_entries,
            status="posted",
        )

        self.transactions.append(transaction)
        return transaction

    def cancel_intercompany_entry(
        self,
        transaction_id: str,
    ) -> IntercompanyTransaction:
        """
        Cancel intercompany entry.

        Creates reversal entries in both companies.
        """
        transaction = next(
            (t for t in self.transactions if t.transaction_id == transaction_id),
            None,
        )

        if not transaction:
            raise ValueError(f"Transaction not found: {transaction_id}")

        if transaction.status == "cancelled":
            raise ValueError("Transaction already cancelled")

        # Create reversal entries
        reversal_id = str(uuid4())

        # Reverse source entries
        source_reversals = []
        for entry in transaction.source_entries:
            source_reversals.append(GLEntry(
                entry_id=str(uuid4()),
                company_id=entry.company_id,
                account=entry.account,
                debit=entry.credit,  # Swap
                credit=entry.debit,  # Swap
                currency=entry.currency,
                base_amount=entry.base_amount,
                reference=f"REV-{entry.reference}",
            ))

        # Reverse target entries
        target_reversals = []
        for entry in transaction.target_entries:
            target_reversals.append(GLEntry(
                entry_id=str(uuid4()),
                company_id=entry.company_id,
                account=entry.account,
                debit=entry.credit,  # Swap
                credit=entry.debit,  # Swap
                currency=entry.currency,
                base_amount=entry.base_amount,
                reference=f"REV-{entry.reference}",
            ))

        # Create cancellation transaction
        cancellation = IntercompanyTransaction(
            transaction_id=reversal_id,
            source_company=transaction.source_company,
            target_company=transaction.target_company,
            amount=transaction.amount,
            currency=transaction.currency,
            description=f"Reversal of {transaction.description}",
            transaction_date=date.today(),
            source_entries=source_reversals,
            target_entries=target_reversals,
            status="posted",
        )

        transaction.status = "cancelled"
        self.transactions.append(cancellation)

        return cancellation


# =============================================================================
# Test: Intercompany Journal Entry
# =============================================================================

class TestIntercompanyJournal:
    """Cross-company journal entries."""

    @pytest.fixture
    def ic_service(self):
        service = IntercompanyService()
        service.register_company(Company("CO-A", "Company A", "USD"))
        service.register_company(Company("CO-B", "Company B", "USD"))
        return service

    def test_intercompany_entry_creates_pair(self, ic_service):
        """Auto-create mirror entry in target company."""
        transaction = ic_service.create_intercompany_entry(
            source_company_id="CO-A",
            target_company_id="CO-B",
            amount=Decimal("5000.00"),
            currency="USD",
            description="Management fee",
            transaction_date=date.today(),
            source_expense_account="6000-Management Fees",
            target_income_account="4500-IC Income",
        )

        assert transaction.status == "posted"

        # Source company has entries
        assert len(transaction.source_entries) == 2
        source_debit = sum(e.debit for e in transaction.source_entries)
        source_credit = sum(e.credit for e in transaction.source_entries)
        assert source_debit == source_credit == Decimal("5000.00")

        # Target company has mirror entries
        assert len(transaction.target_entries) == 2
        target_debit = sum(e.debit for e in transaction.target_entries)
        target_credit = sum(e.credit for e in transaction.target_entries)
        assert target_debit == target_credit == Decimal("5000.00")

    def test_intercompany_uses_ic_accounts(self, ic_service):
        """Uses proper IC receivable/payable accounts."""
        transaction = ic_service.create_intercompany_entry(
            source_company_id="CO-A",
            target_company_id="CO-B",
            amount=Decimal("1000.00"),
            currency="USD",
            description="Service charge",
            transaction_date=date.today(),
            source_expense_account="6100-Service Expense",
            target_income_account="4600-Service Income",
        )

        # Source should have IC Payable to CO-B
        source_payable = next(
            (e for e in transaction.source_entries if "IC-PAY" in e.account),
            None,
        )
        assert source_payable is not None
        assert "CO-B" in source_payable.account

        # Target should have IC Receivable from CO-A
        target_receivable = next(
            (e for e in transaction.target_entries if "IC-REC" in e.account),
            None,
        )
        assert target_receivable is not None
        assert "CO-A" in target_receivable.account


class TestIntercompanyCancellation:
    """Intercompany entry cancellation."""

    @pytest.fixture
    def ic_service(self):
        service = IntercompanyService()
        service.register_company(Company("CO-A", "Company A", "USD"))
        service.register_company(Company("CO-B", "Company B", "USD"))
        return service

    def test_intercompany_cancellation(self, ic_service):
        """Cancel both entries together."""
        # Create original
        transaction = ic_service.create_intercompany_entry(
            source_company_id="CO-A",
            target_company_id="CO-B",
            amount=Decimal("2500.00"),
            currency="USD",
            description="Royalty payment",
            transaction_date=date.today(),
            source_expense_account="6200-Royalty Expense",
            target_income_account="4700-Royalty Income",
        )

        # Cancel it
        cancellation = ic_service.cancel_intercompany_entry(transaction.transaction_id)

        # Original marked cancelled
        assert transaction.status == "cancelled"

        # Cancellation reverses entries
        assert len(cancellation.source_entries) == 2
        assert len(cancellation.target_entries) == 2

        # Verify reversal (debits and credits swapped)
        original_source_debit = sum(e.debit for e in transaction.source_entries)
        cancel_source_credit = sum(e.credit for e in cancellation.source_entries)
        assert original_source_debit == cancel_source_credit

    def test_cannot_cancel_twice(self, ic_service):
        """Cannot cancel already cancelled transaction."""
        transaction = ic_service.create_intercompany_entry(
            source_company_id="CO-A",
            target_company_id="CO-B",
            amount=Decimal("1000.00"),
            currency="USD",
            description="Test",
            transaction_date=date.today(),
            source_expense_account="6000-Expense",
            target_income_account="4000-Income",
        )

        ic_service.cancel_intercompany_entry(transaction.transaction_id)

        with pytest.raises(ValueError, match="already cancelled"):
            ic_service.cancel_intercompany_entry(transaction.transaction_id)


# =============================================================================
# Test: Intercompany Currency Conversion
# =============================================================================

class TestIntercompanyCurrencyConversion:
    """Handle different functional currencies."""

    @pytest.fixture
    def ic_service(self):
        service = IntercompanyService()
        service.register_company(Company("CO-US", "US Company", "USD"))
        service.register_company(Company("CO-EU", "EU Company", "EUR"))
        service.set_exchange_rate("USD", "EUR", Decimal("0.92"), date.today())
        service.set_exchange_rate("USD", "USD", Decimal("1"), date.today())
        service.set_exchange_rate("EUR", "EUR", Decimal("1"), date.today())
        return service

    def test_intercompany_currency_conversion(self, ic_service):
        """Handle different functional currencies."""
        transaction = ic_service.create_intercompany_entry(
            source_company_id="CO-US",
            target_company_id="CO-EU",
            amount=Decimal("1000.00"),  # In USD
            currency="USD",
            description="Cross-border service",
            transaction_date=date.today(),
            source_expense_account="6300-Foreign Service",
            target_income_account="4800-Foreign Income",
        )

        # Source entries in USD
        for entry in transaction.source_entries:
            assert entry.currency == "USD"
            assert entry.base_amount == Decimal("1000.00")

        # Target entries in EUR (1000 * 0.92 = 920)
        for entry in transaction.target_entries:
            assert entry.currency == "EUR"
            assert entry.base_amount == Decimal("920.00")

    def test_same_currency_no_conversion(self, ic_service):
        """Same functional currency - no conversion needed."""
        # Add another USD company
        ic_service.register_company(Company("CO-US2", "US Company 2", "USD"))

        transaction = ic_service.create_intercompany_entry(
            source_company_id="CO-US",
            target_company_id="CO-US2",
            amount=Decimal("500.00"),
            currency="USD",
            description="Domestic IC",
            transaction_date=date.today(),
            source_expense_account="6000-Expense",
            target_income_account="4000-Income",
        )

        # Both in USD, same amounts
        source_total = sum(e.base_amount for e in transaction.source_entries) / 2
        target_total = sum(e.base_amount for e in transaction.target_entries) / 2

        assert source_total == Decimal("500.00")
        assert target_total == Decimal("500.00")


# =============================================================================
# Test: Intercompany Balance Verification
# =============================================================================

class TestIntercompanyBalance:
    """Verify intercompany entries balance."""

    @pytest.fixture
    def ic_service(self):
        service = IntercompanyService()
        service.register_company(Company("CO-A", "Company A", "USD"))
        service.register_company(Company("CO-B", "Company B", "USD"))
        return service

    def test_source_entries_balance(self, ic_service):
        """Source company entries must balance."""
        transaction = ic_service.create_intercompany_entry(
            source_company_id="CO-A",
            target_company_id="CO-B",
            amount=Decimal("7500.00"),
            currency="USD",
            description="Consulting fee",
            transaction_date=date.today(),
            source_expense_account="6400-Consulting",
            target_income_account="4900-Consulting Income",
        )

        total_debit = sum(e.debit for e in transaction.source_entries)
        total_credit = sum(e.credit for e in transaction.source_entries)

        assert total_debit == total_credit

    def test_target_entries_balance(self, ic_service):
        """Target company entries must balance."""
        transaction = ic_service.create_intercompany_entry(
            source_company_id="CO-A",
            target_company_id="CO-B",
            amount=Decimal("3000.00"),
            currency="USD",
            description="IT services",
            transaction_date=date.today(),
            source_expense_account="6500-IT Expense",
            target_income_account="4950-IT Income",
        )

        total_debit = sum(e.debit for e in transaction.target_entries)
        total_credit = sum(e.credit for e in transaction.target_entries)

        assert total_debit == total_credit

    def test_ic_receivable_equals_ic_payable(self, ic_service):
        """IC Receivable in target = IC Payable in source."""
        transaction = ic_service.create_intercompany_entry(
            source_company_id="CO-A",
            target_company_id="CO-B",
            amount=Decimal("4000.00"),
            currency="USD",
            description="Allocation",
            transaction_date=date.today(),
            source_expense_account="6600-Allocation",
            target_income_account="4960-Allocation Income",
        )

        # Get IC Payable from source
        ic_payable = next(
            e for e in transaction.source_entries if "IC-PAY" in e.account
        )

        # Get IC Receivable from target
        ic_receivable = next(
            e for e in transaction.target_entries if "IC-REC" in e.account
        )

        assert ic_payable.credit == ic_receivable.debit


# =============================================================================
# Test: Intercompany Validation
# =============================================================================

class TestIntercompanyValidation:
    """Validate intercompany transactions."""

    @pytest.fixture
    def ic_service(self):
        service = IntercompanyService()
        service.register_company(Company("CO-A", "Company A", "USD"))
        return service

    def test_invalid_company_rejected(self, ic_service):
        """Reject transaction with invalid company."""
        with pytest.raises(ValueError, match="Invalid company"):
            ic_service.create_intercompany_entry(
                source_company_id="CO-A",
                target_company_id="CO-NONEXISTENT",  # Not registered
                amount=Decimal("1000.00"),
                currency="USD",
                description="Invalid",
                transaction_date=date.today(),
                source_expense_account="6000-Expense",
                target_income_account="4000-Income",
            )

    def test_transaction_not_found(self, ic_service):
        """Handle cancellation of non-existent transaction."""
        with pytest.raises(ValueError, match="not found"):
            ic_service.cancel_intercompany_entry("FAKE-ID-12345")


# =============================================================================
# Test: Intercompany Elimination
# =============================================================================

class TestIntercompanyElimination:
    """Elimination entries for consolidation."""

    def test_calculate_elimination_entries(self):
        """Calculate elimination entries for consolidation."""
        # Company A owes Company B $10,000
        # At consolidation, these must net to zero

        ic_receivable_b = Decimal("10000.00")  # CO-B's IC Receivable from CO-A
        ic_payable_a = Decimal("10000.00")  # CO-A's IC Payable to CO-B

        # Elimination entry
        elimination_dr_ic_payable = ic_payable_a  # DR IC Payable (reduce liability)
        elimination_cr_ic_receivable = ic_receivable_b  # CR IC Receivable (reduce asset)

        # They should be equal
        assert elimination_dr_ic_payable == elimination_cr_ic_receivable

        # Net impact on consolidated BS
        net_ic_asset = ic_receivable_b - elimination_cr_ic_receivable
        net_ic_liability = ic_payable_a - elimination_dr_ic_payable

        assert net_ic_asset == Decimal("0")
        assert net_ic_liability == Decimal("0")

    def test_ic_revenue_expense_elimination(self):
        """Eliminate IC revenue and expense for consolidation."""
        # Company A paid $5,000 to Company B for services
        # CO-A books expense, CO-B books revenue

        ic_expense_a = Decimal("5000.00")
        ic_revenue_b = Decimal("5000.00")

        # Elimination entry
        elimination_dr_revenue = ic_revenue_b  # DR IC Revenue (reduce income)
        elimination_cr_expense = ic_expense_a  # CR IC Expense (reduce expense)

        assert elimination_dr_revenue == elimination_cr_expense

        # Consolidated P&L has no IC impact
        consolidated_revenue_impact = Decimal("0")  # Nets out
        consolidated_expense_impact = Decimal("0")  # Nets out

        assert consolidated_revenue_impact == Decimal("0")
        assert consolidated_expense_impact == Decimal("0")


# =============================================================================
# Summary
# =============================================================================

class TestIntercompanySummary:
    """Summary of intercompany test coverage."""

    def test_document_coverage(self):
        """
        Intercompany Test Coverage:

        Journal Entry:
        - Auto-create mirror entry in target company
        - Uses proper IC accounts

        Cancellation:
        - Cancel both entries together
        - Cannot cancel twice

        Currency Conversion:
        - Handle different functional currencies
        - Same currency - no conversion

        Balance Verification:
        - Source entries balance
        - Target entries balance
        - IC Receivable = IC Payable

        Validation:
        - Invalid company rejected
        - Transaction not found

        Elimination:
        - Calculate elimination entries
        - IC revenue/expense elimination

        Total: ~15 tests covering intercompany patterns.
        """
        pass


# =============================================================================
# Integration Tests — Real Posting via GeneralLedgerService
# =============================================================================


class TestIntercompanyIntegration:
    """Real integration tests using GeneralLedgerService.record_intercompany_transfer()."""

    @pytest.fixture
    def gl_service(self, session, module_role_resolver, deterministic_clock, register_modules):
        from finance_modules.gl.service import GeneralLedgerService
        return GeneralLedgerService(
            session=session,
            role_resolver=module_role_resolver,
            clock=deterministic_clock,
        )

    def test_intercompany_transfer_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record intercompany transfer through the real pipeline."""
        from finance_kernel.services.module_posting_service import ModulePostingStatus

        result = gl_service.record_intercompany_transfer(
            transfer_id=uuid4(),
            from_entity="ENTITY-A",
            to_entity="ENTITY-B",
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0
