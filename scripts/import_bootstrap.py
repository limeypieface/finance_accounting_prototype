"""
Configuration-driven import bootstrap: fiscal periods and optional bootstrap party.

Reads optional import_bootstrap.yaml from a config set directory. Used by
reset_db_ironflow.py and run_ironflow_import.py so any company's config set
defines its own fiscal periods (and optionally bootstrap party) for migration.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from finance_config.loader import load_yaml_file


def load_import_bootstrap(config_set_dir: Path) -> dict[str, Any]:
    """
    Load import_bootstrap.yaml from a config set directory if present.

    Returns dict with keys:
      - fiscal_periods: list of {period_code, name, start_date, end_date}
      - bootstrap_party_code: optional str (e.g. "SYSTEM")

    If the file is absent or invalid, returns {"fiscal_periods": []}.
    """
    path = config_set_dir / "import_bootstrap.yaml"
    if not path.exists():
        return {"fiscal_periods": []}
    try:
        data = load_yaml_file(path) or {}
        periods = data.get("fiscal_periods")
        if not isinstance(periods, list):
            periods = []
        return {
            "fiscal_periods": periods,
            "bootstrap_party_code": data.get("bootstrap_party_code"),
        }
    except Exception:
        return {"fiscal_periods": []}


def ensure_fiscal_periods_from_config(
    session: Any,
    config_set_dir: Path,
    created_by_id: UUID,
) -> None:
    """
    Ensure all fiscal periods listed in the config set's import_bootstrap.yaml
    exist (create if missing). Idempotent.
    """
    bootstrap = load_import_bootstrap(config_set_dir)
    periods = bootstrap.get("fiscal_periods") or []
    if not periods:
        return

    from sqlalchemy import select
    from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus

    for p in periods:
        if not isinstance(p, dict):
            continue
        period_code = p.get("period_code")
        name = p.get("name") or period_code
        start_s = p.get("start_date")
        end_s = p.get("end_date")
        if not period_code or not start_s or not end_s:
            continue
        try:
            start_date = date.fromisoformat(str(start_s).strip())
            end_date = date.fromisoformat(str(end_s).strip())
        except (TypeError, ValueError):
            continue

        existing = session.execute(
            select(FiscalPeriod).where(FiscalPeriod.period_code == period_code)
        ).scalars().first()
        if existing:
            continue
        session.add(
            FiscalPeriod(
                period_code=period_code,
                name=str(name),
                start_date=start_date,
                end_date=end_date,
                status=PeriodStatus.OPEN,
                allows_adjustments=True,
                created_by_id=created_by_id,
            )
        )
    session.flush()
