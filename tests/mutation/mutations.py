"""
Mutation kill-rate audit: architectural seam mutations.

Each mutation deliberately breaks one seam. When the test suite runs with
MUTATION_NAME set, we expect tests to FAIL (they should detect the break).
If any test still passes, the build fails and we report the test name and mutation.

Mutations:
- SKIP_POLICY_SELECTION: PolicySelector.find_for_event always raises PolicyNotFoundError.
- BYPASS_SEQUENCE_ALLOCATION: SequenceService.next_value always returns 1.
- JOURNAL_WRITER_RETURN_SUCCESS_WITHOUT_WRITING: JournalWriter.write returns success without persisting.
- DISABLE_IMMUTABILITY_GUARD: register_immutability_listeners is a no-op.
- HARDCODE_ENGINE_DISPATCH_RESULT: EngineDispatcher.dispatch returns success without invoking engines.
"""

from __future__ import annotations

from unittest.mock import patch

# Mutation names (used in CI rotation and MUTATION_NAME env var).
MUTATION_NAMES = [
    "SKIP_POLICY_SELECTION",
    "BYPASS_SEQUENCE_ALLOCATION",
    "JOURNAL_WRITER_RETURN_SUCCESS_WITHOUT_WRITING",
    "DISABLE_IMMUTABILITY_GUARD",
    "HARDCODE_ENGINE_DISPATCH_RESULT",
]

# Hold references so patches stay active for the session.
_active_patches: list = []


def _mutate_skip_policy_selection() -> None:
    """Policy selection is skipped: find_for_event always raises (no profile)."""
    from finance_kernel.domain.policy_selector import (
        PolicyNotFoundError,
        PolicySelector,
    )

    def _raiser(cls, event_type, effective_date, scope_value="*", payload=None):
        raise PolicyNotFoundError(event_type, effective_date)

    _active_patches.append(
        patch.object(PolicySelector, "find_for_event", classmethod(_raiser))
    )


def _mutate_bypass_sequence_allocation() -> None:
    """Sequence allocation bypassed: next_value always returns 1."""
    from finance_kernel.services.sequence_service import SequenceService

    def _fake_next_value(self, sequence_name: str) -> int:
        return 1

    _active_patches.append(
        patch.object(SequenceService, "next_value", _fake_next_value)
    )


def _mutate_journal_writer_return_success_without_writing() -> None:
    """Journal writer reports success without persisting any entries."""
    from finance_kernel.services.journal_writer import (
        JournalWriteResult,
        JournalWriter,
        WriteStatus,
    )

    def _fake_write(self, intent, actor_id, event_type="economic.posting"):
        return JournalWriteResult(status=WriteStatus.WRITTEN, entries=())

    _active_patches.append(
        patch.object(JournalWriter, "write", _fake_write)
    )


def _mutate_disable_immutability_guard() -> None:
    """Immutability listeners are not registered (R10 guards disabled)."""
    from finance_kernel.db import immutability

    def _noop() -> None:
        pass

    _active_patches.append(
        patch.object(immutability, "register_immutability_listeners", _noop)
    )


def _mutate_hardcode_engine_dispatch_result() -> None:
    """Engine dispatch returns success without invoking any engine."""
    from finance_kernel.domain.engine_types import (
        EngineDispatchResult,
        EngineTraceRecord,
    )
    from finance_services.engine_dispatcher import EngineDispatcher

    def _fake_dispatch(self, policy, payload):
        traces = []
        for engine_name in getattr(policy, "required_engines", []) or []:
            traces.append(
                EngineTraceRecord(
                    engine_name=engine_name,
                    engine_version="0.0.0",
                    input_fingerprint="",
                    duration_ms=0.0,
                    parameters_used={},
                    success=True,
                    error=None,
                )
            )
        return EngineDispatchResult(
            engine_outputs={},
            traces=tuple(traces),
            all_succeeded=True,
            errors=(),
        )

    _active_patches.append(
        patch.object(EngineDispatcher, "dispatch", _fake_dispatch)
    )


def apply_mutation(name: str) -> None:
    """Apply the named mutation. Call once at session start when MUTATION_NAME is set."""
    global _active_patches
    _active_patches.clear()
    if name == "SKIP_POLICY_SELECTION":
        _mutate_skip_policy_selection()
    elif name == "BYPASS_SEQUENCE_ALLOCATION":
        _mutate_bypass_sequence_allocation()
    elif name == "JOURNAL_WRITER_RETURN_SUCCESS_WITHOUT_WRITING":
        _mutate_journal_writer_return_success_without_writing()
    elif name == "DISABLE_IMMUTABILITY_GUARD":
        _mutate_disable_immutability_guard()
    elif name == "HARDCODE_ENGINE_DISPATCH_RESULT":
        _mutate_hardcode_engine_dispatch_result()
    else:
        raise ValueError(f"Unknown mutation: {name}. Known: {MUTATION_NAMES}")
    for p in _active_patches:
        p.start()
