"""
Shared trace renderer — canonical audit trace output for the system.

Used by:
  - scripts/trace.py (standalone CLI: --event-id, --entry-id)
  - scripts/interactive.py (show_trace, show_failed_traces)
  - tests/trace/show_trace.py (show_trace_for_event)

All trace output (CLI, interactive menu, tests) goes through render_trace or render_bundle
so format and sections stay consistent. Run pytest with -T -s (or SHOW_TRACE=1) to see
this output when using the global trace fixture.
"""

from __future__ import annotations


def render_trace(
    session,
    event_id,
    *,
    event_payload_map=None,
    acct_map=None,
    config=None,
):
    """Render the full audit trace for event_id (same format as interactive.py).

    If acct_map is None, it is built from account_ids in the bundle's journal lines.
    If event_payload_map is None, payload section is omitted or empty.
    """
    from finance_kernel.models.account import Account
    from finance_kernel.selectors.trace_selector import TraceSelector

    selector = TraceSelector(session)
    bundle = selector.trace_by_event_id(event_id)

    if acct_map is None:
        account_ids = set()
        for je in bundle.journal_entries:
            for line in je.lines:
                account_ids.add(line.account_id)
        if account_ids:
            acct_map = {a.id: a for a in session.query(Account).filter(Account.id.in_(account_ids)).all()}
        else:
            acct_map = {}

    if event_payload_map is None:
        event_payload_map = {}

    _render_bundle(
        session,
        bundle,
        event_payload_map=event_payload_map,
        acct_map=acct_map,
        config=config,
    )


def render_bundle(
    session,
    bundle,
    *,
    event_payload_map=None,
    acct_map=None,
    config=None,
):
    """Render the full trace for an already-loaded bundle (e.g. from trace_by_journal_entry_id).

    Use this when you have a TraceBundle but not an event_id (e.g. trace.py --entry-id).
    If acct_map is None, it is built from account_ids in the bundle's journal lines.
    """
    from finance_kernel.models.account import Account

    if acct_map is None:
        account_ids = set()
        for je in bundle.journal_entries:
            for line in je.lines:
                account_ids.add(line.account_id)
        acct_map = (
            {a.id: a for a in session.query(Account).filter(Account.id.in_(account_ids)).all()}
            if account_ids
            else {}
        )
    if event_payload_map is None:
        event_payload_map = {}
    _render_bundle(
        session,
        bundle,
        event_payload_map=event_payload_map,
        acct_map=acct_map,
        config=config,
    )


def _render_bundle(
    session,
    bundle,
    *,
    event_payload_map=None,
    acct_map=None,
    config=None,
):
    """Render the full trace for an already-loaded bundle (same sections as interactive)."""
    from finance_kernel.models.fiscal_period import FiscalPeriod
    from finance_kernel.models.party import Party

    event_payload_map = event_payload_map or {}
    acct_map = acct_map or {}

    # ---------------------------------------------------------------
    # 1. ORIGIN EVENT (enhanced — full payload + source document refs)
    # ---------------------------------------------------------------
    print()
    print("--- ORIGIN EVENT ---")
    print()
    if bundle.origin:
        o = bundle.origin
        print(f"    event_id:       {o.event_id}")
        print(f"    event_type:     {o.event_type}")
        print(f"    occurred_at:    {o.occurred_at}")
        print(f"    effective_date: {o.effective_date}")
        print(f"    producer:       {o.producer}")
        print(f"    schema_version: {o.schema_version}")
        print(f"    payload_hash:   {o.payload_hash}")

        payload = event_payload_map.get(o.event_id) or {}
        if payload:
            print()
            print("    payload:")
            for k, v in payload.items():
                val_str = str(v)
                if len(val_str) > 60:
                    val_str = val_str[:57] + "..."
                print(f"      {k}: {val_str}")

            doc_keys = [
                ("po_number", "Purchase Order"),
                ("contract_number", "Contract"),
                ("vendor_id", "Vendor"),
                ("invoice_number", "Invoice"),
                ("receipt_number", "Receipt"),
            ]
            doc_refs = [(label, payload[k]) for k, label in doc_keys if k in payload and payload[k]]
            qty_keys = [
                ("quantity", "Quantity"),
                ("po_quantity", "PO Qty"),
                ("receipt_quantity", "Receipt Qty"),
                ("invoice_quantity", "Invoice Qty"),
            ]
            qty_refs = [(label, payload[k]) for k, label in qty_keys if k in payload and payload[k]]
            if doc_refs or qty_refs:
                print()
                print("    source documents:")
                for label, val in doc_refs:
                    print(f"      {label}: {val}")
                for label, val in qty_refs:
                    print(f"      {label}: {val}")

    # ---------------------------------------------------------------
    # 2. POSTING CONTEXT (actor, period, config)
    # ---------------------------------------------------------------
    print()
    print("--- POSTING CONTEXT ---")
    print()

    actor_id = bundle.origin.actor_id if bundle.origin else None
    if actor_id:
        try:
            party = session.query(Party).filter(Party.id == actor_id).first()
            if party:
                p_type = party.party_type.value if hasattr(party.party_type, "value") else str(party.party_type)
                p_status = party.status.value if hasattr(party.status, "value") else str(party.status)
                print(f"    actor_id:     {actor_id}")
                print(f"    actor_code:   {party.party_code}")
                print(f"    actor_name:   {party.name}")
                print(f"    actor_type:   {p_type}")
                print(f"    actor_status: {p_status}")
                print(f"    can_transact: {party.can_transact}")
            else:
                print(f"    actor_id:     {actor_id}")
                print("    actor_code:   (not found in party table)")
        except Exception:
            print(f"    actor_id:     {actor_id}")

    eff_date = bundle.origin.effective_date if bundle.origin else None
    if eff_date:
        try:
            period = (
                session.query(FiscalPeriod)
                .filter(
                    FiscalPeriod.start_date <= eff_date,
                    FiscalPeriod.end_date >= eff_date,
                )
                .first()
            )
            if period:
                p_status = period.status.value if hasattr(period.status, "value") else str(period.status)
                print()
                print(f"    period_code:        {period.period_code}")
                print(f"    period_status:      {p_status}")
                print(f"    allows_adjustments: {period.allows_adjustments}")
                print(f"    period_range:       {period.start_date} .. {period.end_date}")
        except Exception:
            pass

    if config:
        print()
        print(f"    config_id:          {config.config_id}")
        print(f"    config_version:     {config.config_version}")
        print(f"    config_checksum:    {config.checksum[:16]}...")
        if hasattr(config, "canonical_fingerprint") and config.canonical_fingerprint:
            print(f"    config_fingerprint: {config.canonical_fingerprint[:16]}...")
        if hasattr(config, "scope") and config.scope:
            s = config.scope
            print(f"    scope:              entity={getattr(s, 'legal_entity', '*')}  "
                  f"jurisdiction={getattr(s, 'jurisdiction', '*')}")

    # ---------------------------------------------------------------
    # 3. JOURNAL ENTRIES (enhanced — account identity, idempotency, ledgers, dims)
    # ---------------------------------------------------------------
    print()
    # Derive ledger/subledger per entry from idempotency_key (format: event_id:ledger_id:version)
    def _ledger_from_idempotency_key(key: str) -> str | None:
        if not key or ":" not in key:
            return None
        parts = key.split(":")
        return parts[1] if len(parts) >= 2 else None

    ledgers_in_bundle = []
    for je in bundle.journal_entries:
        lid = _ledger_from_idempotency_key(je.idempotency_key or "")
        if lid and lid not in ledgers_in_bundle:
            ledgers_in_bundle.append(lid)
    ledgers_str = ", ".join(sorted(ledgers_in_bundle)) if ledgers_in_bundle else "—"

    print(f"--- JOURNAL ENTRIES ({len(bundle.journal_entries)})  Ledgers: {ledgers_str} ---")
    print()
    if not bundle.journal_entries:
        print("    (none — event did not produce journal entries)")
        print()
    for je in bundle.journal_entries:
        ledger_id = _ledger_from_idempotency_key(je.idempotency_key or "")
        print(f"    entry_id:       {je.entry_id}")
        print(f"    ledger:         {ledger_id or '—'}")
        print(f"    status:         {je.status}  seq: {je.seq if je.seq is not None else '-'}")
        print(f"    effective_date: {je.effective_date}  posted_at: {je.posted_at}")
        print(f"    idempotency:    {je.idempotency_key}")
        if je.description:
            print(f"    description:    {je.description}")
        if je.reversal_of_id:
            print(f"    reversal_of:    {je.reversal_of_id}")
        print()
        print(f"      {'seq':>4}  {'side':<7} {'amount':>14}  {'curr':<4}  {'code':<8} {'name':<22} {'type':<10} {'nbal':<7} {'rnd'}")
        print(f"      {'---':>4}  {'----':<7} {'------':>14}  {'----':<4}  {'----':<8} {'----':<22} {'----':<10} {'---':<7} {'---'}")
        for line in je.lines:
            acct = acct_map.get(line.account_id)
            a_name = acct.name[:20] if acct else "?"
            a_type = (
                acct.account_type.value
                if acct and hasattr(acct.account_type, "value")
                else str(acct.account_type) if acct else "?"
            )
            a_nbal = (
                acct.normal_balance.value
                if acct and hasattr(acct.normal_balance, "value")
                else str(acct.normal_balance) if acct else "?"
            )
            print(f"      {line.line_seq:>4}  {line.side:<7} "
                  f"{line.amount:>14}  {line.currency:<4}  "
                  f"{line.account_code:<8} {a_name:<22} {a_type:<10} {a_nbal:<7} {line.is_rounding}")

            if line.dimensions:
                dims = line.dimensions if isinstance(line.dimensions, dict) else {}
                if dims:
                    dim_str = "  ".join(f"{k}={v}" for k, v in dims.items())
                    print(f"             dims: {dim_str}")
        print()

    # ---------------------------------------------------------------
    # 4. INTERPRETATION OUTCOME
    # ---------------------------------------------------------------
    if bundle.interpretation:
        interp = bundle.interpretation
        print("--- INTERPRETATION OUTCOME ---")
        print()
        print(f"    status:            {interp.status}")
        print(f"    profile:           {interp.profile_id} v{interp.profile_version}")
        if interp.profile_hash:
            print(f"    profile_hash:      {interp.profile_hash[:16]}...")
        if interp.reason_code:
            print(f"    reason_code:       {interp.reason_code}")
        if hasattr(interp, "reason_detail") and interp.reason_detail:
            print(f"    reason_detail:     {interp.reason_detail}")
        if hasattr(interp, "failure_type") and interp.failure_type:
            print(f"    failure_type:      {interp.failure_type}")
        if hasattr(interp, "failure_message") and interp.failure_message:
            print(f"    failure_message:   {interp.failure_message}")
        if interp.decision_log:
            print(f"    decision_log_size: {len(interp.decision_log)} records")
        print()

    # ---------------------------------------------------------------
    # 5. DECISION JOURNAL
    # ---------------------------------------------------------------
    log_entries = [t for t in bundle.timeline if t.source == "structured_log"]
    audit_entries = [t for t in bundle.timeline if t.source == "audit_event"]

    print(f"--- DECISION JOURNAL ({len(bundle.timeline)} entries) ---")
    print()

    if log_entries:
        for i, te in enumerate(log_entries):
            action = te.action
            d = te.detail or {}

            if action == "period_check":
                print(f"  [{i:>2}] PERIOD CHECK — {d.get('period_code')}  effective_date: {d.get('effective_date')}  "
                      f"{'PASS' if d.get('passed') else 'FAIL'}")
            elif action == "guard_evaluated":
                print(f"  [{i:>2}] GUARD EVALUATED — {d.get('expression')}  "
                      f"type: {d.get('guard_type')}  passed: {d.get('passed')}  reason_code: {d.get('reason_code')}")
            elif action == "interpretation_started":
                print(f"  [{i:>2}] INTERPRETATION STARTED")
                print(f"       Profile: {d.get('profile_id')} v{d.get('profile_version')}")
                print(f"       Event: {str(d.get('source_event_id', ''))[:8]}...")
                if d.get("profile_source"):
                    print(f"       profile_source: {d.get('profile_source')}")
                if d.get("policy_fingerprint"):
                    print(f"       policy_fingerprint: {str(d.get('policy_fingerprint'))[:24]}...")
            elif action == "config_in_force":
                print(f"  [{i:>2}] CONFIG SNAPSHOT (R21)")
                print(f"       COA: {d.get('coa_version')}  Dim: {d.get('dimension_schema_version')}  "
                      f"Rounding: {d.get('rounding_policy_version')}  Currency: {d.get('currency_registry_version')}")
                if d.get("profile_source"):
                    print(f"       profile_source: {d.get('profile_source')}")
                if d.get("policy_fingerprint"):
                    print(f"       policy_fingerprint: {str(d.get('policy_fingerprint'))[:24]}...")
            elif action == "engine_dispatch_started":
                engines = d.get("required_engines", [])
                print(f"  [{i:>2}] ENGINE DISPATCH STARTED — {engines}")
            elif action in ("engine_invoked", "engine_completed"):
                eng_name = d.get("engine_name", "?")
                print(f"  [{i:>2}] {action.upper()} — {eng_name}")
                for k, v in d.items():
                    if k not in ("ts", "timestamp", "level", "logger", "engine_name", "event"):
                        print(f"       {k}: {v}")
            elif action == "journal_write_started":
                print(f"  [{i:>2}] JOURNAL WRITE STARTED — {d.get('ledger_count')} ledger(s)")
            elif action == "balance_validated":
                balanced = d.get("balanced")
                print(f"  [{i:>2}] BALANCE VALIDATED — {d.get('ledger_id')} {d.get('currency')}  "
                      f"Dr {d.get('sum_debit')} = Cr {d.get('sum_credit')}  "
                      f"{'PASS' if balanced else 'FAIL'}")
            elif action == "entry_balance_validated":
                print(f"  [{i:>2}] ENTRY BALANCE VALIDATED — entry_id={str(d.get('entry_id', ''))[:8]}...  "
                      f"{d.get('ledger_id')} {d.get('currency')} Dr={d.get('sum_debit')} Cr={d.get('sum_credit')}  "
                      f"{'PASS' if d.get('balanced') else 'FAIL'}")
            elif action == "role_resolved":
                role = d.get("role", "")
                if role and str(role).upper() == "BANK":
                    print(f"  [{i:>2}] BANK ACCOUNT RESOLVED — {d.get('account_code')} active")
                else:
                    acct_name = d.get("account_name", "")
                    acct_type = d.get("account_type", "")
                    nbal = d.get("normal_balance", "")
                    cfg_id = d.get("config_id", "")
                    cfg_ver = d.get("config_version", "")
                    eff_from = d.get("binding_effective_from", "")
                    eff_to = d.get("binding_effective_to", "open")
                    print(f"  [{i:>2}] ROLE RESOLVED — {d.get('role')} -> {d.get('account_code')}  "
                          f"{d.get('side')} {d.get('amount')} {d.get('currency')}")
                    if acct_name:
                        print(f"       account: {acct_name}  type={acct_type}  normal={nbal}")
                    if cfg_id:
                        print(f"       binding: config={cfg_id} v{cfg_ver}  "
                              f"effective {eff_from}..{eff_to}")
            elif action == "line_written":
                print(f"  [{i:>2}] LINE WRITTEN — seq {d.get('line_seq')}: "
                      f"{d.get('role')} -> {d.get('account_code')}  "
                      f"{d.get('side')} {d.get('amount')} {d.get('currency')}")
            elif action == "invariant_checked":
                passed = d.get("passed")
                print(f"  [{i:>2}] INVARIANT — {d.get('invariant')}: {'PASS' if passed else 'FAIL'}")
            elif action == "journal_entry_created":
                print(f"  [{i:>2}] ENTRY CREATED — {str(d.get('entry_id', ''))[:8]}...  "
                      f"status: {d.get('status')}  seq: {d.get('seq')}")
                if d.get("idempotency_key"):
                    print(f"       idempotency: {d.get('idempotency_key')}")
            elif action == "journal_write_completed":
                print(f"  [{i:>2}] WRITE COMPLETED — {d.get('entry_count')} entries  {d.get('duration_ms')}ms")
            elif action == "outcome_recorded":
                print(f"  [{i:>2}] OUTCOME RECORDED — {d.get('status')}")
            elif action == "interpretation_posted":
                ledger_ids = d.get("ledger_ids") or []
                ledgers_str = ", ".join(ledger_ids) if ledger_ids else "—"
                print(f"  [{i:>2}] INTERPRETATION POSTED — {d.get('entry_count')} entries  ledgers: {ledgers_str}")
                if d.get("entry_ids"):
                    print(f"       entry_ids: {d.get('entry_ids')}")
            elif action == "reproducibility_proof":
                print(f"  [{i:>2}] REPRODUCIBILITY PROOF")
                print(f"       input:  {str(d.get('input_hash', ''))[:16]}...")
                print(f"       output: {str(d.get('output_hash', ''))[:16]}...")
            elif action == "FINANCE_KERNEL_TRACE":
                print(f"  [{i:>2}] KERNEL TRACE — {d.get('policy_name')} v{d.get('policy_version')}  "
                      f"outcome: {d.get('outcome_status')}")
            elif action == "interpretation_completed":
                print(f"  [{i:>2}] COMPLETED — success: {d.get('success')}  {d.get('duration_ms')}ms")
            elif action == "module_posting_completed":
                ledger_ids = d.get("ledger_ids") or []
                ledgers_str = ", ".join(ledger_ids) if ledger_ids else "—"
                print(f"  [{i:>2}] MODULE POSTING COMPLETED — status: {d.get('status')}  "
                      f"entries: {d.get('entry_count', 0)}  duration: {d.get('duration_ms')}ms")
                print(f"       ledgers: {ledgers_str}")
                if d.get("profile_name"):
                    print(f"       profile: {d.get('profile_name')}")
            elif action == "workflow_transition":
                print(f"  [{i:>2}] WORKFLOW TRANSITION")
                print(f"       workflow: {d.get('workflow')}  action: {d.get('action')}")
                print(f"       entity: {d.get('entity_type')} {str(d.get('entity_id', ''))[:8]}...")
                print(f"       from_state: {d.get('from_state')}  to_state: {d.get('to_state', '-')}")
                print(f"       outcome: {d.get('outcome')}  reason: {d.get('reason', '')}")
                print(f"       duration_ms: {d.get('duration_ms')}")
                if d.get("approval_request_id"):
                    print(f"       approval_request_id: {d.get('approval_request_id')}")
            else:
                print(f"  [{i:>2}] {action}")
                for k, v in d.items():
                    if k not in ("ts", "timestamp", "level", "logger"):
                        print(f"       {k}: {v}")
            print()

    if audit_entries:
        print(f"  Audit trail ({len(audit_entries)}):")
        for i, te in enumerate(audit_entries):
            print(f"    {i:>3}  {te.action:<30} {te.entity_type or '':<15}")
        print()

    # ---------------------------------------------------------------
    # 6. ECONOMIC LINKS
    # ---------------------------------------------------------------
    if bundle.lifecycle_links:
        print(f"--- ECONOMIC LINKS ({len(bundle.lifecycle_links)}) ---")
        print()
        for link in bundle.lifecycle_links:
            print(f"    {link.link_type}:")
            print(f"      parent: {link.parent_artifact_type} {link.parent_artifact_id}")
            print(f"      child:  {link.child_artifact_type} {link.child_artifact_id}")
            print(f"      created_by_event: {link.creating_event_id}")
            if link.link_metadata:
                print(f"      metadata: {link.link_metadata}")
            print()

    # ---------------------------------------------------------------
    # 7. REPRODUCIBILITY (R21 snapshot)
    # ---------------------------------------------------------------
    if bundle.reproducibility:
        r = bundle.reproducibility
        print("--- REPRODUCIBILITY (R21 SNAPSHOT) ---")
        print()
        print(f"    coa_version:              {r.coa_version}")
        print(f"    dimension_schema_version: {r.dimension_schema_version}")
        print(f"    rounding_policy_version:  {r.rounding_policy_version}")
        print(f"    currency_registry_version:{r.currency_registry_version}")
        if hasattr(r, "fx_policy_version") and r.fx_policy_version:
            print(f"    fx_policy_version:        {r.fx_policy_version}")
        if hasattr(r, "posting_rule_version") and r.posting_rule_version:
            print(f"    posting_rule_version:     {r.posting_rule_version}")
        print()

    # ---------------------------------------------------------------
    # 8. INTEGRITY
    # ---------------------------------------------------------------
    print("--- INTEGRITY ---")
    print()
    integrity = bundle.integrity
    print(f"    payload_hash_verified: {integrity.payload_hash_verified}")
    print(f"    balance_verified:      {integrity.balance_verified}")
    print(f"    audit_chain_valid:     {integrity.audit_chain_segment_valid}")
    all_ok = integrity.payload_hash_verified and integrity.balance_verified
    print(f"    result:                {'ALL CHECKS PASSED' if all_ok else 'ISSUES DETECTED'}")

    # ---------------------------------------------------------------
    # 9. MISSING FACTS
    # ---------------------------------------------------------------
    if bundle.missing_facts:
        print()
        print(f"--- MISSING FACTS ({len(bundle.missing_facts)}) ---")
        for mf in bundle.missing_facts:
            print(f"    [{mf.fact}] {mf.expected_source}")
    else:
        print()
        print("  Trace is complete — 0 missing facts.")

    print()
