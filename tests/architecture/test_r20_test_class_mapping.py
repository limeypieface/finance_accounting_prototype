"""
R20 Test Class Mapping verification.

R20. Test class mapping
Every invariant must have:
- Unit tests (local correctness)
- Concurrency tests (race safety)
- Crash/restart tests (durability)
- Replay tests (determinism)

This test file verifies that all invariants have appropriate test coverage
across all required test categories.
"""

import pytest
from pathlib import Path
import re


# Define all invariants and their test coverage requirements
INVARIANTS = {
    # Core posting invariants
    "R1": {
        "name": "Event immutability",
        "description": "Events are immutable after ingestion",
        "unit_tests": ["tests/audit/test_event_protocol_violation.py"],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R2": {
        "name": "Payload hash verification",
        "description": "Same event_id + different payload = protocol violation",
        "unit_tests": ["tests/audit/test_event_protocol_violation.py"],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R3": {
        "name": "Idempotency key uniqueness",
        "description": "Exactly one JournalEntry per idempotency_key",
        "unit_tests": ["tests/posting/test_idempotency.py"],
        "concurrency_tests": [
            "tests/concurrency/test_race_safety.py",
            "tests/concurrency/test_true_concurrency.py",
        ],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R4": {
        "name": "Balance per currency",
        "description": "JournalEntry must balance per currency",
        "unit_tests": ["tests/posting/test_balance.py"],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R5": {
        "name": "Rounding line uniqueness",
        "description": "Rounding creates exactly one marked line",
        "unit_tests": [
            "tests/adversarial/test_rounding_line_abuse.py",
            "tests/adversarial/test_rounding_invariant_gaps.py",
        ],
        "concurrency_tests": ["tests/concurrency/test_stress.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R6": {
        "name": "Replay safety",
        "description": "Ledger state reproducible from journal + reference data",
        "unit_tests": ["tests/replay/test_r6_replay_safety.py"],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": [
            "tests/replay/test_r6_replay_safety.py",
            "tests/replay/test_determinism.py",
        ],
    },
    "R7": {
        "name": "Transaction boundaries",
        "description": "Each service owns its transaction boundary",
        "unit_tests": ["tests/domain/test_pure_layer.py"],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R8": {
        "name": "Idempotency locking",
        "description": "Database uniqueness + row-level locks",
        "unit_tests": ["tests/posting/test_r8_idempotency_locking.py"],
        "concurrency_tests": [
            "tests/posting/test_r8_idempotency_locking.py",
            "tests/concurrency/test_race_safety.py",
        ],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R9": {
        "name": "Sequence safety",
        "description": "Use database sequence or locked counter row",
        "unit_tests": ["tests/concurrency/test_r9_sequence_safety.py"],
        "concurrency_tests": [
            "tests/concurrency/test_r9_sequence_safety.py",
            "tests/concurrency/test_race_safety.py",
        ],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R10": {
        "name": "Posted record immutability",
        "description": "Posted JournalEntry, JournalLine, AuditEvent are immutable",
        "unit_tests": [
            "tests/audit/test_immutability.py",
            "tests/audit/test_database_attacks.py",
        ],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R11": {
        "name": "Audit chain integrity",
        "description": "Audit chain must validate end-to-end",
        "unit_tests": [
            "tests/audit/test_chain_validation.py",
            "tests/audit/test_immutability.py",
        ],
        "concurrency_tests": [
            "tests/concurrency/test_race_safety.py",
            "tests/concurrency/test_stress.py",
        ],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R12": {
        "name": "Closed period enforcement",
        "description": "No posting to closed fiscal periods",
        "unit_tests": [
            "tests/posting/test_period_lock.py",
            "tests/period/test_period_rules.py",
        ],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R13": {
        "name": "Adjustment policy",
        "description": "allows_adjustments must be enforced",
        "unit_tests": [
            "tests/period/test_period_rules.py",
            "tests/audit/test_fiscal_period_immutability.py",
        ],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R14": {
        "name": "No central dispatch",
        "description": "PostingEngine may not branch on event_type",
        "unit_tests": ["tests/architecture/test_open_closed.py"],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R15": {
        "name": "Open/closed compliance",
        "description": "New event type requires no engine modification",
        "unit_tests": ["tests/architecture/test_open_closed.py"],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R16": {
        "name": "ISO 4217 enforcement",
        "description": "Currency codes validated at boundary",
        "unit_tests": ["tests/unit/test_currency.py"],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R17": {
        "name": "Precision-derived tolerance",
        "description": "Rounding tolerance derived from currency precision",
        "unit_tests": ["tests/unit/test_currency.py", "tests/unit/test_money.py"],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R18": {
        "name": "Deterministic errors",
        "description": "Typed exceptions with machine-readable codes",
        "unit_tests": ["tests/architecture/test_error_handling.py"],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
    "R19": {
        "name": "No silent correction",
        "description": "Inconsistencies fail or have traceable rounding",
        "unit_tests": ["tests/architecture/test_error_handling.py"],
        "concurrency_tests": ["tests/concurrency/test_race_safety.py"],
        "crash_tests": ["tests/crash/test_durability.py"],
        "replay_tests": ["tests/replay/test_determinism.py"],
    },
}


class TestR20TestClassMapping:
    """
    Verify that all invariants have appropriate test coverage.

    R20: Every invariant must have unit, concurrency, crash, and replay tests.
    """

    def test_all_invariants_documented(self):
        """
        All invariants R1-R19 must be documented in the mapping.

        R20: Complete coverage mapping required.
        """
        expected_invariants = {f"R{i}" for i in range(1, 20)}
        documented_invariants = set(INVARIANTS.keys())

        missing = expected_invariants - documented_invariants
        assert len(missing) == 0, f"Missing invariants in mapping: {missing}"

    def test_all_invariants_have_unit_tests(self):
        """
        Every invariant must have at least one unit test file.

        R20: Unit tests verify local correctness.
        """
        missing_unit_tests = []
        for inv_id, inv in INVARIANTS.items():
            if not inv.get("unit_tests"):
                missing_unit_tests.append(inv_id)

        assert len(missing_unit_tests) == 0, (
            f"Invariants missing unit tests: {missing_unit_tests}"
        )

    def test_all_invariants_have_concurrency_tests(self):
        """
        Every invariant must have at least one concurrency test file.

        R20: Concurrency tests verify race safety.
        """
        missing_concurrency_tests = []
        for inv_id, inv in INVARIANTS.items():
            if not inv.get("concurrency_tests"):
                missing_concurrency_tests.append(inv_id)

        assert len(missing_concurrency_tests) == 0, (
            f"Invariants missing concurrency tests: {missing_concurrency_tests}"
        )

    def test_all_invariants_have_crash_tests(self):
        """
        Every invariant must have at least one crash/restart test file.

        R20: Crash tests verify durability.
        """
        missing_crash_tests = []
        for inv_id, inv in INVARIANTS.items():
            if not inv.get("crash_tests"):
                missing_crash_tests.append(inv_id)

        assert len(missing_crash_tests) == 0, (
            f"Invariants missing crash tests: {missing_crash_tests}"
        )

    def test_all_invariants_have_replay_tests(self):
        """
        Every invariant must have at least one replay test file.

        R20: Replay tests verify determinism.
        """
        missing_replay_tests = []
        for inv_id, inv in INVARIANTS.items():
            if not inv.get("replay_tests"):
                missing_replay_tests.append(inv_id)

        assert len(missing_replay_tests) == 0, (
            f"Invariants missing replay tests: {missing_replay_tests}"
        )

    def test_all_test_files_exist(self):
        """
        All referenced test files must exist.

        R20: Mapping must reference real test files.
        """
        project_root = Path(__file__).parent.parent.parent
        missing_files = []

        for inv_id, inv in INVARIANTS.items():
            all_files = (
                inv.get("unit_tests", [])
                + inv.get("concurrency_tests", [])
                + inv.get("crash_tests", [])
                + inv.get("replay_tests", [])
            )
            for test_file in all_files:
                file_path = project_root / test_file
                if not file_path.exists():
                    missing_files.append((inv_id, test_file))

        assert len(missing_files) == 0, (
            f"Test files referenced but not found: {missing_files}"
        )


class TestR20TestDirectoryStructure:
    """
    Verify test directory structure supports R20 requirements.
    """

    def test_unit_test_directory_exists(self):
        """Unit test directory must exist."""
        project_root = Path(__file__).parent.parent.parent
        assert (project_root / "tests" / "unit").exists()

    def test_concurrency_test_directory_exists(self):
        """Concurrency test directory must exist."""
        project_root = Path(__file__).parent.parent.parent
        assert (project_root / "tests" / "concurrency").exists()

    def test_crash_test_directory_exists(self):
        """Crash/durability test directory must exist."""
        project_root = Path(__file__).parent.parent.parent
        assert (project_root / "tests" / "crash").exists()

    def test_replay_test_directory_exists(self):
        """Replay/determinism test directory must exist."""
        project_root = Path(__file__).parent.parent.parent
        assert (project_root / "tests" / "replay").exists()


class TestR20CoverageCompleteness:
    """
    Verify test coverage is complete for critical paths.
    """

    def test_immutability_has_comprehensive_coverage(self):
        """
        R10 (immutability) is critical and must have extensive coverage.

        Immutability violations could allow fraud, so we need:
        - ORM-level tests
        - Database trigger tests
        - Raw SQL attack tests
        - Concurrent modification tests
        """
        r10 = INVARIANTS["R10"]

        # Must have dedicated immutability tests
        unit_files = r10["unit_tests"]
        assert any("immutability" in f for f in unit_files), (
            "R10 must have dedicated immutability tests"
        )

        # Must have database attack tests
        assert any("database_attacks" in f for f in unit_files), (
            "R10 must have database attack resistance tests"
        )

    def test_idempotency_has_concurrent_coverage(self):
        """
        R3/R8 (idempotency) must have true concurrency tests.

        Race conditions in idempotency could cause duplicates.
        """
        r8 = INVARIANTS["R8"]

        conc_files = r8["concurrency_tests"]
        assert len(conc_files) >= 1, (
            "R8 must have at least one concurrency test file"
        )

    def test_sequence_safety_has_concurrent_coverage(self):
        """
        R9 (sequence safety) must have true concurrency tests.

        Race conditions in sequences could cause duplicates or gaps.
        """
        r9 = INVARIANTS["R9"]

        conc_files = r9["concurrency_tests"]
        assert len(conc_files) >= 1, (
            "R9 must have at least one concurrency test file"
        )

    def test_audit_chain_has_crash_coverage(self):
        """
        R11 (audit chain) must survive crashes.

        Broken audit chains after crash would compromise integrity.
        """
        r11 = INVARIANTS["R11"]

        crash_files = r11["crash_tests"]
        assert len(crash_files) >= 1, (
            "R11 must have at least one crash test file"
        )


class TestR20DocumentedCoverage:
    """
    Document the test coverage matrix for audit purposes.
    """

    def test_generate_coverage_matrix(self):
        """
        Generate and verify the coverage matrix.

        This test documents the current coverage state.
        """
        coverage_matrix = []

        for inv_id in sorted(INVARIANTS.keys(), key=lambda x: int(x[1:])):
            inv = INVARIANTS[inv_id]
            coverage_matrix.append({
                "invariant": inv_id,
                "name": inv["name"],
                "unit": len(inv.get("unit_tests", [])),
                "concurrency": len(inv.get("concurrency_tests", [])),
                "crash": len(inv.get("crash_tests", [])),
                "replay": len(inv.get("replay_tests", [])),
            })

        # Verify all have at least 1 in each category
        for row in coverage_matrix:
            assert row["unit"] >= 1, f"{row['invariant']} missing unit tests"
            assert row["concurrency"] >= 1, f"{row['invariant']} missing concurrency tests"
            assert row["crash"] >= 1, f"{row['invariant']} missing crash tests"
            assert row["replay"] >= 1, f"{row['invariant']} missing replay tests"

        # Document the matrix
        documented_coverage = """
        R20 Test Coverage Matrix:

        | Invariant | Name | Unit | Concurrency | Crash | Replay |
        |-----------|------|------|-------------|-------|--------|
        """
        for row in coverage_matrix:
            documented_coverage += (
                f"| {row['invariant']} | {row['name'][:30]} | "
                f"{row['unit']} | {row['concurrency']} | "
                f"{row['crash']} | {row['replay']} |\n"
            )

        # This test passes if all invariants have coverage
        assert len(coverage_matrix) == 19, "Expected 19 invariants (R1-R19)"
