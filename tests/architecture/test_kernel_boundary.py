"""
Part 0: Kernel Boundary & Invariants Contract.

Tests that enforce the kernel's architectural boundaries:

1. finance_kernel/** may NOT import finance_services, finance_config,
   or finance_modules. The kernel never depends upward.

2. finance_kernel.models.journal (JournalEntry, JournalLine) may only
   be imported within finance_kernel/ and test files. Module services
   and engine code must never directly import journal models.

3. The kernel invariants declaration is complete and non-empty.

These tests read source code via AST — they cannot break anything.
"""

import ast
import glob
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _python_files(root: str) -> list[str]:
    """Return all .py files under root, relative to cwd."""
    return sorted(glob.glob(f"{root}/**/*.py", recursive=True))


def _is_test_file(path: str) -> bool:
    return "test_" in path or "/tests/" in path or "conftest" in path


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


# ---------------------------------------------------------------------------
# Test: Kernel has no upward dependencies
# ---------------------------------------------------------------------------

class TestKernelNoUpwardDependencies:
    """finance_kernel/** must not import finance_services, finance_config,
    or finance_modules."""

    FORBIDDEN_PREFIXES = (
        "finance_services",
        "finance_config",
        "finance_modules",
    )

    def test_kernel_does_not_import_forbidden_packages(self):
        violations: list[str] = []

        for filepath in _python_files("finance_kernel"):
            for lineno, module in _extract_imports(filepath):
                for prefix in self.FORBIDDEN_PREFIXES:
                    if module == prefix or module.startswith(f"{prefix}."):
                        violations.append(
                            f"  {filepath}:{lineno} imports '{module}'"
                        )

        assert not violations, (
            "Kernel boundary violation — finance_kernel/** must not import "
            "upward packages:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Test: Journal model import gate
# ---------------------------------------------------------------------------

class TestJournalModelImportGate:
    """finance_kernel.models.journal may only be imported by kernel-internal
    code and test files. Module services and engine code must not import it."""

    # These path prefixes are allowed to import journal models.
    ALLOWED_IMPORTERS = (
        "finance_kernel/",
    )

    JOURNAL_MODULE = "finance_kernel.models.journal"

    def test_no_journal_import_from_engines(self):
        """finance_engines/ must not import journal models."""
        violations: list[str] = []

        for filepath in _python_files("finance_engines"):
            for lineno, module in _extract_imports(filepath):
                if module == self.JOURNAL_MODULE or module.startswith(
                    f"{self.JOURNAL_MODULE}."
                ):
                    violations.append(
                        f"  {filepath}:{lineno} imports '{module}'"
                    )

        assert not violations, (
            "Posting boundary violation — finance_engines/ must not import "
            "journal models:\n" + "\n".join(violations)
        )

    def test_no_journal_import_from_modules(self):
        """finance_modules/ must not directly import journal models."""
        violations: list[str] = []

        for filepath in _python_files("finance_modules"):
            for lineno, module in _extract_imports(filepath):
                if module == self.JOURNAL_MODULE or module.startswith(
                    f"{self.JOURNAL_MODULE}."
                ):
                    violations.append(
                        f"  {filepath}:{lineno} imports '{module}'"
                    )

        assert not violations, (
            "Posting boundary violation — finance_modules/ must not import "
            "journal models directly. Use the posting facade instead:\n"
            + "\n".join(violations)
        )

    def test_no_journal_import_from_services_package(self):
        """finance_services/ (future stateful services) must not import
        journal models directly."""
        # finance_services/ may not exist yet — skip gracefully
        service_files = _python_files("finance_services")
        if not service_files:
            return  # Package not yet created

        violations: list[str] = []
        for filepath in service_files:
            for lineno, module in _extract_imports(filepath):
                if module == self.JOURNAL_MODULE or module.startswith(
                    f"{self.JOURNAL_MODULE}."
                ):
                    violations.append(
                        f"  {filepath}:{lineno} imports '{module}'"
                    )

        assert not violations, (
            "Posting boundary violation — finance_services/ must not import "
            "journal models directly:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Test: Kernel domain purity (subset of Part 0 — domain layer)
# ---------------------------------------------------------------------------

class TestKernelDomainPurity:
    """finance_kernel/domain/** must not import ORM or DB packages."""

    FORBIDDEN_MODULES = (
        "sqlalchemy",
        "psycopg2",
        "psycopg",
        "sqlite3",
        "finance_kernel.db",
    )

    # Allowlist: dtos.py has a TYPE_CHECKING import for journal types
    ALLOWED_EXCEPTIONS = {
        "finance_kernel/domain/dtos.py",
    }

    def test_domain_no_orm_imports(self):
        violations: list[str] = []

        for filepath in _python_files("finance_kernel/domain"):
            if filepath in self.ALLOWED_EXCEPTIONS:
                # Only allow TYPE_CHECKING imports, not runtime
                self._check_type_checking_only(filepath, violations)
                continue

            for lineno, module in _extract_imports(filepath):
                for forbidden in self.FORBIDDEN_MODULES:
                    if module == forbidden or module.startswith(
                        f"{forbidden}."
                    ):
                        violations.append(
                            f"  {filepath}:{lineno} imports '{module}'"
                        )

        assert not violations, (
            "Domain purity violation — finance_kernel/domain/** must not "
            "import ORM/DB packages at runtime:\n" + "\n".join(violations)
        )

    def _check_type_checking_only(
        self, filepath: str, violations: list[str]
    ) -> None:
        """Verify that forbidden imports in allowed files are under
        TYPE_CHECKING only."""
        try:
            source = Path(filepath).read_text()
            tree = ast.parse(source, filename=filepath)
        except (SyntaxError, UnicodeDecodeError):
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                # Check if this is `if TYPE_CHECKING:`
                is_type_checking = (
                    isinstance(node.test, ast.Name)
                    and node.test.id == "TYPE_CHECKING"
                ) or (
                    isinstance(node.test, ast.Attribute)
                    and node.test.attr == "TYPE_CHECKING"
                )

                if is_type_checking:
                    continue  # Imports under TYPE_CHECKING are fine

            # Check top-level imports (not inside TYPE_CHECKING)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = ""
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module = alias.name
                elif isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module

                for forbidden in self.FORBIDDEN_MODULES:
                    if module == forbidden or module.startswith(
                        f"{forbidden}."
                    ):
                        # Check if this import is at module level
                        # (not inside an if TYPE_CHECKING block)
                        violations.append(
                            f"  {filepath}:{node.lineno} imports '{module}' "
                            "at runtime (must be under TYPE_CHECKING)"
                        )


# ---------------------------------------------------------------------------
# Test: Invariants declaration exists and is complete
# ---------------------------------------------------------------------------

class TestKernelInvariantsDeclaration:
    """The kernel invariants contract must be declared and complete."""

    def test_invariants_module_exists(self):
        """finance_kernel.invariants must be importable."""
        from finance_kernel.invariants import ALL_KERNEL_INVARIANTS, KernelInvariant
        assert len(ALL_KERNEL_INVARIANTS) > 0

    def test_required_invariants_declared(self):
        """All six core invariants must be declared."""
        from finance_kernel.invariants import KernelInvariant

        required = {
            "DOUBLE_ENTRY_BALANCE",
            "IMMUTABILITY",
            "PERIOD_LOCK",
            "LINK_LEGALITY",
            "SEQUENCE_MONOTONICITY",
            "IDEMPOTENCY",
        }
        declared = {inv.name for inv in KernelInvariant}
        missing = required - declared
        assert not missing, f"Missing kernel invariants: {missing}"

    def test_forbidden_imports_declared(self):
        """The forbidden import list must include the three upward packages."""
        from finance_kernel.invariants import FORBIDDEN_KERNEL_IMPORTS

        for pkg in ("finance_services", "finance_config", "finance_modules"):
            assert pkg in FORBIDDEN_KERNEL_IMPORTS, (
                f"'{pkg}' not in FORBIDDEN_KERNEL_IMPORTS"
            )
