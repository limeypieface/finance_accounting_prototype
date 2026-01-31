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
        """Amount dataclass fields outside kernel should use Money, not raw Decimal.

        Relaxations:
        - Method parameters are not checked (only dataclass fields).
        - Frozen dataclasses are skipped — they are immutable DTOs that carry
          amounts alongside currency context at a higher level (report metadata,
          parent entity).  Only *mutable* dataclasses are checked, since those
          are more likely to be domain primitives that should use Money.
        - Mutable dataclasses that define a ``currency`` field are allowed —
          they have consciously paired amount + currency.
        - Config threshold / limit fields are allowed (e.g., ``max_amount``,
          ``match_tolerance_amount``) — they are limits, not monetary amounts.
        """
        THRESHOLD_SUFFIXES = ("_threshold", "_limit", "_max", "_min", "_tolerance")

        def _is_frozen_dataclass(decorator_list) -> bool:
            """Check if any @dataclass(..., frozen=True) decorator is present."""
            for d in decorator_list:
                if isinstance(d, ast.Call):
                    for kw in d.keywords:
                        if (
                            kw.arg == "frozen"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True
                        ):
                            return True
            return False

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
                if not isinstance(node, ast.ClassDef):
                    continue
                # Check if this class is a dataclass
                is_dataclass = any(
                    (isinstance(d, ast.Name) and d.id == "dataclass")
                    or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "dataclass")
                    or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
                    or (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "dataclass")
                    for d in node.decorator_list
                )
                if not is_dataclass:
                    continue

                # Frozen dataclasses are immutable DTOs — they carry amounts
                # alongside currency context at a higher level (report
                # metadata, parent entity).  Only mutable dataclasses could
                # be Money-like primitives that should use the kernel type.
                if _is_frozen_dataclass(node.decorator_list):
                    continue

                # Collect all field names on the dataclass
                field_names = {
                    item.target.id
                    for item in node.body
                    if isinstance(item, ast.AnnAssign)
                    and item.target
                    and isinstance(item.target, ast.Name)
                }

                # If the dataclass carries a currency field, it has
                # consciously paired amount + currency — allow it
                has_currency = any("currency" in f.lower() for f in field_names)
                if has_currency:
                    continue

                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and item.target and isinstance(item.target, ast.Name):
                        field_name = item.target.id
                        if "amount" not in field_name.lower():
                            continue
                        field_lower = field_name.lower()
                        # Skip config thresholds / limits
                        if any(field_lower.endswith(s) for s in THRESHOLD_SUFFIXES):
                            continue
                        if field_lower.startswith(("max_", "min_")):
                            continue
                        if "tolerance" in field_lower:
                            continue
                        ann = item.annotation
                        if isinstance(ann, ast.Name) and ann.id == "Decimal":
                            violations.append(f"{path}:{item.lineno}: {field_name}: Decimal")
                        elif isinstance(ann, ast.Attribute) and ann.attr == "Decimal":
                            violations.append(f"{path}:{item.lineno}: {field_name}: Decimal")

        assert not violations, f"Use Money not Decimal for dataclass amount fields:\n" + "\n".join(violations)

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

                    # Enum subclasses are not posting strategies
                    if any(b in ("Enum", "str") for b in bases):
                        continue

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
