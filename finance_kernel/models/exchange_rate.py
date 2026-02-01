"""
Module: finance_kernel.models.exchange_rate
Responsibility: ORM persistence for currency exchange rates used in multi-currency
    journal postings.  Each rate record is a timestamped, sourced conversion factor
    between two ISO 4217 currencies.
Architecture position: Kernel > Models.  May import from db/base.py only.
    MUST NOT import from services/, selectors/, domain/, or outer layers.

Invariants enforced:
    R10 -- Immutability when referenced.  Once an ExchangeRate row is used by
           any JournalLine (via exchange_rate_id FK), the rate value is frozen.
           ORM listener in db/immutability.py + DB trigger 08_exchange_rate.sql.
    R16 -- ISO 4217 enforcement.  from_currency and to_currency are 3-character
           codes validated at the ingestion boundary.
    R21 -- Reference snapshot determinism.  JournalLine stores exchange_rate_id
           so that replay can recompute the exact converted amount.

Failure modes:
    - ExchangeRateImmutableError on UPDATE to rate when referenced (R10).
    - ExchangeRateReferencedError on DELETE when referenced (R10).
    - InvalidExchangeRateError on non-positive or excessively large rate values.

Audit relevance:
    Exchange rates are the bridge between reporting currency and transaction
    currency.  Changing a referenced rate retroactively silently alters the
    value of historical journal entries.  Immutability after first use is a
    foundational audit guarantee for multi-currency ledgers.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import TrackedBase


class ExchangeRate(TrackedBase):
    """
    Currency exchange rate record -- one directional conversion factor.

    Contract:
        Each ExchangeRate represents a point-in-time conversion factor from
        one currency to another (from_currency * rate = to_currency amount).
        Once referenced by any JournalLine via exchange_rate_id, the rate
        value becomes immutable (R10).

    Guarantees:
        - rate is always a positive Decimal with up to 18 decimal places.
        - from_currency and to_currency are 3-character ISO 4217 codes (R16).
        - effective_at records when the rate became authoritative.
        - source records the rate provider for audit trail.

    Non-goals:
        - This model does NOT enforce inverse rate consistency; the caller
          must create separate ExchangeRate rows for the reverse direction.
        - This model does NOT perform triangulation; that is the responsibility
          of the multicurrency engine.
    """

    __tablename__ = "exchange_rates"

    __table_args__ = (
        Index("idx_rate_currencies", "from_currency", "to_currency"),
        Index("idx_rate_effective", "effective_at"),
        Index(
            "idx_rate_lookup",
            "from_currency",
            "to_currency",
            "effective_at",
        ),
    )

    # Source currency (ISO 4217)
    from_currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    # Target currency (ISO 4217)
    to_currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    # Exchange rate (from_amount * rate = to_amount)
    # High precision for accurate calculations
    rate: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
    )

    # When this rate became effective
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Source of the rate (e.g., "ECB", "OANDA", "manual")
    source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # Optional reference or description
    reference: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<ExchangeRate {self.from_currency}/{self.to_currency} = {self.rate}>"

    def convert(self, amount: Decimal) -> Decimal:
        """
        Convert an amount using this rate.

        Preconditions: amount is a Decimal value in from_currency.
        Postconditions: Returns amount * rate (the equivalent in to_currency).
            Does NOT round -- callers are responsible for applying currency-
            precision rounding via round_money() (R17).

        Args:
            amount: Amount in from_currency.

        Returns:
            Amount in to_currency.
        """
        return amount * self.rate

    @property
    def inverse_rate(self) -> Decimal:
        """Get the inverse rate (to -> from).

        Postconditions: Returns 1 / rate.  Callers should use this for display
            only; for posting, a separate ExchangeRate row in the reverse
            direction is the canonical source.
        """
        return Decimal("1") / self.rate
