#!/usr/bin/env python3
"""
Convert QuickBooks Online XLSX/CSV exports in a folder to structured JSON in the upload directory.

Usage:
  python3 scripts/run_qbo_convert.py [source_dir] [-o output_dir] [--one-file-per-type]

See scripts/qbo/README.md for supported report types and output format.
"""

import sys
from pathlib import Path

# Add project root so "scripts" package is importable when run as scripts/run_qbo_convert.py
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts.qbo.run import main

if __name__ == "__main__":
    raise SystemExit(main())
