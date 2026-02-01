"""
finance_modules.cash.helpers
=============================

Responsibility:
    Pure helper functions for bank statement parsing (MT940, BAI2, CAMT.053)
    and payment file formatting (NACHA/ACH).  Zero I/O, zero side effects.

Architecture:
    Module layer (finance_modules).  Called by ``CashService.import_bank_statement``
    and ``CashService.generate_payment_file``.

Invariants enforced:
    - All parsed ``amount`` values are ``Decimal`` -- never ``float``.
    - Functions are stateless and referentially transparent.

Failure modes:
    - Malformed input lines are silently skipped (by-design for
      simplified parsers).
    - ``Decimal`` conversion failure on invalid amount strings ->
      ``decimal.InvalidOperation``.

Audit relevance:
    Parsed records feed into the reconciliation pipeline.  Accuracy of
    parsing directly affects the integrity of reconciliation adjustments.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal


def parse_mt940(raw_data: str) -> list[dict]:
    """
    Parse MT940 bank statement format into normalized records.

    Preconditions:
        - ``raw_data`` is a pipe-delimited text representation.
    Postconditions:
        - Returns list of dicts with: date, amount (Decimal), reference,
          description, type.
    Raises:
        ``decimal.InvalidOperation`` if amount field is not numeric.

    Simplified parser for the SWIFT MT940 standard.
    """
    records: list[dict] = []
    for line in raw_data.strip().splitlines():
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            records.append({
                "date": parts[0].strip(),
                "amount": Decimal(parts[1].strip()),
                "reference": parts[2].strip(),
                "description": parts[3].strip(),
                "type": parts[4].strip() if len(parts) > 4 else "UNKNOWN",
            })
    return records


def parse_bai2(raw_data: str) -> list[dict]:
    """
    Parse BAI2 bank statement format into normalized records.

    Preconditions:
        - ``raw_data`` is comma-delimited BAI2 format text.
    Postconditions:
        - Returns list of dicts with: date, amount (Decimal, cents converted
          to dollars), reference, description, type.
    Raises:
        ``decimal.InvalidOperation`` if amount field is not numeric.

    Simplified parser for the BAI2 cash management standard.
    """
    records: list[dict] = []
    for line in raw_data.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("01") or line.startswith("02") or line.startswith("03"):
            continue
        if line.startswith("16"):
            # Transaction detail record
            parts = line.split(",")
            if len(parts) >= 4:
                records.append({
                    "date": parts[1].strip() if len(parts) > 1 else "",
                    "amount": Decimal(parts[2].strip()) / Decimal("100") if len(parts) > 2 else Decimal("0"),
                    "reference": parts[3].strip() if len(parts) > 3 else "",
                    "description": parts[4].strip() if len(parts) > 4 else "",
                    "type": "CREDIT" if line.startswith("16,") else "DEBIT",
                })
    return records


def parse_camt053(raw_data: str) -> list[dict]:
    """
    Parse CAMT.053 (ISO 20022) bank statement format.

    Preconditions:
        - ``raw_data`` is a pipe-delimited text representation.
    Postconditions:
        - Returns list of dicts with: date, amount (Decimal), reference,
          description, type.
    Raises:
        ``decimal.InvalidOperation`` if amount field is not numeric.

    Simplified parser that handles pipe-delimited representation.
    """
    records: list[dict] = []
    for line in raw_data.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            records.append({
                "date": parts[0].strip(),
                "amount": Decimal(parts[1].strip()),
                "reference": parts[2].strip(),
                "description": parts[3].strip(),
                "type": parts[4].strip() if len(parts) > 4 else "UNKNOWN",
            })
    return records


def format_nacha(payments: list[dict], company_name: str, company_id: str) -> str:
    """
    Format payment data into NACHA/ACH file format.

    Preconditions:
        - Each payment dict contains keys: name, account, routing, amount.
        - ``amount`` values are convertible to ``Decimal``.
    Postconditions:
        - Returns a pipe-delimited string with FILE_HEADER, BATCH_HEADER,
          ENTRY lines, BATCH_CONTROL, and FILE_CONTROL.
        - Total in BATCH_CONTROL/FILE_CONTROL equals sum of entry amounts.
    Raises:
        ``decimal.InvalidOperation`` if amount is not numeric.

    Simplified formatter producing a pipe-delimited representation
    of ACH batch records.
    """
    lines: list[str] = []
    lines.append(f"FILE_HEADER|{company_name}|{company_id}")
    lines.append(f"BATCH_HEADER|PPD|{company_name}")

    total = Decimal("0")
    for i, payment in enumerate(payments, 1):
        amount = Decimal(str(payment.get("amount", "0")))
        total += amount
        lines.append(
            f"ENTRY|{i}|{payment.get('routing', '')}|"
            f"{payment.get('account', '')}|{amount}|"
            f"{payment.get('name', '')}"
        )

    lines.append(f"BATCH_CONTROL|{len(payments)}|{total}")
    lines.append(f"FILE_CONTROL|1|{len(payments)}|{total}")
    return "\n".join(lines)
