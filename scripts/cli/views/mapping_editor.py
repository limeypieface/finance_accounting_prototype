"""CLI view: Define or update import mappings by uploading a file and mapping columns to target fields."""

from __future__ import annotations

import csv
from pathlib import Path

from scripts.cli import config as cli_config
from scripts.cli.mapping_schema import DEFAULT_VALIDATIONS, ENTITY_TARGETS


def _probe_csv_headers(path: Path, encoding: str = "utf-8-sig", delimiter: str = ",") -> list[str]:
    """Read first data row as headers. Returns list of column names."""
    with path.open("r", encoding=encoding, newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        row = next(reader, None)
    return list(row) if row else []


def _probe_xlsx_headers(path: Path) -> list[str]:
    """Probe XLSX file for column headers (auto-detect header row). Returns list of column names."""
    from finance_ingestion.adapters.xlsx_adapter import XlsxSourceAdapter

    adapter = XlsxSourceAdapter()
    probe = adapter.probe(path, {"auto_detect_header": True})
    return list(probe.columns) if probe.columns else []


def _custom_mappings_path(config_id: str) -> Path:
    """Path to custom import mappings YAML for the active config set."""
    return (
        cli_config.ROOT
        / "finance_config"
        / "sets"
        / config_id
        / "import_mappings"
        / "custom.yaml"
    )


def _load_existing_custom(path: Path) -> list[dict]:
    """Load existing custom.yaml import_mappings list, or return [] if missing."""
    if not path.exists():
        return []
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data or not isinstance(data, dict):
        return []
    return list(data.get("import_mappings", []))


def _save_custom(path: Path, mappings: list[dict]) -> None:
    """Write custom.yaml with the given import_mappings list."""
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {"import_mappings": mappings}
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(out, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _build_field_mapping(source: str, target: str, field_type: str, required: bool, default, transform) -> dict:
    entry = {
        "source": source,
        "target": target,
        "field_type": field_type,
        "required": required,
    }
    if default is not None:
        entry["default"] = default
    if transform:
        entry["transform"] = transform
    return entry


def show_mapping_editor(config) -> None:
    """
    Interactive flow: pick a CSV, choose entity type, map each target to a source column,
    name the mapping, then save to the config set's import_mappings/custom.yaml.
    """
    W = 70
    config_id = getattr(config, "config_id", "US-GAAP-2026-v1")
    custom_path = _custom_mappings_path(config_id)

    upload_dir = cli_config.UPLOAD_DIR
    if not upload_dir.is_dir():
        print("\n  No upload folder found. Create 'upload/' and put a CSV or XLSX there, or enter a file path.")
        return
    data_files = sorted(
        p for p in upload_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".csv", ".xlsx")
    )
    if not data_files:
        print("\n  No CSV or XLSX files in upload/. Put a file in upload/ to define a mapping from its columns.")
        return

    print()
    print("=" * W)
    print("  DEFINE IMPORT MAPPING".center(W))
    print("=" * W)
    print()
    print("  Select a file (CSV or XLSX; we'll use its column headers to build the mapping):")
    for i, p in enumerate(data_files, 1):
        print(f"    {i}. {p.name}")
    print("    0. Cancel")
    try:
        choice = input("  File number: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if choice == "0" or not choice.isdigit():
        return
    idx = int(choice)
    if idx < 1 or idx > len(data_files):
        return
    source_path = data_files[idx - 1]

    if source_path.suffix.lower() == ".xlsx":
        headers = _probe_xlsx_headers(source_path)
    else:
        headers = _probe_csv_headers(source_path)
    if not headers:
        print("  Could not read CSV headers. Check encoding and delimiter.")
        return
    print(f"\n  Detected {len(headers)} columns: {', '.join(headers[:10])}{'...' if len(headers) > 10 else ''}")

    entity_types = list(ENTITY_TARGETS.keys())
    print("\n  Entity type (what this import creates):")
    for i, et in enumerate(entity_types, 1):
        print(f"    {i}. {et}")
    print("    0. Cancel")
    try:
        et_choice = input("  Entity type number: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if et_choice == "0" or not et_choice.isdigit():
        return
    et_idx = int(et_choice)
    if et_idx < 1 or et_idx > len(entity_types):
        return
    entity_type = entity_types[et_idx - 1]
    targets = ENTITY_TARGETS[entity_type]

    # Map each target to a source column
    field_mappings = []
    print("\n  Map each target field to a source column (0 = skip this field):")
    for target_name, field_type, required, default, transform in targets:
        req_label = "required" if required else "optional"
        print(f"\n  Target: {target_name} ({field_type}, {req_label})")
        for i, col in enumerate(headers, 1):
            print(f"    {i}. {col!r}")
        print("    0. Skip")
        try:
            col_choice = input(f"  Map '{target_name}' to column: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not col_choice.isdigit():
            if required:
                print(f"  '{target_name}' is required; skipping this mapping will leave it unmapped.")
            continue
        col_num = int(col_choice)
        if col_num == 0:
            if required:
                print("  Required field cannot be skipped. Using first column.")
                col_num = 1
            else:
                continue
        if col_num < 1 or col_num > len(headers):
            continue
        source_col = headers[col_num - 1]
        field_mappings.append(
            _build_field_mapping(source_col, target_name, field_type, required, default, transform)
        )

    if not field_mappings:
        print("  No fields mapped. Aborting.")
        return

    try:
        mapping_name = input("\n  Mapping name (e.g. my_accounts, qbo_coa): ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not mapping_name:
        print("  Mapping name is required.")
        return
    # Sanitize for YAML key usage
    mapping_name = "".join(c for c in mapping_name if c.isalnum() or c in "_-").strip() or "custom_mapping"

    existing = _load_existing_custom(custom_path)
    names = {m.get("name") for m in existing if isinstance(m, dict) and m.get("name")}
    if mapping_name in names:
        try:
            overwrite = input(f"  Mapping '{mapping_name}' already exists. Overwrite? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if overwrite != "y":
            print("  Aborted.")
            return
        existing = [m for m in existing if m.get("name") != mapping_name]

    is_xlsx = source_path.suffix.lower() == ".xlsx"
    new_mapping = {
        "name": mapping_name,
        "version": 1,
        "entity_type": entity_type,
        "source_format": "xlsx" if is_xlsx else "csv",
        "source_options": (
            {"sheet": 0, "auto_detect_header": True}
            if is_xlsx
            else {"delimiter": ",", "encoding": "utf-8", "has_header": True}
        ),
        "dependency_tier": 2 if entity_type in ("vendor", "customer") else 0,
        "field_mappings": field_mappings,
        "validations": DEFAULT_VALIDATIONS.get(entity_type, []),
    }
    existing.append(new_mapping)
    _save_custom(custom_path, existing)

    print(f"\n  Saved mapping '{mapping_name}' to {custom_path}")
    print("  Use it in Import & Staging (I) by choosing this mapping when you upload a file with the same column layout.")
    print()
