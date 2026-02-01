"""
Pure domain layer.

This module contains pure data transfer objects and domain logic
with NO dependencies on:
- ORM (SQLAlchemy)
- Database
- Time/clock
- I/O

All domain objects are immutable and deterministic.
"""

from finance_kernel.domain.bookkeeper import Bookkeeper, BookkeeperResult
from finance_kernel.domain.clock import Clock, DeterministicClock, SystemClock
from finance_kernel.domain.currency import CurrencyInfo, CurrencyRegistry
from finance_kernel.domain.dtos import (
    AccountInfo,
    # Entry DTOs
    EntryStatus,
    EventEnvelope,
    ExchangeRateInfo,
    FiscalPeriodInfo,
    JournalEntryDraft,
    JournalEntryRecord,
    LineSide,
    LineSpec,
    # R3 Compliance DTOs
    PeriodStatus,
    ProposedJournalEntry,
    ProposedLine,
    ReferenceData,
    ValidationError,
    ValidationResult,
)
from finance_kernel.domain.strategy import (
    BasePostingStrategy,
    PostingStrategy,
    StrategyResult,
)
from finance_kernel.domain.strategy_registry import (
    StrategyNotFoundError,
    StrategyRegistry,
    StrategyVersionNotFoundError,
    register_strategy,
)
from finance_kernel.domain.values import Currency, ExchangeRate, Money, Quantity

__all__ = [
    # Value Objects (R4)
    "Currency",
    "Money",
    "Quantity",
    "ExchangeRate",
    # DTOs
    "EntryStatus",
    "EventEnvelope",
    "JournalEntryDraft",
    "JournalEntryRecord",
    "LineSide",
    "LineSpec",
    "ProposedJournalEntry",
    "ProposedLine",
    "ReferenceData",
    "ValidationError",
    "ValidationResult",
    # R3 Compliance DTOs
    "PeriodStatus",
    "FiscalPeriodInfo",
    "AccountInfo",
    "ExchangeRateInfo",
    # Clock
    "Clock",
    "SystemClock",
    "DeterministicClock",
    # Currency
    "CurrencyRegistry",
    "CurrencyInfo",
    # Strategy
    "BasePostingStrategy",
    "PostingStrategy",
    "StrategyResult",
    "StrategyRegistry",
    "StrategyNotFoundError",
    "StrategyVersionNotFoundError",
    "register_strategy",
    # Bookkeeper
    "Bookkeeper",
    "BookkeeperResult",
]
