"""CLI utilities: formatting, logging mute/restore."""

import logging
from decimal import Decimal


def fmt_amount(v) -> str:
    """Format amount for display (e.g. $1,234)."""
    d = Decimal(str(v))
    return f"${d:,.0f}"


def enable_quiet_logging():
    """Mute console handlers so CLI output stays clean. Returns list to pass to restore_logging."""
    fk_logger = logging.getLogger("finance_kernel")
    muted = []
    for h in fk_logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            muted.append((h, h.level))
            h.setLevel(logging.CRITICAL + 1)
    return muted


def restore_logging(muted):
    """Restore muted handlers after a quiet-logging section."""
    for h, orig_level in muted:
        h.setLevel(orig_level)
