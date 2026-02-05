"""
Tests for EngineDispatcher and standard invoker registrations.

Covers:
- EngineDispatcher registration (success, mismatch, validation)
- Dispatch with empty required_engines
- Dispatch with single engine (success, failure, unregistered)
- Dispatch with multiple engines
- Parameter resolution (engine_parameters_ref, fallback, empty)
- Fingerprint computation
- Standard invoker registration via register_standard_engines
- Integration: invokers produce correct outputs for known inputs
"""

from decimal import Decimal
from typing import Any

import pytest

from finance_config.compiler import (
    CompiledPolicy,
    CompiledPolicyPack,
    FrozenEngineParams,
    PolicyDecisionTrace,
    PolicyMatchIndex,
    ResolvedEngineContract,
)
from finance_config.schema import (
    ConfigScope,
    LedgerEffectDef,
    LineMappingDef,
    PolicyMeaningDef,
    PolicyTriggerDef,
)
from finance_kernel.domain.engine_types import (
    EngineDispatchResult,
    EngineTraceRecord,
)
from finance_services.engine_dispatcher import (
    EngineDispatcher,
    EngineInvoker,
)
from finance_services.invokers import register_standard_engines

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def _make_policy(
    name: str = "test_policy",
    required_engines: tuple[str, ...] = (),
    engine_parameters_ref: str | None = None,
    variance_disposition: str | None = None,
    valuation_model: str | None = None,
) -> CompiledPolicy:
    """Create a minimal CompiledPolicy for testing."""
    from datetime import date

    return CompiledPolicy(
        name=name,
        version=1,
        trigger=PolicyTriggerDef(event_type="test.event"),
        meaning=PolicyMeaningDef(economic_type="TEST"),
        ledger_effects=(),
        guards=(),
        effective_from=date(2024, 1, 1),
        effective_to=None,
        scope="entity",
        precedence=None,
        valuation_model=valuation_model,
        line_mappings=(),
        intent_source=None,
        required_engines=required_engines,
        engine_parameters_ref=engine_parameters_ref,
        variance_disposition=variance_disposition,
        capability_tags=(),
        description="Test policy",
        module="test",
    )


def _make_pack(
    engine_contracts: dict[str, ResolvedEngineContract] | None = None,
    resolved_engine_params: dict[str, FrozenEngineParams] | None = None,
) -> CompiledPolicyPack:
    """Create a minimal CompiledPolicyPack for testing."""
    from datetime import date

    return CompiledPolicyPack(
        config_id="test-config",
        config_version=1,
        checksum="abc123",
        scope=ConfigScope(
            legal_entity="TEST_ENTITY",
            jurisdiction="US",
            regulatory_regime="GAAP",
            currency="USD",
            effective_from=date(2024, 1, 1),
        ),
        policies=(),
        match_index=PolicyMatchIndex(entries={}),
        role_bindings=(),
        engine_contracts=engine_contracts or {},
        resolved_engine_params=resolved_engine_params or {},
        controls=(),
        capabilities={},
        canonical_fingerprint="test_fp",
        decision_trace=PolicyDecisionTrace(event_type_decisions={}),
    )


def _echo_invoker(payload: dict, params: FrozenEngineParams) -> dict:
    """Test invoker that echoes payload and params."""
    return {"payload": payload, "params": dict(params.parameters)}


def _failing_invoker(payload: dict, params: FrozenEngineParams) -> dict:
    """Test invoker that always raises."""
    raise ValueError("Engine computation failed")


# ---------------------------------------------------------------------------
# EngineDispatcher — Registration
# ---------------------------------------------------------------------------


class TestEngineDispatcherRegistration:
    """Tests for engine registration."""

    def test_register_success(self):
        """Register an invoker with matching name."""
        pack = _make_pack()
        dispatcher = EngineDispatcher(pack)
        invoker = EngineInvoker(
            engine_name="test_engine",
            engine_version="1.0",
            invoke=_echo_invoker,
        )
        dispatcher.register("test_engine", invoker)
        # No exception means success

    def test_register_mismatch_raises(self):
        """Register with mismatched name raises ValueError."""
        pack = _make_pack()
        dispatcher = EngineDispatcher(pack)
        invoker = EngineInvoker(
            engine_name="engine_a",
            engine_version="1.0",
            invoke=_echo_invoker,
        )
        with pytest.raises(ValueError, match="does not match"):
            dispatcher.register("engine_b", invoker)

    def test_register_overwrites(self):
        """Registering the same name twice overwrites the first."""
        pack = _make_pack()
        dispatcher = EngineDispatcher(pack)

        invoker_v1 = EngineInvoker(
            engine_name="test_engine",
            engine_version="1.0",
            invoke=_echo_invoker,
        )
        invoker_v2 = EngineInvoker(
            engine_name="test_engine",
            engine_version="2.0",
            invoke=_echo_invoker,
        )

        dispatcher.register("test_engine", invoker_v1)
        dispatcher.register("test_engine", invoker_v2)

        # Dispatch should use v2
        policy = _make_policy(required_engines=("test_engine",))
        result = dispatcher.dispatch(policy, {"key": "value"})
        assert result.all_succeeded
        assert result.traces[0].engine_version == "2.0"


# ---------------------------------------------------------------------------
# EngineDispatcher — Dispatch: empty required_engines
# ---------------------------------------------------------------------------


class TestEngineDispatchEmpty:
    """Tests for dispatch with no required engines."""

    def test_empty_required_engines_returns_success(self):
        """Policy with no required engines → empty success result."""
        pack = _make_pack()
        dispatcher = EngineDispatcher(pack)
        policy = _make_policy(required_engines=())
        result = dispatcher.dispatch(policy, {})

        assert result.all_succeeded is True
        assert result.engine_outputs == {}
        assert result.traces == ()
        assert result.errors == ()


# ---------------------------------------------------------------------------
# EngineDispatcher — Dispatch: single engine
# ---------------------------------------------------------------------------


class TestEngineDispatchSingleEngine:
    """Tests for dispatch with a single required engine."""

    def test_dispatch_success(self):
        """Successful dispatch invokes engine and returns output."""
        pack = _make_pack(
            resolved_engine_params={
                "echo": FrozenEngineParams(
                    engine_name="echo",
                    parameters={"mode": "test"},
                ),
            },
        )
        dispatcher = EngineDispatcher(pack)
        dispatcher.register("echo", EngineInvoker(
            engine_name="echo",
            engine_version="1.0",
            invoke=_echo_invoker,
        ))

        policy = _make_policy(required_engines=("echo",))
        result = dispatcher.dispatch(policy, {"amount": "100"})

        assert result.all_succeeded is True
        assert "echo" in result.engine_outputs
        output = result.engine_outputs["echo"]
        assert output["payload"] == {"amount": "100"}
        assert output["params"] == {"mode": "test"}
        assert len(result.traces) == 1
        assert result.traces[0].engine_name == "echo"
        assert result.traces[0].engine_version == "1.0"
        assert result.traces[0].success is True
        assert result.traces[0].duration_ms >= 0
        assert result.traces[0].parameters_used == {"mode": "test"}

    def test_dispatch_unregistered_engine(self):
        """Dispatch with unregistered engine returns error."""
        pack = _make_pack()
        dispatcher = EngineDispatcher(pack)
        policy = _make_policy(required_engines=("nonexistent",))
        result = dispatcher.dispatch(policy, {})

        assert result.all_succeeded is False
        assert len(result.errors) == 1
        assert "nonexistent" in result.errors[0]
        assert "no registered invoker" in result.errors[0]
        assert result.traces[0].success is False
        assert result.traces[0].engine_version == "unknown"

    def test_dispatch_engine_failure(self):
        """Dispatch with failing engine captures error in trace."""
        pack = _make_pack()
        dispatcher = EngineDispatcher(pack)
        dispatcher.register("fail_engine", EngineInvoker(
            engine_name="fail_engine",
            engine_version="1.0",
            invoke=_failing_invoker,
        ))

        policy = _make_policy(required_engines=("fail_engine",))
        result = dispatcher.dispatch(policy, {})

        assert result.all_succeeded is False
        assert len(result.errors) == 1
        assert "Engine computation failed" in result.errors[0]
        assert result.traces[0].success is False
        assert "Engine computation failed" in result.traces[0].error
        assert result.traces[0].duration_ms >= 0
        # Engine output should not be present
        assert "fail_engine" not in result.engine_outputs


# ---------------------------------------------------------------------------
# EngineDispatcher — Dispatch: multiple engines
# ---------------------------------------------------------------------------


class TestEngineDispatchMultipleEngines:
    """Tests for dispatch with multiple required engines."""

    def test_all_succeed(self):
        """All engines succeed → all_succeeded=True."""
        pack = _make_pack(
            resolved_engine_params={
                "engine_a": FrozenEngineParams(
                    engine_name="engine_a",
                    parameters={"p1": "v1"},
                ),
                "engine_b": FrozenEngineParams(
                    engine_name="engine_b",
                    parameters={"p2": "v2"},
                ),
            },
        )
        dispatcher = EngineDispatcher(pack)
        dispatcher.register("engine_a", EngineInvoker(
            engine_name="engine_a",
            engine_version="1.0",
            invoke=_echo_invoker,
        ))
        dispatcher.register("engine_b", EngineInvoker(
            engine_name="engine_b",
            engine_version="2.0",
            invoke=_echo_invoker,
        ))

        policy = _make_policy(required_engines=("engine_a", "engine_b"))
        result = dispatcher.dispatch(policy, {"data": "test"})

        assert result.all_succeeded is True
        assert len(result.engine_outputs) == 2
        assert len(result.traces) == 2
        assert result.errors == ()

    def test_partial_failure(self):
        """One engine fails → all_succeeded=False, but other still runs."""
        pack = _make_pack()
        dispatcher = EngineDispatcher(pack)
        dispatcher.register("good", EngineInvoker(
            engine_name="good",
            engine_version="1.0",
            invoke=_echo_invoker,
        ))
        dispatcher.register("bad", EngineInvoker(
            engine_name="bad",
            engine_version="1.0",
            invoke=_failing_invoker,
        ))

        policy = _make_policy(required_engines=("good", "bad"))
        result = dispatcher.dispatch(policy, {"data": "test"})

        assert result.all_succeeded is False
        assert "good" in result.engine_outputs
        assert "bad" not in result.engine_outputs
        assert len(result.errors) == 1
        assert len(result.traces) == 2
        # Good engine trace
        good_trace = [t for t in result.traces if t.engine_name == "good"][0]
        assert good_trace.success is True
        # Bad engine trace
        bad_trace = [t for t in result.traces if t.engine_name == "bad"][0]
        assert bad_trace.success is False


# ---------------------------------------------------------------------------
# EngineDispatcher — Parameter Resolution
# ---------------------------------------------------------------------------


class TestParameterResolution:
    """Tests for parameter lookup and resolution."""

    def test_engine_parameters_ref_lookup(self):
        """When policy has engine_parameters_ref, uses that key."""
        pack = _make_pack(
            resolved_engine_params={
                "custom_variance_params": FrozenEngineParams(
                    engine_name="variance",
                    parameters={"threshold": "0.05"},
                ),
            },
        )
        dispatcher = EngineDispatcher(pack)

        def _capture_params(payload: dict, params: FrozenEngineParams) -> dict:
            return {"params": dict(params.parameters)}

        dispatcher.register("variance", EngineInvoker(
            engine_name="variance",
            engine_version="1.0",
            invoke=_capture_params,
        ))

        policy = _make_policy(
            required_engines=("variance",),
            engine_parameters_ref="custom_variance_params",
        )
        result = dispatcher.dispatch(policy, {})

        assert result.all_succeeded
        assert result.engine_outputs["variance"]["params"] == {"threshold": "0.05"}

    def test_fallback_to_engine_name(self):
        """When engine_parameters_ref is None, falls back to engine name."""
        pack = _make_pack(
            resolved_engine_params={
                "variance": FrozenEngineParams(
                    engine_name="variance",
                    parameters={"mode": "standard"},
                ),
            },
        )
        dispatcher = EngineDispatcher(pack)
        dispatcher.register("variance", EngineInvoker(
            engine_name="variance",
            engine_version="1.0",
            invoke=_echo_invoker,
        ))

        policy = _make_policy(
            required_engines=("variance",),
            engine_parameters_ref=None,
        )
        result = dispatcher.dispatch(policy, {})

        assert result.all_succeeded
        assert result.engine_outputs["variance"]["params"] == {"mode": "standard"}

    def test_no_params_creates_empty(self):
        """When no params found, creates empty FrozenEngineParams."""
        pack = _make_pack(resolved_engine_params={})
        dispatcher = EngineDispatcher(pack)
        dispatcher.register("orphan", EngineInvoker(
            engine_name="orphan",
            engine_version="1.0",
            invoke=_echo_invoker,
        ))

        policy = _make_policy(required_engines=("orphan",))
        result = dispatcher.dispatch(policy, {})

        assert result.all_succeeded
        assert result.engine_outputs["orphan"]["params"] == {}
        assert result.traces[0].parameters_used == {}

    def test_variance_disposition_merged_into_variance_params(self):
        """policy.variance_disposition is merged into params when dispatching to variance engine."""
        pack = _make_pack(
            resolved_engine_params={
                "variance": FrozenEngineParams(
                    engine_name="variance",
                    parameters={"threshold": "0.02"},
                ),
            },
        )
        dispatcher = EngineDispatcher(pack)

        def _capture_params(payload: dict, params: FrozenEngineParams) -> dict:
            return {"params": dict(params.parameters)}

        dispatcher.register("variance", EngineInvoker(
            engine_name="variance",
            engine_version="1.0",
            invoke=_capture_params,
        ))

        policy = _make_policy(
            required_engines=("variance",),
            variance_disposition="capitalize",
        )
        result = dispatcher.dispatch(policy, {})

        assert result.all_succeeded
        assert result.engine_outputs["variance"]["params"]["variance_disposition"] == "capitalize"
        assert result.engine_outputs["variance"]["params"]["threshold"] == "0.02"
        assert result.traces[0].parameters_used.get("variance_disposition") == "capitalize"

    def test_valuation_model_merged_into_valuation_params(self):
        """policy.valuation_model is merged into params when dispatching to valuation engine."""
        pack = _make_pack(
            resolved_engine_params={
                "valuation": FrozenEngineParams(
                    engine_name="valuation",
                    parameters={"cost_method": "FIFO"},
                ),
            },
        )
        dispatcher = EngineDispatcher(pack)

        def _capture_params(payload: dict, params: FrozenEngineParams) -> dict:
            return {"params": dict(params.parameters)}

        dispatcher.register("valuation", EngineInvoker(
            engine_name="valuation",
            engine_version="1.0",
            invoke=_capture_params,
        ))

        policy = _make_policy(
            required_engines=("valuation",),
            valuation_model="receipt_standard",
        )
        result = dispatcher.dispatch(policy, {})

        assert result.all_succeeded
        assert result.engine_outputs["valuation"]["params"]["valuation_model"] == "receipt_standard"
        assert result.engine_outputs["valuation"]["params"]["cost_method"] == "FIFO"
        assert result.traces[0].parameters_used.get("valuation_model") == "receipt_standard"


# ---------------------------------------------------------------------------
# EngineDispatcher — Fingerprinting
# ---------------------------------------------------------------------------


class TestFingerprinting:
    """Tests for input fingerprint computation."""

    def test_fingerprint_included_in_trace(self):
        """Fingerprint is computed from payload fields and included in trace."""
        pack = _make_pack()
        dispatcher = EngineDispatcher(pack)
        dispatcher.register("fp_engine", EngineInvoker(
            engine_name="fp_engine",
            engine_version="1.0",
            invoke=_echo_invoker,
            fingerprint_fields=("amount", "type"),
        ))

        policy = _make_policy(required_engines=("fp_engine",))
        result = dispatcher.dispatch(policy, {"amount": "100", "type": "test"})

        assert result.all_succeeded
        trace = result.traces[0]
        assert trace.input_fingerprint != ""

    def test_no_fingerprint_fields_empty_string(self):
        """No fingerprint_fields → empty fingerprint in trace."""
        pack = _make_pack()
        dispatcher = EngineDispatcher(pack)
        dispatcher.register("no_fp", EngineInvoker(
            engine_name="no_fp",
            engine_version="1.0",
            invoke=_echo_invoker,
            fingerprint_fields=(),
        ))

        policy = _make_policy(required_engines=("no_fp",))
        result = dispatcher.dispatch(policy, {"data": "irrelevant"})

        assert result.all_succeeded
        assert result.traces[0].input_fingerprint == ""

    def test_same_payload_same_fingerprint(self):
        """Same payload → same fingerprint (deterministic)."""
        pack = _make_pack()
        dispatcher = EngineDispatcher(pack)
        invoker = EngineInvoker(
            engine_name="det",
            engine_version="1.0",
            invoke=_echo_invoker,
            fingerprint_fields=("amount",),
        )
        dispatcher.register("det", invoker)

        policy = _make_policy(required_engines=("det",))
        r1 = dispatcher.dispatch(policy, {"amount": "42"})
        r2 = dispatcher.dispatch(policy, {"amount": "42"})

        assert r1.traces[0].input_fingerprint == r2.traces[0].input_fingerprint

    def test_different_payload_different_fingerprint(self):
        """Different payload → different fingerprint."""
        pack = _make_pack()
        dispatcher = EngineDispatcher(pack)
        invoker = EngineInvoker(
            engine_name="det",
            engine_version="1.0",
            invoke=_echo_invoker,
            fingerprint_fields=("amount",),
        )
        dispatcher.register("det", invoker)

        policy = _make_policy(required_engines=("det",))
        r1 = dispatcher.dispatch(policy, {"amount": "42"})
        r2 = dispatcher.dispatch(policy, {"amount": "99"})

        assert r1.traces[0].input_fingerprint != r2.traces[0].input_fingerprint


# ---------------------------------------------------------------------------
# EngineDispatcher — validate_registration
# ---------------------------------------------------------------------------


class TestValidateRegistration:
    """Tests for validate_registration."""

    def test_all_registered(self):
        """All engine contracts have invokers → empty list."""
        pack = _make_pack(
            engine_contracts={
                "variance": ResolvedEngineContract(
                    engine_name="variance",
                    engine_version="1.0",
                    parameter_key="variance",
                ),
            },
        )
        dispatcher = EngineDispatcher(pack)
        dispatcher.register("variance", EngineInvoker(
            engine_name="variance",
            engine_version="1.0",
            invoke=_echo_invoker,
        ))

        assert dispatcher.validate_registration() == []

    def test_missing_invoker(self):
        """Engine contract without invoker → returned in list."""
        pack = _make_pack(
            engine_contracts={
                "variance": ResolvedEngineContract(
                    engine_name="variance",
                    engine_version="1.0",
                    parameter_key="variance",
                ),
                "allocation": ResolvedEngineContract(
                    engine_name="allocation",
                    engine_version="1.0",
                    parameter_key="allocation",
                ),
            },
        )
        dispatcher = EngineDispatcher(pack)
        dispatcher.register("variance", EngineInvoker(
            engine_name="variance",
            engine_version="1.0",
            invoke=_echo_invoker,
        ))

        unregistered = dispatcher.validate_registration()
        assert unregistered == ["allocation"]

    def test_empty_contracts(self):
        """No engine contracts → empty list."""
        pack = _make_pack(engine_contracts={})
        dispatcher = EngineDispatcher(pack)
        assert dispatcher.validate_registration() == []


# ---------------------------------------------------------------------------
# Standard Invoker Registration
# ---------------------------------------------------------------------------


class TestStandardInvokerRegistration:
    """Tests for register_standard_engines."""

    def test_all_standard_engines_registered(self):
        """register_standard_engines registers all 7 standard engines."""
        pack = _make_pack()
        dispatcher = EngineDispatcher(pack)
        register_standard_engines(dispatcher)

        # Dispatch with each engine name to verify registration exists
        expected_engines = [
            "variance", "allocation", "matching", "tax",
            "allocation_cascade", "billing", "ice",
        ]

        # We can't dispatch without proper payloads, but we can verify
        # the registration by checking validate_registration with contracts
        pack_with_contracts = _make_pack(
            engine_contracts={
                name: ResolvedEngineContract(
                    engine_name=name,
                    engine_version="1.0",
                    parameter_key=name,
                )
                for name in expected_engines
            },
        )
        dispatcher2 = EngineDispatcher(pack_with_contracts)
        register_standard_engines(dispatcher2)

        unregistered = dispatcher2.validate_registration()
        assert unregistered == [], f"Unregistered engines: {unregistered}"


# ---------------------------------------------------------------------------
# EngineTraceRecord immutability
# ---------------------------------------------------------------------------


class TestTraceRecordImmutability:
    """Tests that trace records are frozen dataclasses."""

    def test_trace_record_frozen(self):
        """EngineTraceRecord is immutable."""
        trace = EngineTraceRecord(
            engine_name="test",
            engine_version="1.0",
            input_fingerprint="abc",
            duration_ms=1.5,
            parameters_used={},
            success=True,
        )
        with pytest.raises(AttributeError):
            trace.engine_name = "modified"  # type: ignore[misc]

    def test_dispatch_result_frozen(self):
        """EngineDispatchResult is immutable."""
        result = EngineDispatchResult(
            engine_outputs={},
            traces=(),
            all_succeeded=True,
            errors=(),
        )
        with pytest.raises(AttributeError):
            result.all_succeeded = False  # type: ignore[misc]

    def test_invoker_frozen(self):
        """EngineInvoker is immutable."""
        invoker = EngineInvoker(
            engine_name="test",
            engine_version="1.0",
            invoke=_echo_invoker,
        )
        with pytest.raises(AttributeError):
            invoker.engine_name = "modified"  # type: ignore[misc]
