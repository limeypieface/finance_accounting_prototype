#!/usr/bin/env python3
"""
Compare balance sheet line-by-line: QBO journal-derived vs DB report.

Uses qbo_journal_Journal.json + qbo_coa_mapping to compute expected balance per
account code (same logic as import: aggregate by QBO account name, map name->code,
sum by code, apply natural balance from code prefix). Then compares to DB trial
balance (same natural balance rule). Prints side-by-side and highlights diffs.

Usage:
  python3 scripts/compare_balance_sheet_line_by_line.py [--as-of 2025-12-31]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _decimal(v) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_qbo_coa_mapping(config_set_dir: Path) -> dict[str, str]:
    """QBO account name (input_name) -> target_code. Also input_code -> target_code if set."""
    path = config_set_dir / "import_mappings" / "qbo_coa_mapping.yaml"
    if not path.exists():
        return {}
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = {}
    for m in data.get("mappings") or []:
        if not isinstance(m, dict):
            continue
        target = m.get("target_code")
        if not target:
            continue
        target = str(target).strip()
        for key in ("input_name", "input_code"):
            v = m.get(key)
            if v is not None and str(v).strip():
                out[str(v).strip()] = target
    return out


def trial_balance_from_journal(journal_path: Path, as_of: date | None) -> dict[str, tuple[Decimal, Decimal]]:
    """Aggregate journal by account name -> (debit_total, credit_total)."""
    data = load_json(journal_path)
    rows = data.get("rows") or []
    totals = defaultdict(lambda: (Decimal("0"), Decimal("0")))
    for row in rows:
        if not isinstance(row, dict):
            continue
        if as_of:
            dstr = row.get("date")
            if dstr:
                try:
                    d = datetime.strptime(dstr.strip(), "%m/%d/%Y").date()
                    if d > as_of:
                        continue
                except ValueError:
                    pass
        for line in row.get("lines") or []:
            if not isinstance(line, dict):
                continue
            acc = (line.get("account") or "").strip()
            if not acc:
                continue
            dr = _decimal(line.get("debit"))
            cr = _decimal(line.get("credit"))
            prev_dr, prev_cr = totals[acc]
            totals[acc] = (prev_dr + dr, prev_cr + cr)
    return dict(totals)


def natural_balance_from_code(debit_total: Decimal, credit_total: Decimal, code: str) -> Decimal:
    """Assets/Expenses (1,5,6): dr-cr. Liabilities/Equity/Revenue (2,3,4): cr-dr."""
    if not code or not code.strip():
        return debit_total - credit_total
    c = code.strip()
    if not c[0].isdigit():
        return debit_total - credit_total
    prefix = int(c[0])
    if prefix in (1, 5, 6):
        return debit_total - credit_total
    return credit_total - debit_total


def main() -> int:
    parser = argparse.ArgumentParser(description="Balance sheet line-by-line: QBO journal vs DB")
    parser.add_argument("--as-of", type=str, default="2025-12-31")
    parser.add_argument("--journal", type=Path, default=ROOT / "upload/qbo_journal_Journal.json")
    parser.add_argument(
        "--config-set",
        type=Path,
        default=ROOT / "finance_config/sets" / os.environ.get("FINANCE_IMPORT_CONFIG_ID", "US-GAAP-2026-IRONFLOW-AI"),
        help="Config set directory (default: finance_config/sets/<FINANCE_IMPORT_CONFIG_ID or IRONFLOW>)",
    )
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of)

    # 1. QBO journal: aggregate by account name
    if not args.journal.exists():
        print(f"ERROR: Journal not found: {args.journal}")
        return 1
    tb_by_name = trial_balance_from_journal(args.journal, as_of)

    # 2. Map QBO name -> target_code, then aggregate by code
    name_to_code = load_qbo_coa_mapping(args.config_set)
    if not name_to_code:
        print("ERROR: No qbo_coa_mapping found")
        return 1

    # By code: (debit_total, credit_total) from journal
    by_code_qbo: dict[str, tuple[Decimal, Decimal]] = defaultdict(lambda: (Decimal("0"), Decimal("0")))
    for qbo_name, (dr, cr) in tb_by_name.items():
        code = name_to_code.get(qbo_name)
        if not code:
            continue
        prev_dr, prev_cr = by_code_qbo[code]
        by_code_qbo[code] = (prev_dr + dr, prev_cr + cr)

    # 3. DB trial balance by account code
    db_url = os.environ.get("DATABASE_URL", "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test")
    from finance_kernel.db.engine import get_session, init_engine_from_url
    from finance_kernel.selectors.ledger_selector import LedgerSelector
    from finance_kernel.models.account import Account, NormalBalance

    try:
        init_engine_from_url(db_url)
    except Exception as e:
        print(f"ERROR: DB: {e}")
        return 1
    session = get_session()
    try:
        selector = LedgerSelector(session)
        tb_rows = selector.trial_balance(as_of_date=as_of)
        account_ids = [r.account_id for r in tb_rows]
        accounts = {a.id: (a.code, a.normal_balance) for a in session.query(Account).filter(Account.id.in_(account_ids))}
        by_code_db: dict[str, tuple[Decimal, Decimal, str]] = {}
        for r in tb_rows:
            code = r.account_code
            _, nb = accounts.get(r.account_id, (code, NormalBalance.DEBIT))
            nb_str = nb.value if hasattr(nb, "value") else str(nb)
            # One row per account; sum in case of multiple currencies
            if code not in by_code_db:
                by_code_db[code] = (r.debit_total, r.credit_total, nb_str)
            else:
                prev_dr, prev_cr, _ = by_code_db[code]
                by_code_db[code] = (prev_dr + r.debit_total, prev_cr + r.credit_total, nb_str)
    finally:
        session.close()

    # 4. Build QBO natural balance per code; DB natural balance per code
    all_codes = sorted(set(by_code_qbo) | set(by_code_db))
    lines = [
        "",
        "Balance sheet line-by-line comparison (as-of " + as_of.isoformat() + ")",
        "QBO = from qbo_journal_Journal.json + qbo_coa_mapping.  DB = from database report.",
        "",
        f"{'Code':<6} {'QBO Balance':>18} {'DB Balance':>18} {'Diff':>14}  Match",
        "-" * 70,
    ]
    mismatches = []
    for code in all_codes:
        qbo_dr, qbo_cr = by_code_qbo.get(code, (Decimal("0"), Decimal("0")))
        qbo_nat = natural_balance_from_code(qbo_dr, qbo_cr, code)
        if code in by_code_db:
            db_dr, db_cr, nb_str = by_code_db[code]
            # Use same rule as report: debit-normal -> dr-cr, credit-normal -> cr-dr
            db_nat = (db_dr - db_cr) if (nb_str or "debit").lower() == "debit" else (db_cr - db_dr)
        else:
            db_nat = Decimal("0")
        diff = db_nat - qbo_nat
        match = "OK" if diff == Decimal("0") else "DIFF"
        if diff != Decimal("0"):
            mismatches.append((code, qbo_nat, db_nat, diff))
        lines.append(f"{code:<6} {qbo_nat:>17,.2f}  {db_nat:>17,.2f}  {diff:>+13,.2f}  {match}")

    lines.append("-" * 70)
    total_qbo_assets = sum(natural_balance_from_code(by_code_qbo[c][0], by_code_qbo[c][1], c) for c in all_codes if c and c[0] == "1")
    total_db_assets = Decimal("0")
    for c in by_code_db:
        if not c or c[0] != "1":
            continue
        db_dr, db_cr, nb_str = by_code_db[c]
        total_db_assets += (db_dr - db_cr) if (nb_str or "debit").lower() == "debit" else (db_cr - db_dr)
    lines.append("")
    lines.append("Current Assets (code 10xx, 11xx, ...) subtotal:")
    lines.append(f"  QBO (from journal): {total_qbo_assets:,.2f}")
    lines.append(f"  DB:                  {total_db_assets:,.2f}")
    lines.append("")
    if mismatches:
        lines.append("Mismatches (code, QBO, DB, diff):")
        for code, qbo_nat, db_nat, diff in mismatches[:30]:
            lines.append(f"  {code}  QBO={qbo_nat:,.2f}  DB={db_nat:,.2f}  diff={diff:+,.2f}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
