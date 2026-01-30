"""
Part 6: Comprehensive import-boundary enforcement.

Complements test_kernel_boundary.py (which covers kernel-upward dependencies,
journal model gating, domain purity, and invariants declaration) with tests for:

1. Engine purity      — finance_engines/** may not import DB, ORM, models,
                         services, or config layers.
2. Engine no-impure   — finance_engines/** may not call wall-clock or
                         environment functions at the module/function level.
3. Config centralisation — only finance_config/__init__.py may import internal
                         sub-modules (loader, assembler, validator).
4. Service boundary   — finance_services/** may not import finance_modules.
5. Dependency direction — validates the full dependency DAG.

All scanning is done via AST — these tests are read-only.
"""

import ast
import glob
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_kernel_boundary.py)
# ---------------------------------------------------------------------------

def _python_files(root: str) -> list[str]:
    """Return all .py files under *root*, sorted for deterministic order."""
    return sorted(glob.glob(f"{root}/**/*.py", recursive=True))


def _extract_imports(filepath: str) -> list[tuple[int, str]]:
    """Return (line_number, module_string) for every import in *filepath*."""
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


def _matches_any(module: str, prefixes: tuple[str, ...]) -> bool:
    """True if *module* equals or is a child of any prefix."""
    for prefix in prefixes:
        if module == prefix or module.startswith(f"{prefix}."):
            return True
    return False


def _extract_attribute_calls(filepath: str) -> list[tuple[int, str]]:
    """Return (line_number, 'receiver.attr') for ast.Attribute nodes.

    Only captures two-level attribute references (e.g. datetime.utcnow,
    os.environ) — sufficient for the impure-function scan.
    """
    try:
        source = Path(filepath).read_text()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError):
        return []

    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            results.append((node.lineno, f"{node.value.id}.{node.attr}"))
    return results


# ---------------------------------------------------------------------------
# 1. TestEnginePurity
# ---------------------------------------------------------------------------

class TestEnginePurity:
    """finance_engines/** may not import DB drivers, ORM, kernel models/db,
    services, config, or modules."""

    FORBIDDEN_PREFIXES = (
        "sqlalchemy",
        "psycopg2",
        "psycopg",
        "sqlite3",
        "finance_kernel.models",
        "finance_kernel.db",
        "finance_services",
        "finance_config",
        "finance_modules",
    )

    def test_engine_files_have_no_forbidden_imports(self):
        violations: list[str] = []

        for filepath in _python_files("finance_engines"):
            for lineno, module in _extract_imports(filepath):
                if _matches_any(module, self.FORBIDDEN_PREFIXES):
                    violations.append(
                        f"  {filepath}:{lineno} imports '{module}'"
                    )

        assert not violations, (
            "Engine purity violation — finance_engines/** must not import "
            "DB drivers, ORM, kernel models/db, services, config, or "
            "modules:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# 2. TestEngineNoImpureFunctions
# ---------------------------------------------------------------------------

class TestEngineNoImpureFunctions:
    """finance_engines/** may not call wall-clock or environment functions.

    Forbidden:
        datetime.now, datetime.utcnow, date.today,
        time.time, os.environ, os.getenv

    Allowed (observational-only):
        time.monotonic
    """

    FORBIDDEN_CALLS = frozenset({
        "datetime.now",
        "datetime.utcnow",
        "date.today",
        "time.time",
        "os.environ",
        "os.getenv",
    })

    # Known violations in the current codebase that are tracked for
    # remediation.  Each entry is (normalised_filepath, line, qualname).
    # The test will still flag *new* violations while tolerating these.
    KNOWN_VIOLATIONS: set[tuple[str, int, str]] = {
        ("finance_engines/correction/unwind.py", 342, "datetime.utcnow"),
        ("finance_engines/correction/unwind.py", 396, "datetime.utcnow"),
        ("finance_engines/matching.py", 316, "date.today"),
        ("finance_engines/subledger.py", 272, "date.today"),
        ("finance_engines/subledger.py", 298, "date.today"),
        ("finance_engines/tax.py", 106, "date.today"),
    }

    def test_no_impure_calls_in_engines(self):
        new_violations: list[str] = []

        for filepath in _python_files("finance_engines"):
            for lineno, qualname in _extract_attribute_calls(filepath):
                if qualname not in self.FORBIDDEN_CALLS:
                    continue
                normalised = filepath.replace("\\", "/")
                if (normalised, lineno, qualname) in self.KNOWN_VIOLATIONS:
                    continue  # tracked — do not fail the build
                new_violations.append(
                    f"  {filepath}:{lineno} calls '{qualname}'"
                )

        assert not new_violations, (
            "Engine impurity violation — finance_engines/** must not call "
            "wall-clock or environment functions.  Use an explicit clock "
            "parameter instead:\n" + "\n".join(new_violations)
        )

    def test_known_violations_still_exist(self):
        """Guard against silently removing an allowlist entry that no longer
        applies (stale known-violation).  If a known violation is fixed,
        it must be removed from KNOWN_VIOLATIONS too."""
        still_present: set[tuple[str, int, str]] = set()

        for filepath in _python_files("finance_engines"):
            for lineno, qualname in _extract_attribute_calls(filepath):
                normalised = filepath.replace("\\", "/")
                key = (normalised, lineno, qualname)
                if key in self.KNOWN_VIOLATIONS:
                    still_present.add(key)

        stale = self.KNOWN_VIOLATIONS - still_present
        assert not stale, (
            "Stale known-violation entries — these impure calls have been "
            "fixed and should be removed from KNOWN_VIOLATIONS:\n"
            + "\n".join(f"  {f}:{l} '{q}'" for f, l, q in sorted(stale))
        )


# ---------------------------------------------------------------------------
# 3. TestConfigCentralization
# ---------------------------------------------------------------------------

class TestConfigCentralization:
    """Only finance_config/__init__.py may import internal config sub-modules.

    External code is allowed to import from:
        finance_config          (the package — for get_active_config)
        finance_config.compiler (for CompiledPolicyPack type)
        finance_config.schema   (for type references)
        finance_config.bridges  (for bridge functions)

    Forbidden external imports:
        finance_config.loader
        finance_config.assembler
        finance_config.validator
    """

    FORBIDDEN_INTERNAL_MODULES = (
        "finance_config.loader",
        "finance_config.assembler",
        "finance_config.validator",
    )

    def test_no_external_import_of_config_internals(self):
        violations: list[str] = []

        for filepath in sorted(glob.glob("**/*.py", recursive=True)):
            normalised = filepath.replace("\\", "/")

            # Files inside finance_config/ are internal — skip them.
            if normalised.startswith("finance_config/"):
                continue

            for lineno, module in _extract_imports(filepath):
                if _matches_any(module, self.FORBIDDEN_INTERNAL_MODULES):
                    violations.append(
                        f"  {filepath}:{lineno} imports '{module}'"
                    )

        assert not violations, (
            "Config centralisation violation — only finance_config/ may "
            "import its internal sub-modules (loader, assembler, "
            "validator):\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# 4. TestServiceBoundary
# ---------------------------------------------------------------------------

class TestServiceBoundary:
    """finance_services/** may import finance_engines, finance_kernel,
    and sqlalchemy — but NOT finance_modules."""

    FORBIDDEN_PREFIXES = (
        "finance_modules",
    )

    def test_services_do_not_import_modules(self):
        service_files = _python_files("finance_services")
        if not service_files:
            return  # Package not yet created

        violations: list[str] = []
        for filepath in service_files:
            for lineno, module in _extract_imports(filepath):
                if _matches_any(module, self.FORBIDDEN_PREFIXES):
                    violations.append(
                        f"  {filepath}:{lineno} imports '{module}'"
                    )

        assert not violations, (
            "Service boundary violation — finance_services/** must not "
            "import finance_modules:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# 5. TestDependencyDirection
# ---------------------------------------------------------------------------

class TestDependencyDirection:
    """Verify the overall dependency DAG:

    Allowed edges (→ means "may import"):
        finance_modules  → finance_kernel, finance_engines,
                           finance_services, finance_config
        finance_services → finance_kernel, finance_engines
        finance_engines  → finance_kernel.domain, finance_kernel.logging_config
        finance_kernel   → (stdlib only + internal)

    Forbidden edges:
        finance_engines  ✗ finance_kernel.models, finance_kernel.db,
                           finance_services, finance_config, finance_modules
        finance_kernel   ✗ finance_services, finance_config, finance_modules,
                           finance_engines
        finance_services ✗ finance_modules
    """

    # (source_root, forbidden_prefixes)
    RULES: list[tuple[str, tuple[str, ...]]] = [
        (
            "finance_engines",
            (
                "finance_kernel.models",
                "finance_kernel.db",
                "finance_services",
                "finance_config",
                "finance_modules",
            ),
        ),
        (
            "finance_kernel",
            (
                "finance_services",
                "finance_config",
                "finance_modules",
                "finance_engines",
            ),
        ),
        (
            "finance_services",
            (
                "finance_modules",
            ),
        ),
    ]

    def test_dependency_dag(self):
        violations: list[str] = []

        for source_root, forbidden in self.RULES:
            source_files = _python_files(source_root)
            if not source_files:
                continue  # Package not yet created

            for filepath in source_files:
                for lineno, module in _extract_imports(filepath):
                    if _matches_any(module, forbidden):
                        violations.append(
                            f"  [{source_root}] {filepath}:{lineno} "
                            f"imports '{module}'"
                        )

        assert not violations, (
            "Dependency direction violation — the following imports break "
            "the layered architecture DAG:\n" + "\n".join(violations)
        )
