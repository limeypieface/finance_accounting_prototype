"""
Reporting Module Profiles (``finance_modules.reporting.profiles``).

Responsibility
--------------
Stub module -- the reporting module is **read-only** and does not post
journal entries.  No accounting profiles are needed.  This file exists
to maintain the standard module file convention.

Architecture position
---------------------
**Modules layer** -- no profiles required for read-only modules.

Invariants enforced
-------------------
* No profiles are registered (empty registry).

Failure modes
-------------
* None -- this is a no-op module.

Audit relevance
---------------
* Not applicable (read-only module).
"""

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.reporting.profiles")

MODULE_NAME = "reporting"

REPORTING_PROFILES: dict = {}


def register() -> None:
    """No-op: reporting module has no posting profiles."""
    logger.debug("reporting_profiles_register_noop")
