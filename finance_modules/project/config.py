"""Project Accounting Configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProjectConfig:
    """Configuration for project accounting."""
    default_billing_method: str = "milestone"
    evm_enabled: bool = True
    auto_revenue_recognition: bool = False
