"""Kernel invariants contract -- non-configurable structural law."""

from enum import Enum, unique


@unique
class KernelInvariant(str, Enum):
    """Non-configurable invariants enforced unconditionally by the kernel."""

    DOUBLE_ENTRY_BALANCE = "double_entry_balance"
    """Debits must equal credits in every journal entry."""

    IMMUTABILITY = "immutability"
    """Posted journal entries and lines are append-only."""

    PERIOD_LOCK = "period_lock"
    """No posting to closed fiscal periods."""

    LINK_LEGALITY = "link_legality"
    """Economic links connect only valid, existing entries."""

    SEQUENCE_MONOTONICITY = "sequence_monotonicity"
    """Event sequence numbers are strictly monotonic within a stream."""

    IDEMPOTENCY = "idempotency"
    """The same event cannot produce duplicate postings."""


# All invariants as a frozenset for programmatic checks.
ALL_KERNEL_INVARIANTS: frozenset[KernelInvariant] = frozenset(KernelInvariant)

# The kernel package may not import from these packages.
# This is enforced by tests/architecture/test_kernel_boundary.py.
FORBIDDEN_KERNEL_IMPORTS: tuple[str, ...] = (
    "finance_services",
    "finance_config",
    "finance_modules",
)

# ---------------------------------------------------------------------------
# Architecture invariants (R25–R27). Enforced across the codebase.
# See CLAUDE.md Full Invariant Table and tests/architecture/.
# ---------------------------------------------------------------------------
# R25 — Kernel primitives only. All monetary values, quantities, exchange
#       rates, and artifact identities must use finance_kernel value objects
#       (Money, ArtifactRef, Quantity, ExchangeRate). Modules may not define
#       parallel financial types. Enforced: test_primitive_reuse.py.
# R26 — Journal is the system of record. Module ORM tables are operational
#       projections only and must be derivable from the journal and link
#       graph. Financial truth lives in the journal.
# R27 — Matching is operational. Financial variance treatment and ledger
#       impact are defined by kernel policy (profiles, guards), not module
#       logic. Modules call engines; policy decides accounts and posting.
