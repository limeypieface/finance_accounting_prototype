"""
Architectural tests to prevent duplication of core primitives.
These tests READ code only - they cannot break anything.

Purpose: Catch new modules (like AP, AR) recreating primitives that
already exist in finance_kernel or finance_engines. Core infrastructure
is allowed internal flexibility - these rules apply to code OUTSIDE
the kernel and engines.
"""
import ast
import glob
from pathlib import Path

# Paths that are part of core infrastructure (allowed to use internal patterns)
CORE_PATHS = (
    "finance_kernel/domain/",
    "finance_kernel/models/",
    "finance_kernel/services/",
    "finance_kernel/selectors/",
    "finance_kernel/db/",
    "finance_kernel/exceptions.py",
    "finance_engines/",  # Shared engines are also core infrastructure
)


def is_core_path(path: str) -> bool:
    """Check if path is part of core infrastructure (kernel or engines)."""
    return any(core in path for core in CORE_PATHS)


def is_test_path(path: str) -> bool:
    """Check if path is a test file."""
    return "test_" in path or "/tests/" in path or "conftest" in path


class TestNoDuplicatePrimitives:
    """Ensure new modules reuse finance_kernel and finance_engines primitives."""

    def test_no_custom_money_classes(self):
        """No module outside kernel should define its own Money/Amount class."""
        violations = []
        for path in glob.glob("**/*.py", recursive=True):
            if is_core_path(path) or is_test_path(path):
                continue

            content = Path(path).read_text()
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    name_lower = node.name.lower()
                    if name_lower in ("money", "amount", "currency", "quantity"):
                        violations.append(f"{path}: class {node.name}")

        assert not violations, f"Duplicate primitives found:\n" + "\n".join(violations)

    def test_no_raw_decimal_amount_fields_outside_kernel(self):
        """Amount fields outside kernel should use Money, not raw Decimal."""
        violations = []
        for path in glob.glob("**/*.py", recursive=True):
            if is_core_path(path) or is_test_path(path):
                continue

            content = Path(path).read_text()
            for i, line in enumerate(content.split("\n"), 1):
                # Catch: amount: Decimal, total: Decimal, etc.
                if "amount" in line.lower() and ": Decimal" in line:
                    violations.append(f"{path}:{i}: {line.strip()}")

        assert not violations, f"Use Money not Decimal:\n" + "\n".join(violations)

    def test_posting_strategies_extend_base(self):
        """All posting strategies must extend BasePostingStrategy."""
        # Patterns that look like "Strategy" but aren't posting strategies
        EXCLUDED_SUFFIXES = ("Error", "Exception", "Registry", "Config", "Settings")

        violations = []
        for path in glob.glob("**/*.py", recursive=True):
            if "finance_kernel/domain/strategy.py" in path:
                continue
            if is_test_path(path):
                continue

            content = Path(path).read_text()
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and "Strategy" in node.name:
                    # Skip non-posting-strategy classes
                    if any(node.name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
                        continue

                    # Must end with "Strategy" to be a posting strategy
                    if not node.name.endswith("Strategy"):
                        continue

                    bases = [self._get_base_name(b) for b in node.bases]
                    if not any("BasePostingStrategy" in b or "PostingStrategy" in b for b in bases):
                        violations.append(f"{path}: {node.name} doesn't extend BasePostingStrategy")

        assert not violations, f"Strategy classes must extend BasePostingStrategy:\n" + "\n".join(violations)

    def _get_base_name(self, node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""


class TestEngineDependencies:
    """Ensure finance_engines depends on finance_kernel, not vice versa."""

    def test_engines_import_kernel_primitives(self):
        """finance_engines should import Money/Currency from finance_kernel."""
        import_found = False
        for path in glob.glob("finance_engines/*.py", recursive=True):
            if "__init__" in path:
                continue

            content = Path(path).read_text()
            if "from finance_kernel" in content:
                import_found = True
                break

        assert import_found, "finance_engines should import from finance_kernel"

    def test_kernel_does_not_import_engines(self):
        """finance_kernel should NOT import from finance_engines."""
        violations = []
        for path in glob.glob("finance_kernel/**/*.py", recursive=True):
            content = Path(path).read_text()
            if "from finance_engines" in content or "import finance_engines" in content:
                violations.append(path)

        assert not violations, f"Kernel imports engines (wrong direction):\n" + "\n".join(violations)


class TestNoParallelHierarchies:
    """Prevent creation of parallel type hierarchies outside core."""

    def test_no_custom_exception_base_classes(self):
        """Exceptions outside kernel should extend FinanceKernelError."""
        violations = []
        for path in glob.glob("**/*.py", recursive=True):
            if is_core_path(path) or is_test_path(path):
                continue

            content = Path(path).read_text()
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name.endswith("Error"):
                    bases = [self._get_base_name(b) for b in node.bases]
                    # Should extend something from kernel, not raw Exception
                    if "Exception" in bases or "BaseException" in bases:
                        violations.append(
                            f"{path}: {node.name} extends Exception directly. "
                            "Use FinanceKernelError or a subclass."
                        )

        assert not violations, f"Use kernel exception hierarchy:\n" + "\n".join(violations)

    def _get_base_name(self, node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""
