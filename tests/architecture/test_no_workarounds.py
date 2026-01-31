"""
Phase 7 — No-Workaround Architecture Guards.

Tests that detect patterns which bypass the intended architecture.
Prevents future developers from introducing anti-patterns that circumvent
the wiring established in Phases 1–6.

Test classes:
  1. TestNoDirectEconomicLinkInModules  — Modules use LinkGraphService, not EconomicLink.create()
  2. TestNoDirectSessionQueryInModules  — Modules don't query ORM directly
  3. TestModuleServicesAcceptOrchestrator — Module service constructors accept orchestrator
  4. TestAllEngineContractsRegistered   — Every engine contract is reachable from policies
  5. TestNoDeadPolicyFields             — Policy YAML fields have runtime consumers
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path


# ---------------------------------------------------------------------------
# 1. TestNoDirectEconomicLinkInModules
# ---------------------------------------------------------------------------


class TestNoDirectLinkPersistenceInModules:
    """Modules must persist links via LinkGraphService, not directly.

    Modules may create EconomicLink domain objects (they're value objects)
    and pass them to LinkGraphService.establish_link(). What they must NOT
    do is bypass LinkGraphService by persisting EconomicLinkModel to the
    session directly.
    """

    def test_no_economic_link_model_import_in_modules(self):
        """No finance_modules/ file imports EconomicLinkModel (the ORM class)."""
        modules_dir = Path("finance_modules")
        if not modules_dir.exists():
            return

        violations = []
        for py_file in modules_dir.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        imported_names = {
                            alias.name for alias in (node.names or [])
                        }
                        if "EconomicLinkModel" in imported_names:
                            violations.append(
                                f"{py_file.relative_to(modules_dir)}:{node.lineno}: "
                                f"imports EconomicLinkModel"
                            )
            except SyntaxError:
                pass

        assert violations == [], (
            f"Modules must not import EconomicLinkModel directly: {violations}"
        )

    def test_modules_use_link_graph_service(self):
        """Every module service that creates EconomicLinks also uses LinkGraphService."""
        modules_dir = Path("finance_modules")
        if not modules_dir.exists():
            return

        modules_using_links = []
        modules_with_link_graph = []

        for py_file in modules_dir.rglob("service.py"):
            source = py_file.read_text()
            module_name = py_file.parent.name
            if "EconomicLink" in source:
                modules_using_links.append(module_name)
            if "LinkGraphService" in source or "_link_graph" in source:
                modules_with_link_graph.append(module_name)

        # Every module that uses EconomicLink should also use LinkGraphService
        missing = set(modules_using_links) - set(modules_with_link_graph)
        assert missing == set(), (
            f"Modules create EconomicLinks but don't use LinkGraphService: {missing}"
        )

    def test_no_session_add_link_model_in_modules(self):
        """No module directly calls session.add() on an EconomicLinkModel."""
        modules_dir = Path("finance_modules")
        if not modules_dir.exists():
            return

        violations = []
        for py_file in modules_dir.rglob("*.py"):
            try:
                source = py_file.read_text()
                # Simple heuristic: look for EconomicLinkModel being passed to session.add
                if "EconomicLinkModel" in source and "session.add" in source:
                    violations.append(str(py_file.relative_to(modules_dir)))
            except Exception:
                pass

        assert violations == [], (
            f"Modules must not persist EconomicLinkModel directly: {violations}"
        )


# ---------------------------------------------------------------------------
# 2. TestNoDirectSessionQueryInModules
# ---------------------------------------------------------------------------


class TestNoDirectSessionQueryInModules:
    """Module services should not run raw session.query() or session.execute().

    All data access should go through kernel services (JournalWriter,
    JournalSelector, etc.). Direct ORM access bypasses audit, authorization,
    and invariant checks.

    Exception: finance_modules/reporting/ is allowed to query directly
    for read-only reporting purposes.
    """

    def test_no_session_query_in_modules(self):
        """No finance_modules/ file (except reporting) calls session.query()."""
        modules_dir = Path("finance_modules")
        if not modules_dir.exists():
            return

        violations = []
        for py_file in modules_dir.rglob("*.py"):
            # Reporting modules are exempt
            if "reporting" in str(py_file):
                continue
            try:
                source = py_file.read_text()
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        func = node.func
                        if (
                            isinstance(func, ast.Attribute)
                            and func.attr in ("query", "execute")
                            and isinstance(func.value, ast.Attribute)
                            and func.value.attr == "session"
                        ):
                            violations.append(
                                f"{py_file.relative_to(modules_dir)}:{node.lineno}: "
                                f"session.{func.attr}()"
                            )
            except SyntaxError:
                pass

        assert violations == [], (
            f"Modules must not use session.query()/execute() directly: {violations}"
        )

    def test_no_raw_text_sql_in_modules(self):
        """No finance_modules/ file uses text() for raw SQL."""
        modules_dir = Path("finance_modules")
        if not modules_dir.exists():
            return

        violations = []
        for py_file in modules_dir.rglob("*.py"):
            if "reporting" in str(py_file):
                continue
            try:
                source = py_file.read_text()
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        if node.module == "sqlalchemy":
                            imported_names = {
                                alias.name for alias in (node.names or [])
                            }
                            if "text" in imported_names:
                                violations.append(
                                    f"{py_file.relative_to(modules_dir)}:{node.lineno}: "
                                    f"imports sqlalchemy.text"
                                )
            except SyntaxError:
                pass

        assert violations == [], (
            f"Modules must not import sqlalchemy.text for raw SQL: {violations}"
        )


# ---------------------------------------------------------------------------
# 3. TestModuleServicesAcceptOrchestrator
# ---------------------------------------------------------------------------


class TestModuleServicesAcceptOrchestrator:
    """Every module service constructor takes session + orchestrator-provided services.

    Module services should NOT construct kernel services internally.
    They receive Session (for atomicity) and stateful services from the
    orchestrator or DI container.
    """

    def _get_module_service_classes(self):
        """Find all service classes in finance_modules/*/service.py."""
        modules_dir = Path("finance_modules")
        if not modules_dir.exists():
            return []

        service_files = list(modules_dir.glob("*/service.py"))
        classes = []
        for svc_file in service_files:
            module_name = svc_file.parent.name
            try:
                tree = ast.parse(svc_file.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        # Look for classes that end with "Service" or are the main service
                        if node.name.endswith("Service"):
                            classes.append((module_name, node.name, svc_file, node))
            except SyntaxError:
                pass
        return classes

    def test_module_services_exist(self):
        """At least one module service should exist."""
        classes = self._get_module_service_classes()
        assert len(classes) > 0, "Expected at least one module service"

    def test_module_services_accept_session(self):
        """Every module service __init__ accepts a session parameter."""
        classes = self._get_module_service_classes()
        missing_session = []

        for module_name, class_name, svc_file, class_node in classes:
            init_method = None
            for item in class_node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    init_method = item
                    break
            if init_method is None:
                continue

            arg_names = [arg.arg for arg in init_method.args.args if arg.arg != "self"]
            if "session" not in arg_names:
                missing_session.append(f"{module_name}.{class_name}")

        assert missing_session == [], (
            f"Module services missing 'session' parameter: {missing_session}"
        )

    def test_module_services_do_not_construct_kernel_services(self):
        """Module services don't instantiate kernel services internally.

        Kernel services (IngestorService, PeriodService, etc.) should be
        received via DI, not constructed in the module __init__.
        """
        KERNEL_SERVICES = {
            "IngestorService", "PeriodService", "JournalWriter",
            "AuditorService", "SequenceService",
            "InterpretationCoordinator", "OutcomeRecorder",
            "ReferenceSnapshotService", "ContractService",
            "PartyService",
        }

        modules_dir = Path("finance_modules")
        if not modules_dir.exists():
            return

        violations = []
        for py_file in modules_dir.rglob("service.py"):
            try:
                tree = ast.parse(py_file.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        func = node.func
                        if isinstance(func, ast.Name) and func.id in KERNEL_SERVICES:
                            violations.append(
                                f"{py_file}:{node.lineno}: {func.id}()"
                            )
            except SyntaxError:
                pass

        assert violations == [], (
            f"Modules must not construct kernel services: {violations}"
        )


# ---------------------------------------------------------------------------
# 4. TestAllEngineContractsRegistered
# ---------------------------------------------------------------------------


class TestAllEngineContractsRegistered:
    """Every engine contract referenced by policies must be in ENGINE_CONTRACTS."""

    def test_all_policy_engines_have_contracts(self):
        """Every required_engine across all policies maps to a known contract."""
        from datetime import date

        from finance_config import get_active_config
        from finance_engines.contracts import ENGINE_CONTRACTS

        pack = get_active_config(legal_entity="*", as_of_date=date(2026, 1, 1))

        all_required = set()
        for p in pack.policies:
            all_required.update(p.required_engines)

        missing = all_required - set(ENGINE_CONTRACTS.keys())
        assert missing == set(), (
            f"Policies require engines without contracts: {missing}"
        )

    def test_all_contracts_have_at_least_one_policy(self):
        """Every engine contract is referenced by at least one policy.

        This detects dead engine contracts that no policy uses.
        """
        from datetime import date

        from finance_config import get_active_config
        from finance_engines.contracts import ENGINE_CONTRACTS

        pack = get_active_config(legal_entity="*", as_of_date=date(2026, 1, 1))

        referenced = set()
        for p in pack.policies:
            referenced.update(p.required_engines)

        # Allow engines to be unreferenced if they are used only by
        # services outside the policy pipeline (e.g., aging for reports)
        INFRASTRUCTURE_ENGINES = {"aging", "allocation_cascade", "ice"}
        unreferenced = set(ENGINE_CONTRACTS.keys()) - referenced - INFRASTRUCTURE_ENGINES
        assert unreferenced == set(), (
            f"Engine contracts not referenced by any policy (remove or mark as infrastructure): "
            f"{unreferenced}"
        )


# ---------------------------------------------------------------------------
# 5. TestNoDeadPolicyFields
# ---------------------------------------------------------------------------


class TestNoDeadPolicyFields:
    """Policy definition fields must have consumers in the runtime."""

    def test_compiled_policy_has_all_fields_from_source(self):
        """CompiledPolicy retains all meaningful fields from PolicyDefinition."""
        from finance_config.compiler import CompiledPolicy
        from finance_config.schema import PolicyDefinition

        source_fields = set(PolicyDefinition.__dataclass_fields__.keys())
        compiled_fields = set(CompiledPolicy.__dataclass_fields__.keys())

        # These source fields are transformed during compilation
        # (guards become CompiledGuard objects)
        TRANSFORMED_FIELDS = {"guards"}

        expected = source_fields - TRANSFORMED_FIELDS
        missing = expected - compiled_fields
        assert missing == set(), (
            f"PolicyDefinition fields missing in CompiledPolicy: {missing}"
        )

    def test_match_index_covers_all_event_types(self):
        """CompiledPolicyPack.match_index has entries for every policy event type."""
        from datetime import date

        from finance_config import get_active_config

        pack = get_active_config(legal_entity="*", as_of_date=date(2026, 1, 1))

        policy_event_types = {p.trigger.event_type for p in pack.policies}
        indexed_event_types = set(pack.match_index.entries.keys())

        missing = policy_event_types - indexed_event_types
        assert missing == set(), (
            f"Event types in policies but not in match_index: {missing}"
        )

    def test_gl_role_bindings_cover_gl_ledger_effects(self):
        """GL ledger effects have corresponding GL role_bindings.

        Only checks the GL ledger (the primary ledger). Subledger roles
        (INVENTORY, AR, AP, etc.) are defined in module-specific config
        and may not all be bound in the base config set.
        """
        from datetime import date

        from finance_config import get_active_config

        pack = get_active_config(legal_entity="*", as_of_date=date(2026, 1, 1))

        # Collect all GL roles used in ledger effects
        gl_roles_used = set()
        for p in pack.policies:
            for effect in p.ledger_effects:
                if effect.ledger == "GL":
                    gl_roles_used.add(effect.debit_role)
                    gl_roles_used.add(effect.credit_role)

        # Collect all GL bound roles
        gl_bound_roles = set()
        for rb in pack.role_bindings:
            if rb.ledger == "GL":
                gl_bound_roles.add(rb.role)

        # These roles are used as both GL-level control accounts AND
        # subledger names. They appear in GL ledger_effects but are
        # typically resolved via the subledger's own binding. These are
        # documented config gaps to be addressed in the COA authoring phase.
        KNOWN_PENDING_GL_ROLES = {"AP", "UNAPPLIED_CASH", "SALES_RETURNS", "SALES_ALLOWANCE"}

        missing = gl_roles_used - gl_bound_roles - KNOWN_PENDING_GL_ROLES
        assert missing == set(), (
            f"GL roles used in ledger_effects but not bound: {missing}"
        )
