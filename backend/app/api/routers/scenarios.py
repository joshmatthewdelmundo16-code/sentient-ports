"""Scenario & Baseline API — D19.

Smallest surface to exercise D19 end to end:
  POST /api/baselines                         create a baseline
  GET  /api/baselines[/{id}]                  list / get baselines
  POST /api/baselines/{id}/execute            run the baseline graph (publishes normally)
  POST /api/scenarios                          create a scenario linked to a baseline
  GET  /api/scenarios[/{id}]                  list / get scenarios
  PUT  /api/scenarios/{id}/overrides          set (create/replace) generic field overrides
  GET  /api/scenarios/{id}/overrides          list overrides
  POST /api/scenarios/{id}/execute            run the scenario graph (read-only: no publish)
  GET  /api/scenarios/{id}/comparison         compare scenario run vs its baseline run

Follows the D11 conventions: service dependencies, MAPPED_ERRORS → to_http.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, HTTPException

from backend.app.api.deps import (
    get_scenario_comparison_service,
    get_scenario_service,
)
from backend.app.api.errors import MAPPED_ERRORS, to_http
from backend.app.api.schemas import (
    BaselineCreate,
    BaselineOut,
    BaselineRunOut,
    ComparisonOut,
    OverrideOut,
    OverridesRequest,
    ScenarioCreate,
    ScenarioOut,
    ScenarioRunOut,
)
from backend.app.services.scenario_comparison import ScenarioComparisonService
from backend.app.services.scenarios import ScenarioExecutionError, ScenarioService

from backend.app.security import audit  # noqa: E402

router = APIRouter(prefix="/api", tags=["Scenarios"])


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

@router.post("/baselines", response_model=BaselineOut, status_code=201,
             summary="Create a named baseline")
def create_baseline(
    body: BaselineCreate,
    svc: ScenarioService = Depends(get_scenario_service),
) -> BaselineOut:
    try:
        b = svc.create_baseline(
            name=body.name, description=body.description,
            target_version_id=body.target_version_id, status=body.status,
        )
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    return BaselineOut.model_validate(b)


@router.get("/baselines", response_model=list[BaselineOut], summary="List baselines")
def list_baselines(
    limit: int = 50, svc: ScenarioService = Depends(get_scenario_service),
) -> list[BaselineOut]:
    return [BaselineOut.model_validate(b) for b in svc.list_baselines(limit=limit)]


@router.get("/baselines/{baseline_id}", response_model=BaselineOut, summary="Get a baseline")
def get_baseline(
    baseline_id: str, svc: ScenarioService = Depends(get_scenario_service),
) -> BaselineOut:
    try:
        return BaselineOut.model_validate(svc.get_baseline(baseline_id))
    except MAPPED_ERRORS as exc:
        raise to_http(exc)


@router.post("/baselines/{baseline_id}/execute", response_model=BaselineRunOut, status_code=201,
             summary="Execute a baseline as one GraphRun (publishes dataset values normally)")
def execute_baseline(
    baseline_id: str, request: Request, svc: ScenarioService = Depends(get_scenario_service),
) -> BaselineRunOut:
    try:
        go = svc.execute_baseline(baseline_id)
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    audit.record(svc._db, action="baseline.executed", request=request, target_type="run",
                 target_id=go.run_id, outcome="success" if go.success else "failure",
                 summary=f"Baseline {svc.get_baseline(baseline_id).name} · {go.status}")
    return BaselineRunOut(
        baseline_id=baseline_id, graph_run_id=go.run_id, status=go.status,
        success=go.success, execution_order=go.execution_order, error=go.error,
        recorded_result_ids=list(go.result_ids),
        published_change_event_ids=list(go.published_change_event_ids),
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

@router.post("/scenarios", response_model=ScenarioOut, status_code=201,
             summary="Create a scenario linked to a baseline")
def create_scenario(
    body: ScenarioCreate, svc: ScenarioService = Depends(get_scenario_service),
) -> ScenarioOut:
    try:
        s = svc.create_scenario(
            baseline_id=body.baseline_id, name=body.name,
            description=body.description, target_version_id=body.target_version_id,
        )
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    return ScenarioOut.model_validate(s)


@router.get("/scenarios", response_model=list[ScenarioOut], summary="List scenarios")
def list_scenarios(
    baseline_id: str | None = None, limit: int = 50,
    svc: ScenarioService = Depends(get_scenario_service),
) -> list[ScenarioOut]:
    try:
        scenarios = svc.list_scenarios(baseline_id=baseline_id, limit=limit)
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    return [ScenarioOut.model_validate(s) for s in scenarios]


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut, summary="Get a scenario")
def get_scenario(
    scenario_id: str, svc: ScenarioService = Depends(get_scenario_service),
) -> ScenarioOut:
    try:
        return ScenarioOut.model_validate(svc.get_scenario(scenario_id))
    except MAPPED_ERRORS as exc:
        raise to_http(exc)


@router.put("/scenarios/{scenario_id}/overrides", response_model=list[OverrideOut],
            summary="Set (create/replace) generic dataset-field overrides")
def set_overrides(
    scenario_id: str, body: OverridesRequest,
    svc: ScenarioService = Depends(get_scenario_service),
) -> list[OverrideOut]:
    try:
        created = svc.set_overrides(
            scenario_id, [o.model_dump() for o in body.overrides]
        )
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    return [OverrideOut.model_validate(o) for o in created]


@router.get("/scenarios/{scenario_id}/overrides", response_model=list[OverrideOut],
            summary="List a scenario's overrides")
def list_overrides(
    scenario_id: str, svc: ScenarioService = Depends(get_scenario_service),
) -> list[OverrideOut]:
    try:
        overrides = svc.list_overrides(scenario_id)
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    return [OverrideOut.model_validate(o) for o in overrides]


@router.delete("/scenarios/{scenario_id}/overrides/{override_id}", status_code=204,
               summary="Remove one override from a scenario")
def remove_override(
    scenario_id: str, override_id: str, svc: ScenarioService = Depends(get_scenario_service),
) -> None:
    try:
        svc.remove_override(scenario_id, override_id)
    except MAPPED_ERRORS as exc:
        raise to_http(exc)


@router.post("/scenarios/{scenario_id}/execute", response_model=ScenarioRunOut, status_code=201,
             summary="Execute a scenario as one GraphRun (read-only: shared state not mutated)")
def execute_scenario(
    scenario_id: str, request: Request, svc: ScenarioService = Depends(get_scenario_service),
) -> ScenarioRunOut:
    try:
        go = svc.execute_scenario(scenario_id)
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    audit.record(svc._db, action="scenario.executed", request=request, target_type="run",
                 target_id=go.run_id, outcome="success" if go.success else "failure",
                 summary=f"Scenario {svc.get_scenario(scenario_id).name} · {go.status} (read-only)")
    return ScenarioRunOut(
        scenario_id=scenario_id, graph_run_id=go.run_id, status=go.status,
        success=go.success, execution_order=go.execution_order, error=go.error,
        recorded_result_ids=list(go.result_ids),
        published_change_event_ids=list(go.published_change_event_ids),
    )


@router.get("/scenarios/{scenario_id}/comparison", response_model=ComparisonOut,
            summary="Compare a scenario's run against its baseline's run")
def compare_scenario(
    scenario_id: str,
    svc: ScenarioService = Depends(get_scenario_service),
    cmp_svc: ScenarioComparisonService = Depends(get_scenario_comparison_service),
) -> ComparisonOut:
    try:
        scenario = svc.get_scenario(scenario_id)
        baseline = svc.get_baseline(scenario.baseline_id)
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    if not baseline.baseline_run_id:
        raise HTTPException(status_code=409, detail="Baseline has not been executed yet.")
    if not scenario.scenario_run_id:
        raise HTTPException(status_code=409, detail="Scenario has not been executed yet.")
    try:
        result = cmp_svc.compare(baseline.baseline_run_id, scenario.scenario_run_id)
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    return ComparisonOut(**result.as_dict())
