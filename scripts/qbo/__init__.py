"""
QuickBooks Online (QBO) toolset: read standard QBO XLSX/CSV exports and write
structured JSON to the upload directory for use with the import pipeline.

Supported export types: Account List, Customer Contact List, Vendor Contact List,
Journal (Transaction List by Date), General Ledger, Trial Balance, and similar
standard QBO report exports.
"""

from scripts.qbo.detect import detect_qbo_type
from scripts.qbo.readers import read_qbo_file
from scripts.qbo.run import run_qbo_convert

__all__ = ["detect_qbo_type", "read_qbo_file", "run_qbo_convert"]
