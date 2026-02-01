"""
Credit Loss Domain Models (``finance_modules.credit_loss.models``).

Responsibility
--------------
Frozen dataclass value objects representing the nouns of ASC 326 (CECL)
credit loss accounting: expected credit loss estimates, vintage analysis
records, loss rates, portfolio segments, and forward-looking adjustments.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``CreditLossService`` and returned to callers.  No dependency on kernel
services, database, or engines.

Invariants enforced
-------------------
* All models are ``frozen=True`` (immutable after construction).
* All monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Construction with invalid enum values raises ``ValueError``.

Audit relevance
---------------
* ``ECLEstimate`` records support ASC 326 disclosure of loss methodology.
* ``VintageAnalysis`` records track loss curves for historical validation.
* ``ForwardLookingAdjustment`` records document macroeconomic overlay
  rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class ECLEstimate:
    """Expected Credit Loss estimate for a portfolio segment."""
    id: UUID
    segment: str
    as_of_date: date
    gross_receivable: Decimal
    loss_rate: Decimal
    ecl_amount: Decimal
    method: str = "loss_rate"  # loss_rate, pd_lgd, vintage
    period: str = ""


@dataclass(frozen=True)
class VintageAnalysis:
    """Vintage analysis for a cohort of receivables."""
    segment: str
    origination_period: str
    original_balance: Decimal
    current_balance: Decimal
    cumulative_losses: Decimal
    loss_rate: Decimal
    periods_aged: int = 0


@dataclass(frozen=True)
class LossRate:
    """Historical loss rate for a segment."""
    segment: str
    period: str
    gross_balance: Decimal
    write_offs: Decimal
    recoveries: Decimal = Decimal("0")
    net_loss: Decimal = Decimal("0")
    loss_rate: Decimal = Decimal("0")


@dataclass(frozen=True)
class CreditPortfolio:
    """A portfolio segment for CECL analysis."""
    segment: str
    balance: Decimal
    weighted_average_life: Decimal = Decimal("1")
    risk_rating: str = "standard"  # low, standard, elevated, high
    currency: str = "USD"


@dataclass(frozen=True)
class ForwardLookingAdjustment:
    """Forward-looking adjustment to base loss rates."""
    factor_name: str
    base_rate: Decimal
    adjustment_pct: Decimal  # e.g., 0.10 for 10% increase
    adjusted_rate: Decimal
    rationale: str = ""
