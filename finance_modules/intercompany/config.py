"""Intercompany module configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class IntercompanyConfig:
    """Configuration for intercompany processing."""

    default_currency: str = "USD"
    reconciliation_tolerance: Decimal = Decimal("0.01")
    auto_eliminate: bool = False
    require_agreement: bool = True
