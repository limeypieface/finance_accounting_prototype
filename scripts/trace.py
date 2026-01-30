#!/usr/bin/env python3
"""
Trace any posted journal entry or event — full auditor-readable decision journal.

Usage:
    python3 scripts/trace.py --event-id <uuid>
    python3 scripts/trace.py --entry-id <uuid>
    python3 scripts/trace.py --event-id <uuid> --json

Examples:
    # Trace by source event ID (human-readable)
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
from decimal import Decimal
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_DB_URL = (
    "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"
)

W = 80


# =============================================================================
# Formatting helpers
# =============================================================================


def banner(title: str) -> None:
    print()
    print("=" * W)
    print(f"  {title}")
    print("=" * W)


def section(title: str) -> None:
    print()
    print(f"--- {title} ---")
    print()


def field(name: str, value, indent: int = 4) -> None:
    print(f"{' ' * indent}{name}: {value}")


def short_id(uid) -> str:
    return str(uid)[:8] + "..."


# =============================================================================
# Printers
# =============================================================================


def print_origin(origin) -> None:
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


def print_journal_entries(entries) -> None:
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


def print_interpretation(interp) -> None:
    section("INTERPRETATION OUTCOME")
    if interp is None:
        print("    (none -- Pipeline A event or no outcome recorded)")
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


def print_reproducibility(repro) -> None:
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


def print_decision_journal(timeline) -> None:
    log_entries = [t for t in timeline if t.source == "structured_log"]
    audit_entries = [t for t in timeline if t.source == "audit_event"]

    section(f"DECISION JOURNAL ({len(timeline)} entries)")

    if log_entries:
        print(f"  Structured log decisions ({len(log_entries)}):")
        print()
        for i, te in enumerate(log_entries):
            action = te.action
            d = te.detail or {}

            if action == "interpretation_started":
                print(f"  [{i:>2}] INTERPRETATION STARTED")
                print(f"       Profile: {d.get('profile_id')} v{d.get('profile_version')}")
                print(f"       Event: {str(d.get('source_event_id', ''))[:8]}...")
                print(f"       Effective date: {d.get('effective_date')}")

            elif action == "config_in_force":
                print(f"  [{i:>2}] CONFIGURATION SNAPSHOT (R21)")
                print(f"       COA version: {d.get('coa_version')}")
                print(f"       Dimension schema: {d.get('dimension_schema_version')}")
                print(f"       Rounding policy: {d.get('rounding_policy_version')}")
                print(f"       Currency registry: {d.get('currency_registry_version')}")

            elif action == "journal_write_started":
                print(f"  [{i:>2}] JOURNAL WRITE STARTED")
                print(f"       Ledger count: {d.get('ledger_count')}")

            elif action == "balance_validated":
                print(f"  [{i:>2}] BALANCE VALIDATED")
                print(f"       Ledger: {d.get('ledger_id')}  Currency: {d.get('currency')}")
                print(f"       Debits:  {d.get('sum_debit')}")
                print(f"       Credits: {d.get('sum_credit')}")
                print(f"       Balanced: {d.get('balanced')}")

            elif action == "role_resolved":
                print(f"  [{i:>2}] ROLE RESOLVED")
                acct_id = str(d.get('account_id', ''))[:8]
                print(f"       {d.get('role')} -> {d.get('account_code')} ({acct_id}...)")
                print(f"       Side: {d.get('side')}  Amount: {d.get('amount')} {d.get('currency')}")

            elif action == "line_written":
                print(f"  [{i:>2}] LINE WRITTEN")
                print(f"       Seq {d.get('line_seq')}: {d.get('role')} -> {d.get('account_code')}")
                print(f"       Side: {d.get('side')}  Amount: {d.get('amount')} {d.get('currency')}")

            elif action == "invariant_checked":
                print(f"  [{i:>2}] INVARIANT CHECKED")
                print(f"       {d.get('invariant')}: {'PASS' if d.get('passed') else 'FAIL'}")

            elif action == "journal_entry_created":
                print(f"  [{i:>2}] JOURNAL ENTRY CREATED")
                print(f"       Entry: {str(d.get('entry_id', ''))[:8]}...")
                print(f"       Status: {d.get('status')}  Seq: {d.get('seq')}")
                print(f"       Profile: {d.get('profile_id')}")

            elif action == "journal_write_completed":
                print(f"  [{i:>2}] JOURNAL WRITE COMPLETED")
                print(f"       Entries: {d.get('entry_count')}  Duration: {d.get('duration_ms')}ms")

            elif action == "outcome_recorded":
                print(f"  [{i:>2}] OUTCOME RECORDED")
                print(f"       Status: {d.get('status')}")
                ids = d.get('journal_entry_ids')
                if ids:
                    print(f"       Journal entries: {len(ids)}")

            elif action == "interpretation_posted":
                print(f"  [{i:>2}] INTERPRETATION POSTED")
                print(f"       Entry count: {d.get('entry_count')}")

            elif action == "reproducibility_proof":
                print(f"  [{i:>2}] REPRODUCIBILITY PROOF")
                print(f"       Input hash:  {str(d.get('input_hash', ''))[:16]}...")
                print(f"       Output hash: {str(d.get('output_hash', ''))[:16]}...")

            elif action == "FINANCE_KERNEL_TRACE":
                print(f"  [{i:>2}] FINANCE_KERNEL_TRACE")
                print(f"       Policy: {d.get('policy_name')} v{d.get('policy_version')}")
                print(f"       Outcome: {d.get('outcome_status')}")
                ih = str(d.get('input_hash', ''))
                if ih:
                    print(f"       Input hash:  {ih[:16]}...")
                    print(f"       Output hash: {str(d.get('output_hash', ''))[:16]}...")

            elif action == "interpretation_completed":
                print(f"  [{i:>2}] INTERPRETATION COMPLETED")
                print(f"       Success: {d.get('success')}  Duration: {d.get('duration_ms')}ms")

            else:
                print(f"  [{i:>2}] {action}")
                for k, v in d.items():
                    if k not in ("ts", "timestamp", "level", "logger"):
                        print(f"       {k}: {v}")

            print()

    if audit_entries:
        print(f"  Audit trail ({len(audit_entries)}):")
        print(f"  {'#':>3}  {'action':<30} {'entity_type':<15} {'seq':>5}")
        print(f"  {'---':>3}  {'------':<30} {'-----------':<15} {'---':>5}")
        for i, te in enumerate(audit_entries):
            print(f"  {i:>3}  {te.action:<30} "
                  f"{te.entity_type or '':<15} {te.seq or '':>5}")
        print()


def print_lifecycle_links(links) -> None:
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


def print_integrity(integrity) -> None:
    section("INTEGRITY")
    field("bundle_hash", integrity.bundle_hash)
    field("payload_hash_verified", integrity.payload_hash_verified)
    field("balance_verified", integrity.balance_verified)
    field("audit_chain_valid", integrity.audit_chain_segment_valid)


def print_missing_facts(facts) -> None:
    if not facts:
        return
    section(f"MISSING FACTS ({len(facts)})")
    for mf in facts:
        print(f"  [{mf.fact}]")
        field("expected_source", mf.expected_source, indent=6)
        if mf.correlation_key:
            field("correlation_key", mf.correlation_key, indent=6)
        if mf.detail:
            field("detail", mf.detail, indent=6)


# =============================================================================
# Main
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trace a posted journal entry or event.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
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
    parser.add_argument(
        "--json", action="store_true",
        help="Output full JSON bundle instead of formatted text",
    )
    parser.add_argument(
        "--db-url", type=str, default=DEFAULT_DB_URL,
        help=f"Database URL (default: {DEFAULT_DB_URL})",
    )

    args = parser.parse_args()

    # Parse UUID
    try:
        if args.event_id:
            target_id = UUID(args.event_id)
        else:
            target_id = UUID(args.entry_id)
    except ValueError as exc:
        print(f"  ERROR: Invalid UUID: {exc}", file=sys.stderr)
        return 1

    # Suppress library logging
    logging.disable(logging.CRITICAL)

    from finance_kernel.db.engine import init_engine_from_url, get_session
    from finance_kernel.selectors.trace_selector import TraceSelector

    # Connect
    try:
        init_engine_from_url(args.db_url, echo=False)
    except Exception as exc:
        print(f"  ERROR: Cannot connect to database: {exc}", file=sys.stderr)
        return 1

    session = get_session()

    try:
        selector = TraceSelector(session)

        if args.event_id:
            bundle = selector.trace_by_event_id(target_id)
        else:
            bundle = selector.trace_by_journal_entry_id(target_id)

        # JSON mode
        if args.json:
            bundle_dict = asdict(bundle)
            print(json.dumps(bundle_dict, indent=2, default=str))
            return 0

        # Human-readable mode
        banner("TRACE BUNDLE")
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

        banner("DONE")
        return 0

    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 1

    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
