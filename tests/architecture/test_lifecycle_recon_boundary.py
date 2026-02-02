"""
Architecture boundary enforcement for reconciliation engines (GAP-REC + GAP-BRC).

Engine files (checker.py, lifecycle_types.py, bank_checker.py, bank_recon_types.py)
must be pure: no I/O, no database, no services.  They may only import from
finance_kernel/domain/ (values, economic_link), finance_engines/reconciliation/,
finance_engines/tracer, and standard library.
"""

import ast
import glob
from pathlib import Path


def _python_files(root: str) -> list[str]:
    return sorted(glob.glob(f"{root}/**/*.py", recursive=True))


def _extract_imports(filepath: str) -> list[tuple[int, str]]:
    try:
        source = Path(filepath).read_text()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError):
        return []
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                results.append((node.lineno, node.module))
    return results


# Modules the reconciliation engine is allowed to import
ALLOWED_PREFIXES = (
    "finance_kernel.domain",
    "finance_kernel.logging_config",
    "finance_engines.reconciliation",
    # Standard library and typing are always OK
)

# Modules that would violate purity
FORBIDDEN_PREFIXES = (
    "sqlalchemy",
    "finance_kernel.db",
    "finance_kernel.models",
    "finance_kernel.services",
    "finance_kernel.selectors",
    "finance_services",
    "finance_modules",
    "finance_batch",
    "finance_ingestion",
    "finance_config",
)


class TestReconciliationEnginePurity:
    """Lifecycle reconciliation engine files must remain pure (zero I/O)."""

    ENGINE_FILES = [
        "finance_engines/reconciliation/lifecycle_types.py",
        "finance_engines/reconciliation/checker.py",
        "finance_engines/reconciliation/bank_recon_types.py",
        "finance_engines/reconciliation/bank_checker.py",
    ]

    def test_no_io_imports(self):
        violations: list[str] = []
        for filepath in self.ENGINE_FILES:
            if not Path(filepath).exists():
                continue
            for lineno, module in _extract_imports(filepath):
                for prefix in FORBIDDEN_PREFIXES:
                    if module == prefix or module.startswith(f"{prefix}."):
                        violations.append(
                            f"  {filepath}:{lineno} imports '{module}'"
                        )
        assert not violations, (
            "Reconciliation engine must remain pure (no ORM/DB/services). "
            "Violations:\n" + "\n".join(violations)
        )

    def test_no_sqlalchemy_in_engine(self):
        """Engine files must not import sqlalchemy directly or indirectly."""
        violations: list[str] = []
        for filepath in self.ENGINE_FILES:
            if not Path(filepath).exists():
                continue
            for lineno, module in _extract_imports(filepath):
                if module.startswith("sqlalchemy"):
                    violations.append(
                        f"  {filepath}:{lineno} imports '{module}'"
                    )
        assert not violations, (
            "Reconciliation engine must not import sqlalchemy. "
            "Violations:\n" + "\n".join(violations)
        )


class TestServiceLayerImports:
    """Service wrapper may import from kernel but not from finance_modules."""

    SERVICE_FILES = [
        "finance_services/lifecycle_reconciliation_service.py",
        "finance_services/bank_reconciliation_check_service.py",
    ]

    def test_service_does_not_import_modules(self):
        violations: list[str] = []
        for filepath in self.SERVICE_FILES:
            if not Path(filepath).exists():
                continue
            for lineno, module in _extract_imports(filepath):
                if module.startswith("finance_modules"):
                    violations.append(
                        f"  {filepath}:{lineno} imports '{module}'"
                    )
        assert not violations, (
            "Lifecycle reconciliation service must not import finance_modules. "
            "Violations:\n" + "\n".join(violations)
        )
