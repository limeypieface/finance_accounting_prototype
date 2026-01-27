"""
Database engine and session management.

Supports both SQLite (for development/testing) and PostgreSQL (for production/concurrency testing).
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

# Module-level engine and session factory
_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _enable_foreign_keys(dbapi_conn, connection_record):
    """Enable foreign key support in SQLite."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_engine(
    db_path: str | Path | None = None,
    echo: bool = False,
) -> Engine:
    """
    Initialize the SQLAlchemy engine with SQLite.

    Args:
        db_path: Path to SQLite database file. If None, uses in-memory database.
        echo: If True, log all SQL statements.

    Returns:
        SQLAlchemy Engine instance.
    """
    global _engine, _SessionFactory

    if db_path is None:
        # In-memory database for testing
        url = "sqlite:///:memory:"
    else:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path}"

    _engine = create_engine(
        url,
        echo=echo,
        # SQLite-specific: check_same_thread=False for multi-threaded access
        connect_args={"check_same_thread": False},
    )

    # Enable foreign keys for SQLite
    event.listen(_engine, "connect", _enable_foreign_keys)

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)

    return _engine


def init_engine_from_url(
    database_url: str,
    echo: bool = False,
    pool_size: int = 20,
    max_overflow: int = 10,
    pool_pre_ping: bool = True,
) -> Engine:
    """
    Initialize the SQLAlchemy engine from a database URL.

    Supports PostgreSQL and other databases via URL.

    Args:
        database_url: Database connection URL (e.g., postgresql://user:pass@host/db)
        echo: If True, log all SQL statements.
        pool_size: Number of connections to keep in the pool.
        max_overflow: Max connections beyond pool_size.
        pool_pre_ping: If True, test connections before use (handles stale connections).

    Returns:
        SQLAlchemy Engine instance.
    """
    global _engine, _SessionFactory

    # Detect database type from URL
    is_sqlite = database_url.startswith("sqlite")

    if is_sqlite:
        # SQLite doesn't support connection pooling the same way
        _engine = create_engine(
            database_url,
            echo=echo,
            connect_args={"check_same_thread": False},
        )
        event.listen(_engine, "connect", _enable_foreign_keys)
    else:
        # PostgreSQL and other databases - use connection pooling
        _engine = create_engine(
            database_url,
            echo=echo,
            poolclass=QueuePool,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=pool_pre_ping,
            # PostgreSQL-specific: Better handling of concurrent transactions
            isolation_level="READ COMMITTED",
        )

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)

    return _engine


def get_engine() -> Engine:
    """
    Get the current engine instance.

    Raises:
        RuntimeError: If engine has not been initialized.
    """
    if _engine is None:
        raise RuntimeError("Engine not initialized. Call init_engine() first.")
    return _engine


def get_session() -> Session:
    """
    Get a new session instance.

    Raises:
        RuntimeError: If engine has not been initialized.
    """
    if _SessionFactory is None:
        raise RuntimeError("Engine not initialized. Call init_engine() first.")
    return _SessionFactory()


def get_session_factory() -> sessionmaker[Session]:
    """
    Get the session factory for creating sessions.

    Useful for multi-threaded scenarios where each thread needs its own session.

    Raises:
        RuntimeError: If engine has not been initialized.
    """
    if _SessionFactory is None:
        raise RuntimeError("Engine not initialized. Call init_engine() first.")
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
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_tables(install_triggers: bool = True) -> None:
    """
    Create all tables defined in the models.

    Must be called after all models are imported.

    Args:
        install_triggers: If True and using PostgreSQL, install database-level
                         immutability triggers for R10 compliance.
    """
    from finance_kernel.db.base import Base

    engine = get_engine()
    Base.metadata.create_all(engine)

    # Install PostgreSQL triggers for defense-in-depth
    if install_triggers and engine.dialect.name == "postgresql":
        from finance_kernel.db.triggers import install_immutability_triggers
        install_immutability_triggers(engine)


def drop_tables() -> None:
    """
    Drop all tables. Use with caution - primarily for testing.
    """
    from finance_kernel.db.base import Base

    engine = get_engine()

    # Uninstall triggers first (if PostgreSQL)
    if engine.dialect.name == "postgresql":
        from finance_kernel.db.triggers import uninstall_immutability_triggers
        uninstall_immutability_triggers(engine)

    Base.metadata.drop_all(engine)


def reset_engine() -> None:
    """
    Reset the engine and session factory.

    Useful for test cleanup when switching between database backends.
    """
    global _engine, _SessionFactory

    if _engine is not None:
        _engine.dispose()
        _engine = None

    _SessionFactory = None


def is_postgres() -> bool:
    """Check if the current engine is PostgreSQL."""
    if _engine is None:
        return False
    return _engine.dialect.name == "postgresql"


def is_sqlite() -> bool:
    """Check if the current engine is SQLite."""
    if _engine is None:
        return False
    return _engine.dialect.name == "sqlite"
