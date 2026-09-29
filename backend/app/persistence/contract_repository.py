"""DataContractRepository — D2."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import DataContract
from backend.app.persistence.exceptions import NotFoundError


class DataContractRepository(BaseRepository[DataContract]):
    model_class = DataContract

    def get_by_dataset_and_semver(self, dataset_id: str, semver: str) -> DataContract:
        obj = (
            self._db.query(DataContract)
            .filter(
                DataContract.dataset_id == dataset_id,
                DataContract.semver == semver,
            )
            .first()
        )
        if obj is None:
            raise NotFoundError(
                f"DataContract dataset_id={dataset_id!r} semver={semver!r} not found"
            )
        return obj

    def list_by_dataset(self, dataset_id: str) -> list[DataContract]:
        return (
            self._db.query(DataContract)
            .filter(DataContract.dataset_id == dataset_id)
            .order_by(DataContract.semver)
            .all()
        )
