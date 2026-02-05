"""CLI main loop: menu dispatch, resume/reset, post scenarios."""

import logging
import sys
from datetime import UTC, datetime

from scripts.cli import config as cli_config
from scripts.cli.close import handle_close_workflow, handle_health_check
from scripts.cli.data import (
    ALL_PIPELINE_SCENARIOS,
    SIMPLE_EVENTS,
    SUBLEDGER_SCENARIOS,
)
from scripts.cli.menu import print_menu
from scripts.cli.posting import (
    post_ar_invoice_workflow_scenario,
    post_engine_scenario,
    post_subledger_scenario,
)
from scripts.cli.setup import full_setup, has_accounts, resume_setup, tables_exist
from scripts.cli.util import enable_quiet_logging, fmt_amount, restore_logging
from scripts.cli.views import (
    show_accounts,
    show_failed_traces,
    show_import_staging,
    show_journal,
    show_mapping_editor,
    show_reports,
    show_subledger_reports,
    show_trace,
    show_vendors_and_customers,
)


class _FlushingFileHandler(logging.FileHandler):
    """FileHandler that flushes after every emit so interactive.log updates immediately."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def main() -> int:
    from pathlib import Path

    log_dir = cli_config.ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "interactive.log"

    from finance_kernel.logging_config import StructuredFormatter
    _file_handler = _FlushingFileHandler(str(log_path), mode="a")
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(StructuredFormatter())
    fk_logger = logging.getLogger("finance_kernel")
    fk_logger.addHandler(_file_handler)
    fk_logger.setLevel(logging.DEBUG)
    for h in fk_logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.setLevel(logging.CRITICAL + 1)

    # Early canary so interactive.log gets a line even if DB init fails below
    fk_logger.info(
        "interactive_cli_starting",
        extra={"log_path": str(log_path), "phase": "pre_init"},
    )
    _file_handler.flush()

    from finance_kernel.db.engine import get_session, init_engine_from_url
    from finance_kernel.domain.clock import DeterministicClock
    from finance_kernel.models.journal import JournalEntry

    try:
        init_engine_from_url(cli_config.DB_URL, echo=False)
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 1

    # Re-apply after init_engine_from_url() which calls configure_logging() and overwrites level
    fk_logger.setLevel(logging.DEBUG)
    for h in fk_logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.setLevel(logging.CRITICAL + 1)

    # Canary: CLI and DB ready
    fk_logger.info(
        "interactive_cli_started",
        extra={"log_path": str(log_path), "config_id": getattr(cli_config, "ENTITY", "?")},
    )
    _file_handler.flush()
    print(f"  Logging to: {log_path}", file=sys.stderr)

    clock = DeterministicClock(datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC))

    _tmp_session = get_session()
    has_data = tables_exist(_tmp_session) and has_accounts(_tmp_session)

    if has_data:
        entry_count = _tmp_session.query(JournalEntry).count()
        _tmp_session.close()
        print(f"\n  Existing data found ({entry_count} journal entries).")
        try:
            choice = input("  Resume or Reset? [R/x]: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice == "X":
            print("  Resetting database...")
            session, post_simple, engine_service, actor_id, config, orchestrator = full_setup(None, clock)
        else:
            print("  Resuming with existing data...")
            session, post_simple, engine_service, actor_id, config, orchestrator = resume_setup(clock)
    else:
        _tmp_session.close()
        print("\n  Setting up database with YAML config (US-GAAP-2026-v1)...")
        session, post_simple, engine_service, actor_id, config, orchestrator = full_setup(None, clock)

    print(f"  Config: {config.config_id} v{config.config_version}")
    print(f"  Policies: {len(config.policies)}  Role bindings: {len(config.role_bindings)}")
    entry_count = session.query(JournalEntry).count()
    print(f"  Journal has {entry_count} entries.")

    total_items = len(SIMPLE_EVENTS) + len(ALL_PIPELINE_SCENARIOS) + len(SUBLEDGER_SCENARIOS)

    while True:
        print_menu()
        try:
            choice = input("  Pick: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print("\n")
            break

        if choice == "Q":
            print("\n  Goodbye.\n")
            break
        elif choice == "R":
            show_reports(session, clock)
        elif choice == "J":
            show_journal(session)
        elif choice == "S":
            show_subledger_reports(session, clock)
        elif choice == "T":
            show_trace(session, config)
        elif choice == "F":
            show_failed_traces(session, config)
        elif choice == "L":
            show_accounts(session)
        elif choice == "P":
            show_vendors_and_customers(session)
        elif choice == "I":
            show_import_staging(session, clock, actor_id, config)
        elif choice == "M":
            show_mapping_editor(config)
        elif choice == "H":
            handle_health_check(session, orchestrator, clock, config)
        elif choice == "C":
            handle_close_workflow(session, orchestrator, clock, config, actor_id)
        elif choice == "A":
            print("\n  Posting all scenarios...")
            muted = enable_quiet_logging()
            ok = 0
            fail = 0
            for desc, dr, cr, amt, dr_lbl, cr_lbl in SIMPLE_EVENTS:
                result = post_simple(dr, cr, amt, desc)
                if result.success:
                    ok += 1
                    session.commit()
                else:
                    fail += 1
                    session.rollback()
                    err = getattr(result, "error_message", None) or getattr(result, "error_code", "?")
                    print(f"    FAIL: {desc} — {err}")
            for scenario in ALL_PIPELINE_SCENARIOS:
                result, evt_id = post_engine_scenario(engine_service, scenario, actor_id)
                if result.is_success:
                    ok += 1
                    session.commit()
                else:
                    fail += 1
                    session.rollback()
                    print(f"    FAIL: {scenario['label']} — {result.message}")
            for scenario in SUBLEDGER_SCENARIOS:
                result, sl_type = post_subledger_scenario(session, post_simple, orchestrator, scenario, actor_id, clock)
                if result.success:
                    ok += 1
                    session.commit()
                else:
                    fail += 1
                    session.rollback()
                    err = getattr(result, "error_message", None) or getattr(result, "error_code", "?")
                    print(f"    FAIL: {scenario['label']} — {err}")
            restore_logging(muted)
            print(f"\n  Done: {ok} posted, {fail} failed (of {total_items} total).\n")
        elif choice == "X":
            print("\n  Resetting database...")
            session.close()
            session, post_simple, engine_service, actor_id, config, orchestrator = full_setup(None, clock)
            print("  Done. Database is empty.\n")
        elif choice.isdigit():
            idx = int(choice) - 1
            n_simple = len(SIMPLE_EVENTS)
            n_pipeline = len(ALL_PIPELINE_SCENARIOS)
            n_subledger = len(SUBLEDGER_SCENARIOS)
            if 0 <= idx < n_simple:
                desc, dr, cr, amt, dr_lbl, cr_lbl = SIMPLE_EVENTS[idx]
                muted = enable_quiet_logging()
                result = post_simple(dr, cr, amt, desc)
                restore_logging(muted)
                if result.success:
                    session.commit()
                    print(f"\n  Posted: {desc} -- {fmt_amount(amt)}  (Dr {dr_lbl} / Cr {cr_lbl})")
                    print("    Use T to trace this entry.")
                else:
                    session.rollback()
                    err = getattr(result, "error_message", None) or getattr(result, "error_code", "?")
                    print(f"\n  FAILED: {err}")
            elif n_simple <= idx < n_simple + n_pipeline:
                scenario = ALL_PIPELINE_SCENARIOS[idx - n_simple]
                muted = enable_quiet_logging()
                if scenario.get("use_workflow_path"):
                    result, evt_id = post_ar_invoice_workflow_scenario(session, orchestrator, scenario, actor_id)
                else:
                    result, evt_id = post_engine_scenario(engine_service, scenario, actor_id)
                restore_logging(muted)
                status = result.status.value.upper()
                eng_tag = f" ({scenario.get('engine', '')} engine)" if scenario.get("engine") else ""
                if scenario.get("use_workflow_path"):
                    eng_tag = " (workflow trace — use T to see workflow + interpretation)"
                if result.is_success:
                    session.commit()
                    entry_ids = list(result.journal_entry_ids)
                    print(f"\n  [{status}] {scenario['label']}{eng_tag}")
                    print(f"    {scenario['business']}")
                    print(f"    {len(entry_ids)} journal entries created. Use T to trace.")
                else:
                    session.rollback()
                    print(f"\n  [{status}] {scenario['label']}{eng_tag}")
                    print(f"    {result.message}")
            elif n_simple + n_pipeline <= idx < n_simple + n_pipeline + n_subledger:
                scenario = SUBLEDGER_SCENARIOS[idx - n_simple - n_pipeline]
                muted = enable_quiet_logging()
                result, sl_type = post_subledger_scenario(session, post_simple, orchestrator, scenario, actor_id, clock)
                restore_logging(muted)
                if result.success:
                    session.commit()
                    print(f"\n  Posted: {scenario['label']} -- {fmt_amount(scenario['amount'])}  [{scenario['sl_type']}]")
                    print(f"    GL: Dr {scenario['gl_debit']} / Cr {scenario['gl_credit']}")
                    print(f"    SL: {scenario['sl_type']} entity={scenario['entity_id']}  doc={scenario['doc_type']}")
                    print("    Use S to view subledger reports, T to trace the GL entry.")
                else:
                    session.rollback()
                    err = getattr(result, "error_message", None) or getattr(result, "error_code", "?")
                    print(f"\n  FAILED: {err}")
            else:
                print(f"\n  Invalid number. Pick 1-{total_items}.")
        else:
            print(f"\n  Unknown command '{choice}'. Try a number, R, J, S, T, F, L, P, I, M, H, C, A, X, or Q.")

    session.close()
    return 0
