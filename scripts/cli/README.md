# Interactive Accounting CLI

Global CLI application for operating the finance system: post events, view reports, import data, close periods, trace decisions. Not a demo-only script — this is the primary way to use the system through the CLI.

## Entry point

- **`scripts/interactive.py`** — Thin launcher; adds project root to `sys.path` and runs `main()`.
- **`python -m scripts.cli`** — Alternative (run from project root).

## Package layout

| Module | Responsibility |
|--------|----------------|
| **config.py** | DB URL, entity, dates, `UPLOAD_DIR`. |
| **data.py** | `SIMPLE_EVENTS`, `ENGINE_SCENARIOS`, `NON_ENGINE_SCENARIOS`, `SUBLEDGER_SCENARIOS`. |
| **util.py** | `fmt_amount`, `enable_quiet_logging`, `restore_logging`. |
| **setup.py** | `full_setup`, `resume_setup`, DB helpers, account creation from config, simple pipeline builder. |
| **menu.py** | `print_menu()`. |
| **posting.py** | `post_engine_scenario`, `post_ar_invoice_workflow_scenario`, `post_subledger_scenario`. |
| **close.py** | `handle_health_check`, `handle_close_workflow`, `_build_close_orchestrator`. |
| **views/** | Display and sub-screens: accounts, reports, journal, trace, failed traces, import & staging. |
| **main.py** | Main loop: menu, input, dispatch to views/posting/close. |

## Reuse

- Import **config** for `ENTITY`, `EFFECTIVE`, `UPLOAD_DIR`, `DB_URL`, etc.
- Import **views** for `show_accounts`, `show_reports`, `show_import_staging`, etc., if building another entry point (e.g. a different menu or script that reuses the same screens).
- Import **setup** for `full_setup` / `resume_setup` when you need a wired session and orchestrator.
