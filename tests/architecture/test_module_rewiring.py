"""
Phase 5 — Module Rewiring Tests.

Tests that prove Phase 5 wiring gaps (G6, G16) are closed:
  G6:  Every variance policy declares required_engines and variance_disposition
  G16: Module engine ownership is compliant (stateless in modules, dispatcher
       for policy-driven invocation)

Test classes:
  1. TestVariancePolicyYAML          — YAML files declare required_engines + disposition
  2. TestVarianceDispositionCompiled  — variance_disposition flows through compiler
  3. TestEngineDispatcherWiring       — EngineDispatcher reads compiled fields correctly
  4. TestModuleEngineOwnership        — Modules own stateless engines; kernel stays clean
  5. TestEngineContractCoverage       — Every required_engine maps to a known contract
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# 1. TestVariancePolicyYAML — YAML files declare required_engines + disposition
# ---------------------------------------------------------------------------


class TestVariancePolicyYAML:
    """Every variance policy YAML declares required_engines and variance_disposition."""

    def _load_policies_from_yaml(self, filename: str):
        """Load all policies from a YAML fragment."""
        from pathlib import Path

        from finance_config.loader import load_yaml_file, parse_policy

        path = Path("finance_config/sets/US-GAAP-2026-v1/policies") / filename
        data = load_yaml_file(path)
        return [parse_policy(p) for p in data.get("policies", [])]

    def test_inventory_receipt_with_variance_has_required_engines(self):
        """InventoryReceiptWithVariance declares required_engines: [variance]."""
        policies = self._load_policies_from_yaml("inventory.yaml")
        policy = next(p for p in policies if p.name == "InventoryReceiptWithVariance")
        assert "variance" in policy.required_engines

    def test_inventory_receipt_with_variance_has_variance_disposition(self):
        """InventoryReceiptWithVariance declares variance_disposition: post."""
        policies = self._load_policies_from_yaml("inventory.yaml")
        policy = next(p for p in policies if p.name == "InventoryReceiptWithVariance")
        assert policy.variance_disposition == "post"

    def test_inventory_receipt_with_variance_has_engine_parameters_ref(self):
        """InventoryReceiptWithVariance has engine_parameters_ref: variance."""
        policies = self._load_policies_from_yaml("inventory.yaml")
        policy = next(p for p in policies if p.name == "InventoryReceiptWithVariance")
        assert policy.engine_parameters_ref == "variance"

    def test_wip_labor_variance_has_required_engines(self):
        """WipLaborVariance declares required_engines: [variance]."""
        policies = self._load_policies_from_yaml("wip.yaml")
        policy = next(p for p in policies if p.name == "WipLaborVariance")
        assert "variance" in policy.required_engines
        assert policy.variance_disposition == "post"
        assert policy.engine_parameters_ref == "variance"

    def test_wip_material_variance_has_required_engines(self):
        """WipMaterialVariance declares required_engines: [variance]."""
        policies = self._load_policies_from_yaml("wip.yaml")
        policy = next(p for p in policies if p.name == "WipMaterialVariance")
        assert "variance" in policy.required_engines
        assert policy.variance_disposition == "post"
        assert policy.engine_parameters_ref == "variance"

    def test_wip_overhead_variance_has_required_engines(self):
        """WipOverheadVariance declares required_engines: [variance]."""
        policies = self._load_policies_from_yaml("wip.yaml")
        policy = next(p for p in policies if p.name == "WipOverheadVariance")
        assert "variance" in policy.required_engines
        assert policy.variance_disposition == "post"
        assert policy.engine_parameters_ref == "variance"

    def test_non_variance_policies_have_no_disposition(self):
        """Non-variance inventory policies should NOT have variance_disposition."""
        policies = self._load_policies_from_yaml("inventory.yaml")
        non_variance = [p for p in policies if p.name != "InventoryReceiptWithVariance"]
        assert len(non_variance) > 0, "Expected non-variance policies"
        for p in non_variance:
            assert p.variance_disposition is None, (
                f"Policy {p.name} should not have variance_disposition"
            )


# ---------------------------------------------------------------------------
# 2. TestVarianceDispositionCompiled — flows through compiler
# ---------------------------------------------------------------------------


class TestVarianceDispositionCompiled:
    """variance_disposition survives compilation to CompiledPolicy."""

    def _get_compiled_pack(self):
        from datetime import date

        from finance_config import get_active_config

        return get_active_config(legal_entity="*", as_of_date=date(2026, 1, 1))

    def test_compiled_policy_has_variance_disposition(self):
        """CompiledPolicy includes variance_disposition field."""
        from finance_config.compiler import CompiledPolicy

        assert hasattr(CompiledPolicy, "__dataclass_fields__")
        assert "variance_disposition" in CompiledPolicy.__dataclass_fields__

    def test_variance_policies_carry_disposition_through_compilation(self):
        """Variance policies retain variance_disposition=post after compilation."""
        pack = self._get_compiled_pack()
        variance_names = {
            "InventoryReceiptWithVariance",
            "WipLaborVariance",
            "WipMaterialVariance",
            "WipOverheadVariance",
        }
        found = [p for p in pack.policies if p.name in variance_names]
        assert len(found) == 4, f"Expected 4 variance policies, got {len(found)}"
        for p in found:
            assert p.variance_disposition == "post", (
                f"Policy {p.name} should have variance_disposition='post', "
                f"got {p.variance_disposition!r}"
            )

    def test_non_variance_policies_have_null_disposition_in_pack(self):
        """Non-variance compiled policies have variance_disposition=None."""
        pack = self._get_compiled_pack()
        variance_names = {
            "InventoryReceiptWithVariance",
            "WipLaborVariance",
            "WipMaterialVariance",
            "WipOverheadVariance",
        }
        non_variance = [p for p in pack.policies if p.name not in variance_names]
        assert len(non_variance) > 0
        for p in non_variance:
            assert p.variance_disposition is None, (
                f"Policy {p.name} should have variance_disposition=None"
            )

    def test_compiled_required_engines_are_tuples(self):
        """CompiledPolicy.required_engines is always a tuple."""
        pack = self._get_compiled_pack()
        for p in pack.policies:
            assert isinstance(p.required_engines, tuple), (
                f"Policy {p.name} required_engines should be tuple"
            )


# ---------------------------------------------------------------------------
# 3. TestEngineDispatcherWiring — dispatcher reads compiled fields
# ---------------------------------------------------------------------------


class TestEngineDispatcherWiring:
    """EngineDispatcher reads required_engines and engine_parameters_ref correctly."""

    def _make_compiled_policy(self, **overrides):
        """Create a minimal CompiledPolicy for testing."""
        from datetime import date

        from finance_config.compiler import CompiledPolicy
        from finance_config.schema import PolicyMeaningDef, PolicyTriggerDef

        defaults = dict(
            name="TestPolicy",
            version=1,
            trigger=PolicyTriggerDef(event_type="test.event"),
            meaning=PolicyMeaningDef(economic_type="TEST"),
            ledger_effects=(),
            guards=(),
            effective_from=date(2024, 1, 1),
            effective_to=None,
            scope="*",
            precedence=None,
            valuation_model=None,
            line_mappings=(),
            required_engines=(),
            engine_parameters_ref=None,
            variance_disposition=None,
            capability_tags=(),
            description="Test",
            module="test",
        )
        defaults.update(overrides)
        return CompiledPolicy(**defaults)

    def _make_pack(self, policies=(), engine_params=None):
        """Create a minimal CompiledPolicyPack for testing."""
        from datetime import date

        from finance_config.compiler import (
            CompiledPolicyPack,
            PolicyDecisionTrace,
            PolicyMatchIndex,
        )
        from finance_config.schema import ConfigScope

        return CompiledPolicyPack(
            config_id="TEST",
            config_version=1,
            checksum="abc123",
            scope=ConfigScope(
                legal_entity="*",
                jurisdiction="US",
                regulatory_regime="GAAP",
                currency="USD",
                effective_from=date(2024, 1, 1),
            ),
            policies=tuple(policies),
            match_index=PolicyMatchIndex(entries={}),
            role_bindings=(),
            engine_contracts={},
            resolved_engine_params=engine_params or {},
            controls=(),
            capabilities={},
            canonical_fingerprint="test_fingerprint",
            decision_trace=PolicyDecisionTrace(event_type_decisions={}),
        )

    def test_dispatch_no_engines_returns_empty_success(self):
        """Policy with no required_engines returns empty success result."""
        from finance_kernel.services.engine_dispatcher import EngineDispatcher

        policy = self._make_compiled_policy(required_engines=())
        pack = self._make_pack(policies=[policy])
        dispatcher = EngineDispatcher(pack)
        result = dispatcher.dispatch(policy, {"test": "payload"})
        assert result.all_succeeded is True
        assert result.engine_outputs == {}
        assert result.traces == ()

    def test_dispatch_unregistered_engine_reports_error(self):
        """Dispatching an unregistered engine produces an error, not a crash."""
        from finance_kernel.services.engine_dispatcher import EngineDispatcher

        policy = self._make_compiled_policy(
            name="VariancePolicy",
            required_engines=("variance",),
            engine_parameters_ref="variance",
        )
        pack = self._make_pack(policies=[policy])
        dispatcher = EngineDispatcher(pack)
        result = dispatcher.dispatch(policy, {"test": "payload"})
        assert result.all_succeeded is False
        assert len(result.errors) == 1
        assert "variance" in result.errors[0]
        assert "no registered invoker" in result.errors[0]

    def test_dispatch_registered_engine_invokes_correctly(self):
        """Registered engine receives payload and params, returns result."""
        from finance_config.compiler import FrozenEngineParams
        from finance_kernel.services.engine_dispatcher import (
            EngineDispatcher,
            EngineInvoker,
        )

        policy = self._make_compiled_policy(
            name="VariancePolicy",
            required_engines=("variance",),
            engine_parameters_ref="variance",
        )
        params = FrozenEngineParams(
            engine_name="variance",
            parameters={"tolerance_percent": 5.0},
        )
        pack = self._make_pack(
            policies=[policy],
            engine_params={"variance": params},
        )
        dispatcher = EngineDispatcher(pack)

        captured = {}

        def fake_invoke(payload, frozen_params):
            captured["payload"] = payload
            captured["params"] = frozen_params
            return {"variance_amount": 42}

        invoker = EngineInvoker(
            engine_name="variance",
            engine_version="1.0",
            invoke=fake_invoke,
        )
        dispatcher.register("variance", invoker)

        result = dispatcher.dispatch(policy, {"quantity": 10, "price": 100})
        assert result.all_succeeded is True
        assert result.engine_outputs["variance"] == {"variance_amount": 42}
        assert captured["params"].parameters == {"tolerance_percent": 5.0}
        assert len(result.traces) == 1
        assert result.traces[0].engine_name == "variance"
        assert result.traces[0].success is True

    def test_dispatch_engine_exception_captured_as_error(self):
        """Engine raising an exception is captured, not propagated."""
        from finance_config.compiler import FrozenEngineParams
        from finance_kernel.services.engine_dispatcher import (
            EngineDispatcher,
            EngineInvoker,
        )

        policy = self._make_compiled_policy(
            name="FailPolicy",
            required_engines=("variance",),
        )
        params = FrozenEngineParams(engine_name="variance", parameters={})
        pack = self._make_pack(
            policies=[policy],
            engine_params={"variance": params},
        )
        dispatcher = EngineDispatcher(pack)

        def exploding_invoke(payload, frozen_params):
            raise ValueError("Division by zero in variance calc")

        invoker = EngineInvoker(
            engine_name="variance",
            engine_version="1.0",
            invoke=exploding_invoke,
        )
        dispatcher.register("variance", invoker)

        result = dispatcher.dispatch(policy, {})
        assert result.all_succeeded is False
        assert "Division by zero" in result.errors[0]
        assert result.traces[0].success is False

    def test_validate_registration_detects_missing_engines(self):
        """validate_registration returns unregistered engine names."""
        from finance_config.compiler import ResolvedEngineContract
        from finance_kernel.services.engine_dispatcher import EngineDispatcher

        pack = self._make_pack()
        # Override engine_contracts directly
        object.__setattr__(pack, "engine_contracts", {
            "variance": ResolvedEngineContract(
                engine_name="variance",
                engine_version="1.0",
                parameter_key="variance",
            ),
            "matching": ResolvedEngineContract(
                engine_name="matching",
                engine_version="1.0",
                parameter_key="matching",
            ),
        })
        dispatcher = EngineDispatcher(pack)
        unregistered = dispatcher.validate_registration()
        assert set(unregistered) == {"variance", "matching"}

    def test_engine_parameters_ref_resolves_from_pack(self):
        """engine_parameters_ref is used to look up resolved_engine_params."""
        from finance_config.compiler import FrozenEngineParams
        from finance_kernel.services.engine_dispatcher import (
            EngineDispatcher,
            EngineInvoker,
        )

        policy = self._make_compiled_policy(
            name="CustomRefPolicy",
            required_engines=("variance",),
            engine_parameters_ref="inventory_variance",
        )
        params = FrozenEngineParams(
            engine_name="variance",
            parameters={"tolerance_percent": 10.0},
        )
        pack = self._make_pack(
            policies=[policy],
            engine_params={"inventory_variance": params},
        )
        dispatcher = EngineDispatcher(pack)

        received_params = {}

        def capture_invoke(payload, frozen_params):
            received_params.update(frozen_params.parameters)
            return {}

        invoker = EngineInvoker(
            engine_name="variance",
            engine_version="1.0",
            invoke=capture_invoke,
        )
        dispatcher.register("variance", invoker)
        dispatcher.dispatch(policy, {})
        assert received_params["tolerance_percent"] == 10.0


# ---------------------------------------------------------------------------
# 4. TestModuleEngineOwnership — modules own stateless engines
# ---------------------------------------------------------------------------


class TestModuleEngineOwnership:
    """Module services own stateless engines; kernel stays engine-free."""

    def test_posting_orchestrator_has_no_engine_imports(self):
        """PostingOrchestrator imports no finance_engines module."""
        import inspect

        from finance_kernel.services import posting_orchestrator

        source = inspect.getsource(posting_orchestrator)
        # PostingOrchestrator should only reference engine_dispatcher,
        # never import finance_engines directly
        assert "from finance_engines" not in source, (
            "PostingOrchestrator must not import finance_engines directly"
        )

    def test_module_posting_service_has_no_engine_imports(self):
        """ModulePostingService imports no finance_engines module."""
        import inspect

        from finance_kernel.services import module_posting_service

        source = inspect.getsource(module_posting_service)
        assert "from finance_engines" not in source, (
            "ModulePostingService must not import finance_engines directly"
        )

    def test_journal_writer_has_no_engine_imports(self):
        """JournalWriter imports no finance_engines module."""
        import inspect

        from finance_kernel.services import journal_writer

        source = inspect.getsource(journal_writer)
        assert "from finance_engines" not in source, (
            "JournalWriter must not import finance_engines directly"
        )

    def test_engine_dispatcher_is_the_only_kernel_engine_bridge(self):
        """Only engine_dispatcher.py in finance_kernel imports finance_engines."""
        import ast
        from pathlib import Path

        kernel_services = Path("finance_kernel/services")
        violators = []
        for py_file in kernel_services.glob("*.py"):
            if py_file.name == "engine_dispatcher.py":
                continue
            try:
                tree = ast.parse(py_file.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        if node.module.startswith("finance_engines"):
                            violators.append(f"{py_file.name}: {node.module}")
            except SyntaxError:
                pass

        assert violators == [], (
            f"Only engine_dispatcher.py should import finance_engines, "
            f"but found: {violators}"
        )

    def test_orchestrator_creates_engine_dispatcher(self):
        """PostingOrchestrator creates an EngineDispatcher instance."""
        import inspect

        from finance_kernel.services import posting_orchestrator

        source = inspect.getsource(posting_orchestrator)
        assert "EngineDispatcher" in source
        assert "self.engine_dispatcher" in source


# ---------------------------------------------------------------------------
# 5. TestEngineContractCoverage — every required_engine maps to a contract
# ---------------------------------------------------------------------------


class TestEngineContractCoverage:
    """Every required_engine in compiled policies maps to a known engine contract."""

    def _get_compiled_pack(self):
        from datetime import date

        from finance_config import get_active_config

        return get_active_config(legal_entity="*", as_of_date=date(2026, 1, 1))

    def test_all_required_engines_have_contracts(self):
        """Every required_engine across all policies exists in ENGINE_CONTRACTS."""
        from finance_engines.contracts import ENGINE_CONTRACTS

        pack = self._get_compiled_pack()
        all_required = set()
        for p in pack.policies:
            all_required.update(p.required_engines)
        for engine_name in all_required:
            assert engine_name in ENGINE_CONTRACTS, (
                f"Engine '{engine_name}' required by policy but has no contract"
            )

    def test_engine_contracts_in_compiled_pack(self):
        """CompiledPolicyPack.engine_contracts covers all required engines."""
        pack = self._get_compiled_pack()
        all_required = set()
        for p in pack.policies:
            all_required.update(p.required_engines)
        for engine_name in all_required:
            assert engine_name in pack.engine_contracts, (
                f"Engine '{engine_name}' not in pack.engine_contracts"
            )

    def test_resolved_params_exist_for_engine_refs(self):
        """Every engine_parameters_ref resolves to a key in resolved_engine_params."""
        pack = self._get_compiled_pack()
        for p in pack.policies:
            if p.engine_parameters_ref:
                assert p.engine_parameters_ref in pack.resolved_engine_params, (
                    f"Policy {p.name} references engine params "
                    f"'{p.engine_parameters_ref}' but it's not in resolved_engine_params"
                )

    def test_variance_contract_version(self):
        """Variance engine contract has a known version."""
        from finance_engines.contracts import VARIANCE_CONTRACT

        assert VARIANCE_CONTRACT.engine_name == "variance"
        assert VARIANCE_CONTRACT.engine_version == "1.0"

    def test_all_engine_contracts_have_schemas(self):
        """Every engine contract has a non-empty parameter_schema."""
        from finance_engines.contracts import ENGINE_CONTRACTS

        for name, contract in ENGINE_CONTRACTS.items():
            assert isinstance(contract.parameter_schema, dict), (
                f"Contract {name} has no parameter_schema"
            )
            assert contract.parameter_schema.get("type") == "object", (
                f"Contract {name} parameter_schema should be type=object"
            )

    def test_compiled_pack_has_engine_params_for_variance(self):
        """Compiled pack resolves variance engine parameters."""
        pack = self._get_compiled_pack()
        assert "variance" in pack.resolved_engine_params, (
            "variance engine params should be in compiled pack"
        )
        params = pack.resolved_engine_params["variance"]
        assert params.engine_name == "variance"
