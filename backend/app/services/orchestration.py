"""Graph Orchestration service — D8, rebuilt around the GraphRun in D16.

One graph execution == one ExecutionRun (run_kind="graph") with one ExecutionStep per
planned model version:

    plan (D5 ordering)  →  validate every node before anything runs:
                           exists · executable (active version, model not retired/deprecated)
                           · adapter resolvable · direct input_data accepted by some node
    GraphRun(status=running) + steps(status=pending)
    for each step: ModelExecutionService.run_step (dataset-aware inputs, contracts, results)
    first failure → remaining steps "skipped", run "failed", nothing published
    all succeeded → staged outputs published to Dataset.current_value (ChangeEvents),
                    run "succeeded"

The dependency graph (D5) remains the only source of execution ordering.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, NamedTuple, Sequence

from sqlalchemy.orm import Session

from backend.app.execution.executor import Executor, is_terminal
from backend.app.persistence.database import ChangeEvent, ExecutionRun, ExecutionStep
from backend.app.persistence.exceptions import NotFoundError
from backend.app.persistence.execution_run_repository import ExecutionRunRepository
from backend.app.persistence.execution_step_repository import ExecutionStepRepository
from backend.app.persistence.model_version_repository import ModelVersionRepository
from backend.app.services.adapter_registry import AdapterRegistry
from backend.app.services.dataset_values import DatasetValueService
from backend.app.services.dependency_graph import (
    CycleDetectedError,
    DependencyGraphService,
)
from backend.app.services.execution import (
    ExecutionOutcome,
    ModelExecutionService,
    RunContext,
)
from backend.app.services.federation import FederationService, VersionNotExecutableError

__all__ = [
    "GraphOrchestrationService",
    "GraphExecutionOutcome",
    "OrchestrationTargetNotFoundError",
    "OrchestrationCycleError",
    "OrchestrationError",
    "DirectInputNotAcceptedError",
    "VersionNotExecutableError",
]

IN_PROCESS_EXECUTOR = "in_process"


# ---------------------------------------------------------------------------
# Service-level errors
# ---------------------------------------------------------------------------

class OrchestrationTargetNotFoundError(Exception):
    """The requested target model version does not exist."""


class OrchestrationCycleError(Exception):
    """The dependency graph contains a cycle; execution is not possible."""

    def __init__(self, message: str, cycle_nodes: list[str]) -> None:
        super().__init__(message)
        self.cycle_nodes = cycle_nodes


class OrchestrationError(Exception):
    """General orchestration failure (unexpected)."""


class DirectInputNotAcceptedError(Exception):
    """input_data was supplied but every planned model reads declared datasets instead."""


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class GraphExecutionOutcome(NamedTuple):
    success: bool
    execution_order: list[str]                    # version_ids planned (in order)
    step_outcomes: dict[str, ExecutionOutcome]    # executed steps only
    first_failure_version_id: str | None
    error: str | None
    run_id: str | None = None                     # the GraphRun
    status: str | None = None                     # run status
    result_ids: tuple[str, ...] = ()
    published_change_event_ids: tuple[str, ...] = ()
    external_ref: str | None = None               # external executor run id (Airflow dag_run_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Orchestration service
# ---------------------------------------------------------------------------

class GraphOrchestrationService:
    def __init__(self, db: Session, adapter_registry: AdapterRegistry) -> None:
        self._db = db
        self._versions = ModelVersionRepository(db)
        self._runs = ExecutionRunRepository(db)
        self._steps = ExecutionStepRepository(db)
        self._graph_svc = DependencyGraphService(db)
        self._exec_svc = ModelExecutionService(db, adapter_registry)
        self._federation = FederationService(db)
        self._datasets = DatasetValueService(db)

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def _full_order(self) -> list[str]:
        try:
            return self._graph_svc.topological_order()
        except CycleDetectedError as exc:
            raise OrchestrationCycleError(
                f"Cannot execute graph: cycle detected involving nodes {exc.cycle_nodes}.",
                cycle_nodes=exc.cycle_nodes,
            ) from exc

    def plan_for_target(self, target_version_id: str) -> list[str]:
        """Target plus all transitive upstream versions, in dependency order."""
        try:
            self._versions.get(target_version_id)
        except NotFoundError as exc:
            raise OrchestrationTargetNotFoundError(
                f"Target model version_id={target_version_id!r} not found."
            ) from exc
        full_order = self._full_order()
        nodes = {target_version_id} | set(self._graph_svc.get_upstream(target_version_id))
        order = [v for v in full_order if v in nodes]
        if target_version_id not in order:
            order.append(target_version_id)
        return order

    def plan_downstream(self, source_version_ids: Sequence[str]) -> list[str]:
        """Sources plus all transitive downstream versions, in dependency order."""
        full_order = self._full_order()
        affected: set[str] = set(source_version_ids)
        for vid in source_version_ids:
            affected |= set(self._graph_svc.get_downstream(vid))
        order = [v for v in full_order if v in affected]
        missing = [v for v in source_version_ids if v not in order]
        return missing + order

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    def execute_graph(
        self,
        target_version_id: str,
        input_data: dict[str, Any] | None = None,
        *,
        triggered_by: str | None = None,
        trigger_type: str = "api",
        trigger_events: Sequence[ChangeEvent] = (),
        scenario_id: str | None = None,
        overrides: dict[str, dict[str, Any]] | None = None,
        publish: bool = True,
    ) -> GraphExecutionOutcome:
        order = self.plan_for_target(target_version_id)
        return self._execute_plan(
            order, input_data or {},
            triggered_by=triggered_by, trigger_type=trigger_type,
            target_version_id=target_version_id, trigger_events=trigger_events,
            scenario_id=scenario_id, overrides=overrides, publish=publish,
        )

    def execute_ordered_subgraph(
        self,
        ordered_version_ids: list[str],
        input_data: dict[str, Any] | None = None,
        *,
        triggered_by: str | None = None,
        trigger_type: str = "propagation",
        trigger_events: Sequence[ChangeEvent] = (),
    ) -> GraphExecutionOutcome:
        """Execute a caller-supplied, already-ordered plan (D9 computes it via D5)."""
        return self._execute_plan(
            list(ordered_version_ids), input_data or {},
            triggered_by=triggered_by, trigger_type=trigger_type,
            target_version_id=ordered_version_ids[-1] if ordered_version_ids else None,
            trigger_events=trigger_events,
        )

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    def _execute_plan(
        self,
        order: list[str],
        input_data: dict[str, Any],
        *,
        triggered_by: str | None,
        trigger_type: str,
        target_version_id: str | None,
        trigger_events: Sequence[ChangeEvent],
        scenario_id: str | None = None,
        overrides: dict[str, dict[str, Any]] | None = None,
        publish: bool = True,
    ) -> GraphExecutionOutcome:
        if not order:
            return GraphExecutionOutcome(True, [], {}, None, None, status="succeeded")

        versions, adapters = self._plan_and_validate(order, input_data)
        run, steps = self._create_graphrun(
            order, input_data,
            executor_name=IN_PROCESS_EXECUTOR, status="running",
            target_version_id=target_version_id, trigger_type=trigger_type,
            triggered_by=triggered_by, scenario_id=scenario_id,
        )
        return self._run_and_finalize(
            run, steps, order, input_data, versions, adapters,
            trigger_events=trigger_events, overrides=overrides, publish=publish,
        )

    # ------------------------------------------------------------------
    # Shared building blocks (used by the in-process and external paths alike)
    # ------------------------------------------------------------------

    def _plan_and_validate(
        self, order: list[str], input_data: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Validate the whole plan before anything is created or executed."""
        versions = {vid: self._exec_svc.get_version(vid) for vid in order}
        for version in versions.values():
            self._federation.assert_executable(version)
        adapters = {vid: self._exec_svc.resolve_adapter(v) for vid, v in versions.items()}
        if input_data and all(self._exec_svc.has_declared_inputs(vid) for vid in order):
            raise DirectInputNotAcceptedError(
                "input_data was provided but every model in the plan reads its inputs "
                "from declared datasets; supply dataset values instead."
            )
        return versions, adapters

    def _create_graphrun(
        self,
        order: list[str],
        input_data: dict[str, Any],
        *,
        executor_name: str,
        status: str,
        target_version_id: str | None,
        trigger_type: str,
        triggered_by: str | None,
        scenario_id: str | None,
    ) -> tuple[ExecutionRun, list[ExecutionStep]]:
        run = self._runs.add(ExecutionRun(
            run_kind="graph",
            executor=executor_name,
            target_version_id=target_version_id,
            subgraph_json=json.dumps(order),
            input_snapshot=json.dumps(input_data),
            status=status,
            triggered_by=triggered_by,
            trigger_type=trigger_type,
            scenario_id=scenario_id,
            started_at=_now(),
        ))
        steps = [
            self._steps.add(ExecutionStep(
                run_id=run.id, model_version_id=vid, step_order=i, status="pending",
            ))
            for i, vid in enumerate(order)
        ]
        return run, steps

    def _run_and_finalize(
        self,
        run: ExecutionRun,
        steps: list[ExecutionStep],
        order: list[str],
        input_data: dict[str, Any],
        versions: dict[str, Any],
        adapters: dict[str, Any],
        *,
        trigger_events: Sequence[ChangeEvent] = (),
        overrides: dict[str, dict[str, Any]] | None = None,
        publish: bool = True,
    ) -> GraphExecutionOutcome:
        ctx = RunContext(run=run, direct_inputs=dict(input_data), overrides=overrides or {})
        step_outcomes: dict[str, ExecutionOutcome] = {}
        first_failure: str | None = None
        for i, vid in enumerate(order):
            outcome = self._exec_svc.run_step(ctx, versions[vid], adapters[vid], steps[i])
            step_outcomes[vid] = outcome
            if outcome.status != "succeeded":
                first_failure = vid
                for skipped in steps[i + 1:]:
                    skipped.status = "skipped"
                break

        result_ids = tuple(rid for o in step_outcomes.values() for rid in o.result_ids)
        published: list[str] = []
        error: str | None = None
        if first_failure is None:
            # D19 read-only scenario invariant: when publish is False (scenario runs), staged
            # outputs are NOT published to Dataset.current_value, NO output ChangeEvents are
            # created, and consumed change events are NOT marked propagated — so a scenario run
            # never mutates shared baseline state nor triggers downstream propagation. Results
            # and lineage are already recorded per step; intra-run staged values still flow
            # between downstream models regardless of publish.
            if publish:
                plan_set = set(order)
                for dataset_id, staged in ctx.staged.items():
                    write = self._datasets.publish_model_output(
                        dataset_id, staged.record,
                        run_id=run.id, result_id=staged.result_id, version_id=staged.version_id,
                        propagated_in_run=self._federation.consumers_of(dataset_id) <= plan_set,
                    )
                    if write.changed:
                        published.append(write.event.id)
                # A change is propagated once every consumer of its dataset ran in this run.
                for event in {**{e.id: e for e in trigger_events}, **ctx.consumed_events}.values():
                    if event.run_id is None and self._federation.consumers_of(event.dataset_id) <= plan_set:
                        event.run_id = run.id
            run.status = "succeeded"
        else:
            error = f"Step {first_failure!r} failed: {step_outcomes[first_failure].error}"
            run.status = "failed"
            run.error_message = error
        run.finished_at = _now()
        self._db.flush()

        return GraphExecutionOutcome(
            success=first_failure is None,
            execution_order=list(order),
            step_outcomes=step_outcomes,
            first_failure_version_id=first_failure,
            error=error,
            run_id=run.id,
            status=run.status,
            result_ids=result_ids,
            published_change_event_ids=tuple(published),
        )

    # ------------------------------------------------------------------
    # External executor (D21): submit / callback / reconcile
    # ------------------------------------------------------------------

    def submit_graph(
        self,
        target_version_id: str,
        input_data: dict[str, Any] | None = None,
        *,
        executor: Executor,
        triggered_by: str | None = None,
        trigger_type: str = "api",
    ) -> GraphExecutionOutcome:
        """Create a GraphRun and hand it to the selected executor.

        Non-external executors run inline (identical to execute_graph). External executors
        (Airflow) create the run, trigger the external system, and return while the run is
        still 'running'; execution is carried out later via execute_submitted_run() and the
        status reconciled via reconcile_run(). Scenario execution is never routed here — D19
        scenarios remain in-process (D21 correction #4).
        """
        if not executor.is_external:
            return self.execute_graph(
                target_version_id, input_data,
                triggered_by=triggered_by, trigger_type=trigger_type,
            )

        order = self.plan_for_target(target_version_id)
        if not order:
            return GraphExecutionOutcome(True, [], {}, None, None, status="succeeded")
        self._plan_and_validate(order, input_data or {})
        run, _steps = self._create_graphrun(
            order, input_data or {},
            executor_name=executor.name, status="requested",
            target_version_id=target_version_id, trigger_type=trigger_type,
            triggered_by=triggered_by, scenario_id=None,
        )
        # Flush so the run row is persisted before the external system can call back.
        self._db.flush()
        submit = executor.submit(run, order)  # type: ignore[attr-defined]
        run.status = submit.status
        self._db.flush()
        return GraphExecutionOutcome(
            success=False,
            execution_order=list(order),
            step_outcomes={},
            first_failure_version_id=None,
            error=None,
            run_id=run.id,
            status=run.status,
            external_ref=submit.external_ref,
        )

    def execute_submitted_run(self, run_id: str) -> GraphExecutionOutcome:
        """Carry out a previously-submitted external GraphRun, inline, into its existing run.

        Idempotent: a run already in a terminal status is returned unchanged (no
        re-execution, no duplicate results). Called by the Airflow callback after the API
        layer has verified existence, executor, correlation and non-terminal state.
        Always publishes (external runs carry out baseline/normal execution, never scenarios).
        """
        run = self._runs.get(run_id)
        if is_terminal(run.status):
            return self._outcome_from_run(run)

        order = json.loads(run.subgraph_json or "[]")
        input_data = json.loads(run.input_snapshot or "{}")
        versions, adapters = self._plan_and_validate(order, input_data)
        steps = self._steps.list_by_run(run_id)
        run.started_at = run.started_at or _now()
        run.status = "running"
        self._db.flush()
        return self._run_and_finalize(
            run, steps, order, input_data, versions, adapters,
            trigger_events=(), overrides=None, publish=True,
        )

    def reconcile_run(self, run_id: str, executor: Executor) -> ExecutionRun:
        """Reconcile a run's status from an external executor. Terminal statuses are sticky.

        Handles the case where the external run fails before ever calling back: the platform
        run would otherwise remain 'running'. A non-external executor is a no-op.
        """
        run = self._runs.get(run_id)
        if not executor.is_external or is_terminal(run.status):
            return run
        platform_status = executor.poll_status(run)  # type: ignore[attr-defined]
        if is_terminal(platform_status):
            run.status = platform_status
            if platform_status == "failed" and not run.error_message:
                run.error_message = (
                    executor.fetch_failure(run) or "External run failed."  # type: ignore[attr-defined]
                )
            run.finished_at = _now()
            self._db.flush()
        return run

    def _outcome_from_run(self, run: ExecutionRun) -> GraphExecutionOutcome:
        order = json.loads(run.subgraph_json or "[]")
        return GraphExecutionOutcome(
            success=(run.status == "succeeded"),
            execution_order=order,
            step_outcomes={},
            first_failure_version_id=None,
            error=run.error_message,
            run_id=run.id,
            status=run.status,
        )
