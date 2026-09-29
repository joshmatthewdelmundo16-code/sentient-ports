"""Model Registry API — D11."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.deps import get_model_registry, get_results_service
from backend.app.api.schemas import ModelOut, ModelVersionOut, ResultOut
from backend.app.services.model_registry import (
    ModelNotFoundError,
    ModelRegistry,
    ModelVersionNotFoundError,
)
from backend.app.services.results_lineage import ResultsLineageService

router = APIRouter(prefix="/api/models", tags=["Models"])


@router.get("", response_model=list[ModelOut], summary="List all registered models")
def list_models(
    registry: ModelRegistry = Depends(get_model_registry),
) -> list[ModelOut]:
    models = registry.list_models()
    return [ModelOut.model_validate(m) for m in models]


@router.get("/{model_id}", response_model=ModelOut, summary="Get a model by ID")
def get_model(
    model_id: str,
    registry: ModelRegistry = Depends(get_model_registry),
) -> ModelOut:
    try:
        model = registry.get_model(model_id)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ModelOut.model_validate(model)


@router.get(
    "/{model_id}/versions",
    response_model=list[ModelVersionOut],
    summary="List versions of a model",
)
def list_model_versions(
    model_id: str,
    registry: ModelRegistry = Depends(get_model_registry),
) -> list[ModelVersionOut]:
    try:
        registry.get_model(model_id)  # validate model exists
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    versions = registry.list_model_versions(model_id)
    return [ModelVersionOut.model_validate(v) for v in versions]


@router.get(
    "/{model_id}/results",
    response_model=list[ResultOut],
    summary="List results for all versions of a model",
)
def list_model_results(
    model_id: str,
    registry: ModelRegistry = Depends(get_model_registry),
    results_svc: ResultsLineageService = Depends(get_results_service),
) -> list[ResultOut]:
    try:
        registry.get_model(model_id)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    versions = registry.list_model_versions(model_id)
    all_results: list[ResultOut] = []
    for v in versions:
        for r in results_svc.list_results_for_version(v.id):
            all_results.append(ResultOut.model_validate(r))
    all_results.sort(key=lambda r: r.created_at)
    return all_results
