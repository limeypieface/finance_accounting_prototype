"""
Cross-module structural and integration validation tests.

Validates that ALL 12 module services:
1. Are importable and follow the uniform constructor pattern (structural)
2. Can post a real event through the full posting pipeline (integration)
"""

from __future__ import annotations

import inspect
import importlib
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus

# All 12 module service classes
MODULE_SERVICES = [
    ("finance_modules.inventory.service", "InventoryService"),
    ("finance_modules.ap.service", "APService"),
    ("finance_modules.ar.service", "ARService"),
    ("finance_modules.cash.service", "CashService"),
    ("finance_modules.procurement.service", "ProcurementService"),
    ("finance_modules.wip.service", "WipService"),
    ("finance_modules.payroll.service", "PayrollService"),
    ("finance_modules.contracts.service", "GovernmentContractsService"),
    ("finance_modules.tax.service", "TaxService"),
    ("finance_modules.assets.service", "FixedAssetService"),
    ("finance_modules.gl.service", "GeneralLedgerService"),
    ("finance_modules.expense.service", "ExpenseService"),
]


class TestAllServicesExist:
    """Every module has a service.py with an importable service class."""

    @pytest.mark.parametrize("module_path,class_name", MODULE_SERVICES)
    def test_service_importable(self, module_path, class_name):
        """Each module service class is importable."""
        mod = importlib.import_module(module_path)
        svc_class = getattr(mod, class_name)
        assert svc_class is not None


class TestUniformConstructorSignature:
    """All services share (session, role_resolver, clock) constructor."""

    @pytest.mark.parametrize("module_path,class_name", MODULE_SERVICES)
    def test_constructor_has_session(self, module_path, class_name):
        mod = importlib.import_module(module_path)
        svc_class = getattr(mod, class_name)
        sig = inspect.signature(svc_class.__init__)
        assert "session" in sig.parameters

    @pytest.mark.parametrize("module_path,class_name", MODULE_SERVICES)
    def test_constructor_has_role_resolver(self, module_path, class_name):
        mod = importlib.import_module(module_path)
        svc_class = getattr(mod, class_name)
        sig = inspect.signature(svc_class.__init__)
        assert "role_resolver" in sig.parameters

    @pytest.mark.parametrize("module_path,class_name", MODULE_SERVICES)
    def test_constructor_has_clock(self, module_path, class_name):
        mod = importlib.import_module(module_path)
        svc_class = getattr(mod, class_name)
        sig = inspect.signature(svc_class.__init__)
        assert "clock" in sig.parameters


# =============================================================================
# Integration: Every service can post at least one event
# =============================================================================

# One representative call per module — uses the simplest method with a registered profile.
_MODULE_POST_CALLS = [
    (
        "finance_modules.inventory.service", "InventoryService",
        "receive_inventory",
        {
            "receipt_id": uuid4, "item_id": "WIDGET-001", "quantity": Decimal("10"),
            "unit_cost": Decimal("25.00"),
        },
    ),
    (
        "finance_modules.ap.service", "APService",
        "record_invoice",
        {"invoice_id": uuid4, "vendor_id": uuid4, "amount": Decimal("5000.00")},
    ),
    (
        "finance_modules.ar.service", "ARService",
        "record_invoice",
        {"invoice_id": uuid4, "customer_id": uuid4, "amount": Decimal("10000.00")},
    ),
    (
        "finance_modules.cash.service", "CashService",
        "record_receipt",
        {"receipt_id": uuid4, "amount": Decimal("5000.00")},
    ),
    (
        "finance_modules.procurement.service", "ProcurementService",
        "create_purchase_order",
        {
            "po_id": uuid4, "vendor_id": "V-001",
            "lines": [{"item_code": "W-001", "quantity": "10", "unit_price": "25.00"}],
        },
    ),
    (
        "finance_modules.wip.service", "WipService",
        "record_labor_charge",
        {
            "charge_id": uuid4, "job_id": "JOB-100",
            "hours": Decimal("40"), "rate": Decimal("50.00"),
        },
    ),
    (
        "finance_modules.payroll.service", "PayrollService",
        "record_payroll_run",
        {
            "run_id": uuid4, "employee_id": "EMP-001",
            "gross_pay": Decimal("5000.00"),
        },
    ),
    (
        "finance_modules.contracts.service", "GovernmentContractsService",
        "record_cost_incurrence",
        {
            "contract_id": "FA8750-21-C-0001",
            "cost_type": "DIRECT_LABOR",
            "amount": Decimal("50000.00"),
        },
    ),
    (
        "finance_modules.tax.service", "TaxService",
        "record_tax_obligation",
        {
            "obligation_id": uuid4, "tax_type": "sales_tax_collected",
            "amount": Decimal("600.00"), "jurisdiction": "CA",
        },
    ),
    (
        "finance_modules.assets.service", "FixedAssetService",
        "record_asset_acquisition",
        {
            "asset_id": uuid4, "cost": Decimal("50000.00"),
            "asset_class": "MACHINERY", "useful_life_months": 60,
        },
    ),
    (
        "finance_modules.gl.service", "GeneralLedgerService",
        "record_closing_entry",
        {"period_id": "2024-12", "net_income": Decimal("50000.00")},
    ),
    (
        "finance_modules.expense.service", "ExpenseService",
        "record_expense",
        {
            "expense_id": uuid4, "category": "TRAVEL",
            "amount": Decimal("500.00"),
        },
    ),
]


class TestAllServicesPostSuccessfully:
    """Every module service can post at least one real event through the full pipeline."""

    @pytest.mark.parametrize(
        "module_path,class_name,method_name,kwargs",
        _MODULE_POST_CALLS,
        ids=[c[1] for c in _MODULE_POST_CALLS],
    )
    def test_service_posts_event(
        self,
        module_path,
        class_name,
        method_name,
        kwargs,
        session,
        module_role_resolver,
        deterministic_clock,
        register_modules,
        current_period,
        test_actor_id,
    ):
        """Instantiate service and call one method through the real pipeline."""
        mod = importlib.import_module(module_path)
        svc_class = getattr(mod, class_name)
        svc = svc_class(
            session=session,
            role_resolver=module_role_resolver,
            clock=deterministic_clock,
        )

        # Resolve uuid4 callables to actual UUIDs
        resolved_kwargs = {}
        for k, v in kwargs.items():
            if v is uuid4:
                resolved_kwargs[k] = uuid4()
            else:
                resolved_kwargs[k] = v

        # Inject common parameters
        resolved_kwargs["effective_date"] = deterministic_clock.now().date()
        resolved_kwargs["actor_id"] = test_actor_id

        method = getattr(svc, method_name)
        result = method(**resolved_kwargs)

        assert result.status == ModulePostingStatus.POSTED, (
            f"{class_name}.{method_name}() returned {result.status} instead of POSTED"
        )
        assert result.is_success
