"""
Lightweight domain validation helpers (R25: kernel primitives).

Pure checks with no I/O. Used at module/kernel boundaries to enforce
Decimal for monetary amounts and other invariants.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def require_decimal(value: Any, name: str = "amount") -> None:
    """Assert that value is a Decimal; raise AssertionError otherwise (R25 / R4)."""
    assert isinstance(value, Decimal), (
        f"{name} must be Decimal, not {type(value).__name__}"
    )
