"""
Base selector class.

Selectors are the read side of the CQRS-lite pattern.
They handle queries and return DTOs (not ORM models).
"""

from abc import ABC
from typing import Generic, TypeVar

from sqlalchemy.orm import Session

from finance_kernel.db.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseSelector(ABC, Generic[ModelType]):
    """
    Abstract base class for all selectors.

    Selectors:
    - Accept a Session from the caller
    - Perform read-only queries
    - Return DTOs or computed results (not ORM models for external use)
    """

    def __init__(self, session: Session):
        """
        Initialize the selector.

        Args:
            session: SQLAlchemy session for database operations.
        """
        self.session = session
