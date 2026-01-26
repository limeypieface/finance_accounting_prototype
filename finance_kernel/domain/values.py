"""
Domain value objects.

These are immutable, self-validating types that enforce domain constraints.
They replace primitive types (Decimal, str) for financial fields.

R4 Compliance: Money, Quantity, Currency, ExchangeRate must be value objects.
Primitive numeric or string types are forbidden for financial fields in domain logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from finance_kernel.domain.currency import CurrencyRegistry


@dataclass(frozen=True, slots=True)
class Currency:
    """
    ISO 4217 currency code value object.

    Validated on construction - invalid codes raise ValueError.
    Immutable and hashable.
    """

    code: str

    def __post_init__(self) -> None:
        # Normalize and validate
        normalized = self.code.upper().strip() if self.code else ""
        if not CurrencyRegistry.is_valid(normalized):
            raise ValueError(f"Invalid ISO 4217 currency code: {self.code}")
        # Override frozen to set normalized value
        object.__setattr__(self, "code", normalized)

    @property
    def decimal_places(self) -> int:
        """Get the number of decimal places for this currency."""
        return CurrencyRegistry.get_decimal_places(self.code)

    @property
    def rounding_tolerance(self) -> Decimal:
        """Get the rounding tolerance for this currency."""
        return CurrencyRegistry.get_rounding_tolerance(self.code)

    @property
    def name(self) -> str:
        """Get the currency name."""
        info = CurrencyRegistry.get_info(self.code)
        return info.name if info else self.code

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"Currency({self.code!r})"


@dataclass(frozen=True, slots=True)
class Money:
    """
    Monetary amount value object.

    Pairs an amount with its currency - they are NEVER separated.
    Validated on construction:
    - Amount must be a valid Decimal
    - Currency must be valid ISO 4217

    Immutable and hashable.
    """

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        # Ensure amount is Decimal
        if not isinstance(self.amount, Decimal):
            try:
                object.__setattr__(self, "amount", Decimal(str(self.amount)))
            except (InvalidOperation, ValueError) as e:
                raise ValueError(f"Invalid amount: {self.amount}") from e

        # Ensure currency is Currency object
        if isinstance(self.currency, str):
            object.__setattr__(self, "currency", Currency(self.currency))
        elif not isinstance(self.currency, Currency):
            raise TypeError(f"currency must be Currency or str, got {type(self.currency)}")

    @classmethod
    def of(cls, amount: Decimal | str | int, currency: str | Currency) -> Money:
        """
        Factory method for creating Money.

        Args:
            amount: The monetary amount.
            currency: ISO 4217 currency code or Currency object.

        Returns:
            Money instance.
        """
        if isinstance(amount, (str, int)):
            amount = Decimal(str(amount))
        if isinstance(currency, str):
            currency = Currency(currency)
        return cls(amount=amount, currency=currency)

    @classmethod
    def zero(cls, currency: str | Currency) -> Money:
        """Create a zero amount in the given currency."""
        if isinstance(currency, str):
            currency = Currency(currency)
        return cls(amount=Decimal("0"), currency=currency)

    @property
    def is_zero(self) -> bool:
        """Check if amount is zero."""
        return self.amount == Decimal("0")

    @property
    def is_positive(self) -> bool:
        """Check if amount is positive."""
        return self.amount > Decimal("0")

    @property
    def is_negative(self) -> bool:
        """Check if amount is negative."""
        return self.amount < Decimal("0")

    def round(self, rounding: str = ROUND_HALF_UP) -> Money:
        """
        Round to the currency's decimal places.

        Returns a new Money instance with rounded amount.
        """
        decimal_places = self.currency.decimal_places
        quantize_str = "0." + "0" * decimal_places if decimal_places > 0 else "1"
        rounded = self.amount.quantize(Decimal(quantize_str), rounding=rounding)
        return Money(amount=rounded, currency=self.currency)

    def __add__(self, other: Money) -> Money:
        """Add two Money values. Must be same currency."""
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot add Money with different currencies: "
                f"{self.currency} and {other.currency}"
            )
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        """Subtract two Money values. Must be same currency."""
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot subtract Money with different currencies: "
                f"{self.currency} and {other.currency}"
            )
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __neg__(self) -> Money:
        """Negate the amount."""
        return Money(amount=-self.amount, currency=self.currency)

    def __abs__(self) -> Money:
        """Absolute value."""
        return Money(amount=abs(self.amount), currency=self.currency)

    def __mul__(self, factor: Decimal | int | str) -> Money:
        """Multiply by a scalar."""
        if isinstance(factor, (int, str)):
            factor = Decimal(str(factor))
        if not isinstance(factor, Decimal):
            return NotImplemented
        return Money(amount=self.amount * factor, currency=self.currency)

    def __rmul__(self, factor: Decimal | int | str) -> Money:
        """Multiply by a scalar (reversed)."""
        return self.__mul__(factor)

    def __truediv__(self, divisor: Decimal | int | str) -> Money:
        """Divide by a scalar."""
        if isinstance(divisor, (int, str)):
            divisor = Decimal(str(divisor))
        if not isinstance(divisor, Decimal):
            return NotImplemented
        return Money(amount=self.amount / divisor, currency=self.currency)

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError("Cannot compare Money with different currencies")
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError("Cannot compare Money with different currencies")
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError("Cannot compare Money with different currencies")
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError("Cannot compare Money with different currencies")
        return self.amount >= other.amount

    def __str__(self) -> str:
        return f"{self.amount} {self.currency.code}"

    def __repr__(self) -> str:
        return f"Money({self.amount!r}, {self.currency!r})"


@dataclass(frozen=True, slots=True)
class Quantity:
    """
    Numeric quantity with unit value object.

    Used for non-monetary quantities like inventory counts.
    Immutable and hashable.
    """

    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        # Ensure value is Decimal
        if not isinstance(self.value, Decimal):
            try:
                object.__setattr__(self, "value", Decimal(str(self.value)))
            except (InvalidOperation, ValueError) as e:
                raise ValueError(f"Invalid quantity value: {self.value}") from e

        # Validate unit
        if not self.unit or not self.unit.strip():
            raise ValueError("Quantity unit is required")
        object.__setattr__(self, "unit", self.unit.strip())

    @classmethod
    def of(cls, value: Decimal | str | int, unit: str) -> Quantity:
        """Factory method for creating Quantity."""
        if isinstance(value, (str, int)):
            value = Decimal(str(value))
        return cls(value=value, unit=unit)

    @classmethod
    def zero(cls, unit: str) -> Quantity:
        """Create a zero quantity with the given unit."""
        return cls(value=Decimal("0"), unit=unit)

    @property
    def is_zero(self) -> bool:
        return self.value == Decimal("0")

    @property
    def is_positive(self) -> bool:
        return self.value > Decimal("0")

    @property
    def is_negative(self) -> bool:
        return self.value < Decimal("0")

    def __add__(self, other: Quantity) -> Quantity:
        if not isinstance(other, Quantity):
            return NotImplemented
        if self.unit != other.unit:
            raise ValueError(
                f"Cannot add Quantity with different units: {self.unit} and {other.unit}"
            )
        return Quantity(value=self.value + other.value, unit=self.unit)

    def __sub__(self, other: Quantity) -> Quantity:
        if not isinstance(other, Quantity):
            return NotImplemented
        if self.unit != other.unit:
            raise ValueError(
                f"Cannot subtract Quantity with different units: {self.unit} and {other.unit}"
            )
        return Quantity(value=self.value - other.value, unit=self.unit)

    def __neg__(self) -> Quantity:
        return Quantity(value=-self.value, unit=self.unit)

    def __abs__(self) -> Quantity:
        return Quantity(value=abs(self.value), unit=self.unit)

    def __mul__(self, factor: Decimal | int | str) -> Quantity:
        if isinstance(factor, (int, str)):
            factor = Decimal(str(factor))
        if not isinstance(factor, Decimal):
            return NotImplemented
        return Quantity(value=self.value * factor, unit=self.unit)

    def __rmul__(self, factor: Decimal | int | str) -> Quantity:
        return self.__mul__(factor)

    def __truediv__(self, divisor: Decimal | int | str) -> Quantity:
        if isinstance(divisor, (int, str)):
            divisor = Decimal(str(divisor))
        if not isinstance(divisor, Decimal):
            return NotImplemented
        return Quantity(value=self.value / divisor, unit=self.unit)

    def __lt__(self, other: Quantity) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        if self.unit != other.unit:
            raise ValueError("Cannot compare Quantity with different units")
        return self.value < other.value

    def __le__(self, other: Quantity) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        if self.unit != other.unit:
            raise ValueError("Cannot compare Quantity with different units")
        return self.value <= other.value

    def __gt__(self, other: Quantity) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        if self.unit != other.unit:
            raise ValueError("Cannot compare Quantity with different units")
        return self.value > other.value

    def __ge__(self, other: Quantity) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        if self.unit != other.unit:
            raise ValueError("Cannot compare Quantity with different units")
        return self.value >= other.value

    def __str__(self) -> str:
        return f"{self.value} {self.unit}"

    def __repr__(self) -> str:
        return f"Quantity({self.value!r}, {self.unit!r})"


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    """
    Exchange rate between two currencies.

    Represents: 1 unit of from_currency = rate units of to_currency

    Immutable and hashable.
    """

    from_currency: Currency
    to_currency: Currency
    rate: Decimal

    def __post_init__(self) -> None:
        # Convert string currencies to Currency objects
        if isinstance(self.from_currency, str):
            object.__setattr__(self, "from_currency", Currency(self.from_currency))
        if isinstance(self.to_currency, str):
            object.__setattr__(self, "to_currency", Currency(self.to_currency))

        # Ensure rate is Decimal
        if not isinstance(self.rate, Decimal):
            try:
                object.__setattr__(self, "rate", Decimal(str(self.rate)))
            except (InvalidOperation, ValueError) as e:
                raise ValueError(f"Invalid exchange rate: {self.rate}") from e

        # Validate rate is positive
        if self.rate <= Decimal("0"):
            raise ValueError(f"Exchange rate must be positive: {self.rate}")

    @classmethod
    def of(
        cls,
        from_currency: str | Currency,
        to_currency: str | Currency,
        rate: Decimal | str | int,
    ) -> ExchangeRate:
        """Factory method for creating ExchangeRate."""
        if isinstance(from_currency, str):
            from_currency = Currency(from_currency)
        if isinstance(to_currency, str):
            to_currency = Currency(to_currency)
        if isinstance(rate, (str, int)):
            rate = Decimal(str(rate))
        return cls(from_currency=from_currency, to_currency=to_currency, rate=rate)

    def convert(self, money: Money) -> Money:
        """
        Convert money from one currency to another using this rate.

        Args:
            money: Money in from_currency.

        Returns:
            Money in to_currency.

        Raises:
            ValueError: If money currency doesn't match from_currency.
        """
        if money.currency != self.from_currency:
            raise ValueError(
                f"Money currency {money.currency} doesn't match "
                f"rate from_currency {self.from_currency}"
            )
        converted_amount = money.amount * self.rate
        return Money(amount=converted_amount, currency=self.to_currency)

    def inverse(self) -> ExchangeRate:
        """
        Get the inverse rate.

        If this rate is USD->EUR at 0.85, inverse is EUR->USD at 1/0.85.
        """
        return ExchangeRate(
            from_currency=self.to_currency,
            to_currency=self.from_currency,
            rate=Decimal("1") / self.rate,
        )

    @property
    def pair(self) -> tuple[str, str]:
        """Get the currency pair as a tuple."""
        return (self.from_currency.code, self.to_currency.code)

    def __str__(self) -> str:
        return f"{self.from_currency}/{self.to_currency} = {self.rate}"

    def __repr__(self) -> str:
        return (
            f"ExchangeRate({self.from_currency!r}, "
            f"{self.to_currency!r}, {self.rate!r})"
        )
