"""ExecutionRunRepository — D2."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import ExecutionRun
from backend.app.persistence.exceptions import NotFoundError, PersistenceError


class ExecutionRunRepository(BaseRepository[ExecutionRun]):
    model_class = ExecutionRun

    def list_recent(self, limit: int = 20) -> list[ExecutionRun]:
        """Return most-recently-created runs first."""
        return (
            self._db.query(ExecutionRun)
            .order_by(ExecutionRun.created_at.desc())
            .limit(limit)
            .all()
        )

    def update_status(self, run_id: str, status: str) -> ExecutionRun:
        """Update the status field; return the updated run."""
        run = self.get(run_id)
        try:
            run.status = status
            self._db.flush()
            return run
        except Exception as exc:
            self._db.rollback()
            raise PersistenceError(str(exc)) from exc
