"""Thin base repository — D2.

Provides add/get/list/delete so per-entity repositories avoid repeating
the same four patterns. Keep this small; do not grow it into a framework.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.persistence.exceptions import DuplicateError, NotFoundError, PersistenceError

T = TypeVar("T")


class BaseRepository(Generic[T]):
    """Persistence boundary for one ORM entity type."""

    model_class: type  # subclass sets this

    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Shared primitives
    # ------------------------------------------------------------------

    def add(self, entity: T) -> T:
        """Persist a new entity; raise DuplicateError on constraint violation."""
        try:
            self._db.add(entity)
            self._db.flush()
            return entity
        except IntegrityError as exc:
            self._db.rollback()
            raise DuplicateError(str(exc.orig)) from exc
        except Exception as exc:
            self._db.rollback()
            raise PersistenceError(str(exc)) from exc

    def get(self, entity_id: str) -> T:
        """Return entity by primary key; raise NotFoundError if absent."""
        obj = self._db.get(self.model_class, entity_id)
        if obj is None:
            raise NotFoundError(
                f"{self.model_class.__name__} with id={entity_id!r} not found"
            )
        return obj  # type: ignore[return-value]

    def list(self, limit: int = 100, offset: int = 0) -> list[T]:
        """Return up to `limit` entities ordered by primary key."""
        return (
            self._db.query(self.model_class)
            .order_by(self.model_class.id)
            .offset(offset)
            .limit(limit)
            .all()
        )

    def delete(self, entity: T) -> None:
        """Delete an entity; raises PersistenceError on failure."""
        try:
            self._db.delete(entity)
            self._db.flush()
        except Exception as exc:
            self._db.rollback()
            raise PersistenceError(str(exc)) from exc
