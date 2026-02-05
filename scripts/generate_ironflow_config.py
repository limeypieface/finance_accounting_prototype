#!/usr/bin/env python3
"""
Generate a US-GAAP-2026-IRONFLOW-AI config set from QBO accounts JSON.

Uses the same mapping logic as map_coa (distinct account codes in standard
numbering) so the Ironflow config has exactly the accounts from QBO with
assigned codes. Outputs:
  - root.yaml, chart_of_accounts.yaml, accounts_ironflow.yaml, ledgers.yaml
  - import_mappings/qbo_coa_mapping.yaml — QBO → system mapping so accounts
    map to original (one row per QBO account, target_code/target_name = our
    named accounts). Use for journal import and verification.

When you run map_coa with --config US-GAAP-2026-IRONFLOW-AI, every QBO
account maps 1:1 to the config.

Usage:
  python3 scripts/generate_ironflow_config.py --input "upload/qbo_accounts_Ironflow AI INC_Account List _2_.json"
  python3 scripts/generate_ironflow_config.py --input "upload/qbo_accounts_*.json" --output-dir finance_config/sets/US-GAAP-2026-IRONFLOW-AI
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts.qbo.coa_config import load_config_coa_options
from scripts.qbo.coa_extract import extract_input_coa
from scripts.qbo.coa_map import recommend_account_mapping


CONFIG_ID = "US-GAAP-2026-IRONFLOW-AI"


def _safe_role(code: str, name: str) -> str:
    """Produce a valid role name: ACCT_<code> or slug from name, safe for YAML."""
    code_clean = re.sub(r"[^A-Za-z0-9]", "_", str(code)).strip("_") or "UNK"
    return f"ACCT_{code_clean}"


def _yaml_str(s: str) -> str:
    if not s:
        return "''"
    if any(c in s for c in ":[]{}#&*!|>\"'%\n"):
        return repr(s)
    return f"'{s}'"


def write_root(out_dir: Path) -> None:
    root = """# Ironflow AI — chart derived from QBO Account List; accounts map 1:1 to original.
config_id: US-GAAP-2026-IRONFLOW-AI
version: 1
status: published
predecessor: US-GAAP-2026-v1
scope:
  legal_entity: '*'
  jurisdiction: US
  regulatory_regime: GAAP
  currency: USD
  effective_from: '2024-01-01'
  effective_to: null
capabilities:
  inventory: true
  ap: true
  ar: true
  cash: true
  expense: true
  gl: true
  payroll: true
  procurement: true
  tax: true
  wip: true
  assets: true
  contracts: true
  dcaa: false
  ifrs: false
  multicurrency: false
precedence_rules:
- name: specificity_first
  description: More specific where-clause wins over generic
  rule_type: specificity
"""
    (out_dir / "root.yaml").write_text(root, encoding="utf-8")


def write_chart_of_accounts(out_dir: Path, recommendations: list) -> None:
    lines = [
        "# Ironflow AI — one role per QBO account so map_coa maps 1:1 to original.",
        "role_bindings:",
    ]
    for rec in recommendations:
        role = _safe_role(rec.target_code, rec.target_name)
        code = rec.target_code
        lines.append(f"- role: {role}")
        lines.append(f"  account_code: {_yaml_str(code)}")
        lines.append("  ledger: GL")
    (out_dir / "chart_of_accounts.yaml").write_text("\n".join(lines), encoding="utf-8")


def write_accounts_ironflow(out_dir: Path, recommendations: list) -> None:
    """Named accounts list (code, name, type) for reference and Phase 3 create-accounts."""
    lines = [
        "# Ironflow AI — named accounts from QBO; matches original. Use for create-accounts then journal upload.",
        "accounts:",
    ]
    for rec in recommendations:
        lines.append(f"  - code: {_yaml_str(rec.target_code)}")
        lines.append(f"    name: {_yaml_str(rec.target_name)}")
        lines.append(f"    account_type: {_yaml_str(rec.input_type)}")
    (out_dir / "accounts_ironflow.yaml").write_text("\n".join(lines), encoding="utf-8")


def write_qbo_coa_mapping(out_dir: Path, recommendations: list) -> None:
    """Write import_mappings/qbo_coa_mapping.yaml so QBO accounts map 1:1 to our named accounts."""
    import_dir = out_dir / "import_mappings"
    import_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Ironflow AI — QBO → system COA mapping. One row per QBO account; target_code/target_name",
        "# match our named accounts so journals map to original. Generated from accounts_ironflow.",
        f"config_id: {CONFIG_ID}",
        "mappings:",
    ]
    for i, rec in enumerate(recommendations, start=1):
        lines.append(f"  - input_name: {_yaml_str(rec.input_name)}")
        lines.append(f"    input_code: {_yaml_str(rec.input_code or '')}")
        lines.append(f"    input_type: {_yaml_str(rec.input_type)}")
        lines.append(f"    import_row: {i}")
        lines.append(f"    target_code: {_yaml_str(rec.target_code)}")
        lines.append(f"    target_name: {_yaml_str(rec.target_name)}")
    (import_dir / "qbo_coa_mapping.yaml").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Ironflow AI config set from QBO accounts JSON.")
    parser.add_argument("--input", "-i", required=True, type=Path, help="Path to QBO accounts JSON")
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=None,
        help=f"Output config set directory (default: finance_config/sets/{CONFIG_ID})",
    )
    parser.add_argument("--config-sets-dir", type=Path, default=None, help="Path to config sets (for loading v1)")
    args = parser.parse_args()

    input_path = args.input
    if not input_path.is_file():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        input_coa = extract_input_coa(input_path)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not input_coa:
        print("Error: no accounts in input.", file=sys.stderr)
        return 1

    config_options = load_config_coa_options(args.config_sets_dir)
    v1 = next((o for o in config_options if o.config_id == "US-GAAP-2026-v1"), None)
    if not v1:
        print("Error: US-GAAP-2026-v1 not found.", file=sys.stderr)
        return 1

    recommendations = recommend_account_mapping(input_coa, v1)
    out_dir = args.output_dir or (_project_root / "finance_config" / "sets" / CONFIG_ID)
    out_dir.mkdir(parents=True, exist_ok=True)

    write_root(out_dir)
    write_chart_of_accounts(out_dir, recommendations)
    write_accounts_ironflow(out_dir, recommendations)
    write_qbo_coa_mapping(out_dir, recommendations)

    # Copy ledgers so the set loads like v1
    v1_dir = _project_root / "finance_config" / "sets" / "US-GAAP-2026-v1"
    ledgers_src = v1_dir / "ledgers.yaml"
    if ledgers_src.exists():
        (out_dir / "ledgers.yaml").write_text(ledgers_src.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"Created {out_dir}")
    print(f"  root.yaml, chart_of_accounts.yaml ({len(recommendations)} accounts), accounts_ironflow.yaml, ledgers.yaml")
    print(f"  import_mappings/qbo_coa_mapping.yaml (QBO → named accounts, map to original)")
    print(f"Run: python3 scripts/map_coa.py --input {args.input} --config {CONFIG_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
