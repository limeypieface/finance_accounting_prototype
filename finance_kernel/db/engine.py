"""
SQLite engine and session management.

Provides thread-safe session handling for the finance kernel.
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

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
    Initialize the SQLAlchemy engine.

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


def create_tables() -> None:
    """
    Create all tables defined in the models.

    Must be called after all models are imported.
    """
    from finance_kernel.db.base import Base

    engine = get_engine()
    Base.metadata.create_all(engine)


def drop_tables() -> None:
    """
    Drop all tables. Use with caution - primarily for testing.
    """
    from finance_kernel.db.base import Base

    engine = get_engine()
    Base.metadata.drop_all(engine)
