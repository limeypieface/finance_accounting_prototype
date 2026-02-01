#!/usr/bin/env python3
"""
View journal entries and their lines from the database.

Usage:
    python3 scripts/view_journal.py
"""

import logging
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"

W = 80


def _fmt(v) -> str:
    d = Decimal(str(v))
    return f"${d:,.2f}"


def main() -> int:
    logging.disable(logging.CRITICAL)

    from finance_kernel.db.engine import get_session, init_engine_from_url
    from finance_kernel.models.account import Account
    from finance_kernel.models.event import Event
    from finance_kernel.models.journal import JournalEntry, JournalLine

    try:
        init_engine_from_url(DB_URL, echo=False)
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 1

    session = get_session()

    try:
        # Load account lookup
        acct_map = {a.id: a for a in session.query(Account).all()}

        # Load event lookup (for memo)
        event_map = {}
        for evt in session.query(Event).all():
            memo = ""
            if evt.payload and isinstance(evt.payload, dict):
                memo = evt.payload.get("memo", "")
            event_map[evt.event_id] = memo

        # Query all entries ordered by sequence
        entries = (
            session.query(JournalEntry)
            .order_by(JournalEntry.seq)
            .all()
        )

        if not entries:
            print("  No journal entries found. Run seed_data.py first.")
            return 1

        print()
        print("=" * W)
        print("JOURNAL ENTRIES".center(W))
        print("=" * W)
        print()

        for entry in entries:
            memo = event_map.get(entry.source_event_id, "")
            status_str = entry.status.value if hasattr(entry.status, 'value') else entry.status
            print(f"  Entry #{entry.seq}  |  {status_str.upper()}  |  {entry.effective_date}")
            if memo:
                print(f"  Memo: {memo}")
            print(f"  {'Account':<30} {'Debit':>14} {'Credit':>14}")
            print(f"  {'-'*30} {'-'*14} {'-'*14}")

            lines = (
                session.query(JournalLine)
                .filter(JournalLine.journal_entry_id == entry.id)
                .order_by(JournalLine.line_seq)
                .all()
            )

            for line in lines:
                acct = acct_map.get(line.account_id)
                name = f"{acct.code}  {acct.name}" if acct else str(line.account_id)

                side_val = line.side.value if hasattr(line.side, 'value') else line.side
                if side_val == "debit":
                    print(f"  {name:<30} {_fmt(line.amount):>14} {'':>14}")
                else:
                    print(f"  {name:<30} {'':>14} {_fmt(line.amount):>14}")

            print()

        print(f"  Total: {len(entries)} journal entries")
        print()
        return 0

    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
