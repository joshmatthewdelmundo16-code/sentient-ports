"""ChangeEventRepository — D2."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import ChangeEvent


class ChangeEventRepository(BaseRepository[ChangeEvent]):
    model_class = ChangeEvent

    def list_recent(self, limit: int = 20) -> list[ChangeEvent]:
        return (
            self._db.query(ChangeEvent)
            .order_by(ChangeEvent.created_at.desc())
            .limit(limit)
            .all()
        )

    def list_by_dataset(self, dataset_id: str) -> list[ChangeEvent]:
        return (
            self._db.query(ChangeEvent)
            .filter(ChangeEvent.dataset_id == dataset_id)
            .order_by(ChangeEvent.created_at.desc())
            .all()
        )

    def latest_value_event(self, dataset_id: str) -> ChangeEvent | None:
        """Most recent event that wrote a value to the dataset (excludes model triggers)."""
        return (
            self._db.query(ChangeEvent)
            .filter(
                ChangeEvent.dataset_id == dataset_id,
                ChangeEvent.new_hash.isnot(None),
            )
            .order_by(ChangeEvent.created_at.desc(), ChangeEvent.id.desc())
            .first()
        )

    def list_by_run(self, run_id: str) -> list[ChangeEvent]:
        return (
            self._db.query(ChangeEvent)
            .filter(ChangeEvent.run_id == run_id)
            .order_by(ChangeEvent.created_at)
            .all()
        )
