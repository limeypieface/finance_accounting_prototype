"""CLI menu: print main menu."""

from scripts.cli.data import (
    ALL_PIPELINE_SCENARIOS,
    ENGINE_SCENARIOS,
    NON_ENGINE_SCENARIOS,
    SIMPLE_EVENTS,
    SUBLEDGER_SCENARIOS,
)
from scripts.cli.util import fmt_amount


def print_menu():
    """Print the main interactive menu."""
    W = 80
    print()
    print("=" * W)
    print("  INTERACTIVE ACCOUNTING CLI".center(W))
    print("=" * W)
    print()
    print("  SIMPLE BOOKKEEPING:")
    for i, (desc, dr, cr, amt, dr_lbl, cr_lbl) in enumerate(SIMPLE_EVENTS, 1):
        print(f"   {i:>2}.  {desc:<34} {fmt_amount(amt):>10}    Dr {dr_lbl} / Cr {cr_lbl}")
    print()
    print("  PIPELINE B — ENGINE SCENARIOS:")
    offset = len(SIMPLE_EVENTS)
    for i, s in enumerate(ENGINE_SCENARIOS):
        n = offset + i + 1
        eng = f"({s['engine']})" if s.get("engine") else ""
        print(f"   {n:>2}.  {s['label']:<40} {fmt_amount(s['amount']):>10}  {eng}")
    print()
    print("  PIPELINE B — MODULE SCENARIOS:")
    offset2 = offset + len(ENGINE_SCENARIOS)
    for i, s in enumerate(NON_ENGINE_SCENARIOS):
        n = offset2 + i + 1
        print(f"   {n:>2}.  {s['label']:<40} {fmt_amount(s['amount']):>10}")
    print()
    print("  SUBLEDGER SCENARIOS:")
    offset3 = offset2 + len(NON_ENGINE_SCENARIOS)
    for i, s in enumerate(SUBLEDGER_SCENARIOS):
        n = offset3 + i + 1
        print(f"   {n:>2}.  {s['label']:<40} {fmt_amount(s['amount']):>10}  [{s['sl_type']}]")
    print()
    print("  View:")
    print("    R   View all reports")
    print("    J   View journal entries")
    print("    S   Subledger reports (entity balances, open items)")
    print("    T   Trace a journal entry (full auditor decision trail)")
    print("    F   Trace a failed/rejected/blocked event")
    print("    L   List chart of accounts (imported or config-based)")
    print("    P   List vendors and customers (imported or created)")
    print("    I   Import & Staging (review staged data, fix issues, promote)")
    print("    M   Define import mapping (upload CSV, map columns, save for future imports)")
    print()
    print("  Close:")
    print("    H   Pre-close health check (read-only diagnostic)")
    print("    C   Close a period (guided workflow)")
    print()
    print("  Other:")
    print("    A   Post ALL scenarios at once")
    print("    X   Reset database (drop all data, start fresh)")
    print("    Q   Quit")
    print()
