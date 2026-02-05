#!/usr/bin/env python3
"""
Trace any posted journal entry or event — full auditor-readable decision journal.

Uses the canonical trace renderer (trace_render) so output matches the interactive
CLI (T menu) and tests. Trace is a core part of the system: one implementation.

Usage:
    python3 scripts/trace.py --event-id <uuid>
    python3 scripts/trace.py --entry-id <uuid>
    python3 scripts/trace.py --event-id <uuid> --json
    python3 scripts/trace.py --list

Examples:
    # List all traceable journal entries
    python3 scripts/trace.py --list

    # Trace by source event ID (human-readable, same format as interactive T menu)
    python3 scripts/trace.py --event-id a1b2c3d4-e5f6-7890-abcd-ef1234567890

    # Trace by journal entry ID
    python3 scripts/trace.py --entry-id b2c3d4e5-f6a7-8901-bcde-f12345678901

    # Output full JSON bundle
    python3 scripts/trace.py --event-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 --json

    # Custom database URL
    python3 scripts/trace.py --event-id <uuid> --db-url postgresql://user:pass@host/db
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_DB_URL = (
    "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"
)

W = 80


def banner(title: str) -> None:
    print()
    print("=" * W)
    print(f"  {title}")
    print("=" * W)


def section(title: str) -> None:
    print()
    print(f"--- {title} ---")
    print()


def list_entries(session) -> int:
    """List all traceable journal entries in the database."""
    from finance_kernel.models.account import Account
    from finance_kernel.models.event import Event
    from finance_kernel.models.interpretation_outcome import InterpretationOutcome
    from finance_kernel.models.journal import JournalEntry, JournalLine

    entries = (
        session.query(JournalEntry)
        .order_by(JournalEntry.seq)
        .all()
    )

    if not entries:
        print("\n  No journal entries found.\n")
        return 0

    # Build lookup maps
    event_map = {}
    for evt in session.query(Event).all():
        memo = ""
        if evt.payload and isinstance(evt.payload, dict):
            memo = evt.payload.get("memo", "")
        event_map[evt.event_id] = (evt.event_type, memo)

    acct_map = {a.id: a for a in session.query(Account).all()}

    # Check which entries have decision journals
    outcomes = (
        session.query(InterpretationOutcome)
        .filter(InterpretationOutcome.decision_log.isnot(None))
        .all()
    )
    events_with_journal = {o.source_event_id for o in outcomes}

    banner("TRACEABLE JOURNAL ENTRIES")
    print()
    print(f"  {'#':>3}  {'status':<8}  {'date':<12}  "
          f"{'has_journal':<12}  {'memo'}")
    print(f"  {'---':>3}  {'------':<8}  {'----':<12}  "
          f"{'----------':<12}  {'----'}")

    for entry in entries:
        status_val = entry.status.value if hasattr(entry.status, 'value') else str(entry.status)
        evt_info = event_map.get(entry.source_event_id, ("?", ""))
        evt_type, memo = evt_info
        has_journal = "YES" if entry.source_event_id in events_with_journal else "no"
        print(f"  {entry.seq:>3}  {status_val:<8}  {entry.effective_date!s:<12}  "
              f"{has_journal:<12}  {memo}")

    print()
    print(f"  Total: {len(entries)} journal entries")
    print()

    # Print IDs grouped by entry
    section("ENTRY DETAILS (for trace commands)")
    for entry in entries:
        evt_info = event_map.get(entry.source_event_id, ("?", ""))
        _, memo = evt_info
        has_journal = entry.source_event_id in events_with_journal

        lines = (
            session.query(JournalLine)
            .filter(JournalLine.journal_entry_id == entry.id)
            .order_by(JournalLine.line_seq)
            .all()
        )

        print(f"  Entry #{entry.seq}: {memo}")
        print(f"    entry-id:  {entry.id}")
        print(f"    event-id:  {entry.source_event_id}")
        print(f"    journal:   {'DECISION LOG AVAILABLE' if has_journal else 'no decision log (pre-feature)'}")

        for line in lines:
            acct = acct_map.get(line.account_id)
            acct_label = f"{acct.code} {acct.name}" if acct else "?"
            side_val = line.side.value if hasattr(line.side, 'value') else str(line.side)
            print(f"    {side_val:>7}  {line.amount:>12}  {line.currency}  {acct_label}")

        print(f"    trace:     python3 scripts/trace.py --event-id {entry.source_event_id}")
        print()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trace a posted journal entry or event (canonical trace = trace_render).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python3 scripts/trace.py --list\n"
            "  python3 scripts/trace.py --event-id a1b2c3d4-...\n"
            "  python3 scripts/trace.py --entry-id b2c3d4e5-...\n"
            "  python3 scripts/trace.py --event-id a1b2c3d4-... --json\n"
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--event-id", type=str,
        help="Source event UUID to trace",
    )
    group.add_argument(
        "--entry-id", type=str,
        help="Journal entry UUID to trace",
    )
    group.add_argument(
        "--list", action="store_true",
        help="List all traceable journal entries",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output full JSON bundle instead of formatted text",
    )
    parser.add_argument(
        "--db-url", type=str, default=DEFAULT_DB_URL,
        help=f"Database URL (default: {DEFAULT_DB_URL})",
    )

    args = parser.parse_args()

    # Suppress library logging
    logging.disable(logging.CRITICAL)

    from finance_kernel.db.engine import get_session, init_engine_from_url

    # Connect
    try:
        init_engine_from_url(args.db_url, echo=False)
    except Exception as exc:
        print(f"  ERROR: Cannot connect to database: {exc}", file=sys.stderr)
        return 1

    session = get_session()

    try:
        # List mode
        if args.list:
            return list_entries(session)

        # Parse UUID
        try:
            if args.event_id:
                target_id = UUID(args.event_id)
            else:
                target_id = UUID(args.entry_id)
        except ValueError as exc:
            print(f"  ERROR: Invalid UUID: {exc}", file=sys.stderr)
            return 1

        from finance_kernel.selectors.trace_selector import TraceSelector

        from scripts.trace_render import render_bundle, render_trace

        selector = TraceSelector(session)

        if args.event_id:
            if args.json:
                bundle = selector.trace_by_event_id(target_id)
                bundle_dict = asdict(bundle)
                print(json.dumps(bundle_dict, indent=2, default=str))
                return 0
            render_trace(session, target_id)
            return 0

        # --entry-id
        bundle = selector.trace_by_journal_entry_id(target_id)
        if args.json:
            bundle_dict = asdict(bundle)
            print(json.dumps(bundle_dict, indent=2, default=str))
            return 0
        render_bundle(session, bundle)
        return 0

    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 1

    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
