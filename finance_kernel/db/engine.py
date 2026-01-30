"""
Database engine and session management.

PostgreSQL is the only supported backend.
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
    Create all tables defined in the models.

    Must be called after all models are imported.

    Args:
        install_triggers: If True, install database-level immutability triggers
                         for R10 compliance.
    """
    import time
    from finance_kernel.db.base import Base

    engine = get_engine()

    # Dispose existing connections to avoid stale metadata
    engine.dispose()

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
