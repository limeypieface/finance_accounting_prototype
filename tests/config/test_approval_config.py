"""
Tests for approval policy configuration loading and compilation.

Covers:
- Schema types (ApprovalRuleDef, ApprovalPolicyDef) -- pure, no DB
- Loader (parse_approval_policy) -- YAML dict parsing
- Compiler (_compile_approval_policies) -- validation, sorting, hashing
- End-to-end (get_active_config) -- full YAML-to-CompiledPolicyPack
- CompiledPolicyPack integration -- structural assertions
"""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from finance_config.compiler import (
    CompiledApprovalPolicy,
    CompiledApprovalRule,
    CompiledPolicyPack,
    CompilationFailedError,
    compile_policy_pack,
)
from finance_config.loader import parse_approval_policy
from finance_config.schema import ApprovalPolicyDef, ApprovalRuleDef


# =========================================================================
# 1. Schema types -- pure construction and immutability
# =========================================================================


class TestApprovalRuleDefSchema:
    """Verify ApprovalRuleDef frozen dataclass construction."""

    def test_construct_with_all_fields(self):
        rule = ApprovalRuleDef(
            rule_name="exec_approval",
            priority=50,
            min_amount="10000.00",
            max_amount="500000.00",
            required_roles=("cfo", "ceo"),
            min_approvers=2,
            require_distinct_roles=True,
            guard_expression="payload.amount > 0",
            auto_approve_below="100.00",
            escalation_timeout_hours=72,
        )
        assert rule.rule_name == "exec_approval"
        assert rule.priority == 50
        assert rule.min_amount == "10000.00"
        assert rule.max_amount == "500000.00"
        assert rule.required_roles == ("cfo", "ceo")
        assert rule.min_approvers == 2
        assert rule.require_distinct_roles is True
        assert rule.guard_expression == "payload.amount > 0"
        assert rule.auto_approve_below == "100.00"
        assert rule.escalation_timeout_hours == 72

    def test_defaults_for_optional_fields(self):
        rule = ApprovalRuleDef(rule_name="basic", priority=10)
        assert rule.min_amount is None
        assert rule.max_amount is None
        assert rule.required_roles == ()
        assert rule.min_approvers == 1
        assert rule.require_distinct_roles is False
        assert rule.guard_expression is None
        assert rule.auto_approve_below is None
        assert rule.escalation_timeout_hours is None

    def test_frozen_immutability(self):
        rule = ApprovalRuleDef(rule_name="frozen_test", priority=10)
        with pytest.raises(dataclasses.FrozenInstanceError):
            rule.priority = 99  # type: ignore[misc]


class TestApprovalPolicyDefSchema:
    """Verify ApprovalPolicyDef frozen dataclass construction."""

    def test_construct_with_all_fields(self):
        rule = ApprovalRuleDef(rule_name="r1", priority=10)
        policy = ApprovalPolicyDef(
            policy_name="test_policy",
            version=2,
            applies_to_workflow="ap_invoice",
            applies_to_action="approve",
            policy_currency="USD",
            rules=(rule,),
            effective_from="2026-01-01",
            effective_to="2026-12-31",
        )
        assert policy.policy_name == "test_policy"
        assert policy.version == 2
        assert policy.applies_to_workflow == "ap_invoice"
        assert policy.applies_to_action == "approve"
        assert policy.policy_currency == "USD"
        assert len(policy.rules) == 1
        assert policy.effective_from == "2026-01-01"
        assert policy.effective_to == "2026-12-31"

    def test_defaults_for_optional_fields(self):
        policy = ApprovalPolicyDef(policy_name="minimal")
        assert policy.version == 1
        assert policy.applies_to_workflow == ""
        assert policy.applies_to_action is None
        assert policy.policy_currency is None
        assert policy.rules == ()
        assert policy.effective_from is None
        assert policy.effective_to is None

    def test_frozen_immutability(self):
        policy = ApprovalPolicyDef(policy_name="frozen_test")
        with pytest.raises(dataclasses.FrozenInstanceError):
            policy.version = 99  # type: ignore[misc]


# =========================================================================
# 2. Loader -- parse_approval_policy
# =========================================================================


class TestParseApprovalPolicy:
    """Verify parse_approval_policy parses YAML dicts correctly."""

    def test_parse_valid_policy_with_rules(self):
        data = {
            "policy_name": "ap_invoice_approval",
            "version": 1,
            "applies_to_workflow": "ap_invoice",
            "applies_to_action": "approve",
            "policy_currency": "USD",
            "rules": [
                {
                    "rule_name": "auto_small",
                    "priority": 10,
                    "auto_approve_below": 500,
                },
                {
                    "rule_name": "manager",
                    "priority": 20,
                    "min_amount": 500,
                    "max_amount": 10000,
                    "required_roles": ["ap_manager"],
                    "min_approvers": 1,
                },
            ],
        }
        result = parse_approval_policy(data)

        assert isinstance(result, ApprovalPolicyDef)
        assert result.policy_name == "ap_invoice_approval"
        assert result.version == 1
        assert result.applies_to_workflow == "ap_invoice"
        assert result.applies_to_action == "approve"
        assert result.policy_currency == "USD"
        assert len(result.rules) == 2

    def test_rules_parsed_correctly(self):
        data = {
            "policy_name": "test",
            "rules": [
                {
                    "rule_name": "exec",
                    "priority": 40,
                    "min_amount": 100000,
                    "max_amount": None,
                    "required_roles": ["cfo", "ceo"],
                    "min_approvers": 2,
                    "require_distinct_roles": True,
                    "guard_expression": "payload.amount > 0",
                    "escalation_timeout_hours": 48,
                },
            ],
        }
        result = parse_approval_policy(data)
        rule = result.rules[0]

        assert rule.rule_name == "exec"
        assert rule.priority == 40
        assert rule.min_amount == "100000"
        assert rule.max_amount is None
        assert rule.required_roles == ("cfo", "ceo")
        assert rule.min_approvers == 2
        assert rule.require_distinct_roles is True
        assert rule.guard_expression == "payload.amount > 0"
        assert rule.escalation_timeout_hours == 48

    def test_defaults_when_optional_fields_omitted(self):
        data = {"policy_name": "bare_minimum"}
        result = parse_approval_policy(data)

        assert result.version == 1
        assert result.applies_to_workflow == ""
        assert result.applies_to_action is None
        assert result.policy_currency is None
        assert result.rules == ()
        assert result.effective_from is None
        assert result.effective_to is None

    def test_amount_fields_converted_to_strings(self):
        """Loader converts numeric YAML amounts to strings for Decimal safety."""
        data = {
            "policy_name": "amounts",
            "rules": [
                {
                    "rule_name": "r1",
                    "priority": 10,
                    "min_amount": 1000,
                    "max_amount": 50000.50,
                    "auto_approve_below": 250,
                },
            ],
        }
        result = parse_approval_policy(data)
        rule = result.rules[0]

        assert rule.min_amount == "1000"
        assert rule.max_amount == "50000.5"
        assert rule.auto_approve_below == "250"
        # Verify they are valid Decimal strings
        Decimal(rule.min_amount)
        Decimal(rule.max_amount)
        Decimal(rule.auto_approve_below)


# =========================================================================
# 3. Compiler -- _compile_approval_policies (tested indirectly)
# =========================================================================


class TestCompileApprovalPolicies:
    """Verify approval policy compilation via compile_policy_pack."""

    def _make_minimal_config(self, approval_policies):
        """Build a minimal AccountingConfigurationSet for compilation."""
        from finance_config.schema import (
            AccountingConfigurationSet,
            ConfigScope,
        )
        from finance_config.lifecycle import ConfigStatus

        return AccountingConfigurationSet(
            config_id="test-approval",
            version=1,
            checksum="0" * 64,
            scope=ConfigScope(
                legal_entity="*",
                jurisdiction="US",
                regulatory_regime="GAAP",
                currency="USD",
                effective_from=date(2026, 1, 1),
            ),
            status=ConfigStatus.DRAFT,
            policies=(),
            role_bindings=(),
            approval_policies=tuple(approval_policies),
        )

    def test_policy_hash_computed(self):
        policy_def = ApprovalPolicyDef(
            policy_name="hash_test",
            version=1,
            applies_to_workflow="ap_invoice",
            rules=(
                ApprovalRuleDef(rule_name="r1", priority=10),
            ),
        )
        config = self._make_minimal_config([policy_def])
        pack = compile_policy_pack(config)

        assert len(pack.approval_policies) == 1
        compiled = pack.approval_policies[0]
        assert compiled.policy_hash
        assert len(compiled.policy_hash) == 64  # SHA-256 hex digest

    def test_rules_sorted_by_priority(self):
        policy_def = ApprovalPolicyDef(
            policy_name="sort_test",
            version=1,
            rules=(
                ApprovalRuleDef(rule_name="high", priority=30),
                ApprovalRuleDef(rule_name="low", priority=10),
                ApprovalRuleDef(rule_name="mid", priority=20),
            ),
        )
        config = self._make_minimal_config([policy_def])
        pack = compile_policy_pack(config)

        compiled = pack.approval_policies[0]
        priorities = [r.priority for r in compiled.rules]
        assert priorities == [10, 20, 30]
        assert compiled.rules[0].rule_name == "low"
        assert compiled.rules[1].rule_name == "mid"
        assert compiled.rules[2].rule_name == "high"

    def test_al6_duplicate_priorities_raise_error(self):
        policy_def = ApprovalPolicyDef(
            policy_name="dup_priority",
            version=1,
            rules=(
                ApprovalRuleDef(rule_name="r1", priority=10),
                ApprovalRuleDef(rule_name="r2", priority=10),
            ),
        )
        config = self._make_minimal_config([policy_def])

        with pytest.raises(CompilationFailedError) as exc_info:
            compile_policy_pack(config)

        assert "duplicate rule priorities" in str(exc_info.value).lower()

    def test_invalid_amount_threshold_raises_error(self):
        policy_def = ApprovalPolicyDef(
            policy_name="bad_amount",
            version=1,
            rules=(
                ApprovalRuleDef(
                    rule_name="bad",
                    priority=10,
                    min_amount="not_a_number",
                ),
            ),
        )
        config = self._make_minimal_config([policy_def])

        with pytest.raises(CompilationFailedError) as exc_info:
            compile_policy_pack(config)

        assert "not a valid decimal" in str(exc_info.value).lower()

    def test_invalid_guard_expression_raises_error(self):
        policy_def = ApprovalPolicyDef(
            policy_name="bad_guard",
            version=1,
            rules=(
                ApprovalRuleDef(
                    rule_name="r1",
                    priority=10,
                    guard_expression="import os; os.system('rm -rf /')",
                ),
            ),
        )
        config = self._make_minimal_config([policy_def])

        with pytest.raises(CompilationFailedError) as exc_info:
            compile_policy_pack(config)

        error_msg = str(exc_info.value).lower()
        assert "guard" in error_msg or "approval" in error_msg

    def test_valid_guard_expression_accepted(self):
        policy_def = ApprovalPolicyDef(
            policy_name="good_guard",
            version=1,
            rules=(
                ApprovalRuleDef(
                    rule_name="r1",
                    priority=10,
                    guard_expression="payload.amount > 0",
                ),
            ),
        )
        config = self._make_minimal_config([policy_def])
        pack = compile_policy_pack(config)

        assert len(pack.approval_policies) == 1
        assert pack.approval_policies[0].rules[0].guard_expression == "payload.amount > 0"

    def test_compiled_rule_preserves_all_fields(self):
        rule_def = ApprovalRuleDef(
            rule_name="full",
            priority=20,
            min_amount="1000.00",
            max_amount="50000.00",
            required_roles=("manager",),
            min_approvers=1,
            require_distinct_roles=False,
            guard_expression="payload.amount > 0",
            auto_approve_below="100.00",
            escalation_timeout_hours=24,
        )
        policy_def = ApprovalPolicyDef(
            policy_name="preserve_test",
            version=1,
            applies_to_workflow="test_wf",
            applies_to_action="approve",
            policy_currency="EUR",
            rules=(rule_def,),
            effective_from="2026-01-01",
            effective_to="2026-12-31",
        )
        config = self._make_minimal_config([policy_def])
        pack = compile_policy_pack(config)

        cp = pack.approval_policies[0]
        assert cp.policy_name == "preserve_test"
        assert cp.version == 1
        assert cp.applies_to_workflow == "test_wf"
        assert cp.applies_to_action == "approve"
        assert cp.policy_currency == "EUR"
        assert cp.effective_from == "2026-01-01"
        assert cp.effective_to == "2026-12-31"

        cr = cp.rules[0]
        assert isinstance(cr, CompiledApprovalRule)
        assert cr.rule_name == "full"
        assert cr.priority == 20
        assert cr.min_amount == "1000.00"
        assert cr.max_amount == "50000.00"
        assert cr.required_roles == ("manager",)
        assert cr.min_approvers == 1
        assert cr.require_distinct_roles is False
        assert cr.guard_expression == "payload.amount > 0"
        assert cr.auto_approve_below == "100.00"
        assert cr.escalation_timeout_hours == 24


# =========================================================================
# 4. End-to-end -- get_active_config loads approval policies from YAML
# =========================================================================


class TestEndToEndApprovalConfig:
    """Verify approval policies load from US-GAAP-2026-v1 YAML via get_active_config."""

    def test_compiled_pack_has_approval_policies(self, test_config):
        assert hasattr(test_config, "approval_policies")
        assert isinstance(test_config.approval_policies, tuple)
        assert len(test_config.approval_policies) > 0

    def test_ap_invoice_policy_present(self, test_config):
        names = {p.policy_name for p in test_config.approval_policies}
        assert "ap_invoice_approval" in names

    def test_ap_payment_policy_present(self, test_config):
        names = {p.policy_name for p in test_config.approval_policies}
        assert "ap_payment_approval" in names

    def test_ap_invoice_policy_has_expected_rules(self, test_config):
        invoice_policy = next(
            p for p in test_config.approval_policies
            if p.policy_name == "ap_invoice_approval"
        )
        rule_names = [r.rule_name for r in invoice_policy.rules]
        assert "auto_approve_small" in rule_names
        assert "manager_approval" in rule_names
        assert "director_approval" in rule_names
        assert "executive_approval" in rule_names

    def test_ap_invoice_rules_sorted_by_priority(self, test_config):
        invoice_policy = next(
            p for p in test_config.approval_policies
            if p.policy_name == "ap_invoice_approval"
        )
        priorities = [r.priority for r in invoice_policy.rules]
        assert priorities == sorted(priorities)

    def test_ap_invoice_policy_hash_computed(self, test_config):
        invoice_policy = next(
            p for p in test_config.approval_policies
            if p.policy_name == "ap_invoice_approval"
        )
        assert invoice_policy.policy_hash
        assert len(invoice_policy.policy_hash) == 64


# =========================================================================
# 5. CompiledPolicyPack integration
# =========================================================================


class TestCompiledPolicyPackIntegration:
    """Verify approval_policies attribute on CompiledPolicyPack."""

    def test_approval_policies_attribute_exists(self, test_config):
        assert isinstance(test_config, CompiledPolicyPack)
        assert hasattr(test_config, "approval_policies")

    def test_approval_policies_is_tuple_of_compiled(self, test_config):
        assert isinstance(test_config.approval_policies, tuple)
        for policy in test_config.approval_policies:
            assert isinstance(policy, CompiledApprovalPolicy)

    def test_each_policy_has_tuple_of_compiled_rules(self, test_config):
        for policy in test_config.approval_policies:
            assert isinstance(policy.rules, tuple)
            for rule in policy.rules:
                assert isinstance(rule, CompiledApprovalRule)
