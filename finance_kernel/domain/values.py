"""
Values -- Immutable, self-validating domain value objects.

Responsibility:
    Provides the foundational value types for all financial computations:
    Currency, Money, Quantity, and ExchangeRate. These replace primitive
    types (Decimal, str) wherever financial data appears in domain logic.

Architecture position:
    Kernel > Domain -- pure functional core, zero I/O.
    Imported by every other domain module. No outward dependencies except
    finance_kernel.domain.currency (CurrencyRegistry).

Invariants enforced:
    R4  -- All monetary amounts use Money value objects (never raw Decimal/float)
    R16 -- ISO 4217 enforcement: Currency codes validated at construction time
    R17 -- Precision-derived tolerance: rounding tolerance derived from currency
           decimal places, never hardcoded

Failure modes:
    - ValueError on construction with invalid amounts, currencies, or rates
    - TypeError when Money/Quantity operations mix incompatible types
    - ValueError when arithmetic mixes different currencies or units

Audit relevance:
    These types are the foundation of financial correctness. Auditors rely on
    Money pairing amounts with currencies (never separated), Decimal-only
    arithmetic (never float), and ISO 4217 validation at every boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from finance_kernel.domain.currency import CurrencyRegistry


@dataclass(frozen=True, slots=True)
class Currency:
    """
    ISO 4217 currency code value object.

    Contract:
        Wraps a three-letter ISO 4217 code. Validated and normalized (uppercased)
        on construction. Invalid codes are rejected immediately.

    Guarantees:
        - Immutable and hashable (frozen dataclass with slots)
        - code is always uppercase and stripped of whitespace
        - code is always a valid ISO 4217 code per CurrencyRegistry (R16)

    Non-goals:
        - Does NOT store exchange rates or decimal place info directly
          (delegates to CurrencyRegistry)
        - Does NOT perform currency conversion
    """

    code: str

    def __post_init__(self) -> None:
        # INVARIANT: R16 -- ISO 4217 enforcement at construction boundary
        normalized = self.code.upper().strip() if self.code else ""
        if not CurrencyRegistry.is_valid(normalized):
            raise ValueError(f"Invalid ISO 4217 currency code: {self.code}")
        # Override frozen to set normalized value
        object.__setattr__(self, "code", normalized)

    @property
    def decimal_places(self) -> int:
        """
        Get the number of decimal places for this currency.

        Postconditions:
            - Returns the ISO 4217 standard decimal places (R17).
        """
        return CurrencyRegistry.get_decimal_places(self.code)

    @property
    def rounding_tolerance(self) -> Decimal:
        """
        Get the rounding tolerance for this currency.

        Postconditions:
            - Tolerance is derived from currency precision (R17), never hardcoded.
        """
        # INVARIANT: R17 -- tolerance derived from currency decimal places
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

    Contract:
        Pairs a Decimal amount with its Currency -- they are NEVER separated.
        This is the canonical representation of monetary values throughout the
        entire system.

    Guarantees:
        - Immutable and hashable (frozen dataclass with slots)
        - amount is always a Decimal (never float) -- R4
        - currency is always a valid Currency (ISO 4217) -- R16
        - Arithmetic operations enforce same-currency constraint
        - No silent currency mixing in addition or subtraction

    Non-goals:
        - Does NOT perform currency conversion (use ExchangeRate.convert)
        - Does NOT auto-round -- callers must explicitly call .round()
    """

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        # INVARIANT: R4 -- amount must be Decimal, never float
        if not isinstance(self.amount, Decimal):
            try:
                object.__setattr__(self, "amount", Decimal(str(self.amount)))
            except (InvalidOperation, ValueError) as e:
                raise ValueError(f"Invalid amount: {self.amount}") from e

        # INVARIANT: R16 -- currency must be a valid ISO 4217 Currency
        if isinstance(self.currency, str):
            object.__setattr__(self, "currency", Currency(self.currency))
        elif not isinstance(self.currency, Currency):
            raise TypeError(f"currency must be Currency or str, got {type(self.currency)}")

        assert isinstance(self.amount, Decimal), f"R4 violation: amount must be Decimal, got {type(self.amount)}"

    @classmethod
    def of(cls, amount: Decimal | str | int, currency: str | Currency) -> Money:
        """
        Factory method for creating Money.

        Preconditions:
            - amount is convertible to Decimal (no float allowed at call site)
            - currency is a valid ISO 4217 code or Currency object

        Postconditions:
            - Returns an immutable Money with Decimal amount and Currency

        Raises:
            ValueError: If amount cannot be converted or currency is invalid (R16).

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

        Preconditions:
            - self is a valid Money instance.

        Postconditions:
            - Returns a new Money rounded to the currency's ISO 4217 decimal places (R17).
            - Original Money is unchanged (immutable).

        Raises:
            No exceptions.

        Returns a new Money instance with rounded amount.
        """
        # INVARIANT: R17 -- rounding precision derived from currency decimal places
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

    Contract:
        Pairs a Decimal value with its unit of measure. Used for non-monetary
        quantities like inventory counts, weights, and volumes.

    Guarantees:
        - Immutable and hashable (frozen dataclass with slots)
        - value is always Decimal (never float)
        - unit is always a non-empty, stripped string
        - Arithmetic operations enforce same-unit constraint

    Non-goals:
        - Does NOT perform unit conversion
        - Does NOT validate against a unit registry
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

    Contract:
        Represents: 1 unit of from_currency = rate units of to_currency.
        Validated on construction: rate must be positive, currencies must be
        valid ISO 4217.

    Guarantees:
        - Immutable and hashable (frozen dataclass with slots)
        - rate is always a positive Decimal (never float, never zero or negative)
        - from_currency and to_currency are always valid Currency objects (R16)
        - convert() enforces currency matching

    Non-goals:
        - Does NOT store effective dates (that is ExchangeRateInfo's job)
        - Does NOT handle triangulation or cross rates
    """

    from_currency: Currency
    to_currency: Currency
    rate: Decimal

    def __post_init__(self) -> None:
        # INVARIANT: R16 -- currencies must be valid ISO 4217
        if isinstance(self.from_currency, str):
            object.__setattr__(self, "from_currency", Currency(self.from_currency))
        if isinstance(self.to_currency, str):
            object.__setattr__(self, "to_currency", Currency(self.to_currency))

        # INVARIANT: R4 -- rate must be Decimal, never float
        if not isinstance(self.rate, Decimal):
            try:
                object.__setattr__(self, "rate", Decimal(str(self.rate)))
            except (InvalidOperation, ValueError) as e:
                raise ValueError(f"Invalid exchange rate: {self.rate}") from e

        # Validate rate is positive (economic constraint: zero or negative rates are meaningless)
        if self.rate <= Decimal("0"):
            raise ValueError(f"Exchange rate must be positive: {self.rate}")

        assert isinstance(self.rate, Decimal), f"R4 violation: rate must be Decimal, got {type(self.rate)}"

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

        Preconditions:
            - money.currency must equal self.from_currency

        Postconditions:
            - Returns Money in to_currency with amount = money.amount * rate
            - Original money is unchanged (immutable)

        Raises:
            ValueError: If money currency doesn't match from_currency.

        Args:
            money: Money in from_currency.

        Returns:
            Money in to_currency.
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
