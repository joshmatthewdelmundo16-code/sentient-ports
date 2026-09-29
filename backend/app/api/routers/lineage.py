"""Lineage API — D11."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.deps import get_results_service
from backend.app.api.schemas import LineageEdgeOut, LineageOut
from backend.app.services.results_lineage import ResultNotFoundError, ResultsLineageService

router = APIRouter(prefix="/api/lineage", tags=["Lineage"])


@router.get(
    "/{result_id}",
    response_model=LineageOut,
    summary="Get lineage edges for a result (forward and backward)",
)
def get_lineage(
    result_id: str,
    results_svc: ResultsLineageService = Depends(get_results_service),
) -> LineageOut:
    try:
        results_svc.get_result(result_id)  # validate existence
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    edges_from = results_svc.list_lineage_from_result(result_id)
    edges_to = results_svc.list_lineage_to_result(result_id)

    return LineageOut(
        result_id=result_id,
        edges_from=[LineageEdgeOut.model_validate(e) for e in edges_from],
        edges_to=[LineageEdgeOut.model_validate(e) for e in edges_to],
    )
