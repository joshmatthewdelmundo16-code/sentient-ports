"""DatasetRepository — D2."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import Dataset
from backend.app.persistence.exceptions import NotFoundError


class DatasetRepository(BaseRepository[Dataset]):
    model_class = Dataset

    def get_by_name(self, name: str) -> Dataset:
        obj = self._db.query(Dataset).filter(Dataset.name == name).first()
        if obj is None:
            raise NotFoundError(f"Dataset name={name!r} not found")
        return obj
