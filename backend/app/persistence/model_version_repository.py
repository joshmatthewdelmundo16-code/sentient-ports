"""ModelVersionRepository — D2."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import ModelVersion
from backend.app.persistence.exceptions import NotFoundError


class ModelVersionRepository(BaseRepository[ModelVersion]):
    model_class = ModelVersion

    def list_by_model(self, model_id: str) -> list[ModelVersion]:
        return (
            self._db.query(ModelVersion)
            .filter(ModelVersion.model_id == model_id)
            .order_by(ModelVersion.semver)
            .all()
        )

    def get_by_model_and_semver(self, model_id: str, semver: str) -> ModelVersion:
        obj = (
            self._db.query(ModelVersion)
            .filter(ModelVersion.model_id == model_id, ModelVersion.semver == semver)
            .first()
        )
        if obj is None:
            raise NotFoundError(
                f"ModelVersion model_id={model_id!r} semver={semver!r} not found"
            )
        return obj

    def get_active(self, model_id: str) -> ModelVersion | None:
        """Return the active version for a model, or None if none is active."""
        actives = self.list_active(model_id)
        return actives[0] if actives else None

    def list_active(self, model_id: str) -> list[ModelVersion]:
        return (
            self._db.query(ModelVersion)
            .filter(ModelVersion.model_id == model_id, ModelVersion.is_active.is_(True))
            .order_by(ModelVersion.created_at, ModelVersion.id)
            .all()
        )
