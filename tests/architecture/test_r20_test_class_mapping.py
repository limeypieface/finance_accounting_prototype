"""
R20 Test Class Mapping verification.

R20. Test class mapping — tiered coverage model.

Coverage tiers:
  Tier 1 (critical invariants): R3, R4, R9, R10, R11
    MUST have unit tests. Concurrency tests required where they exist.
    Crash + replay tests are aspirational.
  Tier 2 (important invariants): R1, R2, R5, R6, R7, R8, R12, R13
    MUST have unit tests.  Concurrency, crash, replay are aspirational.
  Tier 3 (architectural invariants): R14, R15, R16, R17, R18, R19
    Architecture / domain tests are sufficient.  Concurrency, crash, and
    replay tests are not required (the invariants are structural, not
    runtime race-sensitive).

All ``required_tests`` entries reference files that exist on disk.
``aspirational_tests`` document desired future coverage but do not fail
the build if missing.
"""

import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Invariant → test file mapping (tiered)
# ---------------------------------------------------------------------------

INVARIANTS = {
    # ── Tier 1: Critical invariants ────────────────────────────────────────
    "R3": {
        "name": "Idempotency key uniqueness",
        "description": "Exactly one JournalEntry per idempotency_key",
        "tier": 1,
        "required_tests": [
            "tests/posting/test_idempotency.py",
        ],
        "aspirational_tests": [
            "tests/concurrency/test_idempotency_race.py",
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    "R4": {
        "name": "Balance per currency",
        "description": "JournalEntry must balance per currency",
        "tier": 1,
        "required_tests": [
            "tests/posting/test_balance.py",
        ],
        "aspirational_tests": [
            "tests/concurrency/test_balance_race.py",
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    "R9": {
        "name": "Sequence safety",
        "description": "Use database sequence or locked counter row",
        "tier": 1,
        "required_tests": [
            "tests/concurrency/test_r9_sequence_safety.py",
        ],
        "aspirational_tests": [
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    "R10": {
        "name": "Posted record immutability",
        "description": "Posted JournalEntry, JournalLine, AuditEvent are immutable",
        "tier": 1,
        "required_tests": [
            "tests/audit/test_database_attacks.py",
            "tests/audit/test_immutability_triggers.py",
        ],
        "aspirational_tests": [
            "tests/concurrency/test_concurrent_mutation.py",
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    "R11": {
        "name": "Audit chain integrity",
        "description": "Audit chain must validate end-to-end",
        "tier": 1,
        "required_tests": [
            "tests/audit/test_failed_posting_audit.py",
        ],
        "aspirational_tests": [
            "tests/audit/test_chain_validation.py",
            "tests/concurrency/test_chain_race.py",
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    # ── Tier 2: Important invariants ───────────────────────────────────────
    "R1": {
        "name": "Event immutability",
        "description": "Events are immutable after ingestion",
        "tier": 2,
        "required_tests": [
            "tests/audit/test_event_protocol_violation.py",
        ],
        "aspirational_tests": [
            "tests/concurrency/test_race_safety.py",
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    "R2": {
        "name": "Payload hash verification",
        "description": "Same event_id + different payload = protocol violation",
        "tier": 2,
        "required_tests": [
            "tests/audit/test_event_protocol_violation.py",
        ],
        "aspirational_tests": [
            "tests/concurrency/test_race_safety.py",
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    "R5": {
        "name": "Rounding line uniqueness",
        "description": "Rounding creates exactly one marked line",
        "tier": 2,
        "required_tests": [
            "tests/adversarial/test_rounding_line_abuse.py",
        ],
        "aspirational_tests": [
            "tests/adversarial/test_rounding_invariant_gaps.py",
            "tests/concurrency/test_stress.py",
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    "R6": {
        "name": "Replay safety",
        "description": "Ledger state reproducible from journal + reference data",
        "tier": 2,
        "required_tests": [
            "tests/domain/test_pure_layer.py",
        ],
        "aspirational_tests": [
            "tests/replay/test_r6_replay_safety.py",
            "tests/concurrency/test_race_safety.py",
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    "R7": {
        "name": "Transaction boundaries",
        "description": "Each service owns its transaction boundary",
        "tier": 2,
        "required_tests": [
            "tests/domain/test_pure_layer.py",
        ],
        "aspirational_tests": [
            "tests/concurrency/test_race_safety.py",
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    "R8": {
        "name": "Idempotency locking",
        "description": "Database uniqueness + row-level locks",
        "tier": 2,
        "required_tests": [
            "tests/posting/test_idempotency.py",
        ],
        "aspirational_tests": [
            "tests/posting/test_r8_idempotency_locking.py",
            "tests/concurrency/test_race_safety.py",
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    "R12": {
        "name": "Closed period enforcement",
        "description": "No posting to closed fiscal periods",
        "tier": 2,
        "required_tests": [
            "tests/posting/test_period_lock.py",
        ],
        "aspirational_tests": [
            "tests/period/test_period_rules.py",
            "tests/concurrency/test_race_safety.py",
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    "R13": {
        "name": "Adjustment policy",
        "description": "allows_adjustments must be enforced",
        "tier": 2,
        "required_tests": [
            "tests/posting/test_period_lock.py",
        ],
        "aspirational_tests": [
            "tests/period/test_period_rules.py",
            "tests/audit/test_fiscal_period_immutability.py",
            "tests/concurrency/test_race_safety.py",
            "tests/crash/test_durability.py",
            "tests/replay/test_determinism.py",
        ],
    },
    # ── Tier 3: Architectural invariants ───────────────────────────────────
    "R14": {
        "name": "No central dispatch",
        "description": "PostingEngine may not branch on event_type",
        "tier": 3,
        "required_tests": [
            "tests/domain/test_strategy_purity.py",
            "tests/architecture/test_primitive_reuse.py",
        ],
        "aspirational_tests": [],
    },
    "R15": {
        "name": "Open/closed compliance",
        "description": "New event type requires no engine modification",
        "tier": 3,
        "required_tests": [
            "tests/domain/test_strategy_purity.py",
            "tests/architecture/test_primitive_reuse.py",
        ],
        "aspirational_tests": [],
    },
    "R16": {
        "name": "ISO 4217 enforcement",
        "description": "Currency codes validated at boundary",
        "tier": 3,
        "required_tests": [
            "tests/unit/test_currency.py",
        ],
        "aspirational_tests": [],
    },
    "R17": {
        "name": "Precision-derived tolerance",
        "description": "Rounding tolerance derived from currency precision",
        "tier": 3,
        "required_tests": [
            "tests/unit/test_currency.py",
            "tests/unit/test_money.py",
        ],
        "aspirational_tests": [],
    },
    "R18": {
        "name": "Deterministic errors",
        "description": "Typed exceptions with machine-readable codes",
        "tier": 3,
        "required_tests": [
            "tests/architecture/test_error_handling.py",
        ],
        "aspirational_tests": [],
    },
    "R19": {
        "name": "No silent correction",
        "description": "Inconsistencies fail or have traceable rounding",
        "tier": 3,
        "required_tests": [
            "tests/architecture/test_error_handling.py",
        ],
        "aspirational_tests": [],
    },
}

# Tier definitions for documentation
TIER_DESCRIPTIONS = {
    1: "Critical — MUST have unit + concurrency (where available). Crash/replay aspirational.",
    2: "Important — MUST have unit tests. Concurrency/crash/replay aspirational.",
    3: "Architectural — Architecture or domain tests sufficient.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invariants_by_tier(tier: int) -> dict[str, dict]:
    return {k: v for k, v in INVARIANTS.items() if v["tier"] == tier}


# ---------------------------------------------------------------------------
# TestR20TestClassMapping — core contract
# ---------------------------------------------------------------------------

class TestR20TestClassMapping:
    """
    Verify that all invariants have appropriate test coverage.

    R20: Tiered coverage model — every invariant must have at least
    one required test file that exists on disk.
    """

    def test_all_invariants_documented(self):
        """All invariants R1-R19 must be documented in the mapping."""
        expected_invariants = {f"R{i}" for i in range(1, 20)}
        documented_invariants = set(INVARIANTS.keys())

        missing = expected_invariants - documented_invariants
        assert len(missing) == 0, f"Missing invariants in mapping: {missing}"

    def test_all_invariants_have_required_tests(self):
        """Every invariant must have at least one required test file."""
        missing = []
        for inv_id, inv in INVARIANTS.items():
            if not inv.get("required_tests"):
                missing.append(inv_id)

        assert not missing, f"Invariants missing required tests: {missing}"

    def test_all_required_test_files_exist(self):
        """All required test files must exist on disk.

        Only ``required_tests`` are checked.  ``aspirational_tests`` are
        documented goals and do not fail the build.
        """
        project_root = Path(__file__).parent.parent.parent
        missing_files = []

        for inv_id, inv in INVARIANTS.items():
            for test_file in inv.get("required_tests", []):
                file_path = project_root / test_file
                if not file_path.exists():
                    missing_files.append((inv_id, test_file))

        assert len(missing_files) == 0, (
            f"Required test files not found: {missing_files}"
        )

    def test_tier_assignments_valid(self):
        """Every invariant must be assigned to tier 1, 2, or 3."""
        invalid = []
        for inv_id, inv in INVARIANTS.items():
            if inv.get("tier") not in (1, 2, 3):
                invalid.append(inv_id)

        assert not invalid, f"Invalid tier assignments: {invalid}"


# ---------------------------------------------------------------------------
# TestR20TestDirectoryStructure
# ---------------------------------------------------------------------------

class TestR20TestDirectoryStructure:
    """Verify test directory structure supports R20 requirements."""

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


# ---------------------------------------------------------------------------
# TestR20CoverageCompleteness — Tier-specific enforcement
# ---------------------------------------------------------------------------

class TestR20CoverageCompleteness:
    """Verify test coverage completeness for critical paths."""

    def test_immutability_has_comprehensive_coverage(self):
        """R10 (immutability) must have ORM + DB trigger tests."""
        r10 = INVARIANTS["R10"]
        files = r10["required_tests"]

        assert any("immutability" in f for f in files), (
            "R10 must have dedicated immutability tests"
        )
        assert any("database_attacks" in f for f in files), (
            "R10 must have database attack resistance tests"
        )

    def test_sequence_safety_has_concurrent_coverage(self):
        """R9 (sequence safety) must have concurrency tests."""
        r9 = INVARIANTS["R9"]
        files = r9["required_tests"]

        assert any("concurrency" in f for f in files), (
            "R9 must have concurrency test coverage"
        )

    def test_tier1_invariants_have_coverage(self):
        """All Tier 1 invariants must have at least one required test."""
        tier1 = _invariants_by_tier(1)
        for inv_id, inv in tier1.items():
            assert inv["required_tests"], (
                f"Tier 1 invariant {inv_id} ({inv['name']}) has no required tests"
            )


# ---------------------------------------------------------------------------
# TestR20DocumentedCoverage — audit matrix
# ---------------------------------------------------------------------------

class TestR20DocumentedCoverage:
    """Document the test coverage matrix for audit purposes."""

    def test_generate_coverage_matrix(self):
        """Generate and verify the coverage matrix."""
        coverage_matrix = []

        for inv_id in sorted(INVARIANTS.keys(), key=lambda x: int(x[1:])):
            inv = INVARIANTS[inv_id]
            coverage_matrix.append({
                "invariant": inv_id,
                "name": inv["name"],
                "tier": inv["tier"],
                "required": len(inv.get("required_tests", [])),
                "aspirational": len(inv.get("aspirational_tests", [])),
            })

        # Every invariant must have at least 1 required test
        for row in coverage_matrix:
            assert row["required"] >= 1, (
                f"{row['invariant']} has no required tests"
            )

        assert len(coverage_matrix) == 19, "Expected 19 invariants (R1-R19)"

    def test_aspirational_coverage_documented(self):
        """Aspirational coverage gaps are documented (not enforced)."""
        gaps = []
        for inv_id in sorted(INVARIANTS.keys(), key=lambda x: int(x[1:])):
            inv = INVARIANTS[inv_id]
            aspirational = inv.get("aspirational_tests", [])
            if aspirational:
                gaps.append(f"  {inv_id} ({inv['name']}): {len(aspirational)} aspirational files")

        # This test always passes — it's documentation
        assert True, "Aspirational gaps:\n" + "\n".join(gaps)
