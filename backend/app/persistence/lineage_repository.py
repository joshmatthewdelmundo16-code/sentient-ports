"""LineageRepository — D2."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import LineageEdge


class LineageRepository(BaseRepository[LineageEdge]):
    model_class = LineageEdge

    def list_by_run(self, run_id: str) -> list[LineageEdge]:
        return (
            self._db.query(LineageEdge)
            .filter(LineageEdge.run_id == run_id)
            .order_by(LineageEdge.created_at)
            .all()
        )

    def list_by_source(self, source_result_id: str) -> list[LineageEdge]:
        return (
            self._db.query(LineageEdge)
            .filter(LineageEdge.source_result_id == source_result_id)
            .all()
        )

    def list_by_target(self, target_result_id: str) -> list[LineageEdge]:
        return (
            self._db.query(LineageEdge)
            .filter(LineageEdge.target_result_id == target_result_id)
            .all()
        )
