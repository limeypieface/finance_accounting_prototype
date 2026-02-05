"""
Module ORM Registry (``finance_modules._orm_registry``).

Responsibility
--------------
Ensure all module-level SQLAlchemy ORM models are imported so that
``Base.metadata`` contains their table definitions before tables are
created.  ``create_tables()`` in the kernel is now **guarded** — it
refuses to run unless module models have been imported (or
``kernel_only=True`` is passed).  This makes ``create_all_tables()``
the only safe way to get a complete schema.

Also provides ``create_all_tables()`` -- the production-safe entry point
that registers all module ORM models and then creates every table.

Architecture position
---------------------
**Modules layer** -- utility.  Imports from sibling ``finance_modules``
packages and from ``finance_kernel.db.engine`` (allowed: modules → kernel).
MUST NOT be imported by ``finance_kernel`` or ``finance_services``.

Usage
-----
Scripts, entrypoints, and ``tests/conftest.py`` all call
``create_all_tables()`` — one orchestration function for every consumer.
"""


def import_all_orm_models() -> None:
    """Import kernel models and every ``finance_modules.*.orm`` module to register ORM models.

    Kernel models (Contract, FiscalPeriod, Account, etc.) must be registered first
    so ``Base.metadata`` includes their tables before ``create_all()`` runs.
    Module ORM modules may reference kernel tables (e.g. FK to contracts.id).

    This function is idempotent -- repeated calls are harmless.
    """
    # Kernel tables first (contracts, fiscal_periods, accounts, etc.)
    import finance_kernel.models  # noqa: F401
    # fmt: off
    import finance_modules.ap.orm  # noqa: F401
    import finance_modules.ar.orm  # noqa: F401
    import finance_modules.assets.orm  # noqa: F401
    import finance_modules.budget.orm  # noqa: F401
    import finance_modules.cash.orm  # noqa: F401
    import finance_modules.contracts.orm  # noqa: F401
    import finance_modules.expense.orm  # noqa: F401
    import finance_modules.gl.orm  # noqa: F401
    import finance_modules.intercompany.orm  # noqa: F401
    import finance_modules.inventory.orm  # noqa: F401
    import finance_modules.lease.orm  # noqa: F401
    import finance_modules.payroll.orm  # noqa: F401
    import finance_modules.procurement.orm  # noqa: F401
    import finance_modules.project.orm  # noqa: F401
    import finance_modules.revenue.orm  # noqa: F401
    import finance_modules.tax.orm  # noqa: F401
    import finance_modules.wip.orm  # noqa: F401
    import finance_services.orm  # noqa: F401
    import finance_ingestion.models  # noqa: F401  # ERP ingestion staging tables
    import finance_batch.models  # noqa: F401  # Batch processing tables
    import finance_modules.payroll.dcaa_orm  # noqa: F401  # DCAA timesheet tables
    import finance_modules.expense.dcaa_orm  # noqa: F401  # DCAA expense tables
    import finance_modules.contracts.rate_orm  # noqa: F401  # DCAA rate control tables
    # fmt: on


def create_all_tables(install_triggers: bool = True) -> None:
    """Create kernel + all module ORM tables, then optionally install triggers.

    This is the canonical entry point for any script or entrypoint that
    needs the full schema (kernel tables + 106 module tables).

    Preconditions:
        Engine must be initialized via ``init_engine_from_url()``.
    Postconditions:
        All kernel and module tables exist.  If *install_triggers* is True,
        R10 immutability triggers are installed.
    """
    from finance_kernel.db.engine import create_tables

    import_all_orm_models()
    create_tables(install_triggers=install_triggers)
