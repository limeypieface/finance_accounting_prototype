#!/usr/bin/env python3
"""
Recommend which config COA best matches a QBO Account List export.

Usage:
  python3 scripts/recommend_coa.py --input "upload/qbo_accounts_Ironflow AI INC_Account List _2_.json"
  python3 scripts/recommend_coa.py --input "upload/qbo_accounts_Ironflow AI INC_Account List _2_.json" --config-sets-dir finance_config/sets

Reads the QBO accounts JSON (from run_qbo_convert), extracts input CoA, loads all
config set COA options, scores each by coverage of input account types, and
prints the recommended config set (and all scores).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts.qbo.coa_config import load_config_coa_options
from scripts.qbo.coa_extract import extract_input_coa
from scripts.qbo.coa_recommend import recommend_coa


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recommend which config COA best matches a QBO Account List JSON file."
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        type=Path,
        help='Path to QBO accounts JSON (e.g. upload/qbo_accounts_Ironflow AI INC_Account List _2_.json)',
    )
    parser.add_argument(
        "--config-sets-dir",
        type=Path,
        default=None,
        help="Path to config sets directory (default: finance_config/sets under project root)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only print the top recommended config_id",
    )
    args = parser.parse_args()

    input_path = args.input
    if not input_path.is_file():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        input_coa = extract_input_coa(input_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not input_coa:
        print("Error: no accounts found in input file (empty or invalid rows).", file=sys.stderr)
        return 1

    config_options = load_config_coa_options(args.config_sets_dir)
    if not config_options:
        print("Error: no config COA options found (check --config-sets-dir).", file=sys.stderr)
        return 1

    recommendations = recommend_coa(input_coa, config_options)
    if not recommendations:
        print("Error: no recommendations produced.", file=sys.stderr)
        return 1

    if args.quiet:
        print(recommendations[0].config_id)
        return 0

    # Summary
    unique_types = {r.account_type for r in input_coa if r.account_type}
    print(f"Input CoA: {len(input_coa)} accounts, {len(unique_types)} unique account types")
    print(f"Config options: {len(config_options)}")
    print()
    print("Recommended config COA (by coverage of input account types):")
    print("-" * 50)
    for i, rec in enumerate(recommendations, start=1):
        marker = " <-- recommended" if i == 1 else ""
        print(f"  {i}. {rec.config_id}  score={rec.score:.2f}{marker}")
    print()
    print("Use the top config_id when mapping your QBO accounts to the system COA (Phase 2).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
