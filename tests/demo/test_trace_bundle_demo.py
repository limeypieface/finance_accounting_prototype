"""
Demonstration test for TraceBundle — shows the full trace output for a posted event.

Run with:
    python -m pytest tests/demo/test_trace_bundle_demo.py -v -s

The -s flag is important to see all the print output!

Requirements:
    - PostgreSQL must be running (uses the standard test fixtures from conftest.py)
    - Database 'finance_kernel_test' must exist
"""

import json
from dataclasses import asdict
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.models.economic_link import EconomicLinkModel
from finance_kernel.selectors.trace_selector import TraceSelector


# =============================================================================
# PRETTY PRINTING HELPERS
# =============================================================================


def banner(title: str) -> None:
    """Print a banner for visual separation."""
    width = 80
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def section(title: str) -> None:
    """Print a section header."""
    print()
    print(f"--- {title} ---")
    print()


def field(name: str, value, indent: int = 4) -> None:
    """Print a single field."""
    prefix = " " * indent
    print(f"{prefix}{name}: {value}")


def short_id(uid) -> str:
    """Truncate a UUID for display."""
    return str(uid)[:8] + "..."


# =============================================================================
# DEMO TEST
# =============================================================================


class TestTraceBundleDemo:
    """
    Posts a $250 event through Pipeline B, creates an economic link,
    then traces the event and prints the full TraceBundle.
    """

    def test_full_trace_output(
        self,
        session,
        standard_accounts,
        current_period,
        post_via_coordinator,
        deterministic_clock,
        test_actor_id,
    ):
        banner("TRACE BUNDLE DEMO")
        print()
        print("  Scenario: Post $250 CashAsset -> SalesRevenue through Pipeline B,")
        print("  decision journal persisted on InterpretationOutcome automatically,")
        print("  then trace by event_id with full auditor-readable decision journal.")

        # =====================================================================
        # STEP 1: Post event (decision journal captured automatically)
        # =====================================================================
        section("Step 1: Post event via InterpretationCoordinator")

        source_event_id = uuid4()

        result = post_via_coordinator(
            source_event_id=source_event_id,
            debit_role="CashAsset",
            credit_role="SalesRevenue",
            amount=Decimal("250.00"),
            event_type="sales.cash_receipt",
        )

        assert result.success
        print(f"  Posted event: {short_id(source_event_id)}")
        print(f"  Pipeline result: success={result.success}")
        print(f"  Decision journal persisted on outcome automatically")

        # =====================================================================
        # STEP 2: Create an economic link (receipt -> journal_entry)
        # =====================================================================
        section("Step 2: Create economic link")

        from sqlalchemy import select
        from finance_kernel.models.journal import JournalEntry

        entry = session.execute(
            select(JournalEntry).where(
                JournalEntry.source_event_id == source_event_id
            )
        ).scalar_one()

        receipt_id = uuid4()
        link = EconomicLinkModel(
            link_type="FULFILLED_BY",
            parent_artifact_type="receipt",
            parent_artifact_id=receipt_id,
            child_artifact_type="journal_entry",
            child_artifact_id=entry.id,
            creating_event_id=source_event_id,
            created_at=deterministic_clock.now(),
            link_metadata={"receipt_number": "RCV-2026-0001", "amount": "250.00"},
        )
        session.add(link)
        session.flush()
        print(f"  Link: receipt({short_id(receipt_id)}) --[FULFILLED_BY]--> "
              f"journal_entry({short_id(entry.id)})")
        print(f"  Metadata: {link.link_metadata}")

        # =====================================================================
        # STEP 3: Assemble the trace bundle (reads decision_log from DB)
        # =====================================================================
        section("Step 3: Assemble trace bundle (decision journal from DB)")

        selector = TraceSelector(
            session,
            clock=deterministic_clock,
        )
        bundle = selector.trace_by_event_id(source_event_id)
        print(f"  Bundle assembled successfully (no LogCapture needed).")

        # =====================================================================
        # STEP 4: Print the auditor-readable narrative
        # =====================================================================

        # --- HEADER ---
        banner("TRACE BUNDLE")
        field("version", bundle.version)
        field("trace_id", bundle.trace_id)
        field("generated_at", bundle.generated_at)
        field("artifact_type", bundle.artifact.artifact_type)
        field("artifact_id", bundle.artifact.artifact_id)

        # --- ORIGIN EVENT ---
        section("ORIGIN EVENT")
        if bundle.origin:
            field("event_id", bundle.origin.event_id)
            field("event_type", bundle.origin.event_type)
            field("occurred_at", bundle.origin.occurred_at)
            field("effective_date", bundle.origin.effective_date)
            field("actor_id", bundle.origin.actor_id)
            field("producer", bundle.origin.producer)
            field("payload_hash", bundle.origin.payload_hash)
            field("schema_version", bundle.origin.schema_version)
            field("ingested_at", bundle.origin.ingested_at)
        else:
            print("    (none)")

        # --- JOURNAL ENTRIES ---
        section(f"JOURNAL ENTRIES ({len(bundle.journal_entries)})")
        for i, je in enumerate(bundle.journal_entries):
            print(f"  [{i}] entry_id: {je.entry_id}")
            field("source_event_id", je.source_event_id, indent=6)
            field("source_event_type", je.source_event_type, indent=6)
            field("effective_date", je.effective_date, indent=6)
            field("posted_at", je.posted_at, indent=6)
            field("status", je.status, indent=6)
            field("seq", je.seq, indent=6)
            field("idempotency_key", je.idempotency_key, indent=6)
            field("reversal_of_id", je.reversal_of_id, indent=6)
            field("description", je.description, indent=6)
            print()
            print(f"      Lines ({len(je.lines)}):")
            print(f"      {'seq':>4}  {'side':<7} {'amount':>12}  {'currency':<4}  {'account_code':<12}  {'rounding'}")
            print(f"      {'---':>4}  {'----':<7} {'------':>12}  {'--------':<4}  {'------------':<12}  {'--------'}")
            for line in je.lines:
                print(f"      {line.line_seq:>4}  {line.side:<7} {line.amount:>12}  {line.currency:<4}  "
                      f"{line.account_code:<12}  {line.is_rounding}")
            print()
            print(f"      R21 snapshot:")
            field("coa_version", je.coa_version, indent=8)
            field("dimension_schema_version", je.dimension_schema_version, indent=8)
            field("rounding_policy_version", je.rounding_policy_version, indent=8)
            field("currency_registry_version", je.currency_registry_version, indent=8)
            field("posting_rule_version", je.posting_rule_version, indent=8)

        # --- INTERPRETATION ---
        section("INTERPRETATION OUTCOME")
        if bundle.interpretation:
            field("source_event_id", bundle.interpretation.source_event_id)
            field("status", bundle.interpretation.status)
            field("econ_event_id", bundle.interpretation.econ_event_id)
            field("journal_entry_ids", bundle.interpretation.journal_entry_ids)
            field("reason_code", bundle.interpretation.reason_code)
            field("reason_detail", bundle.interpretation.reason_detail)
            field("profile_id", bundle.interpretation.profile_id)
            field("profile_version", bundle.interpretation.profile_version)
            field("profile_hash", bundle.interpretation.profile_hash)
            field("trace_id", bundle.interpretation.trace_id)
        else:
            print("    (none)")

        # --- REPRODUCIBILITY ---
        section("R21 REPRODUCIBILITY")
        if bundle.reproducibility:
            field("coa_version", bundle.reproducibility.coa_version)
            field("dimension_schema_version", bundle.reproducibility.dimension_schema_version)
            field("rounding_policy_version", bundle.reproducibility.rounding_policy_version)
            field("currency_registry_version", bundle.reproducibility.currency_registry_version)
            field("fx_policy_version", bundle.reproducibility.fx_policy_version)
            field("posting_rule_version", bundle.reproducibility.posting_rule_version)
        else:
            print("    (none)")

        # --- DECISION JOURNAL (STRUCTURED LOG TIMELINE) ---
        section(f"DECISION JOURNAL ({len(bundle.timeline)} entries)")
        print()
        print("  This is the auditor-readable decision trail showing every")
        print("  function, role resolution, balance check, and posting decision.")
        print()

        log_entries = [t for t in bundle.timeline if t.source == "structured_log"]
        audit_entries = [t for t in bundle.timeline if t.source == "audit_event"]

        if log_entries:
            print(f"  Structured log decisions ({len(log_entries)}):")
            print()
            for i, te in enumerate(log_entries):
                action = te.action
                detail = te.detail or {}

                # Format each decision type for auditor readability
                if action == "interpretation_started":
                    print(f"  [{i:>2}] INTERPRETATION STARTED")
                    print(f"       Profile: {detail.get('profile_id')} v{detail.get('profile_version')}")
                    print(f"       Event: {detail.get('source_event_id', '')[:8]}...")
                    print(f"       Effective date: {detail.get('effective_date')}")
                    print(f"       Ledgers: {detail.get('ledger_count')}")

                elif action == "config_in_force":
                    print(f"  [{i:>2}] CONFIGURATION SNAPSHOT (R21)")
                    print(f"       COA version: {detail.get('coa_version')}")
                    print(f"       Dimension schema: {detail.get('dimension_schema_version')}")
                    print(f"       Rounding policy: {detail.get('rounding_policy_version')}")
                    print(f"       Currency registry: {detail.get('currency_registry_version')}")

                elif action == "journal_write_started":
                    print(f"  [{i:>2}] JOURNAL WRITE STARTED")
                    print(f"       Ledger count: {detail.get('ledger_count')}")

                elif action == "balance_validated":
                    print(f"  [{i:>2}] BALANCE VALIDATED")
                    print(f"       Ledger: {detail.get('ledger_id')}  Currency: {detail.get('currency')}")
                    print(f"       Sum debits:  {detail.get('sum_debit')}")
                    print(f"       Sum credits: {detail.get('sum_credit')}")
                    print(f"       Balanced: {detail.get('balanced')}")

                elif action == "role_resolved":
                    print(f"  [{i:>2}] ROLE RESOLVED")
                    print(f"       Role: {detail.get('role')} -> Account: {detail.get('account_code')} ({detail.get('account_id', '')[:8]}...)")
                    print(f"       Ledger: {detail.get('ledger_id')}  COA v{detail.get('coa_version')}")
                    print(f"       Side: {detail.get('side')}  Amount: {detail.get('amount')} {detail.get('currency')}")

                elif action == "line_written":
                    print(f"  [{i:>2}] LINE WRITTEN")
                    print(f"       Seq: {detail.get('line_seq')}  Role: {detail.get('role')} -> {detail.get('account_code')}")
                    print(f"       Side: {detail.get('side')}  Amount: {detail.get('amount')} {detail.get('currency')}")
                    print(f"       Rounding: {detail.get('is_rounding')}")

                elif action == "invariant_checked":
                    print(f"  [{i:>2}] INVARIANT CHECKED")
                    print(f"       Invariant: {detail.get('invariant')}")
                    print(f"       Passed: {detail.get('passed')}")

                elif action == "journal_entry_created":
                    print(f"  [{i:>2}] JOURNAL ENTRY CREATED")
                    print(f"       Entry: {detail.get('entry_id', '')[:8]}...")
                    print(f"       Status: {detail.get('status')}  Seq: {detail.get('seq')}")
                    print(f"       Profile: {detail.get('profile_id')}  Ledger: {detail.get('ledger_id')}")
                    print(f"       Effective: {detail.get('effective_date')}  Posted: {detail.get('posted_at')}")

                elif action == "journal_write_completed":
                    print(f"  [{i:>2}] JOURNAL WRITE COMPLETED")
                    print(f"       Entries: {detail.get('entry_count')}  Duration: {detail.get('duration_ms')}ms")

                elif action == "outcome_recorded":
                    print(f"  [{i:>2}] OUTCOME RECORDED")
                    print(f"       Status: {detail.get('status')}")
                    print(f"       Profile: {detail.get('profile_id')} v{detail.get('profile_version')}")
                    entry_ids = detail.get('journal_entry_ids')
                    if entry_ids:
                        print(f"       Journal entries: {len(entry_ids)}")

                elif action == "interpretation_posted":
                    print(f"  [{i:>2}] INTERPRETATION POSTED")
                    print(f"       Entry count: {detail.get('entry_count')}")

                elif action == "reproducibility_proof":
                    print(f"  [{i:>2}] REPRODUCIBILITY PROOF")
                    print(f"       Input hash:  {detail.get('input_hash', '')[:16]}...")
                    print(f"       Output hash: {detail.get('output_hash', '')[:16]}...")

                elif action == "FINANCE_KERNEL_TRACE":
                    print(f"  [{i:>2}] FINANCE_KERNEL_TRACE (final summary)")
                    print(f"       Policy: {detail.get('policy_name')} v{detail.get('policy_version')}")
                    print(f"       Outcome: {detail.get('outcome_status')}")
                    ih = detail.get('input_hash', '')
                    oh = detail.get('output_hash', '')
                    if ih:
                        print(f"       Input hash:  {ih[:16]}...")
                        print(f"       Output hash: {oh[:16]}...")

                elif action == "interpretation_completed":
                    print(f"  [{i:>2}] INTERPRETATION COMPLETED")
                    print(f"       Success: {detail.get('success')}  Duration: {detail.get('duration_ms')}ms")

                else:
                    print(f"  [{i:>2}] {action}")
                    for k, v in detail.items():
                        if k not in ("ts", "timestamp", "level", "logger"):
                            print(f"       {k}: {v}")

                print()

        if audit_entries:
            print(f"  Audit trail events ({len(audit_entries)}):")
            print(f"  {'#':>3}  {'action':<30} {'entity_type':<15} {'seq':>5}")
            print(f"  {'---':>3}  {'------':<30} {'-----------':<15} {'---':>5}")
            for i, te in enumerate(audit_entries):
                print(f"  {i:>3}  {te.action:<30} "
                      f"{te.entity_type or '':<15} {te.seq or '':>5}")

        # --- LIFECYCLE LINKS ---
        section(f"LIFECYCLE LINKS ({len(bundle.lifecycle_links)})")
        for ll in bundle.lifecycle_links:
            print(f"  {ll.parent_artifact_type}({short_id(ll.parent_artifact_id)}) "
                  f"--[{ll.link_type}]--> "
                  f"{ll.child_artifact_type}({short_id(ll.child_artifact_id)})")
            field("link_id", ll.link_id, indent=6)
            field("creating_event_id", ll.creating_event_id, indent=6)
            field("created_at", ll.created_at, indent=6)
            if ll.link_metadata:
                field("metadata", ll.link_metadata, indent=6)

        # --- CONFLICTS ---
        section(f"CONFLICTS ({len(bundle.conflicts)})")
        if bundle.conflicts:
            for c in bundle.conflicts:
                print(f"  {c.action} on {c.entity_type}({short_id(c.entity_id)})")
                field("occurred_at", c.occurred_at, indent=6)
                field("payload", c.payload, indent=6)
        else:
            print("    (none -- clean event)")

        # --- INTEGRITY ---
        section("INTEGRITY")
        field("bundle_hash", bundle.integrity.bundle_hash)
        field("payload_hash_verified", bundle.integrity.payload_hash_verified)
        field("balance_verified", bundle.integrity.balance_verified)
        field("audit_chain_segment_valid", bundle.integrity.audit_chain_segment_valid)

        # --- MISSING FACTS ---
        section(f"MISSING FACTS ({len(bundle.missing_facts)})")
        if bundle.missing_facts:
            for mf in bundle.missing_facts:
                print(f"  [{mf.fact}]")
                field("expected_source", mf.expected_source, indent=6)
                field("correlation_key", mf.correlation_key, indent=6)
                field("detail", mf.detail, indent=6)
        else:
            print("    (none -- all facts present)")

        # =====================================================================
        # STEP 5: Trace by journal_entry_id (proves bidirectional lookup)
        # =====================================================================
        section("Step 5: Trace by journal_entry_id (bidirectional)")

        bundle2 = selector.trace_by_journal_entry_id(entry.id)
        print(f"  Traced from entry {short_id(entry.id)} -> same event")
        print(f"  Origin event: {short_id(bundle2.origin.event_id) if bundle2.origin else 'none'}")
        print(f"  Timeline entries: {len(bundle2.timeline)}")
        assert bundle2.origin is not None
        assert bundle2.origin.event_id == source_event_id

        # =====================================================================
        # STEP 6: Full JSON dump
        # =====================================================================
        banner("FULL JSON BUNDLE")
        bundle_dict = asdict(bundle)
        print(json.dumps(bundle_dict, indent=2, default=str))

        # =====================================================================
        # Assertions (test still validates correctness)
        # =====================================================================
        banner("ASSERTIONS")
        assert bundle.origin is not None
        print("  [PASS] origin is not None")

        assert bundle.origin.event_id == source_event_id
        print("  [PASS] origin.event_id matches source_event_id")

        assert len(bundle.journal_entries) >= 1
        print(f"  [PASS] {len(bundle.journal_entries)} journal entry(ies) found")

        assert bundle.journal_entries[0].status == "posted"
        print("  [PASS] journal entry status is 'posted'")

        assert len(bundle.journal_entries[0].lines) >= 2
        print(f"  [PASS] {len(bundle.journal_entries[0].lines)} lines in entry")

        assert bundle.interpretation is not None
        print("  [PASS] interpretation outcome is present")

        assert bundle.interpretation.status == "posted"
        print("  [PASS] interpretation status is 'posted'")

        assert len(bundle.lifecycle_links) >= 1
        print(f"  [PASS] {len(bundle.lifecycle_links)} lifecycle link(s) found")

        assert bundle.integrity.payload_hash_verified is True
        print("  [PASS] payload hash verified")

        assert bundle.integrity.balance_verified is True
        print("  [PASS] balance verified (debits == credits)")

        assert len(bundle.integrity.bundle_hash) == 64
        print(f"  [PASS] bundle hash is SHA-256 ({bundle.integrity.bundle_hash[:16]}...)")

        # Timeline should have structured log entries (not just audit events)
        assert len(log_entries) >= 5, (
            f"Expected at least 5 structured log entries, got {len(log_entries)}"
        )
        print(f"  [PASS] {len(log_entries)} structured log entries in timeline")

        # Verify key decision steps are present
        log_actions = [t.action for t in log_entries]

        assert "interpretation_started" in log_actions
        print("  [PASS] interpretation_started in timeline")

        assert "config_in_force" in log_actions
        print("  [PASS] config_in_force in timeline (R21 snapshot)")

        assert "balance_validated" in log_actions
        print("  [PASS] balance_validated in timeline")

        assert "role_resolved" in log_actions
        print("  [PASS] role_resolved in timeline")

        assert "journal_entry_created" in log_actions
        print("  [PASS] journal_entry_created in timeline")

        assert "outcome_recorded" in log_actions
        print("  [PASS] outcome_recorded in timeline")

        assert "FINANCE_KERNEL_TRACE" in log_actions
        print("  [PASS] FINANCE_KERNEL_TRACE in timeline")

        # Decision journal persisted on outcome
        assert bundle.interpretation.decision_log is not None, (
            "Expected decision_log on InterpretationOutcome"
        )
        print(f"  [PASS] decision_log persisted ({len(bundle.interpretation.decision_log)} records)")

        # No missing facts (decision_log on outcome provides structured logs)
        assert len(bundle.missing_facts) == 0, (
            f"Expected 0 missing facts with persisted decision_log, got {len(bundle.missing_facts)}: "
            f"{[mf.fact for mf in bundle.missing_facts]}"
        )
        print("  [PASS] 0 missing facts (decision_log on outcome provides all data)")

        print()
        print("  All assertions passed.")
        banner("DEMO COMPLETE")
