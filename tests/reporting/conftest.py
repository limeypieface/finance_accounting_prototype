"""
Reporting-specific test fixtures.

Provides:
- ReportingService instances
- Pre-configured account sets for financial statement testing
- Helper functions for posting events and generating reports
"""

import pytest
from datetime import date
from decimal import Decimal
from uuid import UUID

from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.selectors.ledger_selector import LedgerSelector

from finance_modules.reporting.config import ReportingConfig
from finance_modules.reporting.service import ReportingService
from finance_modules.reporting.statements import AccountInfo

from finance_kernel.models.account import AccountType, NormalBalance


@pytest.fixture
def reporting_config() -> ReportingConfig:
    """Standard reporting configuration for tests."""
    return ReportingConfig.with_defaults()


@pytest.fixture
def reporting_service(
    session,
    deterministic_clock,
    reporting_config,
) -> ReportingService:
    """ReportingService wired to the test session."""
    return ReportingService(
        session=session,
        clock=deterministic_clock,
        config=reporting_config,
    )


# =========================================================================
# Synthetic account data for pure function tests (no DB required)
# =========================================================================


def make_account_info(
    account_id: UUID,
    code: str,
    name: str,
    account_type: AccountType,
    normal_balance: NormalBalance,
    tags: tuple[str, ...] = (),
) -> AccountInfo:
    """Factory for AccountInfo used in pure tests."""
    return AccountInfo(
        account_id=account_id,
        code=code,
        name=name,
        account_type=account_type,
        normal_balance=normal_balance,
        tags=tags,
    )
