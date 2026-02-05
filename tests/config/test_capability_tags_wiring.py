"""Tests for runtime capability_tags gating in PackPolicySource.

When a policy has capability_tags (e.g. ["DCAA"]), it is only selected at runtime
if every tag is enabled in the pack's capabilities (e.g. capabilities.dcaa is True).
"""

from __future__ import annotations

from datetime import date

import pytest

from finance_config.compiler import (
    CompiledPolicy,
    CompiledPolicyPack,
    PolicyDecisionTrace,
    PolicyMatchIndex,
    is_policy_admissible,
)
from finance_config.schema import ConfigScope, PolicyMeaningDef, PolicyTriggerDef
from finance_kernel.domain.policy_selector import PolicyNotFoundError
from finance_services.pack_policy_source import PackPolicySource


def _make_compiled_policy(**overrides) -> CompiledPolicy:
    """Minimal CompiledPolicy for testing."""
    defaults = dict(
        name="TestDCAAPolicy",
        version=1,
        trigger=PolicyTriggerDef(event_type="test.dcaa_event"),
        meaning=PolicyMeaningDef(economic_type="TEST"),
        ledger_effects=(),
        guards=(),
        effective_from=date(2024, 1, 1),
        effective_to=None,
        scope="*",
        precedence=None,
        valuation_model=None,
        line_mappings=(),
        intent_source=None,
        required_engines=(),
        engine_parameters_ref=None,
        variance_disposition=None,
        capability_tags=(),
        description="Test",
        module="test",
    )
    defaults.update(overrides)
    return CompiledPolicy(**defaults)


def _make_pack(
    *,
    policies: tuple[CompiledPolicy, ...],
    match_index: PolicyMatchIndex,
    capabilities: dict[str, bool],
) -> CompiledPolicyPack:
    """Minimal CompiledPolicyPack for testing."""
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
        policies=policies,
        match_index=match_index,
        role_bindings=(),
        engine_contracts={},
        resolved_engine_params={},
        controls=(),
        capabilities=capabilities,
        canonical_fingerprint="test_fingerprint",
        decision_trace=PolicyDecisionTrace(event_type_decisions={}),
    )


class TestIsPolicyAdmissible:
    """is_policy_admissible() filters by capability_tags."""

    def test_no_tags_always_admissible(self):
        """Policy with no capability_tags is always admissible."""
        policy = _make_compiled_policy(capability_tags=())
        assert is_policy_admissible(policy, {}) is True
        assert is_policy_admissible(policy, {"dcaa": False}) is True
        assert is_policy_admissible(policy, {"dcaa": True}) is True

    def test_tag_disabled_excludes_policy(self):
        """Policy with capability_tags is inadmissible when that tag is false."""
        policy = _make_compiled_policy(capability_tags=("DCAA",))
        assert is_policy_admissible(policy, {"dcaa": False}) is False
        assert is_policy_admissible(policy, {}) is False

    def test_tag_enabled_includes_policy(self):
        """Policy with capability_tags is admissible when that tag is true."""
        policy = _make_compiled_policy(capability_tags=("DCAA",))
        assert is_policy_admissible(policy, {"dcaa": True}) is True

    def test_multiple_tags_all_must_be_enabled(self):
        """Policy with multiple tags is admissible only when all are enabled."""
        policy = _make_compiled_policy(capability_tags=("DCAA", "IFRS"))
        assert is_policy_admissible(policy, {"dcaa": True, "ifrs": True}) is True
        assert is_policy_admissible(policy, {"dcaa": True, "ifrs": False}) is False
        assert is_policy_admissible(policy, {"dcaa": False, "ifrs": True}) is False


class TestPackPolicySourceCapabilityGating:
    """PackPolicySource filters candidates by capability_tags at runtime."""

    def test_capability_off_filters_out_tagged_policy_raises_not_found(self):
        """When capabilities.dcaa is False, a policy with capability_tags DCAA is not selected."""
        policy = _make_compiled_policy(capability_tags=("DCAA",))
        match_index = PolicyMatchIndex(entries={"test.dcaa_event": (policy,)})
        pack = _make_pack(
            policies=(policy,),
            match_index=match_index,
            capabilities={"dcaa": False},
        )
        source = PackPolicySource(pack)

        with pytest.raises(PolicyNotFoundError) as exc_info:
            source.get_profile(
                event_type="test.dcaa_event",
                effective_date=date(2026, 1, 1),
                payload=None,
                scope_value="*",
            )

        assert "test.dcaa_event" in str(exc_info.value)

    def test_capability_on_retains_tagged_policy_returns_policy(self):
        """When capabilities.dcaa is True, a policy with capability_tags DCAA is selected."""
        policy = _make_compiled_policy(capability_tags=("DCAA",))
        match_index = PolicyMatchIndex(entries={"test.dcaa_event": (policy,)})
        pack = _make_pack(
            policies=(policy,),
            match_index=match_index,
            capabilities={"dcaa": True},
        )
        source = PackPolicySource(pack)

        profile = source.get_profile(
            event_type="test.dcaa_event",
            effective_date=date(2026, 1, 1),
            payload=None,
            scope_value="*",
        )

        assert profile is not None
        assert profile.name == "TestDCAAPolicy"
