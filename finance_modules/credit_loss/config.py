"""Credit Loss Configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CreditLossConfig:
    """Configuration for credit loss module."""
    default_method: str = "loss_rate"
    forward_looking_enabled: bool = True
    vintage_analysis_enabled: bool = True
