"""
R9 Sequence Safety tests.

R9. Sequence safety

Sequence numbers must be assigned by:
1. Database sequence, OR
2. Locked counter row (SELECT ... FOR UPDATE).

MAX(seq)+1 patterns are FORBIDDEN.

This ensures:
- No duplicate sequence numbers under concurrency
- No gaps reused (monotonically increasing)
- Transaction-safe sequence assignment

Run with: pytest tests/concurrency/test_r9_sequence_safety.py -v
Skip with: pytest -m "not slow_locks"
"""


import ast
import inspect
import re
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from finance_kernel.models.journal import JournalEntry
from finance_kernel.services.sequence_service import SequenceCounter, SequenceService

pytestmark = pytest.mark.slow_locks


class TestR9SequenceImplementation:
    """
    Verify sequence implementation uses approved patterns.

    R9: Must use database sequence or locked counter row.
    """

    def test_sequence_service_uses_locked_counter_row(self, session):
        """
        Verify SequenceService uses locked counter row pattern.

        R9: Locked counter row (SELECT FOR UPDATE) is approved.
        """
        # Verify SequenceCounter table exists
        from sqlalchemy import inspect as sa_inspect
        inspector = sa_inspect(session.bind)
        tables = inspector.get_table_names()

        assert 'sequence_counters' in tables, \
            "sequence_counters table must exist for locked counter pattern"

        # Verify table structure
        columns = {c['name'] for c in inspector.get_columns('sequence_counters')}
        assert 'name' in columns, "sequence_counters must have 'name' column"
        assert 'current_value' in columns, "sequence_counters must have 'current_value' column"

    def test_sequence_service_uses_for_update(self):
        """
        Verify SequenceService.next_value uses SELECT FOR UPDATE.

        R9: Row-level lock required for safe counter increment.
        """
        # Use source file so we see the real implementation, not a pytest-plugin wrapper
        # (e.g. reality detector wraps next_value; inspect.getsource would see the wrapper).
        path = Path(inspect.getfile(SequenceService))
        source = path.read_text()
        # Find the next_value method body: from "def next_value" to next "    def " or end of file
        match = re.search(
            r"def next_value\s*\([^)]*\).*?(?=\n    def \w|\nclass \w|\Z)",
            source,
            re.DOTALL,
        )
        assert match, "SequenceService.next_value not found in source"
        method_source = match.group(0)

        # Must contain with_for_update()
        assert "with_for_update()" in method_source, (
            "SequenceService.next_value must use with_for_update() for row-level locking"
        )

    def test_no_max_seq_pattern_in_sequence_service(self):
        """
        Verify SequenceService does NOT use MAX(seq)+1 pattern.

        R9: MAX(seq)+1 patterns are FORBIDDEN.
        """
        source = inspect.getsource(SequenceService)

        # Check for forbidden patterns
        forbidden_patterns = [
            r'MAX\s*\(',
            r'max\s*\(',
            r'func\.max',
            r'\.max\s*\(',
        ]

        for pattern in forbidden_patterns:
            matches = re.findall(pattern, source, re.IGNORECASE)
            assert len(matches) == 0, \
                f"SequenceService contains forbidden MAX pattern: {pattern}"

    def test_no_max_seq_pattern_in_codebase(self):
        """
        Verify no MAX(seq)+1 patterns exist in the finance_kernel codebase.

        R9: MAX(seq)+1 patterns are FORBIDDEN everywhere.
        """
        # Get all Python files in finance_kernel
        kernel_path = Path(__file__).parent.parent.parent / 'finance_kernel'
        python_files = list(kernel_path.rglob('*.py'))

        forbidden_files = []

        for py_file in python_files:
            content = py_file.read_text()

            # Check for forbidden patterns
            forbidden_patterns = [
                (r'MAX\s*\(\s*\w*\.?seq', 'MAX(seq)'),
                (r'max\s*\(\s*\w*\.?seq', 'max(seq)'),
                (r'\.seq\s*\+\s*1', 'seq + 1'),
                (r'seq\s*=\s*.*\+\s*1', 'seq = ... + 1'),
            ]

            for pattern, desc in forbidden_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    forbidden_files.append((py_file.name, desc))

        assert len(forbidden_files) == 0, \
            f"Found forbidden MAX(seq)+1 patterns in: {forbidden_files}"


class TestR9SequenceMonotonicity:
    """
    Verify sequences are strictly monotonically increasing.

    R9: Sequences must never go backwards or repeat.
    """

    def test_sequences_strictly_increasing(self, session):
        """
        Sequence values must be strictly increasing.

        R9: Monotonicity required for audit trail integrity.
        """
        service = SequenceService(session)

        # Get 100 sequence values
        values = []
        for _ in range(100):
            val = service.next_value("test_r9_monotonic")
            values.append(val)

        # All must be unique
        assert len(set(values)) == 100, "All sequence values must be unique"

        # Must be strictly increasing
        for i in range(1, len(values)):
            assert values[i] > values[i - 1], \
                f"Sequence must increase: {values[i-1]} -> {values[i]}"

    def test_no_duplicate_sequences(self, session):
        """
        No two calls to next_value can return the same number.

        R9: Duplicates would break audit trail and ordering.
        """
        service = SequenceService(session)

        # Get many sequence values
        values = [service.next_value("test_r9_unique") for _ in range(500)]

        # Count occurrences
        from collections import Counter
        counts = Counter(values)

        duplicates = {v: c for v, c in counts.items() if c > 1}
        assert len(duplicates) == 0, f"Found duplicate sequences: {duplicates}"

    def test_sequences_never_decrease(self, session):
        """
        Sequence values must never decrease.

        R9: Decreasing sequences would break ordering guarantees.
        """
        service = SequenceService(session)

        prev_value = 0
        for _ in range(200):
            value = service.next_value("test_r9_no_decrease")
            assert value > prev_value, \
                f"Sequence decreased from {prev_value} to {value}"
            prev_value = value


class TestR9GapHandling:
    """
    Verify gap handling in sequences.

    R9: Gaps from rollbacks must not be reused.
    """

    def test_gaps_not_reused_after_rollback_simulation(self, session):
        """
        Sequence gaps from rollbacks must not be reused.

        R9: Gap reuse would break monotonicity.
        """
        service = SequenceService(session)

        # Get some values
        v1 = service.next_value("test_r9_gaps")
        v2 = service.next_value("test_r9_gaps")
        v3 = service.next_value("test_r9_gaps")

        # Flush to "simulate" partial commit
        session.flush()

        # Continue getting values
        v4 = service.next_value("test_r9_gaps")
        v5 = service.next_value("test_r9_gaps")

        # All must be strictly increasing
        all_values = [v1, v2, v3, v4, v5]
        for i in range(1, len(all_values)):
            assert all_values[i] > all_values[i - 1], \
                f"Gap reuse detected: {all_values[i-1]} -> {all_values[i]}"

    def test_sequence_counter_persists(self, session):
        """
        Sequence counter value persists across flushes.

        R9: Counter must be durable.
        """
        service = SequenceService(session)

        # Get value and flush
        v1 = service.next_value("test_r9_persist")
        session.flush()

        # Get current value
        current = service.current_value("test_r9_persist")
        assert current == v1, f"Counter should be {v1}, got {current}"

        # Next value should be greater
        v2 = service.next_value("test_r9_persist")
        assert v2 > v1, f"Next value {v2} should be > {v1}"


class TestR9JournalEntrySequences:
    """
    Verify journal entries use safe sequence assignment.

    R9: JournalEntry.seq must be assigned via safe mechanism.
    """

    def test_journal_entries_have_unique_sequences(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Posted journal entries must have unique sequence numbers.

        R9: No duplicate sequences allowed.
        """
        entry_ids = []

        # Create multiple entries via the posting pipeline
        for i in range(10):
            result = post_via_coordinator(
                amount=Decimal(str(100 * (i + 1))),
            )
            assert result.success
            entry_ids.append(result.journal_result.entries[0].entry_id)

        # Verify all have unique sequences
        entries = [session.get(JournalEntry, eid) for eid in entry_ids]
        sequences = [e.seq for e in entries]

        assert len(set(sequences)) == 10, "All entries must have unique sequences"

    def test_journal_sequences_monotonically_increasing(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Journal entry sequences must be monotonically increasing.

        R9: Sequences must maintain ordering.
        """
        entry_ids = []

        # Create entries sequentially via the posting pipeline
        for i in range(10):
            result = post_via_coordinator(
                amount=Decimal("100.00"),
            )
            assert result.success
            entry_ids.append(result.journal_result.entries[0].entry_id)

        # Get sequences in creation order
        entries = [session.get(JournalEntry, eid) for eid in entry_ids]
        sequences = [e.seq for e in entries]

        # Must be monotonically increasing
        for i in range(1, len(sequences)):
            assert sequences[i] > sequences[i - 1], \
                f"Sequence not increasing: {sequences[i-1]} -> {sequences[i]}"


class TestR9DocumentedSequenceArchitecture:
    """
    Document the sequence architecture for R9 compliance.
    """

    def test_document_sequence_architecture(self, session):
        """
        Document the sequence architecture.

        R9: Sequence safety via locked counter row.
        """
        sequence_architecture = {
            "mechanism": "Locked counter row (SELECT ... FOR UPDATE)",
            "table": "sequence_counters",
            "columns": ["name (unique)", "current_value (bigint)"],
            "well_known_sequences": [
                "journal_entry - for JournalEntry.seq",
                "audit_event - for AuditEvent.seq",
            ],
            "properties": [
                "Monotonically increasing",
                "No duplicates",
                "Transaction-safe (uncommitted sequences not visible)",
                "Gap-tolerant (gaps from rollbacks not reused)",
            ],
            "forbidden_patterns": [
                "MAX(seq) + 1 - race condition",
                "SELECT MAX(seq) FROM table - not atomic",
                "Application-side counters - not durable",
            ],
        }

        # Verify counter table exists
        from sqlalchemy import inspect as sa_inspect
        inspector = sa_inspect(session.bind)
        assert 'sequence_counters' in inspector.get_table_names()

        # Verify SequenceService constants match documentation
        assert SequenceService.JOURNAL_ENTRY == "journal_entry"
        assert SequenceService.AUDIT_EVENT == "audit_event"
