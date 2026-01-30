"""
Reporting Module Profiles — Stub.

The reporting module is read-only: it does not post journal entries.
No accounting profiles are needed. This file exists to maintain the
6-file module convention.
"""

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.reporting.profiles")

MODULE_NAME = "reporting"

REPORTING_PROFILES: dict = {}


def register() -> None:
    """No-op: reporting module has no posting profiles."""
    logger.debug("reporting_profiles_register_noop")
