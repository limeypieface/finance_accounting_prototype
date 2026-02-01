"""
Module ORM Registry (``finance_modules._orm_registry``).

Responsibility
--------------
Ensure all module-level SQLAlchemy ORM models are imported so that
``Base.metadata`` contains their table definitions before
``create_tables()`` or ``Base.metadata.create_all()`` is called.

Architecture position
---------------------
**Modules layer** -- utility.  Imports only from sibling ``finance_modules``
packages.  MUST NOT be imported by ``finance_kernel``.

Usage
-----
Called automatically by ``finance_kernel.db.engine.create_tables()`` and
by ``tests/conftest.py`` during test setup.
"""


def import_all_orm_models() -> None:
    """Import every ``finance_modules.*.orm`` module to register ORM models.

    Each ``orm.py`` file defines SQLAlchemy ORM models that inherit from
    ``finance_kernel.db.base.TrackedBase`` (or ``Base``).  Importing them
    ensures they appear in ``Base.metadata.tables`` so that
    ``create_all()`` can create the corresponding database tables.

    This function is idempotent -- repeated calls are harmless.
    """
    # fmt: off
    import finance_modules.ap.orm             # noqa: F401
    import finance_modules.ar.orm             # noqa: F401
    import finance_modules.assets.orm         # noqa: F401
    import finance_modules.budget.orm         # noqa: F401
    import finance_modules.cash.orm           # noqa: F401
    import finance_modules.contracts.orm      # noqa: F401
    import finance_modules.expense.orm        # noqa: F401
    import finance_modules.gl.orm             # noqa: F401
    import finance_modules.intercompany.orm   # noqa: F401
    import finance_modules.inventory.orm      # noqa: F401
    import finance_modules.lease.orm          # noqa: F401
    import finance_modules.payroll.orm        # noqa: F401
    import finance_modules.procurement.orm    # noqa: F401
    import finance_modules.project.orm        # noqa: F401
    import finance_modules.revenue.orm        # noqa: F401
    import finance_modules.tax.orm            # noqa: F401
    import finance_modules.wip.orm            # noqa: F401
    import finance_services.orm               # noqa: F401
    # fmt: on
