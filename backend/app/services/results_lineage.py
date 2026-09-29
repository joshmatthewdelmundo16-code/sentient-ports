"""Results & Lineage service — D10, made server-authoritative in D16.

The execution service calls record_step_results() for every successful step.
Which datasets receive a Result is derived from the version's declared outputs
(FederationService), never from client input.

LineageEdge = one input the step consumed → one Result the step produced:
  run_id / step_id          the GraphRun and consuming step
  source_result_id          upstream Result (same run, or the run that produced the
                            persisted value the step read)
  source_dataset_id         dataset the input was read from
  source_change_event_id    ChangeEvent that set that value when it came from
                            outside this run (external write, seed, earlier run)
  target_result_id / target_dataset_id   the produced Result
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from backend.app.persistence.database import LineageEdge, Result
from backend.app.persistence.exceptions import NotFoundError
from backend.app.persistence.lineage_repository import LineageRepository
from backend.app.persistence.result_repository import ResultRepository


@dataclass(frozen=True)
class InputProvenance:
    dataset_id: str
    source_result_id: str | None
    source_change_event_id: str | None


# ---------------------------------------------------------------------------
# Service-level errors
# ---------------------------------------------------------------------------

class ResultNotFoundError(Exception):
    """No Result with the given ID exists."""


class ResultPersistenceError(Exception):
    """Unexpected failure while persisting a Result or LineageEdge."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _value_hash(value_json_str: str) -> str:
    return hashlib.sha256(value_json_str.encode()).hexdigest()


def _value_numeric(outputs: dict[str, Any]) -> float | None:
    """Return the first numeric scalar if there is exactly one; else None."""
    numerics = [v for v in outputs.values() if isinstance(v, (int, float))]
    return float(numerics[0]) if len(numerics) == 1 else None


# ---------------------------------------------------------------------------
# Results & Lineage service
# ---------------------------------------------------------------------------

class ResultsLineageService:
    """
    Records Result and LineageEdge entities after successful model execution.

    Constructor parameters:
        db — open SQLAlchemy session; caller owns commit/rollback.
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._results = ResultRepository(db)
        self._lineage = LineageRepository(db)

    # ------------------------------------------------------------------
    # Step result + lineage recording (called by the execution service)
    # ------------------------------------------------------------------

    def record_step_results(
        self,
        *,
        run_id: str,
        step_id: str,
        version_id: str,
        records: dict[str, tuple[str, dict[str, Any]]],
        input_snapshot: str | None,
        provenance: list[InputProvenance],
        scenario_id: str | None = None,
    ) -> list[Result]:
        """
        Persist one Result per output dataset and one LineageEdge per (input, result).

        records maps dataset_id → (normalized_json, record). Raises ResultPersistenceError.
        """
        created: list[Result] = []
        try:
            for dataset_id, (normalized, record) in records.items():
                result = Result(
                    run_id=run_id,
                    step_id=step_id,
                    dataset_id=dataset_id,
                    model_version_id=version_id,
                    scenario_id=scenario_id,
                    value_json=normalized,
                    value_numeric=_value_numeric(record),
                    value_hash=_value_hash(normalized),
                    input_snapshot=input_snapshot,
                )
                created.append(self._results.add(result))

            for result in created:
                for prov in provenance:
                    self._lineage.add(LineageEdge(
                        run_id=run_id,
                        step_id=step_id,
                        source_result_id=prov.source_result_id,
                        target_result_id=result.id,
                        source_dataset_id=prov.dataset_id,
                        target_dataset_id=result.dataset_id,
                        source_change_event_id=prov.source_change_event_id,
                    ))
        except Exception as exc:
            raise ResultPersistenceError(
                f"Failed to persist results/lineage for version_id={version_id!r} "
                f"in run {run_id!r}: {exc}"
            ) from exc
        return created

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_result(self, result_id: str) -> Result:
        """Return a Result by primary-key ID."""
        try:
            return self._results.get(result_id)
        except NotFoundError as exc:
            raise ResultNotFoundError(str(exc)) from exc

    def list_results_for_run(self, run_id: str) -> list[Result]:
        """All results created under one ExecutionRun, ordered by creation time."""
        return self._results.list_by_run(run_id)

    def list_results_for_step(self, step_id: str) -> list[Result]:
        """All results associated with one ExecutionStep."""
        return self._results.list_by_step(step_id)

    def list_lineage_for_run(self, run_id: str) -> list[LineageEdge]:
        """All LineageEdge records for one ExecutionRun."""
        return self._lineage.list_by_run(run_id)

    def list_lineage_from_result(self, source_result_id: str) -> list[LineageEdge]:
        """LineageEdges sourced at a given Result (forward traversal)."""
        return self._lineage.list_by_source(source_result_id)

    def list_results_for_version(self, model_version_id: str) -> list[Result]:
        """All results whose model_version_id matches, ordered by creation time."""
        return self._results.list_by_version(model_version_id)

    def list_lineage_to_result(self, target_result_id: str) -> list[LineageEdge]:
        """LineageEdges targeting a given Result (backward traversal)."""
        return self._lineage.list_by_target(target_result_id)
