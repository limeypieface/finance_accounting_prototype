"""CLI period close: health check, close workflow."""

from decimal import Decimal

from scripts.cli import config as cli_config
from scripts.cli.util import enable_quiet_logging, restore_logging


def _build_close_orchestrator(session, orchestrator, clock, config):
    """Build a PeriodCloseOrchestrator from the existing PostingOrchestrator."""
    from finance_config.bridges import build_role_resolver
    from finance_modules.gl.service import GeneralLedgerService
    from finance_modules.reporting.config import ReportingConfig
    from finance_modules.reporting.service import ReportingService
    from finance_services.period_close_orchestrator import PeriodCloseOrchestrator

    reporting_config = ReportingConfig(entity_name=cli_config.ENTITY)
    reporting_service = ReportingService(session=session, clock=clock, config=reporting_config)
    role_resolver = build_role_resolver(config)
    gl_service = GeneralLedgerService(session, role_resolver, clock)
    return PeriodCloseOrchestrator.from_posting_orchestrator(orchestrator, reporting_service, gl_service)


def handle_health_check(session, orchestrator, clock, config):
    """Handle 'H' — Pre-close health check (read-only diagnostic)."""
    close_orch = _build_close_orchestrator(session, orchestrator, clock, config)
    W = 72
    print()
    try:
        period_code = input("  Period code (e.g., FY2026): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not period_code:
        return
    period_info = orchestrator.period_service.get_period_by_code(period_code)
    if period_info is None:
        print(f"\n  Period '{period_code}' not found.\n")
        return
    result = close_orch.health_check(period_code=period_code, period_end_date=period_info.end_date)
    print()
    print("=" * W)
    print(f"  PRE-CLOSE HEALTH CHECK: {period_code}".center(W))
    print("=" * W)
    print()
    if result.sl_reconciliation:
        for sl_type, info in result.sl_reconciliation.items():
            sl_bal = info.get("sl_balance", Decimal("0"))
            gl_bal = info.get("gl_balance", Decimal("0"))
            var = info.get("variance", Decimal("0"))
            status = info.get("status", "?")
            print(f"    {sl_type:<12} SL ${sl_bal:>12,.2f}  GL ${gl_bal:>12,.2f}  variance: ${var:>10,.2f}  {status}")
    else:
        print("    (no subledgers configured)")
    print()
    if result.suspense_balances:
        for acct in result.suspense_balances:
            code = acct.get("account_code", "?")
            bal = acct.get("balance", Decimal("0"))
            status = acct.get("status", "?")
            print(f"    {code:<20} ${bal:>12,.2f}  {status}")
    else:
        print("    (none found)")
    print()
    print("  Trial Balance:")
    print(f"    Debits:  ${result.total_debits:>14,.2f}")
    print(f"    Credits: ${result.total_credits:>14,.2f}")
    print(f"    Balanced: {'YES' if result.trial_balance_ok else 'NO'}")
    print()
    print("  Period Activity:")
    print(f"    Entries: {result.period_entry_count}   Rejected: {result.period_rejection_count}")
    print()
    n_blocking = len(result.blocking_issues)
    n_warnings = len(result.warnings)
    if n_blocking == 0 and n_warnings == 0:
        print("  RESULT: No issues found. Period is ready to close.")
    else:
        print(f"  RESULT: {n_blocking} blocking, {n_warnings} warning")
        for issue in result.blocking_issues:
            print(f"    [BLOCKING] {issue.description}")
        for issue in result.warnings:
            print(f"    [WARNING]  {issue.description}")
        if n_blocking > 0:
            print()
            print("  Fix blocking issues before starting close.")
    print()


def handle_close_workflow(session, orchestrator, clock, config, actor_id):
    """Handle 'C' — Close a period (guided workflow)."""
    close_orch = _build_close_orchestrator(session, orchestrator, clock, config)
    W = 72
    print()
    try:
        period_code = input("  Period code (e.g., FY2026): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not period_code:
        return
    period_info = orchestrator.period_service.get_period_by_code(period_code)
    if period_info is None:
        print(f"\n  Period '{period_code}' not found.\n")
        return
    status = close_orch.get_status(period_code)
    if status and status.get("is_closed"):
        print(f"\n  Period {period_code} is already {status.get('status', 'closed').upper()}.\n")
        return
    if status and status.get("is_closing"):
        print(f"\n  Period {period_code} is already in CLOSING state (run_id: {status.get('closing_run_id')}).")
        try:
            choice = input("  Cancel existing close? [y/N]: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "Y":
            close_orch.cancel_close(period_code, actor_id, "User cancelled from CLI")
            session.commit()
            print("  Close cancelled. Period is OPEN again.\n")
        return
    print()
    print("=" * W)
    print(f"  PERIOD CLOSE WORKFLOW: {period_code}".center(W))
    print("=" * W)
    print()
    print("  Running health check...")
    health = close_orch.health_check(period_code, period_info.end_date)
    print(f"  TB: Dr ${health.total_debits:>12,.2f} = Cr ${health.total_credits:>12,.2f}  {'balanced' if health.trial_balance_ok else 'IMBALANCED'}")
    print(f"  Entries: {health.period_entry_count}   Issues: {len(health.blocking_issues)} blocking, {len(health.warnings)} warning")
    if health.blocking_issues:
        print()
        for issue in health.blocking_issues:
            print(f"    [BLOCKING] {issue.description}")
        print()
        try:
            choice = input("  Blocking issues detected. Proceed anyway? [y/N]: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice != "Y":
            return
    print()
    try:
        is_ye_input = input("  Year-end close? [y/N]: ").strip().upper()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    is_year_end = is_ye_input == "Y"
    print()
    print("  Acquiring close lock and executing phases...")
    print()
    muted = enable_quiet_logging()
    try:
        result = close_orch.close_period_full(period_code=period_code, actor_id=actor_id, is_year_end=is_year_end)
        session.commit()
    except Exception as e:
        session.rollback()
        restore_logging(muted)
        print(f"  ERROR: {e}\n")
        return
    restore_logging(muted)
    for pr in result.phase_results:
        tag = "SKIP" if pr.message and "Skipped" in pr.message else ("DONE" if pr.success else "FAIL")
        guard_str = f"  [{pr.guard}: {'PASS' if pr.success else 'FAIL'}]" if pr.guard else ""
        print(f"  [{pr.phase}] {pr.phase_name:<24} {tag}  {pr.message or ''}{guard_str}")
    print()
    if result.certificate:
        cert = result.certificate
        print("=" * W)
        print(f"  PERIOD {period_code} CLOSED SUCCESSFULLY".center(W))
        print("=" * W)
        print()
        print(f"  Close Certificate ID:  {cert.id}")
        print(f"  Closed by:             {cert.closed_by}")
        print(f"  Closed at:             {cert.closed_at}")
        print(f"  Correlation ID:        {cert.correlation_id}")
        print()
        print("  Trial Balance:")
        print(f"    Debits:  ${cert.trial_balance_debits:>14,.2f}")
        print(f"    Credits: ${cert.trial_balance_credits:>14,.2f}")
        print()
        print(f"  Phases completed: {cert.phases_completed}   skipped: {cert.phases_skipped}")
        print(f"  Adjustments:      {cert.adjustments_posted}")
        print(f"  Closing entries:  {cert.closing_entries_posted}")
        print(f"  Subledgers:       {', '.join(cert.subledgers_closed) if cert.subledgers_closed else 'none'}")
        print()
        print(f"  Ledger hash (R24): {cert.ledger_hash}")
        if cert.audit_event_id:
            print(f"  Audit event:       {cert.audit_event_id}")
        print()
        print("  Close events are traceable via 'T'. Rejected events via 'F'.")
        print(f"  Full log: logs/interactive.log (grep {cert.correlation_id[:8]})")
    else:
        print(f"  Close FAILED: {result.message}")
        print()
        if result.phase_results:
            last = result.phase_results[-1]
            if last.exceptions:
                print("  Exception detail:")
                for exc in last.exceptions:
                    print(f"    [{exc.severity.upper()}] {exc.description}")
        print()
        print("  Period remains in CLOSING state. Use 'C' again to retry or cancel.")
    print()
