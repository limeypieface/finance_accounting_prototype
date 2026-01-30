"""
Kernel Invariants Contract.

These invariants are structural law. They are hardcoded in the posting
boundary and database triggers. No AccountingConfigurationSet, capability
toggle, or policy may override them.

This module exists solely to declare these invariants explicitly. The
enforcement is distributed across JournalWriter, immutability triggers,
period_service, sequence_service, and ingestor_service.
"""

from enum import Enum, unique


@unique
class KernelInvariant(str, Enum):
    """Non-configurable invariants enforced by the kernel.

    Each value names one structural guarantee that the kernel provides
    unconditionally. Configuration may influence *what* gets posted, but
    never *whether* these rules apply.
    """

    DOUBLE_ENTRY_BALANCE = "double_entry_balance"
    """Debits must equal credits in every journal entry. Enforced by
    JournalWriter before commit and by DB check constraints."""

    IMMUTABILITY = "immutability"
    """Posted journal entries and lines are append-only. No UPDATE or
    DELETE on posted rows. Enforced by DB triggers
    (finance_kernel.db.immutability)."""

    PERIOD_LOCK = "period_lock"
    """No posting to closed fiscal periods. Enforced by PeriodService
    validation at the posting boundary."""

    LINK_LEGALITY = "link_legality"
    """Economic links connect only valid, existing entries. Enforced by
    LinkGraphService and FK constraints."""

    SEQUENCE_MONOTONICITY = "sequence_monotonicity"
    """Event sequence numbers are strictly monotonic within a stream.
    Enforced by SequenceService with DB advisory locks."""

    IDEMPOTENCY = "idempotency"
    """The same event (by idempotency_key) cannot produce duplicate
    postings. Enforced by IngestorService and unique constraints."""


# All invariants as a frozenset for programmatic checks.
ALL_KERNEL_INVARIANTS: frozenset[KernelInvariant] = frozenset(KernelInvariant)

# The kernel package may not import from these packages.
# This is enforced by tests/architecture/test_kernel_boundary.py.
FORBIDDEN_KERNEL_IMPORTS: tuple[str, ...] = (
    "finance_services",
    "finance_config",
    "finance_modules",
)
