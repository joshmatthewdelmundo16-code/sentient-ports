"""ResultRepository — D2."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import Result


class ResultRepository(BaseRepository[Result]):
    model_class = Result

    def list_by_run(self, run_id: str) -> list[Result]:
        return (
            self._db.query(Result)
            .filter(Result.run_id == run_id)
            .order_by(Result.created_at)
            .all()
        )

    def list_by_step(self, step_id: str) -> list[Result]:
        return (
            self._db.query(Result)
            .filter(Result.step_id == step_id)
            .order_by(Result.created_at)
            .all()
        )

    def list_by_version(self, model_version_id: str) -> list[Result]:
        return (
            self._db.query(Result)
            .filter(Result.model_version_id == model_version_id)
            .order_by(Result.created_at)
            .all()
        )

    def list_by_dataset(self, dataset_id: str) -> list[Result]:
        return (
            self._db.query(Result)
            .filter(Result.dataset_id == dataset_id)
            .order_by(Result.created_at.desc())
            .all()
        )
