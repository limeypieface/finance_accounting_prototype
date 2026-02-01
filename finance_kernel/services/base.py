"""
BaseService -- abstract base for all kernel services.

Responsibility:
    Provides the common constructor and session-handling contract for
    every service in the kernel layer.  All concrete services inherit
    from BaseService, receiving a SQLAlchemy ``Session`` that they use
    via ``session.flush()`` -- never ``session.commit()``.

Architecture position:
    Kernel > Services -- imperative shell infrastructure.
    Every service in ``finance_kernel/services/`` that performs write
    operations extends this class.

Invariants enforced:
    R7  -- Transaction boundaries: services flush within the caller's
           transaction and never commit or rollback themselves.  The
           caller (ModulePostingService, PostingOrchestrator, or test
           harness) owns commit/rollback.

Failure modes:
    - If a subclass violates the flush-only contract by calling
      ``session.commit()``, the atomicity guarantee of multi-step
      pipelines (L5, P11) is broken.

Audit relevance:
    BaseService itself emits no audit events, but every concrete
    subclass that mutates state is expected to log significant
    operations via the structured logging infrastructure.
"""

from abc import ABC
from typing import Generic, TypeVar

from sqlalchemy.orm import Session

from finance_kernel.db.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseService(ABC, Generic[ModelType]):
    """
    Abstract base class for all kernel services.

    Contract:
        Accepts a SQLAlchemy ``Session`` from the caller and uses
        ``session.flush()`` to persist changes within the active
        transaction.

    Guarantees:
        - R7: The service never calls ``session.commit()`` or
          ``session.rollback()`` -- the caller controls transaction
          boundaries, enabling atomic multi-step operations.

    Non-goals:
        - Does NOT manage transaction lifecycle (commit/rollback).
        - Does NOT provide query-only (read) methods -- those belong
          in ``finance_kernel/selectors/``.
    """

    def __init__(self, session: Session):
        """
        Initialize the service.

        Preconditions:
            - ``session`` is a valid, open SQLAlchemy session.

        Args:
            session: SQLAlchemy session for database operations.
        """
        self.session = session
