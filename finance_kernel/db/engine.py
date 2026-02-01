"""
Module: finance_kernel.db.engine
Responsibility: SQLAlchemy engine initialization, session factory management,
    and transactional scope utilities.  This is the single point of database
    connection configuration for the entire system.
Architecture position: Kernel > DB.  May import from db/base.py and db/triggers.py.
    MUST NOT import from models/, services/, selectors/, domain/, or outer layers
    (except for create_tables/drop_tables which import models and triggers).

Invariants enforced:
    - PostgreSQL is the ONLY supported backend (isolation_level, triggers,
      JSONB, SELECT ... FOR UPDATE all require PostgreSQL 15+).
    - Session isolation level is READ COMMITTED (default for PostgreSQL),
      with explicit row-level locking (FOR UPDATE) used where stronger
      isolation is needed (R8, R9, R12).
    - Connection pooling via QueuePool with pre-ping to handle stale connections.

Failure modes:
    - RuntimeError if get_engine/get_session/get_session_factory called before
      init_engine_from_url().
    - OperationalError on deadlock during trigger installation (retried up to 3x).
    - Connection pool exhaustion if pool_size + max_overflow is exceeded.

Audit relevance:
    All database transactions flow through sessions created by this module.
    The session_scope() context manager ensures atomic commit-or-rollback
    semantics, which is foundational for the L5 atomicity invariant.
"""

import atexit
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from finance_kernel.logging_config import configure_logging, get_logger

logger = get_logger("db.engine")

# Module-level engine and session factory
_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def init_engine_from_url(
    database_url: str,
    echo: bool = False,
    pool_size: int = 20,
    max_overflow: int = 10,
    pool_pre_ping: bool = True,
    pool_timeout: int = 30,
    pool_recycle: int = 1800,
) -> Engine:
    """
    Initialize the SQLAlchemy engine from a PostgreSQL database URL.

    Preconditions: database_url is a valid PostgreSQL connection string.
        Must not have been called before without a reset_engine() in between
        (idempotent -- second call overwrites the first).
    Postconditions: Module-level _engine and _SessionFactory are initialized.
        All subsequent get_engine/get_session calls use this engine.

    Args:
        database_url: PostgreSQL connection URL (e.g., postgresql://user:pass@host/db)
        echo: If True, log all SQL statements.
        pool_size: Number of connections to keep in the pool.
        max_overflow: Max connections beyond pool_size.
        pool_pre_ping: If True, test connections before use (handles stale connections).
        pool_timeout: Seconds to wait for a connection from the pool before giving up.
        pool_recycle: Seconds after which a connection is automatically recycled.

    Returns:
        SQLAlchemy Engine instance.
    """
    global _engine, _SessionFactory

    _engine = create_engine(
        database_url,
        echo=echo,
        poolclass=QueuePool,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=pool_pre_ping,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        isolation_level="READ COMMITTED",
    )

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)

    configure_logging()
    logger.info(
        "engine_initialized",
        extra={
            "dialect": "postgresql",
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "echo": echo,
        },
    )

    return _engine


def get_engine() -> Engine:
    """
    Get the current engine instance.

    Raises:
        RuntimeError: If engine has not been initialized.
    """
    if _engine is None:
        raise RuntimeError("Engine not initialized. Call init_engine_from_url() first.")
    return _engine


def get_session() -> Session:
    """
    Get a new session instance.

    Raises:
        RuntimeError: If engine has not been initialized.
    """
    if _SessionFactory is None:
        raise RuntimeError("Engine not initialized. Call init_engine_from_url() first.")
    return _SessionFactory()


def get_session_factory() -> sessionmaker[Session]:
    """
    Get the session factory for creating sessions.

    Useful for multi-threaded scenarios where each thread needs its own session.

    Raises:
        RuntimeError: If engine has not been initialized.
    """
    if _SessionFactory is None:
        raise RuntimeError("Engine not initialized. Call init_engine_from_url() first.")
    return _SessionFactory


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    Provide a transactional scope around a series of operations.

    Preconditions: Engine must be initialized via init_engine_from_url().
    Postconditions: On normal exit, session is committed and closed.
        On exception, session is rolled back and closed.  The exception
        is re-raised to the caller.

    Raises:
        RuntimeError: If engine is not initialized.

    Usage:
        with session_scope() as session:
            session.add(entity)
            # Commits on successful exit, rolls back on exception
    """
    session = get_session()
    logger.debug("transaction_started")
    try:
        yield session
        session.commit()
        logger.debug("transaction_committed")
    except Exception:
        session.rollback()
        logger.warning("transaction_rolled_back", exc_info=True)
        raise
    finally:
        session.close()


def create_tables(install_triggers: bool = True) -> None:
    """
    Create all tables defined in the models and optionally install triggers.

    Preconditions: Engine must be initialized via init_engine_from_url().
        All ORM models must be imported before calling (so Base.metadata
        contains all table definitions).
    Postconditions: All tables exist in the database.  If install_triggers=True,
        all R10 immutability triggers are installed (with deadlock retry).

    Args:
        install_triggers: If True, install database-level immutability triggers
                         for R10 compliance.

    Raises:
        RuntimeError: If engine is not initialized.
        OperationalError: If trigger installation fails after 3 retries.
    """
    import time
    from finance_kernel.db.base import Base

    engine = get_engine()

    # Dispose existing connections to avoid stale metadata
    engine.dispose()

    # Import all module-level ORM models so Base.metadata discovers their tables.
    from finance_modules._orm_registry import import_all_orm_models
    import_all_orm_models()

    Base.metadata.create_all(engine)

    # Install triggers for defense-in-depth.
    # Retry on deadlock: stale connections from a prior run may still hold
    # locks when _kill_orphaned_connections() terminates them right before
    # this function runs.  PostgreSQL needs a moment to fully reclaim the
    # locks released by terminated backends.
    if install_triggers:
        from finance_kernel.db.triggers import install_immutability_triggers
        from sqlalchemy.exc import OperationalError

        max_retries = 3
        for attempt in range(max_retries):
            try:
                install_immutability_triggers(engine)
                break
            except OperationalError as exc:
                if "deadlock" in str(exc).lower() and attempt < max_retries - 1:
                    logger.warning(
                        "trigger_install_deadlock_retry",
                        extra={"attempt": attempt + 1, "max_retries": max_retries},
                    )
                    engine.dispose()
                    time.sleep(0.5 * (attempt + 1))
                else:
                    raise


def drop_tables() -> None:
    """
    Drop all tables. Use with caution - primarily for testing.
    """
    from finance_kernel.db.base import Base
    from finance_kernel.db.triggers import uninstall_immutability_triggers

    engine = get_engine()
    uninstall_immutability_triggers(engine)
    Base.metadata.drop_all(engine)


def reset_engine() -> None:
    """
    Reset the engine and session factory.

    Useful for test cleanup.
    """
    global _engine, _SessionFactory

    if _engine is not None:
        _engine.dispose()
        _engine = None

    _SessionFactory = None


def _atexit_dispose():
    """Dispose the engine on process exit to release all pooled connections."""
    global _engine
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:
            pass


atexit.register(_atexit_dispose)


def is_postgres() -> bool:
    """Check if the current engine is PostgreSQL."""
    if _engine is None:
        return False
    return _engine.dialect.name == "postgresql"
