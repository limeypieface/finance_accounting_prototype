#!/usr/bin/env python3
"""
Approve a configuration set by writing its canonical fingerprint to
APPROVED_FINGERPRINT.

Usage:
    python scripts/approve_config.py [config_set_directory]

If no directory is given, defaults to
finance_config/sets/US-GAAP-2026-v1/

The script:
  1. Assembles fragments from the directory
  2. Validates the assembled config
  3. Compiles to CompiledPolicyPack
  4. Writes pack.canonical_fingerprint to APPROVED_FINGERPRINT

The APPROVED_FINGERPRINT file is a separate git artifact from the
YAML fragments — changing any fragment without re-running approval
will cause get_active_config() to raise ConfigIntegrityError.
"""

import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from finance_config.assembler import assemble_from_directory
from finance_config.compiler import compile_policy_pack
from finance_config.integrity import PINFILE_NAME
from finance_config.validator import validate_configuration


def approve(fragment_dir: Path) -> str:
    """Assemble, validate, compile, and write the pin file.

    Returns the canonical fingerprint that was written.
    """
    print(f"Assembling fragments from: {fragment_dir}")
    config_set = assemble_from_directory(fragment_dir)
    print(f"  config_id: {config_set.config_id}")
    print(f"  version:   {config_set.version}")
    print(f"  status:    {config_set.status.value}")
    print(f"  checksum:  {config_set.checksum[:16]}...")

    print("Validating...")
    result = validate_configuration(config_set)
    if not result.is_valid:
        print("VALIDATION FAILED:")
        for err in result.errors:
            print(f"  ERROR: {err}")
        sys.exit(1)
    for w in result.warnings:
        print(f"  WARNING: {w}")

    print("Compiling...")
    pack = compile_policy_pack(config_set)
    fingerprint = pack.canonical_fingerprint
    print(f"  canonical_fingerprint: {fingerprint}")

    pin_path = fragment_dir / PINFILE_NAME
    pin_path.write_text(fingerprint + "\n")
    print(f"Wrote {pin_path}")
    return fingerprint


def main():
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        target = ROOT / "finance_config" / "sets" / "US-GAAP-2026-v1"

    if not target.is_dir():
        print(f"Error: directory not found: {target}", file=sys.stderr)
        sys.exit(1)

    approve(target)
    print("Done. Config is now pinned.")


if __name__ == "__main__":
    main()
