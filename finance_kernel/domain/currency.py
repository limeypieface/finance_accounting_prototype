"""Currency -- ISO 4217 registry and precision-derived rounding."""

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar


@dataclass(frozen=True)
class CurrencyInfo:
    """Information about a single ISO 4217 currency."""

    code: str
    decimal_places: int
    name: str

    @property
    def rounding_tolerance(self) -> Decimal:
        """Maximum rounding tolerance derived from decimal places (R17)."""
        if self.decimal_places == 0:
            return Decimal("1")
        return Decimal("0." + "0" * (self.decimal_places - 1) + "1")

    @property
    def quantize_string(self) -> str:
        """String for Decimal.quantize() to round to this currency's precision."""
        if self.decimal_places == 0:
            return "1"
        return "0." + "0" * self.decimal_places


class CurrencyRegistry:
    """Registry of ISO 4217 currencies with decimal places (R16, R17)."""

    # ISO 4217 currencies with their decimal places
    # Source: https://www.iso.org/iso-4217-currency-codes.html
    _CURRENCIES: ClassVar[dict[str, CurrencyInfo]] = {
        # Major currencies
        "USD": CurrencyInfo("USD", 2, "US Dollar"),
        "EUR": CurrencyInfo("EUR", 2, "Euro"),
        "GBP": CurrencyInfo("GBP", 2, "Pound Sterling"),
        "JPY": CurrencyInfo("JPY", 0, "Japanese Yen"),
        "CHF": CurrencyInfo("CHF", 2, "Swiss Franc"),
        "CAD": CurrencyInfo("CAD", 2, "Canadian Dollar"),
        "AUD": CurrencyInfo("AUD", 2, "Australian Dollar"),
        "NZD": CurrencyInfo("NZD", 2, "New Zealand Dollar"),
        # Zero decimal currencies
        "BIF": CurrencyInfo("BIF", 0, "Burundian Franc"),
        "CLP": CurrencyInfo("CLP", 0, "Chilean Peso"),
        "DJF": CurrencyInfo("DJF", 0, "Djiboutian Franc"),
        "GNF": CurrencyInfo("GNF", 0, "Guinean Franc"),
        "ISK": CurrencyInfo("ISK", 0, "Icelandic Krona"),
        "KMF": CurrencyInfo("KMF", 0, "Comorian Franc"),
        "KRW": CurrencyInfo("KRW", 0, "South Korean Won"),
        "PYG": CurrencyInfo("PYG", 0, "Paraguayan Guarani"),
        "RWF": CurrencyInfo("RWF", 0, "Rwandan Franc"),
        "UGX": CurrencyInfo("UGX", 0, "Ugandan Shilling"),
        "VND": CurrencyInfo("VND", 0, "Vietnamese Dong"),
        "VUV": CurrencyInfo("VUV", 0, "Vanuatu Vatu"),
        "XAF": CurrencyInfo("XAF", 0, "Central African CFA Franc"),
        "XOF": CurrencyInfo("XOF", 0, "West African CFA Franc"),
        "XPF": CurrencyInfo("XPF", 0, "CFP Franc"),
        # Three decimal currencies
        "BHD": CurrencyInfo("BHD", 3, "Bahraini Dinar"),
        "IQD": CurrencyInfo("IQD", 3, "Iraqi Dinar"),
        "JOD": CurrencyInfo("JOD", 3, "Jordanian Dinar"),
        "KWD": CurrencyInfo("KWD", 3, "Kuwaiti Dinar"),
        "LYD": CurrencyInfo("LYD", 3, "Libyan Dinar"),
        "OMR": CurrencyInfo("OMR", 3, "Omani Rial"),
        "TND": CurrencyInfo("TND", 3, "Tunisian Dinar"),
        # Four decimal currencies (special)
        "CLF": CurrencyInfo("CLF", 4, "Chilean Unidad de Fomento"),
        # Standard two decimal currencies (partial list - full set below)
        "AED": CurrencyInfo("AED", 2, "UAE Dirham"),
        "AFN": CurrencyInfo("AFN", 2, "Afghan Afghani"),
        "ALL": CurrencyInfo("ALL", 2, "Albanian Lek"),
        "AMD": CurrencyInfo("AMD", 2, "Armenian Dram"),
        "ANG": CurrencyInfo("ANG", 2, "Netherlands Antillean Guilder"),
        "AOA": CurrencyInfo("AOA", 2, "Angolan Kwanza"),
        "ARS": CurrencyInfo("ARS", 2, "Argentine Peso"),
        "AWG": CurrencyInfo("AWG", 2, "Aruban Florin"),
        "AZN": CurrencyInfo("AZN", 2, "Azerbaijan Manat"),
        "BAM": CurrencyInfo("BAM", 2, "Bosnia and Herzegovina Convertible Mark"),
        "BBD": CurrencyInfo("BBD", 2, "Barbadian Dollar"),
        "BDT": CurrencyInfo("BDT", 2, "Bangladeshi Taka"),
        "BGN": CurrencyInfo("BGN", 2, "Bulgarian Lev"),
        "BMD": CurrencyInfo("BMD", 2, "Bermudian Dollar"),
        "BND": CurrencyInfo("BND", 2, "Brunei Dollar"),
        "BOB": CurrencyInfo("BOB", 2, "Bolivian Boliviano"),
        "BOV": CurrencyInfo("BOV", 2, "Bolivian Mvdol"),
        "BRL": CurrencyInfo("BRL", 2, "Brazilian Real"),
        "BSD": CurrencyInfo("BSD", 2, "Bahamian Dollar"),
        "BTN": CurrencyInfo("BTN", 2, "Bhutanese Ngultrum"),
        "BWP": CurrencyInfo("BWP", 2, "Botswana Pula"),
        "BYN": CurrencyInfo("BYN", 2, "Belarusian Ruble"),
        "BZD": CurrencyInfo("BZD", 2, "Belize Dollar"),
        "CDF": CurrencyInfo("CDF", 2, "Congolese Franc"),
        "CHE": CurrencyInfo("CHE", 2, "WIR Euro"),
        "CHW": CurrencyInfo("CHW", 2, "WIR Franc"),
        "CNY": CurrencyInfo("CNY", 2, "Chinese Yuan"),
        "COP": CurrencyInfo("COP", 2, "Colombian Peso"),
        "COU": CurrencyInfo("COU", 2, "Colombian Unidad de Valor Real"),
        "CRC": CurrencyInfo("CRC", 2, "Costa Rican Colon"),
        "CUC": CurrencyInfo("CUC", 2, "Cuban Convertible Peso"),
        "CUP": CurrencyInfo("CUP", 2, "Cuban Peso"),
        "CVE": CurrencyInfo("CVE", 2, "Cape Verdean Escudo"),
        "CZK": CurrencyInfo("CZK", 2, "Czech Koruna"),
        "DKK": CurrencyInfo("DKK", 2, "Danish Krone"),
        "DOP": CurrencyInfo("DOP", 2, "Dominican Peso"),
        "DZD": CurrencyInfo("DZD", 2, "Algerian Dinar"),
        "EGP": CurrencyInfo("EGP", 2, "Egyptian Pound"),
        "ERN": CurrencyInfo("ERN", 2, "Eritrean Nakfa"),
        "ETB": CurrencyInfo("ETB", 2, "Ethiopian Birr"),
        "FJD": CurrencyInfo("FJD", 2, "Fijian Dollar"),
        "FKP": CurrencyInfo("FKP", 2, "Falkland Islands Pound"),
        "GEL": CurrencyInfo("GEL", 2, "Georgian Lari"),
        "GHS": CurrencyInfo("GHS", 2, "Ghanaian Cedi"),
        "GIP": CurrencyInfo("GIP", 2, "Gibraltar Pound"),
        "GMD": CurrencyInfo("GMD", 2, "Gambian Dalasi"),
        "GTQ": CurrencyInfo("GTQ", 2, "Guatemalan Quetzal"),
        "GYD": CurrencyInfo("GYD", 2, "Guyanese Dollar"),
        "HKD": CurrencyInfo("HKD", 2, "Hong Kong Dollar"),
        "HNL": CurrencyInfo("HNL", 2, "Honduran Lempira"),
        "HRK": CurrencyInfo("HRK", 2, "Croatian Kuna"),
        "HTG": CurrencyInfo("HTG", 2, "Haitian Gourde"),
        "HUF": CurrencyInfo("HUF", 2, "Hungarian Forint"),
        "IDR": CurrencyInfo("IDR", 2, "Indonesian Rupiah"),
        "ILS": CurrencyInfo("ILS", 2, "Israeli New Shekel"),
        "INR": CurrencyInfo("INR", 2, "Indian Rupee"),
        "IRR": CurrencyInfo("IRR", 2, "Iranian Rial"),
        "JMD": CurrencyInfo("JMD", 2, "Jamaican Dollar"),
        "KES": CurrencyInfo("KES", 2, "Kenyan Shilling"),
        "KGS": CurrencyInfo("KGS", 2, "Kyrgyzstani Som"),
        "KHR": CurrencyInfo("KHR", 2, "Cambodian Riel"),
        "KPW": CurrencyInfo("KPW", 2, "North Korean Won"),
        "KYD": CurrencyInfo("KYD", 2, "Cayman Islands Dollar"),
        "KZT": CurrencyInfo("KZT", 2, "Kazakhstani Tenge"),
        "LAK": CurrencyInfo("LAK", 2, "Lao Kip"),
        "LBP": CurrencyInfo("LBP", 2, "Lebanese Pound"),
        "LKR": CurrencyInfo("LKR", 2, "Sri Lankan Rupee"),
        "LRD": CurrencyInfo("LRD", 2, "Liberian Dollar"),
        "LSL": CurrencyInfo("LSL", 2, "Lesotho Loti"),
        "MAD": CurrencyInfo("MAD", 2, "Moroccan Dirham"),
        "MDL": CurrencyInfo("MDL", 2, "Moldovan Leu"),
        "MGA": CurrencyInfo("MGA", 2, "Malagasy Ariary"),
        "MKD": CurrencyInfo("MKD", 2, "Macedonian Denar"),
        "MMK": CurrencyInfo("MMK", 2, "Myanmar Kyat"),
        "MNT": CurrencyInfo("MNT", 2, "Mongolian Tugrik"),
        "MOP": CurrencyInfo("MOP", 2, "Macanese Pataca"),
        "MRU": CurrencyInfo("MRU", 2, "Mauritanian Ouguiya"),
        "MUR": CurrencyInfo("MUR", 2, "Mauritian Rupee"),
        "MVR": CurrencyInfo("MVR", 2, "Maldivian Rufiyaa"),
        "MWK": CurrencyInfo("MWK", 2, "Malawian Kwacha"),
        "MXN": CurrencyInfo("MXN", 2, "Mexican Peso"),
        "MXV": CurrencyInfo("MXV", 2, "Mexican Unidad de Inversion"),
        "MYR": CurrencyInfo("MYR", 2, "Malaysian Ringgit"),
        "MZN": CurrencyInfo("MZN", 2, "Mozambican Metical"),
        "NAD": CurrencyInfo("NAD", 2, "Namibian Dollar"),
        "NGN": CurrencyInfo("NGN", 2, "Nigerian Naira"),
        "NIO": CurrencyInfo("NIO", 2, "Nicaraguan Cordoba"),
        "NOK": CurrencyInfo("NOK", 2, "Norwegian Krone"),
        "NPR": CurrencyInfo("NPR", 2, "Nepalese Rupee"),
        "PAB": CurrencyInfo("PAB", 2, "Panamanian Balboa"),
        "PEN": CurrencyInfo("PEN", 2, "Peruvian Sol"),
        "PGK": CurrencyInfo("PGK", 2, "Papua New Guinean Kina"),
        "PHP": CurrencyInfo("PHP", 2, "Philippine Peso"),
        "PKR": CurrencyInfo("PKR", 2, "Pakistani Rupee"),
        "PLN": CurrencyInfo("PLN", 2, "Polish Zloty"),
        "QAR": CurrencyInfo("QAR", 2, "Qatari Riyal"),
        "RON": CurrencyInfo("RON", 2, "Romanian Leu"),
        "RSD": CurrencyInfo("RSD", 2, "Serbian Dinar"),
        "RUB": CurrencyInfo("RUB", 2, "Russian Ruble"),
        "SAR": CurrencyInfo("SAR", 2, "Saudi Riyal"),
        "SBD": CurrencyInfo("SBD", 2, "Solomon Islands Dollar"),
        "SCR": CurrencyInfo("SCR", 2, "Seychellois Rupee"),
        "SDG": CurrencyInfo("SDG", 2, "Sudanese Pound"),
        "SEK": CurrencyInfo("SEK", 2, "Swedish Krona"),
        "SGD": CurrencyInfo("SGD", 2, "Singapore Dollar"),
        "SHP": CurrencyInfo("SHP", 2, "Saint Helena Pound"),
        "SLE": CurrencyInfo("SLE", 2, "Sierra Leonean Leone"),
        "SLL": CurrencyInfo("SLL", 2, "Sierra Leonean Leone (old)"),
        "SOS": CurrencyInfo("SOS", 2, "Somali Shilling"),
        "SRD": CurrencyInfo("SRD", 2, "Surinamese Dollar"),
        "SSP": CurrencyInfo("SSP", 2, "South Sudanese Pound"),
        "STN": CurrencyInfo("STN", 2, "Sao Tome and Principe Dobra"),
        "SVC": CurrencyInfo("SVC", 2, "Salvadoran Colon"),
        "SYP": CurrencyInfo("SYP", 2, "Syrian Pound"),
        "SZL": CurrencyInfo("SZL", 2, "Swazi Lilangeni"),
        "THB": CurrencyInfo("THB", 2, "Thai Baht"),
        "TJS": CurrencyInfo("TJS", 2, "Tajikistani Somoni"),
        "TMT": CurrencyInfo("TMT", 2, "Turkmenistan Manat"),
        "TOP": CurrencyInfo("TOP", 2, "Tongan Paanga"),
        "TRY": CurrencyInfo("TRY", 2, "Turkish Lira"),
        "TTD": CurrencyInfo("TTD", 2, "Trinidad and Tobago Dollar"),
        "TWD": CurrencyInfo("TWD", 2, "New Taiwan Dollar"),
        "TZS": CurrencyInfo("TZS", 2, "Tanzanian Shilling"),
        "UAH": CurrencyInfo("UAH", 2, "Ukrainian Hryvnia"),
        "USN": CurrencyInfo("USN", 2, "US Dollar (Next day)"),
        "UYI": CurrencyInfo("UYI", 0, "Uruguay Peso en Unidades Indexadas"),
        "UYU": CurrencyInfo("UYU", 2, "Uruguayan Peso"),
        "UYW": CurrencyInfo("UYW", 4, "Unidad Previsional"),
        "UZS": CurrencyInfo("UZS", 2, "Uzbekistani Som"),
        "VED": CurrencyInfo("VED", 2, "Venezuelan Bolivar Digital"),
        "VES": CurrencyInfo("VES", 2, "Venezuelan Bolivar Soberano"),
        "WST": CurrencyInfo("WST", 2, "Samoan Tala"),
        "XCD": CurrencyInfo("XCD", 2, "East Caribbean Dollar"),
        "XDR": CurrencyInfo("XDR", 0, "Special Drawing Rights"),
        "YER": CurrencyInfo("YER", 2, "Yemeni Rial"),
        "ZAR": CurrencyInfo("ZAR", 2, "South African Rand"),
        "ZMW": CurrencyInfo("ZMW", 2, "Zambian Kwacha"),
        "ZWL": CurrencyInfo("ZWL", 2, "Zimbabwean Dollar"),
        # Precious metals and special codes
        "XAG": CurrencyInfo("XAG", 0, "Silver (troy ounce)"),
        "XAU": CurrencyInfo("XAU", 0, "Gold (troy ounce)"),
        "XBA": CurrencyInfo("XBA", 0, "European Composite Unit"),
        "XBB": CurrencyInfo("XBB", 0, "European Monetary Unit"),
        "XBC": CurrencyInfo("XBC", 0, "European Unit of Account 9"),
        "XBD": CurrencyInfo("XBD", 0, "European Unit of Account 17"),
        "XPD": CurrencyInfo("XPD", 0, "Palladium (troy ounce)"),
        "XPT": CurrencyInfo("XPT", 0, "Platinum (troy ounce)"),
        "XSU": CurrencyInfo("XSU", 0, "Sucre"),
        "XTS": CurrencyInfo("XTS", 0, "Testing Code"),
        "XUA": CurrencyInfo("XUA", 0, "ADB Unit of Account"),
        "XXX": CurrencyInfo("XXX", 0, "No currency"),
    }

    @classmethod
    def is_valid(cls, code: str) -> bool:
        """Check if a currency code is valid ISO 4217."""
        if not code or not isinstance(code, str):
            return False
        return code.upper().strip() in cls._CURRENCIES

    @classmethod
    def get_info(cls, code: str) -> CurrencyInfo | None:
        """Get currency information by code."""
        if not code or not isinstance(code, str):
            return None
        return cls._CURRENCIES.get(code.upper().strip())

    @classmethod
    def get_decimal_places(cls, code: str) -> int:
        """Get decimal places for a currency (R17)."""
        info = cls.get_info(code)
        return info.decimal_places if info else cls.DEFAULT_DECIMAL_PLACES

    # Default decimal places for unknown currencies (R17 compliance)
    DEFAULT_DECIMAL_PLACES: ClassVar[int] = 2

    @classmethod
    def get_rounding_tolerance(cls, code: str) -> Decimal:
        """Get rounding tolerance derived from currency precision (R17)."""
        info = cls.get_info(code)
        if info:
            return info.rounding_tolerance
        # Derive tolerance from default decimal places (R17: no fixed decimals)
        return cls._tolerance_from_decimal_places(cls.DEFAULT_DECIMAL_PLACES)

    @classmethod
    def _tolerance_from_decimal_places(cls, decimal_places: int) -> Decimal:
        """Compute rounding tolerance from decimal places (R17)."""
        if decimal_places == 0:
            return Decimal("1")
        return Decimal("0." + "0" * (decimal_places - 1) + "1")

    @classmethod
    def validate(cls, code: str) -> str:
        """Validate and normalize a currency code (R16)."""
        if not code or not isinstance(code, str):
            raise ValueError(f"Invalid currency code: {code!r}")

        normalized = code.upper().strip()

        if len(normalized) != 3:
            raise ValueError(f"Currency code must be 3 characters: {code!r}")

        if normalized not in cls._CURRENCIES:
            raise ValueError(f"Invalid ISO 4217 currency code: {code!r}")

        return normalized

    @classmethod
    def all_codes(cls) -> frozenset[str]:
        """Get all valid currency codes."""
        return frozenset(cls._CURRENCIES.keys())

    @classmethod
    def all_currencies(cls) -> dict[str, CurrencyInfo]:
        """Get all currency information."""
        return dict(cls._CURRENCIES)
