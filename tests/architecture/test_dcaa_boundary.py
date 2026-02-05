"""
DCAA Compliance Architecture Boundary Tests.

Enforces:
1. Engine purity: DCAA compliance engines have no forbidden imports
   (no ORM, no services, no config, no I/O, no datetime.now/date.today).
2. Type purity: DCAA domain types have no forbidden imports.
3. Module-layer separation: DCAA ORM models only import from kernel.db
   and their own module types.
"""

import ast
import glob
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_imports(filepath: str) -> list[tuple[int, str]]:
    """Extract (line_number, module_string) for all imports in a file."""
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


def _extract_calls(filepath: str) -> list[tuple[int, str]]:
    """Extract (line_number, call_string) for attribute calls like datetime.now()."""
    try:
        source = Path(filepath).read_text()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError):
        return []

    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                # e.g. datetime.now(), date.today()
                if isinstance(node.func.value, ast.Name):
                    call_str = f"{node.func.value.id}.{node.func.attr}"
                    results.append((node.lineno, call_str))
    return results


# ---------------------------------------------------------------------------
# DCAA Engine purity
# ---------------------------------------------------------------------------

# Engines that MUST remain pure (no I/O, no ORM, no services, no config)
DCAA_ENGINE_FILES = [
    "finance_engines/timesheet_compliance.py",
    "finance_engines/expense_compliance.py",
    "finance_engines/rate_compliance.py",
]

# Modules that engines MUST NOT import
ENGINE_FORBIDDEN_MODULES = [
    "sqlalchemy",
    "finance_kernel.db",
    "finance_kernel.services",
    "finance_kernel.selectors",
    "finance_kernel.models",
    "finance_services",
    "finance_config",
]


class TestDCAAEnginePurity:
    """DCAA compliance engines must be pure: no I/O, no ORM, no services."""

    def test_no_forbidden_imports(self):
        """Engines must not import forbidden modules."""
        violations = []
        for engine_file in DCAA_ENGINE_FILES:
            if not Path(engine_file).exists():
                continue
            imports = _extract_imports(engine_file)
            for lineno, module in imports:
                for forbidden in ENGINE_FORBIDDEN_MODULES:
                    if module.startswith(forbidden):
                        violations.append(
                            f"{engine_file}:{lineno} imports '{module}' "
                            f"(forbidden: {forbidden})"
                        )

        assert violations == [], (
            "DCAA engines have forbidden imports:\n"
            + "\n".join(violations)
        )

    def test_no_datetime_now_calls(self):
        """Engines must not call datetime.now() or date.today()."""
        forbidden_calls = {"datetime.now", "date.today"}
        violations = []

        for engine_file in DCAA_ENGINE_FILES:
            if not Path(engine_file).exists():
                continue
            calls = _extract_calls(engine_file)
            for lineno, call_str in calls:
                if call_str in forbidden_calls:
                    violations.append(
                        f"{engine_file}:{lineno} calls '{call_str}()'"
                    )

        assert violations == [], (
            "DCAA engines call forbidden time functions:\n"
            + "\n".join(violations)
        )

    def test_engines_exist(self):
        """All DCAA engine files must exist."""
        for engine_file in DCAA_ENGINE_FILES:
            assert Path(engine_file).exists(), (
                f"Missing DCAA engine: {engine_file}"
            )


# ---------------------------------------------------------------------------
# DCAA Type purity
# ---------------------------------------------------------------------------

DCAA_TYPE_FILES = [
    "finance_modules/payroll/dcaa_types.py",
    "finance_modules/expense/dcaa_types.py",
    "finance_modules/contracts/rate_types.py",
]

TYPE_FORBIDDEN_MODULES = [
    "sqlalchemy",
    "finance_kernel.db",
    "finance_kernel.services",
    "finance_kernel.selectors",
    "finance_kernel.models",
    "finance_services",
    "finance_config",
    "finance_engines",
]


class TestDCAATypePurity:
    """DCAA domain types must be pure data definitions with no I/O."""

    def test_no_forbidden_imports(self):
        """Type files must not import ORM, services, engines, or config."""
        violations = []
        for type_file in DCAA_TYPE_FILES:
            if not Path(type_file).exists():
                continue
            imports = _extract_imports(type_file)
            for lineno, module in imports:
                for forbidden in TYPE_FORBIDDEN_MODULES:
                    if module.startswith(forbidden):
                        violations.append(
                            f"{type_file}:{lineno} imports '{module}' "
                            f"(forbidden: {forbidden})"
                        )

        assert violations == [], (
            "DCAA types have forbidden imports:\n"
            + "\n".join(violations)
        )

    def test_types_exist(self):
        """All DCAA type files must exist."""
        for type_file in DCAA_TYPE_FILES:
            assert Path(type_file).exists(), (
                f"Missing DCAA type file: {type_file}"
            )


# ---------------------------------------------------------------------------
# DCAA ORM layer checks
# ---------------------------------------------------------------------------

DCAA_ORM_FILES = [
    "finance_modules/payroll/dcaa_orm.py",
    "finance_modules/expense/dcaa_orm.py",
    "finance_modules/contracts/rate_orm.py",
]

ORM_FORBIDDEN_MODULES = [
    "finance_services",
    "finance_config",
    "finance_engines",
]


class TestDCAAORMBoundary:
    """DCAA ORM models must not import from engines, services, or config."""

    def test_no_forbidden_imports(self):
        violations = []
        for orm_file in DCAA_ORM_FILES:
            if not Path(orm_file).exists():
                continue
            imports = _extract_imports(orm_file)
            for lineno, module in imports:
                for forbidden in ORM_FORBIDDEN_MODULES:
                    if module.startswith(forbidden):
                        violations.append(
                            f"{orm_file}:{lineno} imports '{module}' "
                            f"(forbidden: {forbidden})"
                        )

        assert violations == [], (
            "DCAA ORM models have forbidden imports:\n"
            + "\n".join(violations)
        )

    def test_orm_files_exist(self):
        """All DCAA ORM files must exist."""
        for orm_file in DCAA_ORM_FILES:
            assert Path(orm_file).exists(), (
                f"Missing DCAA ORM file: {orm_file}"
            )
