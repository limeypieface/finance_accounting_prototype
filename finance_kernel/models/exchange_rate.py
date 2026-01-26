"""
Exchange Rate model.

Manages currency conversion rates with full auditability.

Hard invariants:
- Rates are append-only
- Posting uses a frozen rate selection (rate_id is stored on the JournalLine)
- Historical postings never re-evaluate rates
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import TrackedBase


class ExchangeRate(TrackedBase):
    """
    Currency exchange rate record.

    Rates are immutable - new rates are added, old rates are never modified.
    Each posting records the specific rate_id used for conversions.
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

        Args:
            amount: Amount in from_currency.

        Returns:
            Amount in to_currency.
        """
        return amount * self.rate

    @property
    def inverse_rate(self) -> Decimal:
        """Get the inverse rate (to -> from)."""
        return Decimal("1") / self.rate
