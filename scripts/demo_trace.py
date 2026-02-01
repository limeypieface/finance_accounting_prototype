#!/usr/bin/env python3
"""
Full auditor demo: seed real business transactions and trace every posted entry.

Seeds 8 business transactions through the InterpretationCoordinator pipeline
(which auto-captures decision journals), then runs a full trace on each entry
showing the complete runtime decision trail.

Usage:
    python3 scripts/demo_trace.py              # Seed fresh + trace all
    python3 scripts/demo_trace.py --trace-only # Trace existing entries (skip seed)
    python3 scripts/demo_trace.py --json       # Output each trace as JSON
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"
FY_START = date(2025, 1, 1)
FY_END = date(2025, 12, 31)

W = 80


# =============================================================================
# Formatting
# =============================================================================

def hline(char: str = "=") -> str:
    return char * W


def banner(title: str) -> None:
    print()
    print(hline())
    print(f"  {title}")
    print(hline())


def section(title: str) -> None:
    print()
    print(f"--- {title} ---")
    print()


def field(name: str, value, indent: int = 4) -> None:
    print(f"{' ' * indent}{name}: {value}")


def short_id(uid) -> str:
    return str(uid)[:8] + "..."


# =============================================================================
# DB setup
# =============================================================================

def kill_orphaned():
    from sqlalchemy import create_engine, text
    admin_url = DB_URL.rsplit("/", 1)[0] + "/postgres"
    try:
        eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with eng.connect() as conn:
            conn.execute(text("""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = 'finance_kernel_test'
                  AND pid <> pg_backend_pid()
            """))
        eng.dispose()
    except Exception:
        pass


def seed_database():
    """Seed the database with 8 real business transactions. Returns list of (event_id, entry_ids, memo)."""
    from finance_kernel.db.engine import (
        drop_tables,
        get_session,
        init_engine_from_url,
    )
    from finance_kernel.db.immutability import register_immutability_listeners
    from finance_kernel.domain.accounting_intent import (
        AccountingIntent,
        AccountingIntentSnapshot,
        IntentLine,
        LedgerIntent,
    )
    from finance_kernel.domain.clock import DeterministicClock
    from finance_kernel.domain.meaning_builder import (
        EconomicEventData,
        MeaningBuilderResult,
    )
    from finance_kernel.models.account import Account, AccountType, NormalBalance
    from finance_kernel.models.event import Event
    from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
    from finance_kernel.services.auditor_service import AuditorService
    from finance_kernel.services.interpretation_coordinator import (
        InterpretationCoordinator,
    )
    from finance_kernel.services.journal_writer import JournalWriter, RoleResolver
    from finance_kernel.services.outcome_recorder import OutcomeRecorder
    from finance_kernel.utils.hashing import hash_payload
    from finance_modules._orm_registry import create_all_tables

    # Connect + reset
    print("  [1/6] Connecting to PostgreSQL...")
    init_engine_from_url(DB_URL, echo=False)

    print("  [2/6] Dropping old tables and recreating schema...")
    kill_orphaned()
    time.sleep(0.5)
    try:
        drop_tables()
    except Exception:
        kill_orphaned()
        time.sleep(1.0)
        try:
            drop_tables()
        except Exception:
            pass
    create_all_tables(install_triggers=True)
    register_immutability_listeners()

    session = get_session()
    clock = DeterministicClock(datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC))
    actor_id = uuid4()

    # Chart of accounts
    print("  [3/6] Creating chart of accounts (12 accounts)...")
    specs = [
        ("1000", "Cash",                 AccountType.ASSET,     NormalBalance.DEBIT,  None,             "CASH"),
        ("1100", "Accounts Receivable",  AccountType.ASSET,     NormalBalance.DEBIT,  None,             "AR"),
        ("1200", "Inventory",            AccountType.ASSET,     NormalBalance.DEBIT,  None,             "INVENTORY"),
        ("1500", "Equipment",            AccountType.ASSET,     NormalBalance.DEBIT,  None,             "EQUIPMENT"),
        ("2000", "Accounts Payable",     AccountType.LIABILITY, NormalBalance.CREDIT, None,             "AP"),
        ("3200", "Retained Earnings",    AccountType.EQUITY,    NormalBalance.CREDIT, None,             "RETAINED_EARNINGS"),
        ("4000", "Sales Revenue",        AccountType.REVENUE,   NormalBalance.CREDIT, None,             "REVENUE"),
        ("5000", "Cost of Goods Sold",   AccountType.EXPENSE,   NormalBalance.DEBIT,  None,             "COGS"),
        ("5100", "Operating Expense",    AccountType.EXPENSE,   NormalBalance.DEBIT,  None,             "EXPENSE"),
        ("5600", "Depreciation Expense", AccountType.EXPENSE,   NormalBalance.DEBIT,  ["depreciation"], "DEPRECIATION"),
        ("5800", "Salary Expense",       AccountType.EXPENSE,   NormalBalance.DEBIT,  None,             "SALARY"),
        ("9999", "Rounding",             AccountType.EXPENSE,   NormalBalance.DEBIT,  ["rounding"],     "ROUNDING"),
    ]

    accounts = {}
    for code, name, atype, nbal, tags, role_key in specs:
        acct = Account(
            code=code, name=name, account_type=atype, normal_balance=nbal,
            is_active=True, tags=tags, created_by_id=actor_id,
        )
        session.add(acct)
        session.flush()
        accounts[role_key] = acct

    # Fiscal period
    print("  [4/6] Creating fiscal period FY2025...")
    period = FiscalPeriod(
        period_code="FY2025", name="Fiscal Year 2025",
        start_date=FY_START, end_date=FY_END,
        status=PeriodStatus.OPEN, created_by_id=uuid4(),
    )
    session.add(period)
    session.flush()

    # Pipeline
    print("  [5/6] Posting 8 business transactions through InterpretationCoordinator...")
    auditor = AuditorService(session, clock)
    resolver = RoleResolver()
    for role_key, acct in accounts.items():
        resolver.register_binding(role_key, acct.id, acct.code)

    writer = JournalWriter(session, resolver, clock, auditor)
    recorder = OutcomeRecorder(session, clock)
    coordinator = InterpretationCoordinator(session, writer, recorder, clock)

    def post(debit_role, credit_role, amount, memo):
        source_event_id = uuid4()
        effective = clock.now().date()
        payload = {"memo": memo, "amount": str(amount)}
        evt = Event(
            event_id=source_event_id, event_type="demo.trace",
            occurred_at=clock.now(), effective_date=effective,
            actor_id=actor_id, producer="demo_trace",
            payload=payload, payload_hash=hash_payload(payload),
            schema_version=1, ingested_at=clock.now(),
        )
        session.add(evt)
        session.flush()

        econ_data = EconomicEventData(
            source_event_id=source_event_id, economic_type="demo.trace",
            effective_date=effective, profile_id="DemoTraceProfile",
            profile_version=1, profile_hash=None, quantity=amount,
        )
        intent = AccountingIntent(
            econ_event_id=uuid4(), source_event_id=source_event_id,
            profile_id="DemoTraceProfile", profile_version=1,
            effective_date=effective,
            ledger_intents=(
                LedgerIntent(ledger_id="GL", lines=(
                    IntentLine.debit(debit_role, amount, "USD"),
                    IntentLine.credit(credit_role, amount, "USD"),
                )),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )
        result = coordinator.interpret_and_post(
            meaning_result=MeaningBuilderResult.ok(econ_data),
            accounting_intent=intent,
            actor_id=actor_id,
        )
        session.flush()
        return result, source_event_id

    txns = [
        ("CASH",      "RETAINED_EARNINGS", Decimal("500000.00"), "Owner investment — initial capital contribution"),
        ("INVENTORY", "AP",                Decimal("100000.00"), "Inventory purchased on account from supplier"),
        ("CASH",      "REVENUE",           Decimal("150000.00"), "Cash sales — Q1 through Q4 aggregate"),
        ("AR",        "REVENUE",           Decimal("75000.00"),  "Credit sales on account — net 30 terms"),
        ("COGS",      "INVENTORY",         Decimal("60000.00"),  "Cost of goods sold — Q1-Q4 shipped orders"),
        ("SALARY",    "CASH",              Decimal("45000.00"),  "Monthly salaries paid — June 2025"),
        ("CASH",      "AR",                Decimal("25000.00"),  "AR collection — partial payment received"),
        ("EQUIPMENT", "CASH",              Decimal("80000.00"),  "Equipment purchase — manufacturing press"),
    ]

    posted = []  # (event_id, entry_ids, memo)
    for i, (dr, cr, amt, memo) in enumerate(txns, 1):
        result, event_id = post(dr, cr, amt, memo)
        status = "OK" if result.success else "FAIL"
        if result.success:
            entry_ids = list(result.journal_result.entry_ids) if result.journal_result else []
            posted.append((event_id, entry_ids, memo))
        print(f"         {i}. [{status}] {memo}")

    # Commit
    print(f"  [6/6] Committing ({len(posted)}/{len(txns)} transactions)...")
    session.commit()
    session.close()

    return posted


def load_existing_entries():
    """Load all journal entries from the database for tracing."""
    from finance_kernel.db.engine import get_session
    from finance_kernel.models.event import Event
    from finance_kernel.models.interpretation_outcome import InterpretationOutcome
    from finance_kernel.models.journal import JournalEntry

    session = get_session()

    entries = (
        session.query(JournalEntry)
        .order_by(JournalEntry.seq)
        .all()
    )

    if not entries:
        print("\n  No journal entries found in database.")
        print("  Run without --trace-only to seed data first.\n")
        session.close()
        return []

    # Build event memo lookup
    event_map = {}
    for evt in session.query(Event).all():
        memo = ""
        if evt.payload and isinstance(evt.payload, dict):
            memo = evt.payload.get("memo", "")
        event_map[evt.event_id] = memo

    result = []
    for entry in entries:
        memo = event_map.get(entry.source_event_id, f"entry #{entry.seq}")
        result.append((entry.source_event_id, [entry.id], memo))

    session.close()
    return result


# =============================================================================
# Trace display (reuses trace.py formatting)
# =============================================================================

def print_origin(origin):
    section("ORIGIN EVENT")
    if origin is None:
        print("    (not found)")
        return
    field("event_id", origin.event_id)
    field("event_type", origin.event_type)
    field("occurred_at", origin.occurred_at)
    field("effective_date", origin.effective_date)
    field("actor_id", origin.actor_id)
    field("producer", origin.producer)
    field("payload_hash", origin.payload_hash)
    field("schema_version", origin.schema_version)
    field("ingested_at", origin.ingested_at)


def print_journal_entries(entries):
    section(f"JOURNAL ENTRIES ({len(entries)})")
    if not entries:
        print("    (none)")
        return
    for i, je in enumerate(entries):
        print(f"  [{i}] entry_id: {je.entry_id}")
        field("source_event_type", je.source_event_type, indent=6)
        field("effective_date", je.effective_date, indent=6)
        field("posted_at", je.posted_at, indent=6)
        field("status", je.status, indent=6)
        field("seq", je.seq, indent=6)
        field("idempotency_key", je.idempotency_key, indent=6)
        if je.reversal_of_id:
            field("reversal_of_id", je.reversal_of_id, indent=6)
        if je.description:
            field("description", je.description, indent=6)
        print()
        print(f"      {'seq':>4}  {'side':<7} {'amount':>12}  {'curr':<4}  "
              f"{'account_code':<12}  {'rounding'}")
        print(f"      {'---':>4}  {'----':<7} {'------':>12}  {'----':<4}  "
              f"{'------------':<12}  {'--------'}")
        for line in je.lines:
            print(f"      {line.line_seq:>4}  {line.side:<7} "
                  f"{line.amount:>12}  {line.currency:<4}  "
                  f"{line.account_code:<12}  {line.is_rounding}")
        if je.coa_version is not None:
            print()
            print("      R21 snapshot:")
            field("coa_version", je.coa_version, indent=8)
            field("dimension_schema_version", je.dimension_schema_version, indent=8)
            field("rounding_policy_version", je.rounding_policy_version, indent=8)
            field("currency_registry_version", je.currency_registry_version, indent=8)
            field("posting_rule_version", je.posting_rule_version, indent=8)
        print()


def print_interpretation(interp):
    section("INTERPRETATION OUTCOME")
    if interp is None:
        print("    (none — no outcome recorded)")
        return
    field("status", interp.status)
    field("profile_id", interp.profile_id)
    field("profile_version", interp.profile_version)
    if interp.profile_hash:
        field("profile_hash", interp.profile_hash)
    if interp.econ_event_id:
        field("econ_event_id", interp.econ_event_id)
    if interp.reason_code:
        field("reason_code", interp.reason_code)
    if interp.reason_detail:
        field("reason_detail", interp.reason_detail)
    if interp.trace_id:
        field("trace_id", interp.trace_id)
    if interp.decision_log:
        field("decision_log_records", len(interp.decision_log))
        print()
        print("    Decision journal persisted: YES")
        print("    (Full timeline shown below)")


def print_reproducibility(repro):
    section("R21 REPRODUCIBILITY")
    if repro is None:
        print("    (none)")
        return
    field("coa_version", repro.coa_version)
    field("dimension_schema_version", repro.dimension_schema_version)
    field("rounding_policy_version", repro.rounding_policy_version)
    field("currency_registry_version", repro.currency_registry_version)
    field("fx_policy_version", repro.fx_policy_version)
    field("posting_rule_version", repro.posting_rule_version)


def print_decision_journal(timeline):
    """Print the decision journal — the heart of the auditor trace."""
    log_entries = [t for t in timeline if t.source == "structured_log"]
    audit_entries = [t for t in timeline if t.source == "audit_event"]

    section(f"DECISION JOURNAL ({len(timeline)} entries)")

    if not log_entries and not audit_entries:
        print("    (empty — no structured logs or audit events found)")
        return

    if log_entries:
        print(f"  Structured log decisions ({len(log_entries)}):")
        print("  Each entry shows a decision made during posting runtime.")
        print()

        for i, te in enumerate(log_entries):
            action = te.action
            d = te.detail or {}

            if action == "interpretation_started":
                print(f"  [{i:>2}] INTERPRETATION STARTED")
                print("       The pipeline began interpreting an economic event.")
                print(f"       Profile: {d.get('profile_id')} v{d.get('profile_version')}")
                print(f"       Event: {str(d.get('source_event_id', ''))[:8]}...")
                print(f"       Effective date: {d.get('effective_date')}")

            elif action == "config_in_force":
                print(f"  [{i:>2}] CONFIGURATION SNAPSHOT (R21)")
                print("       The system recorded which config versions were active at posting time.")
                print(f"       COA version: {d.get('coa_version')}")
                print(f"       Dimension schema: {d.get('dimension_schema_version')}")
                print(f"       Rounding policy: {d.get('rounding_policy_version')}")
                print(f"       Currency registry: {d.get('currency_registry_version')}")

            elif action == "journal_write_started":
                print(f"  [{i:>2}] JOURNAL WRITE STARTED")
                print(f"       JournalWriter began writing to {d.get('ledger_count')} ledger(s).")

            elif action == "balance_validated":
                print(f"  [{i:>2}] BALANCE VALIDATED")
                print("       Double-entry balance check per currency per ledger.")
                print(f"       Ledger: {d.get('ledger_id')}  Currency: {d.get('currency')}")
                print(f"       Sum debits:  {d.get('sum_debit')}")
                print(f"       Sum credits: {d.get('sum_credit')}")
                balanced = d.get('balanced')
                print(f"       Balanced: {balanced}  {'(R4 PASS)' if balanced else '(R4 FAIL)'}")

            elif action == "role_resolved":
                print(f"  [{i:>2}] ROLE RESOLVED")
                print("       Semantic account role mapped to COA code at posting time (L1).")
                acct_id = str(d.get('account_id', ''))[:8]
                print(f"       Role: {d.get('role')} -> Account {d.get('account_code')} ({acct_id}...)")
                print(f"       Side: {d.get('side')}  Amount: {d.get('amount')} {d.get('currency')}")

            elif action == "line_written":
                print(f"  [{i:>2}] LINE WRITTEN")
                print("       A journal line was persisted to the database.")
                print(f"       Seq {d.get('line_seq')}: {d.get('role')} -> {d.get('account_code')}")
                print(f"       Side: {d.get('side')}  Amount: {d.get('amount')} {d.get('currency')}")
                rounding = d.get('is_rounding', False)
                if rounding:
                    print("       ** ROUNDING LINE (R5/R22) **")

            elif action == "invariant_checked":
                print(f"  [{i:>2}] INVARIANT CHECKED")
                inv = d.get('invariant', '?')
                passed = d.get('passed', False)
                print(f"       {inv}: {'PASS' if passed else 'FAIL'}")
                if inv == "R21_REFERENCE_SNAPSHOT":
                    print("       All version numbers recorded on journal entry for replay.")

            elif action == "journal_entry_created":
                print(f"  [{i:>2}] JOURNAL ENTRY CREATED")
                print("       A new journal entry was committed to the ledger.")
                print(f"       Entry: {str(d.get('entry_id', ''))[:8]}...")
                print(f"       Status: {d.get('status')}  Seq: {d.get('seq')}")
                print(f"       Idempotency key: {d.get('idempotency_key')}")
                print(f"       Profile: {d.get('profile_id')}")

            elif action == "journal_write_completed":
                print(f"  [{i:>2}] JOURNAL WRITE COMPLETED")
                print("       All ledger writes finished successfully.")
                print(f"       Entries created: {d.get('entry_count')}")
                print(f"       Duration: {d.get('duration_ms')}ms")

            elif action == "outcome_recorded":
                print(f"  [{i:>2}] OUTCOME RECORDED")
                print("       InterpretationOutcome written to DB (P15: one per event).")
                print(f"       Status: {d.get('status')}")
                ids = d.get('journal_entry_ids')
                if ids:
                    print(f"       Journal entries linked: {len(ids)}")

            elif action == "interpretation_posted":
                print(f"  [{i:>2}] INTERPRETATION POSTED")
                print("       The coordinator confirmed the posting is complete.")
                print(f"       Total entries: {d.get('entry_count')}")

            elif action == "reproducibility_proof":
                print(f"  [{i:>2}] REPRODUCIBILITY PROOF")
                print("       Canonical hashes for deterministic replay verification.")
                print(f"       Input hash:  {str(d.get('input_hash', ''))[:16]}...")
                print(f"       Output hash: {str(d.get('output_hash', ''))[:16]}...")

            elif action == "FINANCE_KERNEL_TRACE":
                print(f"  [{i:>2}] FINANCE_KERNEL_TRACE")
                print("       Top-level trace emitted by the coordinator.")
                print(f"       Policy: {d.get('policy_name')} v{d.get('policy_version')}")
                print(f"       Outcome: {d.get('outcome_status')}")
                ih = str(d.get('input_hash', ''))
                if ih:
                    print(f"       Input hash:  {ih[:16]}...")
                    print(f"       Output hash: {str(d.get('output_hash', ''))[:16]}...")

            elif action == "interpretation_completed":
                print(f"  [{i:>2}] INTERPRETATION COMPLETED")
                print("       Pipeline execution finished.")
                print(f"       Success: {d.get('success')}")
                print(f"       Duration: {d.get('duration_ms')}ms")

            else:
                print(f"  [{i:>2}] {action}")
                for k, v in d.items():
                    if k not in ("ts", "timestamp", "level", "logger"):
                        print(f"       {k}: {v}")

            print()

    if audit_entries:
        print(f"  Audit trail ({len(audit_entries)}):")
        print("  Each audit event is part of the immutable hash chain (R11).")
        print()
        print(f"  {'#':>3}  {'action':<30} {'entity_type':<15} {'seq':>5}")
        print(f"  {'---':>3}  {'------':<30} {'-----------':<15} {'---':>5}")
        for i, te in enumerate(audit_entries):
            print(f"  {i:>3}  {te.action:<30} "
                  f"{te.entity_type or '':<15} {te.seq or '':>5}")
        print()


def print_lifecycle_links(links):
    section(f"LIFECYCLE LINKS ({len(links)})")
    if not links:
        print("    (none)")
        return
    for ll in links:
        print(f"  {ll.parent_artifact_type}({short_id(ll.parent_artifact_id)}) "
              f"--[{ll.link_type}]--> "
              f"{ll.child_artifact_type}({short_id(ll.child_artifact_id)})")
        if ll.link_metadata:
            field("metadata", ll.link_metadata, indent=6)


def print_integrity(integrity):
    section("INTEGRITY VERIFICATION")
    field("bundle_hash", integrity.bundle_hash)
    field("payload_hash_verified", integrity.payload_hash_verified)
    field("balance_verified", integrity.balance_verified)
    field("audit_chain_valid", integrity.audit_chain_segment_valid)

    all_ok = (
        integrity.payload_hash_verified
        and integrity.balance_verified
        and integrity.audit_chain_segment_valid
    )
    print()
    if all_ok:
        print("    INTEGRITY: ALL CHECKS PASSED")
    else:
        print("    INTEGRITY: ISSUES DETECTED")
        if not integrity.payload_hash_verified:
            print("      - Payload hash mismatch")
        if not integrity.balance_verified:
            print("      - Balance verification failed")
        if not integrity.audit_chain_segment_valid:
            print("      - Audit chain segment invalid")


def print_missing_facts(facts):
    if not facts:
        section("COMPLETENESS")
        print("    All facts resolved. Trace is complete.")
        return
    section(f"MISSING FACTS ({len(facts)})")
    print("    The following facts could not be resolved:")
    print()
    for mf in facts:
        print(f"    [{mf.fact}]")
        field("expected_source", mf.expected_source, indent=6)
        if mf.correlation_key:
            field("correlation_key", mf.correlation_key, indent=6)
        if mf.detail:
            field("detail", mf.detail, indent=6)


# =============================================================================
# Trace execution
# =============================================================================

def trace_entry(session, event_id: UUID, memo: str, entry_num: int, total: int,
                output_json: bool = False):
    """Trace a single entry and print the full auditor report."""
    from finance_kernel.selectors.trace_selector import TraceSelector

    banner(f"AUDIT TRACE {entry_num}/{total}: {memo}")

    selector = TraceSelector(session)
    bundle = selector.trace_by_event_id(event_id)

    if output_json:
        bundle_dict = asdict(bundle)
        print(json.dumps(bundle_dict, indent=2, default=str))
        return bundle

    field("trace_id", bundle.trace_id)
    field("generated_at", bundle.generated_at)
    field("artifact", f"{bundle.artifact.artifact_type} {bundle.artifact.artifact_id}")

    print_origin(bundle.origin)
    print_journal_entries(bundle.journal_entries)
    print_interpretation(bundle.interpretation)
    print_reproducibility(bundle.reproducibility)
    print_decision_journal(bundle.timeline)
    print_lifecycle_links(bundle.lifecycle_links)
    print_integrity(bundle.integrity)
    print_missing_facts(bundle.missing_facts)

    return bundle


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Full auditor demo: seed data and trace every posted entry.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--trace-only", action="store_true",
        help="Skip seeding; trace entries already in the database",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output each trace as JSON",
    )
    parser.add_argument(
        "--db-url", type=str, default=DB_URL,
        help=f"Database URL (default: {DB_URL})",
    )
    args = parser.parse_args()

    from finance_kernel.db.engine import get_session, init_engine_from_url

    # Connect
    try:
        # Suppress noisy library logging during DB init, but keep a handle
        logging.disable(logging.CRITICAL)
        init_engine_from_url(args.db_url, echo=False)
    except Exception as exc:
        print(f"  ERROR: Cannot connect to database: {exc}", file=sys.stderr)
        return 1

    # Seed or load
    if args.trace_only:
        banner("AUDITOR TRACE — EXISTING ENTRIES")
        print()
        print("  Mode: --trace-only (using existing database entries)")
        posted = load_existing_entries()
    else:
        banner("AUDITOR TRACE — FULL DEMO")
        print()
        print("  Seeding fresh data through InterpretationCoordinator...")
        print("  Each transaction captures a full decision journal automatically.")
        print()

        # RE-ENABLE logging during seeding so LogCapture (inside
        # InterpretationCoordinator) can capture structured log records
        # for the decision journal.  logging.disable(CRITICAL) prevents
        # records from reaching ANY handler, including LogCapture.
        logging.disable(logging.NOTSET)

        # Mute the finance_kernel StreamHandler so JSON logs don't
        # clutter the console.  LogCapture (installed by the coordinator)
        # still receives records because it operates at INFO level.
        fk_logger = logging.getLogger("finance_kernel")
        _muted_handlers = []
        for h in fk_logger.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                _muted_handlers.append((h, h.level))
                h.setLevel(logging.CRITICAL + 1)  # effectively mute

        posted = seed_database()

        # Restore handler levels and re-suppress logging
        for h, orig_level in _muted_handlers:
            h.setLevel(orig_level)
        logging.disable(logging.CRITICAL)

    if not posted:
        print("\n  No entries to trace.\n")
        return 1

    # Trace each entry
    session = get_session()
    total = len(posted)

    print()
    print(hline("-"))
    print(f"  Tracing {total} posted entries...")
    print("  Each trace shows the complete runtime decision trail:")
    print("    - Origin event (what happened)")
    print("    - Journal entry (what was recorded)")
    print("    - Interpretation outcome (how it was classified)")
    print("    - Decision journal (every step the system took)")
    print("    - Integrity verification (proof nothing was tampered)")
    print(hline("-"))

    complete_count = 0
    incomplete_count = 0

    for i, (event_id, entry_ids, memo) in enumerate(posted, 1):
        bundle = trace_entry(
            session, event_id, memo, i, total,
            output_json=args.json,
        )
        if bundle and not bundle.missing_facts:
            complete_count += 1
        else:
            incomplete_count += 1

    # Summary
    banner("AUDIT TRACE SUMMARY")
    print()
    field("Total entries traced", total)
    field("Complete traces (0 missing facts)", complete_count)
    field("Incomplete traces", incomplete_count)
    print()

    if complete_count == total:
        print("  RESULT: All traces are complete. Every posted journal entry has")
        print("  a full decision journal showing what happened at runtime.")
        print()
        print("  An auditor can verify:")
        print("    1. What event triggered the posting")
        print("    2. Which configuration was in force (R21 snapshot)")
        print("    3. How account roles were resolved to COA codes (L1)")
        print("    4. That double-entry balance was verified (R4)")
        print("    5. That all invariants passed before commit")
        print("    6. The exact journal lines written")
        print("    7. Integrity hashes for tamper detection")
    else:
        print("  NOTE: Some traces have missing facts. Entries posted before")
        print("  the decision journal feature will show MISSING_FACTS for")
        print("  structured logs. Re-seed with demo_trace.py for full traces.")

    print()

    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
