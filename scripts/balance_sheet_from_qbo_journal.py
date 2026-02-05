#!/usr/bin/env python3
"""
Build a balance sheet directly from QBO journal JSON (no DB).

Use this to compare:
  1. Balance sheet from raw QBO journal + accounts (this script)
  2. Balance sheet from our system (CLI R or view_reports.py after import)

If they match, import and reporting are consistent. If not, the diff points to
classification, date, or data issues.

Usage:
  python3 scripts/balance_sheet_from_qbo_journal.py \\
    --journal upload/qbo_journal_Journal.json \\
    --accounts "upload/qbo_accounts_Ironflow AI INC_Account List _2_.json" \\
    [--as-of 2025-12-31] [--out balance_sheet_from_qbo.txt]

Output: Section totals and A = L + E check. With --out, writes the same to a file.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path


# QBO account_type (from accounts JSON) -> our category for balance sheet
QBO_TYPE_TO_CATEGORY = {
    "bank": "asset",
    "accounts receivable": "asset",
    "other current asset": "asset",
    "other current assets": "asset",
    "fixed asset": "asset",
    "fixed assets": "asset",
    "other asset": "asset",
    "other assets": "asset",
    "accounts payable": "liability",
    "credit card": "liability",
    "other current liability": "liability",
    "other current liabilities": "liability",
    "long term liability": "liability",
    "long term liabilities": "liability",
    "equity": "equity",
    "income": "revenue",
    "other income": "revenue",
    "cost of goods sold": "expense",
    "expense": "expense",
    "expenses": "expense",
    "other expense": "expense",
}


def _decimal(v) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def build_account_type_map(accounts_path: Path) -> dict[str, str]:
    """account name -> category (asset | liability | equity | revenue | expense)."""
    data = load_json(accounts_path)
    rows = data.get("rows") or data.get("data") or []
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = (r.get("name") or "").strip()
        if not name:
            continue
        qbo_type = (r.get("account_type") or "").strip().lower()
        out[name] = QBO_TYPE_TO_CATEGORY.get(qbo_type, "asset")
    return out


def trial_balance_from_journal(
    journal_path: Path,
    as_of_date: datetime | None,
) -> tuple[dict[str, tuple[Decimal, Decimal]], set[str]]:
    """
    Aggregate journal lines by account name: (debit_total, credit_total).
    Returns (account_name -> (debit_total, credit_total), set of account names).
    """
    data = load_json(journal_path)
    rows = data.get("rows") or data.get("data") or []
    totals: dict[str, tuple[Decimal, Decimal]] = defaultdict(lambda: (Decimal("0"), Decimal("0")))
    for row in rows:
        if not isinstance(row, dict):
            continue
        if as_of_date is not None:
            d = parse_date(row.get("date"))
            if d is None or d.date() > as_of_date.date():
                continue
        lines = row.get("lines") or []
        for line in lines:
            if not isinstance(line, dict):
                continue
            acc = (line.get("account") or "").strip()
            if not acc:
                continue
            dr = _decimal(line.get("debit"))
            cr = _decimal(line.get("credit"))
            prev_dr, prev_cr = totals[acc]
            totals[acc] = (prev_dr + dr, prev_cr + cr)
    return dict(totals), set(totals.keys())


def natural_balance(debit_total: Decimal, credit_total: Decimal, category: str) -> Decimal:
    """Debit-normal (asset, expense): dr - cr. Credit-normal (liability, equity, revenue): cr - dr."""
    if category in ("asset", "expense"):
        return debit_total - credit_total
    return credit_total - debit_total


def run(
    journal_path: Path,
    accounts_path: Path,
    as_of_date: datetime | None,
    out_path: Path | None,
) -> None:
    account_type_map = build_account_type_map(accounts_path)
    tb, account_names = trial_balance_from_journal(journal_path, as_of_date)

    # Classify and compute natural balance per account
    sections: dict[str, list[tuple[str, Decimal]]] = {
        "current_assets": [],
        "non_current_assets": [],
        "current_liabilities": [],
        "non_current_liabilities": [],
        "equity": [],
        "revenue": [],
        "expense": [],
    }
    for name in sorted(account_names):
        dr, cr = tb[name]
        cat = account_type_map.get(name, "asset")
        bal = natural_balance(dr, cr, cat)
        if bal == Decimal("0"):
            continue
        if cat == "asset":
            sections["current_assets"].append((name, bal))  # simplified: all assets current
        elif cat == "liability":
            sections["current_liabilities"].append((name, bal))  # simplified
        elif cat == "equity":
            sections["equity"].append((name, bal))
        elif cat == "revenue":
            sections["revenue"].append((name, bal))
        elif cat == "expense":
            sections["expense"].append((name, bal))

    total_assets = sum(b for _, b in sections["current_assets"]) + sum(
        b for _, b in sections["non_current_assets"]
    )
    total_liabilities = sum(b for _, b in sections["current_liabilities"]) + sum(
        b for _, b in sections["non_current_liabilities"]
    )
    equity_from_accounts = sum(b for _, b in sections["equity"])
    net_income = sum(b for _, b in sections["revenue"]) - sum(b for _, b in sections["expense"])
    total_equity = equity_from_accounts + net_income
    total_l_and_e = total_liabilities + total_equity
    is_balanced = total_assets == total_l_and_e

    lines = [
        "Balance Sheet from QBO Journal (direct)",
        "=" * 60,
        f"Journal: {journal_path.name}",
        f"Accounts: {accounts_path.name}",
        f"As-of date: {as_of_date.date().isoformat() if as_of_date else 'all'}",
        "",
        "--- ASSETS ---",
    ]
    for name, bal in sections["current_assets"]:
        lines.append(f"  {name}: {bal:,.2f}")
    for name, bal in sections["non_current_assets"]:
        lines.append(f"  {name}: {bal:,.2f}")
    lines.append(f"  TOTAL ASSETS: {total_assets:,.2f}")
    lines.append("")
    lines.append("--- LIABILITIES ---")
    for name, bal in sections["current_liabilities"]:
        lines.append(f"  {name}: {bal:,.2f}")
    for name, bal in sections["non_current_liabilities"]:
        lines.append(f"  {name}: {bal:,.2f}")
    lines.append(f"  TOTAL LIABILITIES: {total_liabilities:,.2f}")
    lines.append("")
    lines.append("--- EQUITY ---")
    for name, bal in sections["equity"]:
        lines.append(f"  {name}: {bal:,.2f}")
    lines.append(f"  Net Income: {net_income:,.2f}")
    lines.append(f"  TOTAL EQUITY: {total_equity:,.2f}")
    lines.append("")
    lines.append(f"  TOTAL LIABILITIES AND EQUITY: {total_l_and_e:,.2f}")
    lines.append("")
    lines.append(f"  A = L + E? {is_balanced}  (Assets={total_assets:,.2f}, L+E={total_l_and_e:,.2f})")
    lines.append("")

    text = "\n".join(lines)
    print(text)
    if out_path is not None:
        out_path.write_text(text, encoding="utf-8")
        print(f"Wrote {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build balance sheet from QBO journal JSON for comparison.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--journal",
        type=Path,
        default=Path("upload/qbo_journal_Journal.json"),
        help="Path to QBO journal JSON",
    )
    parser.add_argument(
        "--accounts",
        type=Path,
        default=Path("upload/qbo_accounts_Ironflow AI INC_Account List _2_.json"),
        help="Path to QBO accounts JSON (for account type)",
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="As-of date YYYY-MM-DD (default: include all journal entries)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write report to this file",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    journal_path = args.journal if args.journal.is_absolute() else root / args.journal
    accounts_path = args.accounts if args.accounts.is_absolute() else root / args.accounts

    if not journal_path.exists():
        print(f"ERROR: Journal file not found: {journal_path}")
        return 1
    if not accounts_path.exists():
        print(f"ERROR: Accounts file not found: {accounts_path}")
        return 1

    as_of = None
    if args.as_of:
        try:
            as_of = datetime.strptime(args.as_of, "%Y-%m-%d")
        except ValueError:
            print(f"ERROR: Invalid --as-of date (use YYYY-MM-DD): {args.as_of}")
            return 1

    out_path = None
    if args.out is not None:
        out_path = args.out if args.out.is_absolute() else root / args.out

    run(journal_path, accounts_path, as_of, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
