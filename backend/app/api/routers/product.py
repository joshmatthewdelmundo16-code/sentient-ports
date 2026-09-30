"""Product read-model API — D25.

Composite read-only endpoints that the React product is built on. Each is derived from
platform records (see backend/app/product); none writes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.api.errors import MAPPED_ERRORS, to_http
from backend.app.persistence.database import get_db
from backend.app.product.activity import ActivityService
from backend.app.product.catalog import CatalogService
from backend.app.product.explain import ExplanationService, ExplanationUnavailable
from backend.app.product.workspace import WorkspaceService
from backend.app.services.scenario_comparison import ComparisonRunNotFoundError

router = APIRouter(prefix="/api", tags=["Product"])


@router.get("/workspace", summary="Decision workspace: baselines, scenarios, assumptions, default selection")
def get_workspace(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return WorkspaceService(db).summary()


@router.get("/catalog", summary="Every dataset with labels, contract fields, value and provenance")
def get_catalog(db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    return CatalogService(db).datasets()


@router.get("/federation/map", summary="Models, datasets, and the field-level reads/writes between them")
def get_federation_map(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return CatalogService(db).federation_map()


@router.get("/activity", summary="Human-readable activity: runs, dataset changes, uploads")
def get_activity(
    limit: int = Query(40, ge=1, le=200),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    return ActivityService(db).feed(limit=limit)


@router.get("/scenarios/{scenario_id}/explanation",
            summary="Why the scenario differs from its baseline (field-level causal trace)")
def get_scenario_explanation(
    scenario_id: str, db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        return ExplanationService(db).explain_scenario(scenario_id)
    except ExplanationUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except MAPPED_ERRORS as exc:
        raise to_http(exc)


@router.get("/runs/compare", summary="Labelled comparison of any two runs' persisted results")
def compare_runs(
    base: str = Query(..., description="Run to compare from"),
    target: str = Query(..., description="Run to compare to"),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        return ExplanationService(db).compare_runs(base, target)
    except ComparisonRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/ingestions/{ingestion_id}/impact",
            summary="What one workbook upload changed, and what that did downstream")
def get_upload_impact(
    ingestion_id: str, db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    out = WorkspaceService(db).upload_impact(ingestion_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"Ingestion {ingestion_id!r} not found.")
    return out
