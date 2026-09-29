"""DependencyRepository — D2."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import Dependency


class DependencyRepository(BaseRepository[Dependency]):
    model_class = Dependency

    def list_by_producer(self, producer_version_id: str) -> list[Dependency]:
        """All edges where this version is the producer (source)."""
        return (
            self._db.query(Dependency)
            .filter(Dependency.producer_version_id == producer_version_id)
            .all()
        )

    def list_by_consumer(self, consumer_version_id: str) -> list[Dependency]:
        """All edges where this version is the consumer (target)."""
        return (
            self._db.query(Dependency)
            .filter(Dependency.consumer_version_id == consumer_version_id)
            .order_by(Dependency.created_at, Dependency.id)
            .all()
        )

    def list_by_input_dataset(self, dataset_id: str) -> list[Dependency]:
        return (
            self._db.query(Dependency)
            .filter(Dependency.input_dataset_id == dataset_id)
            .all()
        )

    def list_by_output_dataset(self, dataset_id: str) -> list[Dependency]:
        return (
            self._db.query(Dependency)
            .filter(Dependency.output_dataset_id == dataset_id)
            .all()
        )
