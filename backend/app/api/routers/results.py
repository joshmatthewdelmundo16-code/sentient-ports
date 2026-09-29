"""Results API — D11."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.deps import get_results_service
from backend.app.api.schemas import ResultOut
from backend.app.services.results_lineage import ResultNotFoundError, ResultsLineageService

router = APIRouter(prefix="/api/results", tags=["Results"])


@router.get("/{result_id}", response_model=ResultOut, summary="Get a result by ID")
def get_result(
    result_id: str,
    results_svc: ResultsLineageService = Depends(get_results_service),
) -> ResultOut:
    try:
        result = results_svc.get_result(result_id)
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ResultOut.model_validate(result)
