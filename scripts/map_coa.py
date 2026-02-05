#!/usr/bin/env python3
"""
Map QBO accounts to system COA with recommendations.

Uses the chosen config (e.g. US-GAAP-2026-v1) to suggest a target account code
for each QBO account based on account type. Prints a table and optionally
writes an editable YAML (edit target_code and target_name to map or create new). Upload: create accounts first, then journals.

Usage:
  python3 scripts/map_coa.py --input "upload/qbo_accounts_Ironflow AI INC_Account List _2_.json" --config US-GAAP-2026-v1
  python3 scripts/map_coa.py --input "upload/qbo_accounts_Ironflow AI INC_Account List _2_.json" --config US-GAAP-2026-v1 --output qbo_coa_mapping.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts.qbo.coa_config import get_config_set_dir, load_config_coa_options, load_named_accounts
from scripts.qbo.coa_extract import extract_input_coa
from scripts.qbo.coa_map import recommend_account_mapping, mapping_to_yaml


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Map QBO accounts to system COA with recommendations (suggested target code per account)."
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        type=Path,
        help='Path to QBO accounts JSON (e.g. upload/qbo_accounts_Ironflow AI INC_Account List _2_.json)',
    )
    parser.add_argument(
        "--config",
        "-c",
        required=True,
        help="Config COA to map to (e.g. US-GAAP-2026-v1). Use recommend_coa first to pick one.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Write editable mapping YAML to this path (edit target_code to override or TBD).",
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
        help="Only write output file; no table printed.",
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
        print("Error: no accounts found in input file.", file=sys.stderr)
        return 1

    config_options = load_config_coa_options(args.config_sets_dir)
    chosen = next((o for o in config_options if o.config_id == args.config), None)
    if not chosen:
        print(
            f"Error: config '{args.config}' not found. Available: {[o.config_id for o in config_options]}",
            file=sys.stderr,
        )
        return 1

    named_accounts: dict[str, str] = {}
    set_dir = get_config_set_dir(args.config, args.config_sets_dir)
    if set_dir:
        named_accounts = load_named_accounts(set_dir)
    recommendations = recommend_account_mapping(
        input_coa, chosen, named_accounts=named_accounts if named_accounts else None
    )
    yaml_content = mapping_to_yaml(recommendations, args.config)

    if not args.quiet:
        existing_codes = {code for _, code in chosen.role_to_code}
        print(f"Config: {args.config}")
        print(f"Input accounts: {len(recommendations)}")
        print()
        print("QBO account (name / type)     →  role/code or (new)         →  target_code  target_name")
        print("-" * 85)
        for rec in recommendations:
            name_short = (rec.input_name[:26] + "..") if len(rec.input_name) > 28 else rec.input_name
            if rec.recommended_code:
                rec_label = f"{rec.recommended_role or '—'} / {rec.recommended_code} (map)"
            else:
                rec_label = f"new: {rec.suggested_new_code or rec.target_code}"
            action = "map" if rec.target_code in existing_codes else "create"
            tname = (rec.target_name[:20] + "..") if len(rec.target_name) > 22 else rec.target_name
            print(f"  {name_short:<28} {rec.input_type:<10}  →  {rec_label:<28}  →  {rec.target_code:<8} {tname} [{action}]")
        print()
        if args.output:
            print(f"Mapping written to: {args.output}")
            print("Edit target_code and target_name to map to existing or create new. Upload: create accounts first, then journals.")
        else:
            print("Run with --output qbo_coa_mapping.yaml to save an editable mapping file.")

    if args.output:
        args.output.write_text(yaml_content, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
