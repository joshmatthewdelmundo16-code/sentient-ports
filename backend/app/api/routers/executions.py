"""Execution API — D11, GraphRun-based since D16.

POST /api/executions        single-model run (results recorded, datasets not published)
POST /api/graph-executions  one GraphRun toward a target; optional source dataset values
GET  /api/executions[/{run_id}[/results|/lineage|/change-events]]
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.api.deps import (
    get_db,
    get_dataset_value_service,
    get_execution_service,
    get_orchestration_service,
    get_results_service,
)
from backend.app.api.errors import MAPPED_ERRORS, to_http
from backend.app.api.schemas import (
    AirflowCallbackOut,
    AirflowCallbackRequest,
    ChangeEventOut,
    ExecutionDetailOut,
    ExecutionReconcileOut,
    ExecutionRequest,
    ExecutionRunOut,
    ExecutionStepOut,
    ExecutionTriggerOut,
    GraphExecutionOut,
    GraphExecutionRequest,
    GraphStepOut,
    LineageEdgeOut,
    ResultOut,
)
from backend.app.execution.airflow_executor import AirflowExecutor
from backend.app.execution.executor import AIRFLOW, is_terminal
from backend.app.execution.factory import select_executor
from backend.app.persistence.change_event_repository import ChangeEventRepository
from backend.app.services.dataset_values import DatasetValueService
from backend.app.services.execution import (
    ExecutionRunNotFoundError,
    ModelExecutionService,
)
from backend.app.services.orchestration import GraphOrchestrationService
from backend.app.services.results_lineage import ResultsLineageService

router = APIRouter(prefix="/api", tags=["Executions"])


@router.post(
    "/executions",
    response_model=ExecutionTriggerOut,
    status_code=201,
    summary="Run one model version (results recorded server-side; datasets not published)",
)
def trigger_execution(
    body: ExecutionRequest,
    exec_svc: ModelExecutionService = Depends(get_execution_service),
) -> ExecutionTriggerOut:
    try:
        outcome = exec_svc.execute_model(body.version_id, body.input_data, triggered_by="api")
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    return ExecutionTriggerOut(
        run_id=outcome.run_id,
        step_id=outcome.step_id,
        status=outcome.status,
        outputs=outcome.outputs,
        error=outcome.error,
        recorded_result_ids=list(outcome.result_ids),
    )


@router.post(
    "/graph-executions",
    response_model=GraphExecutionOut,
    status_code=201,
    summary="Execute the target and its upstream models as one GraphRun",
)
def trigger_graph_execution(
    body: GraphExecutionRequest,
    orch_svc: GraphOrchestrationService = Depends(get_orchestration_service),
    datasets: DatasetValueService = Depends(get_dataset_value_service),
) -> GraphExecutionOut:
    try:
        # Explicit executor selection: 'airflow' with missing/unreachable config raises a
        # clear configuration error rather than silently falling back (D21 correction #1).
        executor = select_executor(body.executor)
        input_events = []
        for dataset_id, record in (body.dataset_values or {}).items():
            write = datasets.write_external(dataset_id, record, triggered_by="api:graph-execution")
            if write.changed:
                input_events.append(write.event)
        if executor.is_external:
            go = orch_svc.submit_graph(
                body.target_version_id,
                body.input_data,
                executor=executor,
                triggered_by="api",
                trigger_type="api",
            )
        else:
            go = orch_svc.execute_graph(
                body.target_version_id,
                body.input_data,
                triggered_by="api",
                trigger_type="api",
                trigger_events=input_events,
            )
    except MAPPED_ERRORS as exc:
        raise to_http(exc)

    return GraphExecutionOut(
        graph_run_id=go.run_id,
        status=go.status,
        success=go.success,
        execution_order=go.execution_order,
        step_outcomes={
            vid: GraphStepOut(
                run_id=s.run_id, step_id=s.step_id, status=s.status,
                outputs=s.outputs, error=s.error,
            )
            for vid, s in go.step_outcomes.items()
        },
        first_failure_version_id=go.first_failure_version_id,
        error=go.error,
        recorded_result_ids=list(go.result_ids),
        input_change_event_ids=[e.id for e in input_events],
        published_change_event_ids=list(go.published_change_event_ids),
        executor=executor.name,
        external_ref=go.external_ref,
    )


@router.post(
    "/executions/{run_id}/airflow-callback",
    response_model=AirflowCallbackOut,
    summary="Carry out a submitted Airflow-executor GraphRun (called by the DAG task)",
)
def airflow_callback(
    run_id: str,
    body: AirflowCallbackRequest,
    exec_svc: ModelExecutionService = Depends(get_execution_service),
    orch_svc: GraphOrchestrationService = Depends(get_orchestration_service),
    results_svc: ResultsLineageService = Depends(get_results_service),
) -> AirflowCallbackOut:
    """Hardened, non-generic callback (D21 correction #2).

    Only carries out a run that: exists, was submitted to the Airflow executor, whose stored
    Airflow dag_run_id matches the caller's correlation id, and is not already terminal. A
    repeated callback on an already-terminal run is idempotent (returns the recorded status
    without re-executing). This is NOT a generic "execute any GraphRun" endpoint.
    """
    run = _require_run(exec_svc, run_id)
    if run.executor != AIRFLOW:
        raise HTTPException(
            status_code=409,
            detail=f"GraphRun {run_id!r} is not an Airflow-executor run (executor={run.executor!r}).",
        )
    stored_ref = AirflowExecutor.read_external_ref(run)
    if not stored_ref or stored_ref != body.dag_run_id:
        raise HTTPException(
            status_code=409,
            detail="Airflow dag_run_id does not match this GraphRun's stored correlation id.",
        )
    if is_terminal(run.status):
        results = [r.id for r in results_svc.list_results_for_run(run_id)]
        return AirflowCallbackOut(
            graph_run_id=run_id, status=run.status,
            success=(run.status == "succeeded"), already_terminal=True,
            recorded_result_ids=results,
        )
    try:
        go = orch_svc.execute_submitted_run(run_id)
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    return AirflowCallbackOut(
        graph_run_id=run_id, status=go.status or run.status,
        success=go.success, already_terminal=False,
        recorded_result_ids=list(go.result_ids),
    )


@router.post(
    "/executions/{run_id}/reconcile",
    response_model=ExecutionReconcileOut,
    summary="Reconcile a run's status from its external executor (read-only for in-process)",
)
def reconcile_execution(
    run_id: str,
    exec_svc: ModelExecutionService = Depends(get_execution_service),
    orch_svc: GraphOrchestrationService = Depends(get_orchestration_service),
) -> ExecutionReconcileOut:
    run = _require_run(exec_svc, run_id)
    try:
        executor = select_executor(run.executor)
        run = orch_svc.reconcile_run(run_id, executor)
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    return ExecutionReconcileOut(
        graph_run_id=run_id, executor=run.executor,
        status=run.status, error_message=run.error_message,
    )


@router.get("/executions", response_model=list[ExecutionRunOut], summary="List recent runs")
def list_executions(
    limit: int = 20,
    exec_svc: ModelExecutionService = Depends(get_execution_service),
) -> list[ExecutionRunOut]:
    return [ExecutionRunOut.model_validate(r) for r in exec_svc.list_recent_runs(limit=limit)]


def _require_run(exec_svc: ModelExecutionService, run_id: str):
    try:
        return exec_svc.get_execution_run(run_id)
    except ExecutionRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get(
    "/executions/{run_id}",
    response_model=ExecutionDetailOut,
    summary="Get a run (GraphRun) with all of its steps",
)
def get_execution(
    run_id: str,
    exec_svc: ModelExecutionService = Depends(get_execution_service),
) -> ExecutionDetailOut:
    run = _require_run(exec_svc, run_id)
    steps = exec_svc.list_execution_steps(run_id)
    return ExecutionDetailOut(
        run=ExecutionRunOut.model_validate(run),
        steps=[ExecutionStepOut.model_validate(s) for s in steps],
    )


@router.get(
    "/executions/{run_id}/results",
    response_model=list[ResultOut],
    summary="Results recorded by a run",
)
def list_execution_results(
    run_id: str,
    exec_svc: ModelExecutionService = Depends(get_execution_service),
    results_svc: ResultsLineageService = Depends(get_results_service),
) -> list[ResultOut]:
    _require_run(exec_svc, run_id)
    return [ResultOut.model_validate(r) for r in results_svc.list_results_for_run(run_id)]


@router.get(
    "/executions/{run_id}/lineage",
    response_model=list[LineageEdgeOut],
    summary="Lineage edges recorded by a run",
)
def list_execution_lineage(
    run_id: str,
    exec_svc: ModelExecutionService = Depends(get_execution_service),
    results_svc: ResultsLineageService = Depends(get_results_service),
) -> list[LineageEdgeOut]:
    _require_run(exec_svc, run_id)
    return [LineageEdgeOut.model_validate(e) for e in results_svc.list_lineage_for_run(run_id)]


@router.get(
    "/executions/{run_id}/change-events",
    response_model=list[ChangeEventOut],
    summary="Change events propagated in a run",
)
def list_execution_change_events(
    run_id: str,
    exec_svc: ModelExecutionService = Depends(get_execution_service),
    db: Session = Depends(get_db, scope="function"),
) -> list[ChangeEventOut]:
    _require_run(exec_svc, run_id)
    events = ChangeEventRepository(db).list_by_run(run_id)
    return [ChangeEventOut.model_validate(e) for e in events]
