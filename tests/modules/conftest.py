"""
Shared fixtures for module tests.
"""

import pytest
from decimal import Decimal
from uuid import uuid4


@pytest.fixture
def sample_account_mappings():
    """Sample account mappings for testing."""
    return {
        "ar_asset": "1200-000",
        "revenue": "4000-000",
        "cash": "1000-000",
        "tax_liability": "2100-000",
    }


@pytest.fixture
def sample_approval_levels():
    """Sample approval hierarchy for testing."""
    return [
        {"max_amount": Decimal("1000.00"), "approver_role": "supervisor"},
        {"max_amount": Decimal("10000.00"), "approver_role": "manager"},
        {"max_amount": Decimal("100000.00"), "approver_role": "director"},
    ]


@pytest.fixture
def random_uuid():
    """Generate a random UUID."""
    return uuid4()
