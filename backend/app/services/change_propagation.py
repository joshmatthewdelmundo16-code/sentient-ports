"""Change Propagation service — D9, dataset-driven in D16.

Two kinds of change trigger a propagation GraphRun:

  1. Dataset change (apply_dataset_change): a new value is written to a source dataset
     through DatasetValueService (normalize → hash → no-op if unchanged → ChangeEvent with
     old/new value). Affected = every model consuming the dataset + all downstream.
  2. Model trigger (record_change): a model version must re-execute (e.g. new logic).
     ChangeEvent.source_type="model_trigger", source_version_id set.
     Affected = the source version + all downstream.

The affected set is ordered by D5 and executed as ONE GraphRun by D8. ChangeEvent.run_id
links the event to that run and is the duplicate-propagation guard.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from sqlalchemy.orm import Session

from backend.app.persistence.change_event_repository import ChangeEventRepository
from backend.app.persistence.database import ChangeEvent
from backend.app.persistence.exceptions import NotFoundError
from backend.app.persistence.execution_run_repository import ExecutionRunRepository
from backend.app.persistence.model_version_repository import ModelVersionRepository
from backend.app.services.adapter_registry import AdapterRegistry
from backend.app.services.dataset_values import DatasetValueService
from backend.app.services.federation import FederationService
from backend.app.services.orchestration import (
    GraphExecutionOutcome,
    GraphOrchestrationService,
    OrchestrationCycleError,
)


# ---------------------------------------------------------------------------
# Service-level errors
# ---------------------------------------------------------------------------

class PropagationSourceNotFoundError(Exception):
    """The source model version referenced by the change does not exist."""


class PropagationEventNotFoundError(Exception):
    """No ChangeEvent with the given ID exists."""


class PropagationCycleError(Exception):
    """The dependency graph contains a cycle; propagation is not possible."""

    def __init__(self, message: str, cycle_nodes: list[str]) -> None:
        super().__init__(message)
        self.cycle_nodes = cycle_nodes


class PropagationError(Exception):
    """General propagation failure."""


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

class PropagationResult(NamedTuple):
    change_event_id: str
    source_version_id: str | None      # model trigger source (None for dataset changes)
    affected_version_ids: list[str]    # all nodes considered
    execution_order: list[str]         # topological order actually planned
    graph_outcome: GraphExecutionOutcome | None
    success: bool
    error: str | None
    already_processed: bool
    source_dataset_id: str | None = None
    run_id: str | None = None


class DatasetChangeResult(NamedTuple):
    dataset_id: str
    changed: bool
    change_event_id: str | None
    old_value: Any
    new_value: Any
    propagation: PropagationResult | None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ChangePropagationService:
    def __init__(self, db: Session, adapter_registry: AdapterRegistry) -> None:
        self._db = db
        self._events = ChangeEventRepository(db)
        self._runs = ExecutionRunRepository(db)
        self._versions = ModelVersionRepository(db)
        self._federation = FederationService(db)
        self._datasets = DatasetValueService(db)
        self._orchestrator = GraphOrchestrationService(db, adapter_registry)

    # ------------------------------------------------------------------
    # Dataset-driven change (primary path)
    # ------------------------------------------------------------------

    def apply_dataset_change(
        self,
        dataset_id: str,
        value: Any,
        *,
        triggered_by: str | None = None,
        source_ref: str | None = None,
    ) -> DatasetChangeResult:
        """Write a source dataset value; if it changed, propagate to consumers and downstream."""
        write = self._datasets.write_external(
            dataset_id, value, source_ref=source_ref, triggered_by=triggered_by,
        )
        if not write.changed:
            # Unchanged value: no new event. If the value that is already current was never
            # successfully propagated (seeded, or its run failed), retry that propagation.
            current = self._datasets.current_value_event(dataset_id)
            propagation = None
            if current is not None and not self._is_processed(current) \
                    and self._federation.consumers_of(dataset_id):
                propagation = self.propagate(current.id)
            return DatasetChangeResult(
                dataset_id, False, None, write.old_value, write.new_value, propagation,
            )
        propagation = self.propagate(write.event.id)
        return DatasetChangeResult(
            dataset_id, True, write.event.id, write.old_value, write.new_value, propagation,
        )

    # ------------------------------------------------------------------
    # Model-trigger change
    # ------------------------------------------------------------------

    def record_change(
        self,
        *,
        dataset_id: str,
        source_version_id: str,
        new_value_json: str | None = None,
        old_value_json: str | None = None,
        triggered_by: str | None = None,
    ) -> ChangeEvent:
        """
        Record that `source_version_id` must re-execute (and everything downstream).

        Does not modify any dataset value; call propagate(event.id, ...) afterward.
        Raises PropagationSourceNotFoundError if the version does not exist.
        """
        try:
            self._versions.get(source_version_id)
        except NotFoundError as exc:
            raise PropagationSourceNotFoundError(
                f"Source model version_id={source_version_id!r} does not exist."
            ) from exc
        event = ChangeEvent(
            dataset_id=dataset_id,
            source_type="model_trigger",
            source_version_id=source_version_id,
            triggered_by=triggered_by,
            new_value_json=new_value_json,
            old_value_json=old_value_json,
        )
        return self._events.add(event)

    def _is_processed(self, event: ChangeEvent) -> bool:
        """An event is processed once a run it triggered has succeeded (failed runs may retry)."""
        if event.run_id is None:
            return False
        try:
            return self._runs.get(event.run_id).status == "succeeded"
        except NotFoundError:
            return False

    # ------------------------------------------------------------------
    # Propagate a persisted ChangeEvent
    # ------------------------------------------------------------------

    def propagate(
        self,
        change_event_id: str,
        input_data: dict[str, Any] | None = None,
    ) -> PropagationResult:
        try:
            event: ChangeEvent = self._events.get(change_event_id)
        except NotFoundError as exc:
            raise PropagationEventNotFoundError(
                f"ChangeEvent id={change_event_id!r} not found."
            ) from exc

        if self._is_processed(event):
            return PropagationResult(
                change_event_id=event.id,
                source_version_id=event.source_version_id,
                affected_version_ids=[],
                execution_order=[],
                graph_outcome=None,
                success=True,
                error=None,
                already_processed=True,
                source_dataset_id=event.dataset_id,
                run_id=event.run_id,
            )

        if event.source_type == "model_trigger":
            sources = [event.source_version_id] if event.source_version_id else []
            if not sources:
                raise PropagationSourceNotFoundError(
                    f"ChangeEvent {event.id!r} is a model trigger without a source version."
                )
            try:
                self._versions.get(sources[0])
            except NotFoundError as exc:
                raise PropagationSourceNotFoundError(
                    f"Source model version_id={sources[0]!r} does not exist."
                ) from exc
        else:
            sources = sorted(self._federation.consumers_of(event.dataset_id))

        try:
            ordered = self._orchestrator.plan_downstream(sources)
        except OrchestrationCycleError as exc:
            raise PropagationCycleError(
                f"Cannot propagate: cycle detected involving nodes {exc.cycle_nodes}.",
                cycle_nodes=exc.cycle_nodes,
            ) from exc

        graph_outcome = self._orchestrator.execute_ordered_subgraph(
            ordered,
            input_data or {},
            triggered_by=event.triggered_by or f"change_event:{event.id}",
            trigger_type="dataset_change" if event.source_type != "model_trigger" else "model_trigger",
        )
        if graph_outcome.run_id is not None:
            event.run_id = graph_outcome.run_id
            self._db.flush()

        return PropagationResult(
            change_event_id=event.id,
            source_version_id=event.source_version_id,
            affected_version_ids=sorted(ordered),
            execution_order=ordered,
            graph_outcome=graph_outcome,
            success=graph_outcome.success,
            error=graph_outcome.error,
            already_processed=False,
            source_dataset_id=event.dataset_id,
            run_id=graph_outcome.run_id,
        )
