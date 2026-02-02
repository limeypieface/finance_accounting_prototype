"""
Import boundary enforcement (Phase 9).

finance_kernel/** and finance_modules/** must NOT import finance_ingestion.
Ingestion is a consumer of kernel and modules; kernel and modules must not depend on it.
"""

import ast
import glob
from pathlib import Path

FORBIDDEN = "finance_ingestion"


def _python_files(root: str) -> list[str]:
    return sorted(glob.glob(f"{root}/**/*.py", recursive=True))


def _is_test_or_conftest(path: str) -> bool:
    return "test_" in path or "/tests/" in path or "conftest" in path


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


class TestKernelDoesNotImportIngestion:
    """finance_kernel/** must not import finance_ingestion."""

    def test_kernel_no_import_finance_ingestion(self):
        violations: list[str] = []
        for filepath in _python_files("finance_kernel"):
            for lineno, module in _extract_imports(filepath):
                if module == FORBIDDEN or module.startswith(f"{FORBIDDEN}."):
                    violations.append(f"  {filepath}:{lineno} imports '{module}'")
        assert not violations, (
            "Kernel must not import finance_ingestion. Violations:\n" + "\n".join(violations)
        )


class TestModulesDoNotImportIngestion:
    """finance_modules/** must not import finance_ingestion (except _orm_registry for table registration)."""

    ALLOWED_IMPORTS = (
        "finance_ingestion.models",  # _orm_registry imports this to register staging tables (ERP_INGESTION_PLAN)
    )

    def test_modules_no_import_finance_ingestion(self):
        violations: list[str] = []
        for filepath in _python_files("finance_modules"):
            for lineno, module in _extract_imports(filepath):
                if module == FORBIDDEN or module.startswith(f"{FORBIDDEN}."):
                    if module not in self.ALLOWED_IMPORTS:
                        violations.append(f"  {filepath}:{lineno} imports '{module}'")
        assert not violations, (
            "Modules must not import finance_ingestion except allowed. Violations:\n" + "\n".join(violations)
        )


class TestFinanceEnginesDoNotImportIngestion:
    """finance_engines/** must not import finance_ingestion."""

    def test_engines_no_import_finance_ingestion(self):
        violations: list[str] = []
        for filepath in _python_files("finance_engines"):
            for lineno, module in _extract_imports(filepath):
                if module == FORBIDDEN or module.startswith(f"{FORBIDDEN}."):
                    violations.append(f"  {filepath}:{lineno} imports '{module}'")
        assert not violations, (
            "Engines must not import finance_ingestion. Violations:\n" + "\n".join(violations)
        )
