import pytest

from finance_kernel.domain.workflow import Guard
from finance_services.workflow_executor import default_guard_executor


@pytest.fixture
def executor():
    return default_guard_executor()


def test_match_within_tolerance_passes(executor):
    guard = Guard(name="match_within_tolerance", description="")
    ctx = {"invoice_amount": 105, "po_amount": 100, "tolerance_percent": 5}
    assert executor.evaluate(guard, ctx) is True


def test_match_within_tolerance_blocks_over_variance(executor):
    guard = Guard(name="match_within_tolerance", description="")
    ctx = {"invoice_amount": 110, "po_amount": 100, "tolerance_percent": 5}
    assert executor.evaluate(guard, ctx) is False


def test_credit_check_passed_blocks_over_limit(executor):
    guard = Guard(name="credit_check_passed", description="")
    ctx = {"current_balance": 90, "proposed_amount": 20, "credit_limit": 100}
    assert executor.evaluate(guard, ctx) is False


def test_credit_check_passed_allows_within_limit(executor):
    guard = Guard(name="credit_check_passed", description="")
    ctx = {"current_balance": 60, "proposed_amount": 20, "credit_limit": 100}
    assert executor.evaluate(guard, ctx) is True


def test_balance_zero_requires_zero(executor):
    guard = Guard(name="balance_zero", description="")
    assert executor.evaluate(guard, {"balance_due": 0}) is True
    assert executor.evaluate(guard, {"balance_due": 0.01}) is False


def test_receipts_attached_respects_flag(executor):
    guard = Guard(name="receipts_attached", description="")
    assert executor.evaluate(guard, {"receipts_attached": True}) is True
    assert executor.evaluate(guard, {"receipts_attached": False}) is False


def test_fully_depreciated(executor):
    guard = Guard(name="fully_depreciated", description="")
    assert executor.evaluate(guard, {"net_book_value": 10, "salvage_value": 10}) is True
    assert executor.evaluate(guard, {"net_book_value": 11, "salvage_value": 10}) is False

