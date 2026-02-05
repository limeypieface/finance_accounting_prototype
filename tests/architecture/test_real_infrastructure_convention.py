"""
Architecture: tests must use real infrastructure unless isolation is critical.

Invariant: Tests that exercise posting, workflows, or module services must go through
the real pipeline (real DB session, real policy registry, real ModulePostingService
or InterpretationCoordinator). Bypasses (mocks of the posting pipeline, fake sessions,
or fixtures that skip register_modules) are allowed only when the test explicitly
needs isolation (e.g. unit tests of a single component, benchmarks that patch logging).

Enforcement:
1. Module service fixtures (ar_service, ap_service, module_posting_service, etc.) must
   depend on register_modules so that tests use the real policy registry and config.
2. Tests that mock ModulePostingService or InterpretationCoordinator must be in an
   allowlist (documented exceptions for isolated/orchestrator tests).
3. A full-stack proof test exists that posts via build_posting_orchestrator and
   asserts POSTED + journal entry in DB (see test_config_wiring + integration tests).

Run: pytest tests/architecture/test_real_infrastructure_convention.py -v
"""

from __future__ import annotations

import ast
from pathlib import Path


# Fixture names that provide a module posting service (AP, AR, GL, etc.) or the
# kernel ModulePostingService. These MUST require register_modules so tests use
# real policy registry and real config.
REQUIRE_REGISTER_MODULES_FIXTURES = frozenset({
    "ap_service",
    "ar_service",
    "asset_service",
    "assets_service",
    "budget_service",
    "cash_service",
    "contracts_service",
    "credit_loss_service",
    "expense_service",
    "gl_service",
    "ic_service",
    "inventory_service",
    "lease_service",
    "module_posting_service",
    "payroll_service",
    "procurement_service",
    "project_service",
    "revenue_service",
    "tax_service",
    "wip_service",
})

# Directories under tests/ where the convention applies (module and integration
# posting tests). Fixtures defined here with names in REQUIRE_REGISTER_MODULES_FIXTURES
# must list "register_modules" in their parameter list.
TEST_DIRS_TO_CHECK = ("tests/modules", "tests/integration", "tests/fuzzing")

# Files that define module_posting_service or *_service but are allowed to omit
# register_modules (e.g. fuzzing with its own fixture that still uses register_modules).
# If a file is listed here, we skip the register_modules check for that file.
ALLOWLIST_OMIT_REGISTER_MODULES: set[str] = set()

# Files allowed to mock ModulePostingService or InterpretationCoordinator (isolated
# tests: period close orchestrator with mock subledger services, benchmark that
# patches logging, etc.).
ALLOWLIST_MOCK_POSTING_PIPELINE = frozenset({
    "tests/services/test_period_close_orchestrator.py",  # mocks subledger services
    "tests/benchmarks/test_bench_decision_journal.py",   # may patch for timing
})


def _fixture_param_names(node: ast.FunctionDef) -> set[str]:
    """Return the parameter names of a function (args only, no *args/**kwargs)."""
    names = set()
    for arg in node.args.args:
        if arg.arg == "self":
            continue
        names.add(arg.arg)
    return names


def _is_fixture(node: ast.FunctionDef) -> bool:
    """True if the function is decorated with @pytest.fixture."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Attribute) and dec.func.attr == "fixture":
                return True
            if isinstance(dec.func, ast.Name) and dec.func.id == "fixture":
                return True
        if isinstance(dec, ast.Attribute) and dec.attr == "fixture":
            return True
        if isinstance(dec, ast.Name) and dec.id == "fixture":
            return True
    return False


def _collect_fixture_definitions(root: Path, dir_rel: str) -> list[tuple[Path, str, set[str]]]:
    """Return (filepath, fixture_name, param_names) for @pytest.fixture functions in root/dir_rel."""
    directory = root / dir_rel
    if not directory.exists():
        return []
    result = []
    for py_file in sorted(directory.rglob("*.py")):
        if py_file.name.startswith("__"):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name in REQUIRE_REGISTER_MODULES_FIXTURES
                and _is_fixture(node)
            ):
                params = _fixture_param_names(node)
                result.append((py_file, node.name, params))
    return result


class TestModuleServiceFixturesRequireRegisterModules:
    """Module service fixtures must depend on register_modules so tests use real infra."""

    def test_posting_service_fixtures_require_register_modules(self):
        """Any fixture named *_service (posting) in modules/integration/fuzzing must list register_modules."""
        root = Path(__file__).resolve().parents[2]
        violations = []
        for dir_rel in TEST_DIRS_TO_CHECK:
            for filepath, fixture_name, params in _collect_fixture_definitions(root, dir_rel):
                rel_path = str(filepath.relative_to(root))
                if rel_path in ALLOWLIST_OMIT_REGISTER_MODULES:
                    continue
                if "register_modules" not in params:
                    violations.append(
                        f"  {rel_path}: fixture '{fixture_name}' must list register_modules in its parameters "
                        "(tests must use real policy registry and config)"
                    )
        assert not violations, (
            "Real infrastructure convention — posting service fixtures must require register_modules. "
            "Tests that bypass the real policy registry make it easy to miss integration bugs.\n"
            + "\n".join(violations)
        )


def _files_that_mock_posting_pipeline(root: Path) -> list[tuple[str, str]]:
    """Return [(filepath, line_content)] that patch/mock ModulePostingService or InterpretationCoordinator."""
    found = []
    for py_file in root.rglob("*.py"):
        if "tests/" not in str(py_file):
            continue
        try:
            content = py_file.read_text()
            lines = content.splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(py_file.relative_to(root))
        if rel in ALLOWLIST_MOCK_POSTING_PIPELINE:
            continue
        for line in lines:
            stripped = line.strip()
            # Only flag actual mock usage: patch(... or MagicMock(..., and the target must be the pipeline
            if ("patch(" in stripped or "MagicMock(" in stripped) and (
                "ModulePostingService" in stripped or "InterpretationCoordinator" in stripped
            ):
                found.append((rel, stripped))
    return found


class TestNoUnauthorizedMockingOfPostingPipeline:
    """Tests must not mock ModulePostingService or InterpretationCoordinator unless allowlisted."""

    def test_no_mock_module_posting_service_or_coordinator_outside_allowlist(self):
        """Only allowlisted files may patch ModulePostingService or InterpretationCoordinator."""
        root = Path(__file__).resolve().parents[2]
        violations = _files_that_mock_posting_pipeline(root)
        assert not violations, (
            "Real infrastructure convention — do not mock ModulePostingService or "
            "InterpretationCoordinator in tests unless the test is explicitly isolated "
            "(e.g. period close orchestrator with mock subledger). Add the file to "
            "ALLOWLIST_MOCK_POSTING_PIPELINE in this module if the bypass is intentional.\n"
            + "\n".join(f"  {path}: {line}" for path, line in violations)
        )


class TestFullStackProofDocumentation:
    """Document where the full-stack (real config + real DB + real posting) is proven."""

    def test_full_stack_posting_tests_exist(self):
        """At least one test posts via build_posting_orchestrator and asserts real path."""
        # The actual proof is in:
        # - tests/config/test_config_wiring.py::TestBuildFromConfig::test_module_posting_service_from_built_orchestrator_uses_pack
        # - tests/integration/test_reversal_e2e.py (post via ModulePostingService → reverse → verify)
        # - tests/integration/test_module_posting_service.py
        # This test documents that the convention exists and the proof tests are in place.
        root = Path(__file__).resolve().parents[2]
        config_wiring = root / "tests" / "config" / "test_config_wiring.py"
        assert config_wiring.exists(), "test_config_wiring.py must exist (full-stack proof)"
        content = config_wiring.read_text()
        assert "build_posting_orchestrator" in content, "Config wiring must use build_posting_orchestrator"
        assert "ModulePostingService.from_orchestrator" in content, "Must post via ModulePostingService from orchestrator"
        assert "post_event" in content, "Must call post_event (real path)"
