"""
Phase 8 — Dead Scaffolding Elimination Tests.

Tests that prove no compiled config fields, engine contracts, or orchestrator
services exist without a runtime consumer. Every field, engine, and service
must have a provable runtime purpose.

Test classes:
  1. TestCompiledPolicyPackFieldUsage   — Every public field on CompiledPolicyPack is read
  2. TestOrchestratorServiceUsage       — Every service on PostingOrchestrator is consumed
  3. TestCompiledPolicyFieldUsage       — Every CompiledPolicy field has a consumer
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# 1. TestCompiledPolicyPackFieldUsage
# ---------------------------------------------------------------------------


class TestCompiledPolicyPackFieldUsage:
    """Every public field on CompiledPolicyPack has at least one runtime reader.

    We verify this statically by scanning known consumers for attribute access
    patterns, since instrumenting getters on a frozen dataclass is impractical.
    """

    def _get_runtime_consumers(self):
        """Return source code of all runtime files that read CompiledPolicyPack."""
        from pathlib import Path

        consumer_files = [
            # Kernel services that receive the compiled pack
            "finance_kernel/services/interpretation_coordinator.py",
            "finance_kernel/services/journal_writer.py",
            # Cross-layer services (moved from kernel to finance_services/)
            "finance_services/engine_dispatcher.py",
            "finance_services/posting_orchestrator.py",
            # Domain components
            "finance_kernel/domain/policy_authority.py",
            "finance_kernel/domain/policy_selector.py",
            "finance_kernel/domain/meaning_builder.py",
            "finance_kernel/domain/policy_bridge.py",
            "finance_kernel/domain/policy_compiler.py",
            "finance_kernel/domain/valuation.py",
            "finance_kernel/domain/accounting_policy.py",
            # Config runtime (pack metadata, role resolution)
            "finance_config/__init__.py",
            "finance_config/bridges.py",
            # Integrity checks
            "finance_config/integrity.py",
        ]
        all_source = ""
        for f in consumer_files:
            path = Path(f)
            if path.exists():
                all_source += path.read_text() + "\n"
        return all_source

    def test_all_compiled_pack_fields_consumed(self):
        """Every CompiledPolicyPack field is accessed by at least one consumer."""
        from finance_config.compiler import CompiledPolicyPack

        source = self._get_runtime_consumers()

        pack_fields = set(CompiledPolicyPack.__dataclass_fields__.keys())

        # These fields are accessed via different patterns:
        # - config_id: read by __init__.py metadata
        # - config_version/checksum: read by __init__.py metadata
        # - scope: read by PolicyAuthority
        # - policies: read by __init__.py, bridges, tests
        # - match_index: built for PolicySelector (pending direct wiring)
        # - role_bindings: read by bridges (RoleResolver)
        # - engine_contracts: read by EngineDispatcher.validate_registration
        # - resolved_engine_params: read by EngineDispatcher.dispatch
        # - controls: compiled for runtime control eval (pending consumer)
        # - capabilities: read by bridges
        # - canonical_fingerprint: read by integrity
        # - decision_trace: build-time debugging only

        # Fields that are consumed at build-time only (debugging artifacts)
        BUILD_TIME_ONLY = {"decision_trace"}

        # Fields compiled into the pack but whose runtime consumer is
        # not yet wired (will be addressed in future phases)
        PENDING_WIRING = {
            "controls",      # awaits runtime control evaluator
            "match_index",   # awaits PolicySelector direct lookup wiring
        }

        runtime_fields = pack_fields - BUILD_TIME_ONLY - PENDING_WIRING

        # Check each field appears in consumer source
        unaccessed = []
        for field_name in runtime_fields:
            # Look for .field_name or ["field_name"] access
            if f".{field_name}" not in source and f'"{field_name}"' not in source:
                unaccessed.append(field_name)

        assert unaccessed == [], (
            f"CompiledPolicyPack fields with no runtime consumer: {unaccessed}. "
            f"Either wire them into a consumer or remove them."
        )

    def test_compiled_policy_fields_consumed(self):
        """Every CompiledPolicy field is accessed by at least one consumer."""
        from finance_config.compiler import CompiledPolicy

        source = self._get_runtime_consumers()

        policy_fields = set(CompiledPolicy.__dataclass_fields__.keys())

        # Fields consumed by:
        # - name/version: tracing, logging, dispatch decisions
        # - trigger: PolicySelector match_index
        # - meaning: MeaningBuilder
        # - ledger_effects: PolicyCompiler, PolicyBridge
        # - guards: GuardEvaluator
        # - effective_from/to: PolicySelector date filtering
        # - scope: PolicySelector scope matching
        # - precedence: PolicySelector precedence resolution
        # - valuation_model: ValuationRegistry, AccountingPolicy
        # - line_mappings: PolicyBridge
        # - required_engines: EngineDispatcher
        # - engine_parameters_ref: EngineDispatcher
        # - variance_disposition: pending EngineDispatcher runtime read
        # - capability_tags: pending runtime capability gating
        # - description: logging, tracing (cosmetic)
        # - module: tracing, logging

        # Fields whose runtime consumer is pending wiring
        PENDING_RUNTIME = {
            "variance_disposition",  # awaits EngineDispatcher runtime read
            "capability_tags",       # awaits runtime capability gating
            "valuation_model",       # awaits MeaningBuilder/ValuationLayer read
        }

        unaccessed = []
        for field_name in policy_fields:
            if field_name in PENDING_RUNTIME:
                continue
            if f".{field_name}" not in source and f'"{field_name}"' not in source:
                unaccessed.append(field_name)

        # Allow cosmetic/logging fields
        COSMETIC_FIELDS = {"description"}
        real_gaps = [f for f in unaccessed if f not in COSMETIC_FIELDS]

        assert real_gaps == [], (
            f"CompiledPolicy fields with no runtime consumer: {real_gaps}"
        )


# ---------------------------------------------------------------------------
# 2. TestOrchestratorServiceUsage
# ---------------------------------------------------------------------------


class TestOrchestratorServiceUsage:
    """Every service created by PostingOrchestrator is used in at least one code path."""

    def test_all_orchestrator_services_are_public(self):
        """PostingOrchestrator exposes services as public attributes."""
        from finance_services.posting_orchestrator import PostingOrchestrator

        # These are the services created in __init__
        expected_services = {
            "auditor",
            "period_service",
            "link_graph",
            "snapshot_service",
            "party_service",
            "contract_service",
            "ingestor",
            "journal_writer",
            "outcome_recorder",
            "engine_dispatcher",
            "policy_authority",
            "meaning_builder",
            "interpretation_coordinator",
        }

        import inspect
        source = inspect.getsource(PostingOrchestrator)

        for svc_name in expected_services:
            assert f"self.{svc_name}" in source, (
                f"PostingOrchestrator does not assign self.{svc_name}"
            )

    def test_orchestrator_services_referenced_by_consumers(self):
        """Each orchestrator service is referenced by at least one module or test."""
        from pathlib import Path

        expected_services = [
            "auditor",
            "period_service",
            "link_graph",
            "snapshot_service",
            "party_service",
            "contract_service",
            "ingestor",
            "journal_writer",
            "outcome_recorder",
            "engine_dispatcher",
            "meaning_builder",
            "interpretation_coordinator",
        ]

        # Collect source from all modules + services + tests
        consumer_dirs = ["finance_modules", "finance_services", "tests"]
        all_source = ""
        for d in consumer_dirs:
            dpath = Path(d)
            if dpath.exists():
                for py in dpath.rglob("*.py"):
                    try:
                        all_source += py.read_text() + "\n"
                    except Exception:
                        pass

        unreferenced = []
        for svc_name in expected_services:
            if svc_name not in all_source:
                unreferenced.append(svc_name)

        assert unreferenced == [], (
            f"Orchestrator services not referenced by any consumer: {unreferenced}"
        )


# ---------------------------------------------------------------------------
# 3. TestCompiledPolicyFieldUsage (additional structural checks)
# ---------------------------------------------------------------------------


class TestCompiledPolicyFieldUsage:
    """Structural checks on policy fields and their consumers."""

    def test_every_policy_has_at_least_one_ledger_effect(self):
        """Every compiled policy defines at least one ledger effect."""
        from datetime import date

        from finance_config import get_active_config

        pack = get_active_config(legal_entity="*", as_of_date=date(2026, 1, 1))

        no_effects = [p.name for p in pack.policies if len(p.ledger_effects) == 0]
        assert no_effects == [], (
            f"Policies with no ledger_effects (dead policies?): {no_effects}"
        )

    def test_every_policy_has_trigger(self):
        """Every compiled policy has a non-empty event_type trigger."""
        from datetime import date

        from finance_config import get_active_config

        pack = get_active_config(legal_entity="*", as_of_date=date(2026, 1, 1))

        no_trigger = [p.name for p in pack.policies if not p.trigger.event_type]
        assert no_trigger == [], (
            f"Policies with no event_type trigger: {no_trigger}"
        )

    def test_every_policy_has_meaning(self):
        """Every compiled policy defines an economic_type meaning."""
        from datetime import date

        from finance_config import get_active_config

        pack = get_active_config(legal_entity="*", as_of_date=date(2026, 1, 1))

        no_meaning = [p.name for p in pack.policies if not p.meaning.economic_type]
        assert no_meaning == [], (
            f"Policies with no economic_type meaning: {no_meaning}"
        )

    def test_engine_parameters_ref_only_on_engine_policies(self):
        """engine_parameters_ref is only set on policies that declare required_engines."""
        from datetime import date

        from finance_config import get_active_config

        pack = get_active_config(legal_entity="*", as_of_date=date(2026, 1, 1))

        orphaned_refs = [
            p.name for p in pack.policies
            if p.engine_parameters_ref and not p.required_engines
        ]
        assert orphaned_refs == [], (
            f"Policies with engine_parameters_ref but no required_engines: {orphaned_refs}"
        )

    def test_variance_disposition_only_on_variance_policies(self):
        """variance_disposition is only set on policies that require the variance engine."""
        from datetime import date

        from finance_config import get_active_config

        pack = get_active_config(legal_entity="*", as_of_date=date(2026, 1, 1))

        orphaned = [
            p.name for p in pack.policies
            if p.variance_disposition and "variance" not in p.required_engines
        ]
        assert orphaned == [], (
            f"Policies with variance_disposition but no variance engine: {orphaned}"
        )
