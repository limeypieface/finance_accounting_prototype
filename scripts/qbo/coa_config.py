"""
Load config COA options from finance_config sets.

Discovers config set directories (each with root.yaml + chart_of_accounts.yaml),
reads config_id from root and role names from chart_of_accounts, and returns
a list of (config_id, set of role names) for use in CoA recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from finance_config.loader import load_yaml_file, parse_role_binding


@dataclass(frozen=True)
class ConfigCoAOption:
    """A single config set's COA: config_id, role names, and role → account_code for mapping."""

    config_id: str
    roles: frozenset[str]
    role_to_code: frozenset[tuple[str, str]]  # (role, account_code) for first binding per role


def _default_sets_dir() -> Path:
    """Default path to config sets (project root / finance_config / sets)."""
    # Assume we're under project root; find finance_config/sets
    cur = Path(__file__).resolve()
    for _ in range(6):
        sets_dir = cur / "finance_config" / "sets"
        if sets_dir.is_dir():
            return sets_dir
        cur = cur.parent
    return Path("finance_config/sets")


def get_config_set_dir(config_id: str, config_sets_dir: Path | None = None) -> Path | None:
    """Return the directory path for a config set by config_id, or None if not found."""
    base = Path(config_sets_dir or _default_sets_dir())
    if not base.is_dir():
        return None
    for subdir in base.iterdir():
        if not subdir.is_dir():
            continue
        root_path = subdir / "root.yaml"
        if not root_path.exists():
            continue
        try:
            root_data = load_yaml_file(root_path)
            cid = root_data.get("config_id") or subdir.name
            if isinstance(cid, str) and cid == config_id:
                return subdir
        except Exception:
            continue
    return None


def load_named_accounts(config_set_dir: Path) -> dict[str, str]:
    """
    Load name → account_code from accounts_ironflow.yaml (or coa_accounts.yaml) in the set dir.

    Returns a dict mapping account name to code so map_coa can match QBO accounts 1:1.
    """
    for filename in ("accounts_ironflow.yaml", "coa_accounts.yaml"):
        path = config_set_dir / filename
        if not path.exists():
            continue
        try:
            data = load_yaml_file(path)
            accounts = data.get("accounts") or []
            out: dict[str, str] = {}
            for entry in accounts:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                code = entry.get("code")
                if name is not None and code is not None:
                    out[str(name).strip()] = str(code).strip()
            return out
        except Exception:
            continue
    return {}


def load_config_coa_options(config_sets_dir: Path | None = None) -> list[ConfigCoAOption]:
    """
    Load all config COA options from config set directories.

    Each subdirectory of config_sets_dir that contains root.yaml and
    chart_of_accounts.yaml is treated as a config set. Returns a list of
    ConfigCoAOption (config_id, frozenset of role names).
    """
    base = Path(config_sets_dir or _default_sets_dir())
    if not base.is_dir():
        return []
    options: list[ConfigCoAOption] = []
    for subdir in sorted(base.iterdir()):
        if not subdir.is_dir():
            continue
        root_path = subdir / "root.yaml"
        coa_path = subdir / "chart_of_accounts.yaml"
        if not root_path.exists() or not coa_path.exists():
            continue
        try:
            root_data = load_yaml_file(root_path)
            config_id = root_data.get("config_id") or subdir.name
            if not isinstance(config_id, str):
                config_id = subdir.name
            coa_data = load_yaml_file(coa_path)
            bindings_raw = coa_data.get("role_bindings") or []
            roles: set[str] = set()
            role_to_code_list: list[tuple[str, str]] = []
            for rb in bindings_raw:
                if not isinstance(rb, dict):
                    continue
                try:
                    binding = parse_role_binding(rb)
                    roles.add(binding.role)
                    role_to_code_list.append((binding.role, binding.account_code))
                except (KeyError, TypeError, ValueError):
                    continue
            options.append(
                ConfigCoAOption(
                    config_id=config_id,
                    roles=frozenset(roles),
                    role_to_code=frozenset(role_to_code_list),
                )
            )
        except Exception:
            continue
    return options
