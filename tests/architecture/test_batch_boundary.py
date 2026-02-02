"""
Architecture boundary enforcement for finance_batch (BT-9).

finance_kernel/, finance_modules/, finance_engines/, and finance_services/
must NOT import finance_batch.  The batch system is a consumer of kernel
and modules; lower layers must not depend on it.
"""

import ast
import glob
from pathlib import Path

FORBIDDEN = "finance_batch"


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


class TestKernelDoesNotImportBatch:
    """finance_kernel/** must not import finance_batch (BT-9)."""

    def test_kernel_no_import_finance_batch(self):
        violations: list[str] = []
        for filepath in _python_files("finance_kernel"):
            for lineno, module in _extract_imports(filepath):
                if module == FORBIDDEN or module.startswith(f"{FORBIDDEN}."):
                    violations.append(f"  {filepath}:{lineno} imports '{module}'")
        assert not violations, (
            "Kernel must not import finance_batch (BT-9). Violations:\n"
            + "\n".join(violations)
        )


class TestModulesDoNotImportBatch:
    """finance_modules/** must not import finance_batch (BT-9).

    Exception: _orm_registry.py may import finance_batch.models for table
    registration (same pattern as finance_ingestion).
    """

    ALLOWED_IMPORTS = (
        "finance_batch.models",
    )

    def test_modules_no_import_finance_batch(self):
        violations: list[str] = []
        for filepath in _python_files("finance_modules"):
            for lineno, module in _extract_imports(filepath):
                if module == FORBIDDEN or module.startswith(f"{FORBIDDEN}."):
                    if module not in self.ALLOWED_IMPORTS:
                        violations.append(f"  {filepath}:{lineno} imports '{module}'")
        assert not violations, (
            "Modules must not import finance_batch except allowed (BT-9). "
            "Violations:\n" + "\n".join(violations)
        )


class TestEnginesDoNotImportBatch:
    """finance_engines/** must not import finance_batch (BT-9)."""

    def test_engines_no_import_finance_batch(self):
        violations: list[str] = []
        for filepath in _python_files("finance_engines"):
            for lineno, module in _extract_imports(filepath):
                if module == FORBIDDEN or module.startswith(f"{FORBIDDEN}."):
                    violations.append(f"  {filepath}:{lineno} imports '{module}'")
        assert not violations, (
            "Engines must not import finance_batch (BT-9). Violations:\n"
            + "\n".join(violations)
        )


class TestServicesDoNotImportBatch:
    """finance_services/** must not import finance_batch (BT-9)."""

    def test_services_no_import_finance_batch(self):
        violations: list[str] = []
        for filepath in _python_files("finance_services"):
            for lineno, module in _extract_imports(filepath):
                if module == FORBIDDEN or module.startswith(f"{FORBIDDEN}."):
                    violations.append(f"  {filepath}:{lineno} imports '{module}'")
        assert not violations, (
            "Services must not import finance_batch (BT-9). Violations:\n"
            + "\n".join(violations)
        )


class TestBatchDomainPurity:
    """finance_batch/domain/** must not import ORM/DB/services (pure code)."""

    FORBIDDEN_PREFIXES = (
        "sqlalchemy",
        "finance_kernel.db",
        "finance_kernel.services",
        "finance_kernel.selectors",
        "finance_batch.models",
        "finance_batch.services",
    )

    def test_batch_domain_no_io_imports(self):
        violations: list[str] = []
        for filepath in _python_files("finance_batch/domain"):
            for lineno, module in _extract_imports(filepath):
                for prefix in self.FORBIDDEN_PREFIXES:
                    if module == prefix or module.startswith(f"{prefix}."):
                        violations.append(
                            f"  {filepath}:{lineno} imports '{module}'"
                        )
        assert not violations, (
            "Batch domain must remain pure (no ORM/DB/services). Violations:\n"
            + "\n".join(violations)
        )
