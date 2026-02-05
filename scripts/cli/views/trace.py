"""CLI views: trace journal entry, trace failed/rejected events."""

from scripts.trace_render import render_trace


def show_trace(session, config=None):
    """Let the user pick a journal entry by sequence number and trace it."""
    from finance_kernel.models.account import Account
    from finance_kernel.models.event import Event
    from finance_kernel.models.interpretation_outcome import InterpretationOutcome
    from finance_kernel.models.journal import JournalEntry

    entries = session.query(JournalEntry).order_by(JournalEntry.seq).all()
    if not entries:
        print("\n  No journal entries to trace.\n")
        return
    event_map = {}
    event_payload_map = {}
    for evt in session.query(Event).all():
        memo = evt.payload.get("memo", "") if evt.payload and isinstance(evt.payload, dict) else ""
        if not memo:
            memo = evt.event_type or ""
        event_payload_map[evt.event_id] = evt.payload
        event_map[evt.event_id] = memo
    acct_map = {a.id: a for a in session.query(Account).all()}
    outcomes = session.query(InterpretationOutcome).filter(InterpretationOutcome.decision_log.isnot(None)).all()
    events_with_journal = {o.source_event_id for o in outcomes}
    print()
    print("=" * 72)
    print("  TRACE A JOURNAL ENTRY".center(72))
    print("=" * 72)
    print()
    print(f"  {'#':>3}  {'status':<8}  {'journal':<8}  {'memo'}")
    print(f"  {'---':>3}  {'------':<8}  {'-------':<8}  {'----'}")
    for entry in entries:
        status_val = entry.status.value if hasattr(entry.status, "value") else str(entry.status)
        memo = event_map.get(entry.source_event_id, "")
        has_log = "YES" if entry.source_event_id in events_with_journal else "no"
        seq_str = f"{entry.seq:>3}" if entry.seq is not None else "  -"
        print(f"  {seq_str}  {status_val:<8}  {has_log:<8}  {memo}")
    print()
    try:
        pick = input("  Enter entry # to trace (or blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not pick:
        return
    try:
        seq_num = int(pick)
    except ValueError:
        print(f"\n  Invalid number '{pick}'.\n")
        return
    target = None
    for entry in entries:
        if entry.seq == seq_num:
            target = entry
            break
    if target is None:
        print(f"\n  No entry with seq #{seq_num}.\n")
        return
    memo = event_map.get(target.source_event_id, f"entry #{target.seq}")
    print()
    W = 72
    print("=" * W)
    print(f"  AUDIT TRACE: Entry #{target.seq} — {memo}")
    print("=" * W)
    render_trace(session, target.source_event_id, event_payload_map=event_payload_map, acct_map=acct_map, config=config)


def show_failed_traces(session, config=None):
    """List rejected/blocked/failed events and let the user trace one."""
    from finance_kernel.models.account import Account
    from finance_kernel.models.event import Event
    from finance_kernel.models.interpretation_outcome import InterpretationOutcome, OutcomeStatus

    non_posted = (
        session.query(InterpretationOutcome)
        .filter(InterpretationOutcome.status.notin_([OutcomeStatus.POSTED.value, OutcomeStatus.POSTED]))
        .order_by(InterpretationOutcome.created_at.desc())
        .all()
    )
    if not non_posted:
        print("\n  No failed/rejected/blocked events found.\n")
        return
    event_payload_map = {}
    event_type_map = {}
    for evt in session.query(Event).all():
        if evt.payload and isinstance(evt.payload, dict):
            event_payload_map[evt.event_id] = evt.payload
        event_type_map[evt.event_id] = evt.event_type or ""
    acct_map = {a.id: a for a in session.query(Account).all()}
    W = 72
    print()
    print("=" * W)
    print("  FAILED / REJECTED / BLOCKED EVENTS".center(W))
    print("=" * W)
    print()
    print(f"  {'#':>3}  {'status':<12}  {'reason':<20}  {'event_type'}")
    print(f"  {'---':>3}  {'------':<12}  {'------':<20}  {'----------'}")
    for i, outcome in enumerate(non_posted):
        status_val = outcome.status_str
        reason = (outcome.reason_code or "")[:18]
        if len(outcome.reason_code or "") > 18:
            reason = (outcome.reason_code or "")[:15] + "..."
        evt_type = event_type_map.get(outcome.source_event_id, "")
        print(f"  {i + 1:>3}  {status_val:<12}  {reason:<20}  {evt_type}")
    print()
    try:
        pick = input("  Enter # to trace (or blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not pick:
        return
    try:
        idx = int(pick) - 1
    except ValueError:
        print(f"\n  Invalid number '{pick}'.\n")
        return
    if idx < 0 or idx >= len(non_posted):
        print("\n  Number out of range.\n")
        return
    outcome = non_posted[idx]
    evt_type = event_type_map.get(outcome.source_event_id, "unknown")
    print()
    print("=" * W)
    print(f"  AUDIT TRACE: {outcome.status_str.upper()} — {evt_type}")
    print("=" * W)
    render_trace(session, outcome.source_event_id, event_payload_map=event_payload_map, acct_map=acct_map, config=config)
