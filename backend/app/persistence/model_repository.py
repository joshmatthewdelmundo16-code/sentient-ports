"""ModelRepository — D2."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import Model
from backend.app.persistence.exceptions import NotFoundError


class ModelRepository(BaseRepository[Model]):
    model_class = Model

    def get_by_name(self, name: str, owner: str) -> Model:
        """Look up by (owner, name) stable identity; raise NotFoundError if absent."""
        obj = (
            self._db.query(Model)
            .filter(Model.name == name, Model.owner == owner)
            .first()
        )
        if obj is None:
            raise NotFoundError(f"Model owner={owner!r} name={name!r} not found")
        return obj

    def list_by_status(self, status: str) -> list[Model]:
        return self._db.query(Model).filter(Model.status == status).all()
