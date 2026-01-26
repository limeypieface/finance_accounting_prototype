"""
Base service class.

Services are the write side of the CQRS-lite pattern.
They handle state-changing operations and enforce business rules.
"""

from abc import ABC
from typing import Generic, TypeVar

from sqlalchemy.orm import Session

from finance_kernel.db.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseService(ABC, Generic[ModelType]):
    """
    Abstract base class for all services.

    Services:
    - Accept a Session from the caller
    - Use session.flush() to persist changes within the transaction
    - Do NOT call session.commit() - the caller controls transaction boundaries
    - This enables atomic multi-step operations
    """

    def __init__(self, session: Session):
        """
        Initialize the service.

        Args:
            session: SQLAlchemy session for database operations.
        """
        self.session = session
