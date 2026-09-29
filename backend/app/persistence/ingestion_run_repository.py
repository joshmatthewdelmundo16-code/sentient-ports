"""IngestionRunRepository — D18."""

from __future__ import annotations

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import IngestionRun


class IngestionRunRepository(BaseRepository[IngestionRun]):
    model_class = IngestionRun

    def list_recent(self, limit: int = 50) -> list[IngestionRun]:
        return (
            self._db.query(IngestionRun)
            .order_by(IngestionRun.created_at.desc(), IngestionRun.id.desc())
            .limit(limit)
            .all()
        )

    def list_by_hash(self, content_sha256: str) -> list[IngestionRun]:
        return (
            self._db.query(IngestionRun)
            .filter(IngestionRun.content_sha256 == content_sha256)
            .order_by(IngestionRun.created_at.desc())
            .all()
        )
