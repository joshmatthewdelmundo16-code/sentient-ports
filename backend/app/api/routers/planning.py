"""Master planning and optimization API — D27."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.library.packs import PLANNING
from backend.app.persistence.database import get_db
from backend.app.planning.optimize import (
    PERIOD_METRICS,
    PLAN_METRICS,
    OptimizationError,
    OptimizationService,
    default_study,
)
from backend.app.planning.plans import MasterPlanService, PlanError
from backend.app.security import audit

router = APIRouter(prefix="/api", tags=["Planning"])


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanCreate(_Req):
    name: str = Field(..., max_length=255)
    description: str | None = Field(None, max_length=2000)
    spec: dict[str, Any]


class PlanBranch(_Req):
    name: str = Field(..., max_length=255)
    changes: dict[str, Any] = Field(default_factory=dict)


class StudyCreate(_Req):
    name: str = Field(..., max_length=255)
    base_plan_id: str
    spec: dict[str, Any]


DEFAULT_HORIZON = [2026, 2027, 2028, 2029, 2030, 2035]


def default_plan_spec() -> dict[str, Any]:
    return {
        "horizon": DEFAULT_HORIZON, "base_year": 2026, "discount_rate": 0.08,
        "assumptions": {
            "demand_teu": {"type": "growth", "rate": 0.05},
            "electricity_price_usd_per_mwh": {"type": "points", "points": {"2026": 120, "2030": 105, "2035": 90}},
        },
        "investments": [],
        "shocks": [{"name": "Trade disruption", "field": "demand_teu", "year": 2028, "factor": 0.85}],
    }


def _http(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/plans/defaults", summary="Starting spec, base inputs and the fields a plan may vary")
def plan_defaults(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    svc = MasterPlanService(db)
    try:
        base = svc.base_inputs()
    except PlanError as exc:
        return {"installed": False, "reason": str(exc)}
    fields = [{"name": f.name, "label": f.label, "unit": f.unit}
              for f in PLANNING.dataset("planning_inputs").fields]
    return {"installed": True, "spec": default_plan_spec(), "base_inputs": base, "fields": fields,
            "study": default_study(DEFAULT_HORIZON), "plan_metrics": list(PLAN_METRICS),
            "period_metrics": list(PERIOD_METRICS)}


@router.get("/plans", summary="Master plans of this organization")
def list_plans(db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    return MasterPlanService(db).list()


@router.post("/plans", status_code=201, summary="Create a master plan")
def create_plan(body: PlanCreate, request: Request, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    try:
        plan = MasterPlanService(db).create(name=body.name, spec=body.spec, description=body.description)
    except PlanError as exc:
        raise _http(exc)
    audit.record(db, action="plan.created", request=request, target_type="plan", target_id=plan.id,
                 summary=f"Created plan {plan.name}")
    return MasterPlanService(db).detail(plan.id)


@router.get("/plans/compare", summary="Compare two evaluated plans period by period")
def compare_plans(a: str = Query(...), b: str = Query(...), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    try:
        return MasterPlanService(db).compare(a, b)
    except PlanError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc))


@router.get("/plans/{plan_id}", summary="A plan with its evaluated periods and totals")
def get_plan(plan_id: str, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    try:
        return MasterPlanService(db).detail(plan_id)
    except PlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/plans/{plan_id}/evaluate", summary="Evaluate every period through the engine (read-only runs)")
def evaluate_plan(plan_id: str, request: Request, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    try:
        out = MasterPlanService(db).evaluate(plan_id)
    except PlanError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc))
    audit.record(db, action="plan.evaluated", request=request, target_type="plan", target_id=plan_id,
                 summary=f"Evaluated {out['name']} over {len(out['periods'])} periods")
    return out


@router.post("/plans/{plan_id}/branch", status_code=201, summary="Branch a plan with changes")
def branch_plan(plan_id: str, body: PlanBranch, request: Request,
                db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    try:
        plan = MasterPlanService(db).branch(plan_id, name=body.name, changes=body.changes)
    except PlanError as exc:
        raise _http(exc)
    audit.record(db, action="plan.created", request=request, target_type="plan", target_id=plan.id,
                 summary=f"Branched {plan.name}")
    return MasterPlanService(db).detail(plan.id)


@router.get("/optimization", summary="Optimization studies of this organization")
def list_studies(db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    return OptimizationService(db).list()


@router.post("/optimization", status_code=201, summary="Declare an optimization study")
def create_study(body: StudyCreate, request: Request, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    try:
        s = OptimizationService(db).create(name=body.name, base_plan_id=body.base_plan_id, spec=body.spec)
    except (OptimizationError, PlanError) as exc:
        raise _http(exc)
    audit.record(db, action="optimization.created", request=request, target_type="study", target_id=s.id)
    return OptimizationService(db).detail(s.id)


@router.get("/optimization/{study_id}", summary="A study and its result")
def get_study(study_id: str, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    try:
        return OptimizationService(db).detail(study_id)
    except OptimizationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/optimization/{study_id}/run", summary="Enumerate the declared grid and verify the pick")
def run_study(study_id: str, request: Request, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    try:
        out = OptimizationService(db).run(study_id)
    except (OptimizationError, PlanError) as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 422, detail=str(exc))
    res = out["result"] or {}
    audit.record(db, action="optimization.run", request=request, target_type="study", target_id=study_id,
                 summary=f"{res.get('evaluated', 0)} alternatives, {res.get('feasible', 0)} feasible",
                 detail={"verified": (res.get("verification") or {}).get("matches")})
    return out
