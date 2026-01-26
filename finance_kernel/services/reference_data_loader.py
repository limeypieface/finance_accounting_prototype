"""
Reference Data Loader - Loads reference data for the pure strategy layer.

The ReferenceDataLoader queries the database to build a ReferenceData
object that can be passed to the pure Bookkeeper/Strategy layer.

This keeps database access out of the pure domain layer.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.currency import CurrencyRegistry
from finance_kernel.domain.dtos import ReferenceData
from finance_kernel.domain.values import Currency, ExchangeRate as ExchangeRateValue
from finance_kernel.models.account import Account, AccountTag
from finance_kernel.models.exchange_rate import ExchangeRate


class ReferenceDataLoader:
    """
    Loads reference data from the database for the pure strategy layer.

    Creates a ReferenceData object containing:
    - Account code to ID mappings
    - Active account codes
    - Valid currencies
    - Rounding account IDs
    - Exchange rates (if needed)
    """

    def __init__(self, session: Session):
        """
        Initialize the loader.

        Args:
            session: SQLAlchemy session.
        """
        self._session = session

    def load(
        self,
        required_dimensions: frozenset[str] = frozenset(),
        include_exchange_rates: bool = False,
    ) -> ReferenceData:
        """
        Load reference data from the database.

        Args:
            required_dimensions: Set of required dimension codes.
            include_exchange_rates: Whether to load exchange rates.

        Returns:
            ReferenceData object for the pure layer.
        """
        # Load accounts
        accounts = self._load_accounts()

        # Build account mappings
        account_ids_by_code = {
            acc.code: acc.id for acc in accounts
        }
        active_account_codes = frozenset(
            acc.code for acc in accounts if acc.is_active
        )

        # Build rounding account mappings
        rounding_account_ids = self._get_rounding_accounts(accounts)

        # Load exchange rates if needed (as value objects)
        exchange_rates: tuple[ExchangeRateValue, ...] | None = None
        if include_exchange_rates:
            exchange_rates = self._load_exchange_rates_as_values()

        # Build valid currencies as Currency value objects
        valid_currencies = frozenset(
            Currency(code) for code in CurrencyRegistry.all_codes()
        )

        return ReferenceData(
            account_ids_by_code=account_ids_by_code,
            active_account_codes=active_account_codes,
            valid_currencies=valid_currencies,
            rounding_account_ids=rounding_account_ids,
            exchange_rates=exchange_rates,
            required_dimensions=required_dimensions,
        )

    def _load_accounts(self) -> list[Account]:
        """Load all accounts from the database."""
        return list(
            self._session.execute(select(Account)).scalars().all()
        )

    def _get_rounding_accounts(
        self,
        accounts: list[Account],
    ) -> dict[str, UUID]:
        """
        Get rounding account IDs by currency.

        Looks for accounts tagged with ROUNDING.
        Falls back to a general rounding account if no currency-specific one exists.
        """
        rounding_accounts: dict[str, UUID] = {}
        general_rounding_id: UUID | None = None

        for account in accounts:
            if not account.is_active:
                continue

            tags = account.tags or []
            if AccountTag.ROUNDING.value in tags:
                if account.currency:
                    rounding_accounts[account.currency] = account.id
                else:
                    general_rounding_id = account.id

        # If we have a general rounding account, use it as fallback
        # for currencies without a specific one
        if general_rounding_id:
            for code in CurrencyRegistry.all_codes():
                if code not in rounding_accounts:
                    rounding_accounts[code] = general_rounding_id

        return rounding_accounts

    def _load_exchange_rates_as_values(self) -> tuple[ExchangeRateValue, ...]:
        """Load the most recent exchange rates as value objects."""
        rates = self._session.execute(
            select(ExchangeRate)
            .order_by(ExchangeRate.effective_at.desc())
        ).scalars().all()

        # Build rate list (most recent for each pair)
        seen: set[tuple[str, str]] = set()
        rate_values: list[ExchangeRateValue] = []

        for rate in rates:
            key = (rate.from_currency, rate.to_currency)
            if key not in seen:
                rate_values.append(
                    ExchangeRateValue(
                        from_currency=Currency(rate.from_currency),
                        to_currency=Currency(rate.to_currency),
                        rate=rate.rate,
                        effective_at=rate.effective_at,
                    )
                )
                seen.add(key)

        return tuple(rate_values)

    def load_for_currencies(
        self,
        currencies: set[str],
        required_dimensions: frozenset[str] = frozenset(),
    ) -> ReferenceData:
        """
        Load reference data for specific currencies.

        Optimized loader when you know which currencies will be used.

        Args:
            currencies: Set of currency codes to load data for.
            required_dimensions: Set of required dimension codes.

        Returns:
            ReferenceData object.
        """
        # Validate currencies first
        for currency in currencies:
            CurrencyRegistry.validate(currency)

        return self.load(
            required_dimensions=required_dimensions,
            include_exchange_rates=len(currencies) > 1,
        )
