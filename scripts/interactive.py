#!/usr/bin/env python3
"""
Interactive Accounting CLI — entry point.

Post business events, view reports, import data, close periods, trace decisions.
All changes are committed immediately. This is the global CLI application
for operating the finance system interactively (not just a demo).

Usage:
    python3 scripts/interactive.py

Implementation is split into reusable modules under scripts/cli/:
  - config, data, util, setup, menu, posting, close
  - views/ (accounts, reports, trace, import_staging)
  - main (loop and dispatch)
"""

import sys
from pathlib import Path

# Project root on sys.path so "scripts.cli" and "scripts.trace_render" resolve
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
