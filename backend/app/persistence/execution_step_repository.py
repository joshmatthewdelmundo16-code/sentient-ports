"""ExecutionStepRepository — D2."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import ExecutionStep
from backend.app.persistence.exceptions import PersistenceError


class ExecutionStepRepository(BaseRepository[ExecutionStep]):
    model_class = ExecutionStep

    def list_by_run(self, run_id: str) -> list[ExecutionStep]:
        """Steps for a run, ordered by step_order."""
        return (
            self._db.query(ExecutionStep)
            .filter(ExecutionStep.run_id == run_id)
            .order_by(ExecutionStep.step_order)
            .all()
        )

    def update_status(self, step_id: str, status: str) -> ExecutionStep:
        step = self.get(step_id)
        try:
            step.status = status
            self._db.flush()
            return step
        except Exception as exc:
            self._db.rollback()
            raise PersistenceError(str(exc)) from exc
