"""SQLAlchemy engine initialization, session factory, and transactional scope."""

import atexit
from collections.abc import Generator
from contextlib import contextmanager

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
    """Initialize the SQLAlchemy engine from a PostgreSQL database URL."""
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
    """Get the current engine instance."""
    if _engine is None:
        raise RuntimeError("Engine not initialized. Call init_engine_from_url() first.")
    return _engine


def get_session() -> Session:
    """Get a new session instance."""
    if _SessionFactory is None:
        raise RuntimeError("Engine not initialized. Call init_engine_from_url() first.")
    return _SessionFactory()


def get_session_factory() -> sessionmaker[Session]:
    """Get the session factory for creating sessions."""
    if _SessionFactory is None:
        raise RuntimeError("Engine not initialized. Call init_engine_from_url() first.")
    return _SessionFactory


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional scope: commits on success, rolls back on exception."""
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


def create_tables(install_triggers: bool = True, *, kernel_only: bool = False) -> None:
    """Create all tables registered in Base.metadata.

    In a full-system context (default), module ORM models MUST be imported
    into Base.metadata before calling -- use ``create_all_tables()`` from
    ``finance_modules._orm_registry``.  Pass ``kernel_only=True`` to bypass
    the guard for kernel-only unit tests.
    """
    import time

    from finance_kernel.db.base import Base

    engine = get_engine()

    # Guard: prevent incomplete schema in full-system contexts.
    if not kernel_only:
        table_count = len(Base.metadata.tables)
        if table_count < 25:
            raise RuntimeError(
                f"create_tables() found only {table_count} table(s) in "
                f"Base.metadata — module ORM models are not imported and "
                f"the schema would be incomplete.  Use create_all_tables() "
                f"from finance_modules._orm_registry for the full system "
                f"schema, or pass kernel_only=True if you intentionally "
                f"want only kernel tables."
            )

    # Dispose existing connections to avoid stale metadata
    engine.dispose()

    Base.metadata.create_all(engine)

    # Install triggers for defense-in-depth (retry on deadlock).
    if install_triggers:
        from sqlalchemy.exc import OperationalError

        from finance_kernel.db.triggers import install_immutability_triggers

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
    """Drop all tables. Use with caution -- primarily for testing."""
    from finance_kernel.db.base import Base
    from finance_kernel.db.triggers import uninstall_immutability_triggers

    engine = get_engine()
    uninstall_immutability_triggers(engine)
    Base.metadata.drop_all(engine)


def reset_engine() -> None:
    """Reset the engine and session factory. Useful for test cleanup."""
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
