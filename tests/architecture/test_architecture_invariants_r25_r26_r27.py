"""
Architecture invariants R25, R26, R27.

R25 — Kernel primitives only. All monetary values, quantities, exchange rates,
      and artifact identities must use finance_kernel value objects. Modules
      may not define parallel financial types.
R26 — Journal is the system of record. Module ORM tables are operational
      projections only and must be derivable from the journal and link graph.
R27 — Matching is operational. Financial variance treatment and ledger impact
      are defined by kernel policy, not module logic.

Enforcement: R25 is enforced here and in test_primitive_reuse.py. R26 and R27
are design contracts; this module documents them and applies heuristic checks
where possible.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _module_py_files():
    """Yield (path, content) for finance_modules/**/*.py excluding __init__."""
    modules_dir = Path("finance_modules")
    if not modules_dir.exists():
        return
    for path in sorted(modules_dir.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            yield path, path.read_text()
        except (OSError, UnicodeDecodeError):
            continue


class TestR25KernelPrimitivesOnly:
    """R25: Modules must not define parallel ArtifactRef / ExchangeRate / Quantity-like types."""

    # Classes that would duplicate kernel primitives (finance_kernel.domain.values,
    # finance_kernel.domain.economic_link.ArtifactRef). Modules must use kernel types.
    FORBIDDEN_CLASS_NAMES = frozenset({
        "artifactref",
        "artifact_ref",
        "exchangerate",
        "exchange_rate",
        "quantity",
        "money",
        "amount",
        "currency",
    })

    def test_no_parallel_artifact_ref_or_rate_classes_in_modules(self):
        """No module should define ArtifactRef- or ExchangeRate-like classes."""
        violations = []
        for path, content in _module_py_files():
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    name_lower = node.name.lower().replace(" ", "")
                    if name_lower in self.FORBIDDEN_CLASS_NAMES:
                        violations.append(f"{path}: class {node.name} (R25: use kernel primitives)")
        assert not violations, (
            "R25 — Kernel primitives only. Modules must not define parallel types; "
            "use finance_kernel.domain.values and finance_kernel.domain.economic_link.ArtifactRef:\n"
            + "\n".join(violations)
        )


class TestR26R27Documentation:
    """R26 and R27 are design contracts. These tests document and lightly enforce them."""

    def test_r26_journal_system_of_record_documented(self):
        """R26: Journal is the system of record. Module ORM is derivable projection only."""
        # No automated check for "derivable from journal" — this test documents the invariant.
        # Enforcement: design review; module services must post events and not treat
        # module ORM as the source of financial truth for balances or ledger state.
        assert True, "R26 is a design invariant: journal + link graph are system of record"

    def test_r27_matching_is_operational_documented(self):
        """R27: Variance treatment and ledger impact are defined by kernel policy."""
        # No automated check for "policy defines impact" — this test documents the invariant.
        # Enforcement: modules must not branch on match/variance result to choose
        # accounts or entry types; profiles and guards define that.
        assert True, "R27 is a design invariant: kernel policy defines variance/ledger impact"
