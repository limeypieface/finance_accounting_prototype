"""
Architecture: only the finance kernel may assert ModulePostingStatus.POSTED (R29).

Invariant: Services may request posting; they may not declare posting success.
- Service layer may return only: TRANSITION_APPLIED, TRANSITION_BLOCKED,
  TRANSITION_REJECTED, GUARD_BLOCKED, GUARD_REJECTED.
- Kernel layer may return: POSTED, REJECTED, and kernel-originated failure statuses.

Enforcement: finance_modules and finance_services must not construct
ModulePostingResult with status=ModulePostingStatus.POSTED.
"""

import importlib
import inspect
import re
from pathlib import Path


def _fabrication_pattern():
    """Pattern that detects service fabricating POSTED (constructing result with status=POSTED)."""
    return re.compile(
        r"status\s*=\s*ModulePostingStatus\.POSTED|"
        r"ModulePostingResult\s*\([^)]*status\s*=\s*ModulePostingStatus\.POSTED"
    )


def test_service_layer_must_not_assert_posted():
    """No file in finance_modules or finance_services may set status=ModulePostingStatus.POSTED."""
    violations = []
    pattern = _fabrication_pattern()
    for layer in ("finance_modules", "finance_services"):
        root = Path(layer)
        if not root.exists():
            continue
        for py_file in sorted(root.rglob("*.py")):
            try:
                src = py_file.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            if pattern.search(src):
                violations.append(f"  {py_file}: asserts ModulePostingStatus.POSTED (only kernel may)")

    assert not violations, (
        "Only the finance kernel may assert ModulePostingStatus.POSTED (R29). "
        "Service layer may return only TRANSITION_APPLIED, TRANSITION_BLOCKED, "
        "TRANSITION_REJECTED, GUARD_BLOCKED, GUARD_REJECTED.\n"
        + "\n".join(violations)
    )


def test_services_never_fabricate_posted_enumerated():
    """Hard invariant: every module service is explicitly checked — services must never fabricate POSTED."""
    # Explicit list of all module services; adding a new module requires adding here (lock-in).
    service_modules = [
        "finance_modules.ap.service",
        "finance_modules.ar.service",
        "finance_modules.assets.service",
        "finance_modules.budget.service",
        "finance_modules.cash.service",
        "finance_modules.contracts.service",
        "finance_modules.credit_loss.service",
        "finance_modules.expense.service",
        "finance_modules.gl.service",
        "finance_modules.intercompany.service",
        "finance_modules.inventory.service",
        "finance_modules.lease.service",
        "finance_modules.payroll.service",
        "finance_modules.procurement.service",
        "finance_modules.project.service",
        "finance_modules.reporting.service",
        "finance_modules.revenue.service",
        "finance_modules.tax.service",
        "finance_modules.wip.service",
    ]
    pattern = _fabrication_pattern()
    violations = []
    for mod_name in service_modules:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        try:
            src = inspect.getsource(mod)
        except (TypeError, OSError):
            continue
        if pattern.search(src):
            violations.append(f"  {mod_name}: contains status=ModulePostingStatus.POSTED (only kernel may)")

    assert not violations, (
        "Services must never fabricate POSTED. Only ModulePostingService.post_event() may return POSTED (R29).\n"
        + "\n".join(violations)
    )
