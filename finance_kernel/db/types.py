"""Financial-grade column types, rounding, and currency validation (R16, R17)."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from sqlalchemy import BigInteger, Numeric, String

# Monetary amount with high precision
# 38 digits total, 9 decimal places
# Supports values up to 10^29 with 9 decimal places
Money = Annotated[Decimal, Numeric(38, 9)]

# Exchange rate with maximum precision
# 38 digits total, 18 decimal places for rate calculations
Rate = Annotated[Decimal, Numeric(38, 18)]

# ISO 4217 currency code (e.g., "USD", "EUR", "GBP")
Currency = Annotated[str, String(3)]

# Monotonic sequence number for ordering
Sequence = Annotated[int, BigInteger]

# SHA-256 hash as hex string (64 characters)
PayloadHash = Annotated[str, String(64)]

# Short identifier strings
ShortCode = Annotated[str, String(50)]

# Long text for descriptions
LongText = Annotated[str, String(4000)]


# Rounding constants
MONEY_DECIMAL_PLACES = 9
RATE_DECIMAL_PLACES = 18
DEFAULT_ROUNDING = ROUND_HALF_UP


def money_from_str(value: str) -> Decimal:
    """Create a Money value from string."""
    return Decimal(value)


def money_from_int(value: int, decimal_places: int = 2) -> Decimal:
    """Create a Money value from integer minor units (e.g., cents)."""
    divisor = Decimal(10) ** decimal_places
    return Decimal(value) / divisor


def round_money(
    value: Decimal,
    decimal_places: int = 2,
    rounding: str = DEFAULT_ROUNDING,
) -> Decimal:
    """Round a monetary value to specified decimal places.

    # INVARIANT R17: This is the ONLY sanctioned rounding function for
    # financial values in the entire system.
    """
    quantize_str = "0." + "0" * decimal_places
    return value.quantize(Decimal(quantize_str), rounding=rounding)


# ISO 4217 Currency Codes (complete list)
# Source: https://www.iso.org/iso-4217-currency-codes.html
ISO_4217_CURRENCIES: set[str] = {
    # Major currencies
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
    # Other currencies (alphabetical)
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AWG", "AZN",
    "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BOV", "BRL", "BSD", "BTN", "BWP", "BYN", "BZD",
    "CDF", "CHE", "CHW", "CLF", "CLP", "CNY", "COP", "COU", "CRC", "CUC", "CUP", "CVE", "CZK",
    "DJF", "DKK", "DOP", "DZD",
    "EGP", "ERN", "ETB",
    "FJD", "FKP",
    "GEL", "GHS", "GIP", "GMD", "GNF", "GTQ", "GYD",
    "HKD", "HNL", "HRK", "HTG", "HUF",
    "IDR", "ILS", "INR", "IQD", "IRR", "ISK",
    "JMD", "JOD",
    "KES", "KGS", "KHR", "KMF", "KPW", "KRW", "KWD", "KYD", "KZT",
    "LAK", "LBP", "LKR", "LRD", "LSL", "LYD",
    "MAD", "MDL", "MGA", "MKD", "MMK", "MNT", "MOP", "MRU", "MUR", "MVR", "MWK", "MXN", "MXV", "MYR", "MZN",
    "NAD", "NGN", "NIO", "NOK", "NPR",
    "OMR",
    "PAB", "PEN", "PGK", "PHP", "PKR", "PLN", "PYG",
    "QAR",
    "RON", "RSD", "RUB", "RWF",
    "SAR", "SBD", "SCR", "SDG", "SEK", "SGD", "SHP", "SLE", "SLL", "SOS", "SRD", "SSP", "STN", "SVC", "SYP", "SZL",
    "THB", "TJS", "TMT", "TND", "TOP", "TRY", "TTD", "TWD", "TZS",
    "UAH", "UGX", "USN", "UYI", "UYU", "UYW", "UZS",
    "VED", "VES", "VND", "VUV",
    "WST",
    "XAF", "XAG", "XAU", "XBA", "XBB", "XBC", "XBD", "XCD", "XDR", "XOF", "XPD", "XPF", "XPT", "XSU", "XTS", "XUA", "XXX",
    "YER",
    "ZAR", "ZMW", "ZWL",
}


class InvalidCurrencyError(ValueError):
    """Raised when an invalid ISO 4217 currency code is provided."""

    def __init__(self, currency: str):
        self.currency = currency
        super().__init__(f"Invalid ISO 4217 currency code: '{currency}'")


def validate_currency(currency: str) -> str:
    """Validate and normalize an ISO 4217 currency code (R16)."""
    if not currency or not isinstance(currency, str):
        raise InvalidCurrencyError(str(currency))

    normalized = currency.upper().strip()

    if len(normalized) != 3:
        raise InvalidCurrencyError(currency)

    if normalized not in ISO_4217_CURRENCIES:
        raise InvalidCurrencyError(currency)

    return normalized


def is_valid_currency(currency: str) -> bool:
    """Check if a currency code is a valid ISO 4217 code."""
    try:
        validate_currency(currency)
        return True
    except (InvalidCurrencyError, TypeError):
        return False
