"""
Module: finance_kernel.selectors.base
Responsibility: Abstract base class for all read-only query selectors.  Selectors
    form the "Q" side of the CQRS-lite pattern, providing structured read access
    to financial data without mutation capability.
Architecture position: Kernel > Selectors.  May import from db/base.py and
    models/.  MUST NOT import from services/, domain/, or outer layers.
    Selectors NEVER create, modify, or delete data -- they are read-only by design.

Invariants enforced:
    - Read-only access: Selectors accept a Session from the caller but MUST NOT
      call session.add(), session.delete(), session.commit(), or session.flush().
    - DTO return convention: Selectors return frozen dataclasses or computed results,
      NOT raw ORM model instances (for clean layer separation).
    - Session ownership: Selectors do NOT create or manage their own sessions;
      the caller owns the session and its transaction scope (SL-G4 snapshot isolation).

Failure modes:
    - NoResultFound / MultipleResultsFound if selector queries expect specific
      cardinality but the data doesn't match.

Audit relevance:
    Selectors are the canonical read path for financial queries (trial balance,
    account balances, subledger balances, trace bundles).  They derive all
    results from JournalLines -- there are NO stored balances (R6 replay safety).
"""

from abc import ABC
from typing import Generic, TypeVar

from sqlalchemy.orm import Session

from finance_kernel.db.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseSelector(ABC, Generic[ModelType]):
    """
    Abstract base class for all selectors.

    Contract:
        Selectors accept a Session from the caller, perform read-only queries,
        and return DTOs or computed results.  They MUST NOT mutate any data.

    Guarantees:
        - session is stored as a public attribute for subclass query use.
        - No commit, flush, add, or delete operations are performed.

    Non-goals:
        - BaseSelector does NOT define any query methods; subclasses implement
          domain-specific queries (journal, ledger, subledger, trace).
    """

    def __init__(self, session: Session):
        """
        Initialize the selector.

        Preconditions: session is a valid, open SQLAlchemy Session.
        Postconditions: self.session is set for subclass query use.

        Args:
            session: SQLAlchemy session for database operations.
        """
        self.session = session
