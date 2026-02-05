"""
finance_services.engine_dispatcher -- Runtime engine invocation from compiled policy fields.

Responsibility:
    Reads ``required_engines`` and ``engine_parameters_ref`` from a
    CompiledPolicy, looks up ``resolved_engine_params`` from the
    CompiledPolicyPack, invokes registered EngineInvoker callables,
    and collects EngineTraceRecord records for auditor provenance.

    This is the ONLY runtime path for policy-driven engine invocation.
    Modules must not instantiate posting-path engines directly.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    Bridges the config layer (CompiledPolicyPack) to the engine layer
    (pure calculation engines) at posting time.

Invariants enforced:
    - R14 (No central dispatch on event_type): engine dispatch is driven
      by the ``required_engines`` list on CompiledPolicy, not by if/switch
      on event_type.
    - R15 (Open/closed compliance): new engines are added by registering a
      new EngineInvoker; no existing code paths change.
    - Engine name consistency: EngineInvoker.engine_name must match the
      registration key (asserted at register-time).

Failure modes:
    - Missing invoker: if policy.required_engines references an
      unregistered engine, an error trace is recorded and
      EngineDispatchResult.all_succeeded is False.
    - Engine exception: caught per-engine, recorded in trace, does not
      abort other engine invocations in the same dispatch.
    - Missing parameters: empty FrozenEngineParams are synthesised; the
      engine itself must validate.

Audit relevance:
    - Every invocation produces an EngineTraceRecord with engine_name,
      engine_version, input_fingerprint, duration_ms, parameters_used,
      and success/error status -- persisted by the interpretation
      coordinator for post-hoc audit.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from finance_config.compiler import (
    CompiledPolicy,
    CompiledPolicyPack,
    FrozenEngineParams,
)
from finance_engines.tracer import compute_input_fingerprint

# Import pure DTOs from kernel/domain (re-export for backwards compatibility)
from finance_kernel.domain.engine_types import EngineDispatchResult, EngineTraceRecord

_logger = logging.getLogger("finance_services.engine_dispatcher")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineInvoker:
    """A registered engine callable.

    Contract:
        The invoke function receives:
          - payload: dict -- the event payload (read-only context)
          - params: FrozenEngineParams -- resolved configuration parameters
        and returns an arbitrary result object.

    Guarantees:
        Frozen dataclass; once created, the invoker is immutable.

    Non-goals:
        Does not manage engine lifecycle or caching; each invoke call is
        stateless from the dispatcher's perspective.
    """

    engine_name: str
    engine_version: str
    invoke: Callable[[dict, FrozenEngineParams], Any]
    fingerprint_fields: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class EngineDispatcher:
    """Runtime engine dispatch -- reads CompiledPolicyPack fields.

    Contract:
        Given a CompiledPolicy and event payload, dispatch all engines
        listed in ``policy.required_engines``, resolve their parameters
        from the CompiledPolicyPack, and return an EngineDispatchResult.

    Guarantees:
        - Each engine invocation is independent; an exception in one
          engine does not prevent others from running.
        - A full EngineTraceRecord is emitted for every engine, whether
          it succeeds or fails.
        - An empty required_engines list returns an empty success result.

    Non-goals:
        - Does not enforce ordering among engines.
        - Does not persist trace records (caller's responsibility).

    Usage:
        dispatcher = EngineDispatcher(compiled_pack)
        register_standard_engines(dispatcher)
        # ... later, during posting:
        result = dispatcher.dispatch(policy, payload)
    """

    def __init__(self, compiled_pack: CompiledPolicyPack) -> None:
        self._pack = compiled_pack
        self._registry: dict[str, EngineInvoker] = {}

    def register(self, engine_name: str, invoker: EngineInvoker) -> None:
        """Register an engine invoker.

        Preconditions:
            invoker.engine_name == engine_name (consistency check).

        Postconditions:
            The invoker is stored in the internal registry under
            engine_name, replacing any previous registration.

        Raises:
            ValueError: If invoker.engine_name does not match engine_name.
        """
        # INVARIANT [R14]: Registration-key must match invoker identity.
        if invoker.engine_name != engine_name:
            raise ValueError(
                f"Invoker engine_name '{invoker.engine_name}' "
                f"does not match registration key '{engine_name}'"
            )
        self._registry[engine_name] = invoker

    def dispatch(
        self,
        policy: CompiledPolicy,
        payload: dict[str, Any],
    ) -> EngineDispatchResult:
        """Dispatch all required engines for a policy.

        Preconditions:
            policy is a valid CompiledPolicy from the active pack.
            payload is a read-only dict (engine must not mutate it).

        Postconditions:
            Returns EngineDispatchResult where len(traces) ==
            len(policy.required_engines).

        Raises:
            No exceptions are raised to the caller; individual engine
            failures are captured in EngineDispatchResult.errors.

        For each engine in policy.required_engines:
          1. Look up resolved_engine_params via policy.engine_parameters_ref
             or fall back to the engine name.
          2. Invoke the registered engine with payload + params.
          3. Collect EngineTraceRecord per invocation.

        Returns EngineDispatchResult with all outputs and traces.
        If policy.required_engines is empty, returns an empty success result.
        """
        if not policy.required_engines:
            return EngineDispatchResult(
                engine_outputs={},
                traces=(),
                all_succeeded=True,
                errors=(),
            )

        outputs: dict[str, Any] = {}
        traces: list[EngineTraceRecord] = []
        errors: list[str] = []

        # Resolve parameters — use engine_parameters_ref if set,
        # otherwise fall back to engine name as key.
        param_key = policy.engine_parameters_ref

        for engine_name in policy.required_engines:
            invoker = self._registry.get(engine_name)
            if invoker is None:
                error_msg = (
                    f"Engine '{engine_name}' required by policy "
                    f"'{policy.name}' has no registered invoker"
                )
                errors.append(error_msg)
                traces.append(EngineTraceRecord(
                    engine_name=engine_name,
                    engine_version="unknown",
                    input_fingerprint="",
                    duration_ms=0.0,
                    parameters_used={},
                    success=False,
                    error=error_msg,
                ))
                continue

            # Look up frozen params — try param_key first, then engine name
            lookup_key = param_key or engine_name
            frozen_params = self._pack.resolved_engine_params.get(lookup_key)
            if frozen_params is None:
                # Fall back to engine name if param_key didn't match
                frozen_params = self._pack.resolved_engine_params.get(engine_name)
            if frozen_params is None:
                # No params — create empty params
                frozen_params = FrozenEngineParams(
                    engine_name=engine_name,
                    parameters={},
                )

            # Wire policy.variance_disposition into variance engine params (config-driven disposition)
            if engine_name == "variance" and getattr(policy, "variance_disposition", None) is not None:
                frozen_params = FrozenEngineParams(
                    engine_name=frozen_params.engine_name,
                    parameters={**frozen_params.parameters, "variance_disposition": policy.variance_disposition},
                )

            # Wire policy.valuation_model into valuation engine params (config-driven model selection)
            if engine_name == "valuation" and getattr(policy, "valuation_model", None) is not None:
                frozen_params = FrozenEngineParams(
                    engine_name=frozen_params.engine_name,
                    parameters={**frozen_params.parameters, "valuation_model": policy.valuation_model},
                )

            # Compute input fingerprint
            fingerprint = ""
            if invoker.fingerprint_fields:
                fingerprint = compute_input_fingerprint(
                    invoker.fingerprint_fields, payload,
                )

            # Invoke
            start_ns = time.perf_counter_ns()
            try:
                result = invoker.invoke(payload, frozen_params)
                duration_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
                outputs[engine_name] = result

                _logger.info(
                    "FINANCE_ENGINE_DISPATCH",
                    extra={
                        "trace_type": "FINANCE_ENGINE_DISPATCH",
                        "engine_name": engine_name,
                        "engine_version": invoker.engine_version,
                        "policy_name": policy.name,
                        "input_fingerprint": fingerprint,
                        "duration_ms": round(duration_ms, 3),
                        "success": True,
                        "parameters": dict(frozen_params.parameters),
                    },
                )

                traces.append(EngineTraceRecord(
                    engine_name=engine_name,
                    engine_version=invoker.engine_version,
                    input_fingerprint=fingerprint,
                    duration_ms=round(duration_ms, 3),
                    parameters_used=dict(frozen_params.parameters),
                    success=True,
                ))

            except Exception as exc:
                duration_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
                error_msg = (
                    f"Engine '{engine_name}' failed for policy "
                    f"'{policy.name}': {exc}"
                )
                errors.append(error_msg)

                _logger.error(
                    "FINANCE_ENGINE_DISPATCH_FAILED",
                    extra={
                        "trace_type": "FINANCE_ENGINE_DISPATCH_FAILED",
                        "engine_name": engine_name,
                        "engine_version": invoker.engine_version,
                        "policy_name": policy.name,
                        "input_fingerprint": fingerprint,
                        "duration_ms": round(duration_ms, 3),
                        "success": False,
                        "error": str(exc),
                        "parameters": dict(frozen_params.parameters),
                    },
                )

                traces.append(EngineTraceRecord(
                    engine_name=engine_name,
                    engine_version=invoker.engine_version,
                    input_fingerprint=fingerprint,
                    duration_ms=round(duration_ms, 3),
                    parameters_used=dict(frozen_params.parameters),
                    success=False,
                    error=str(exc),
                ))

        return EngineDispatchResult(
            engine_outputs=outputs,
            traces=tuple(traces),
            all_succeeded=len(errors) == 0,
            errors=tuple(errors),
        )

    def validate_registration(self) -> list[str]:
        """Check that every engine contract has a registered invoker.

        Preconditions:
            None.

        Postconditions:
            Returns a list of engine names present in the pack's
            engine_contracts but missing from the registry.
            Empty list means all engines are covered.

        Raises:
            Nothing.
        """
        unregistered = []
        for engine_name in self._pack.engine_contracts:
            if engine_name not in self._registry:
                unregistered.append(engine_name)
        return unregistered
