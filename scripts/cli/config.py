"""CLI configuration: database URL, entity, dates, paths."""

import os
from datetime import date
from pathlib import Path
from uuid import UUID

# Project root (parent of scripts/)
ROOT = Path(__file__).resolve().parent.parent.parent

DB_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"
ENTITY = "Acme Manufacturing Co."

# Fiscal year for reports. Set FINANCE_CLI_FY_YEAR=2025 for Ironflow (reset_db_ironflow uses FY2025).
_fy_year = int(os.environ.get("FINANCE_CLI_FY_YEAR", "2026"))
FY_START = date(_fy_year, 1, 1)
FY_END = date(_fy_year, 12, 31)
EFFECTIVE = date(_fy_year, 6, 15)
COA_UUID_NS = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

# Upload folder for Import & Staging (I): put CSV/JSON here
UPLOAD_DIR = ROOT / "upload"
