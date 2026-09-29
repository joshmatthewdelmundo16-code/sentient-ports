"""ModelIOBindingRepository — D16."""

from __future__ import annotations

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import ModelIOBinding


class ModelIOBindingRepository(BaseRepository[ModelIOBinding]):
    model_class = ModelIOBinding

    def list_by_version(self, model_version_id: str, direction: str) -> list[ModelIOBinding]:
        return (
            self._db.query(ModelIOBinding)
            .filter(
                ModelIOBinding.model_version_id == model_version_id,
                ModelIOBinding.direction == direction,
            )
            .order_by(ModelIOBinding.created_at, ModelIOBinding.id)
            .all()
        )

    def list_by_dataset(self, dataset_id: str, direction: str) -> list[ModelIOBinding]:
        return (
            self._db.query(ModelIOBinding)
            .filter(
                ModelIOBinding.dataset_id == dataset_id,
                ModelIOBinding.direction == direction,
            )
            .all()
        )

    def find(self, model_version_id: str, dataset_id: str, direction: str) -> ModelIOBinding | None:
        return (
            self._db.query(ModelIOBinding)
            .filter(
                ModelIOBinding.model_version_id == model_version_id,
                ModelIOBinding.dataset_id == dataset_id,
                ModelIOBinding.direction == direction,
            )
            .first()
        )

    def list_all(self) -> list[ModelIOBinding]:
        return self._db.query(ModelIOBinding).all()
