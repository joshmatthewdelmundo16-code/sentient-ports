"""Dependency Graph API — D11."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.deps import get_graph_service
from backend.app.api.schemas import DependencyOut, GraphOut
from backend.app.services.dependency_graph import (
    CycleDetectedError,
    DependencyGraphService,
)

router = APIRouter(prefix="/api/graph", tags=["Graph"])


@router.get("", response_model=GraphOut, summary="Inspect the full dependency graph")
def get_graph(
    graph_svc: DependencyGraphService = Depends(get_graph_service),
) -> GraphOut:
    """
    Returns the registered dependency graph:
    - `execution_order`: topological version ordering (raises 422 on cycle)
    - `dependencies`: all registered dependency edges
    """
    try:
        order = graph_svc.topological_order()
    except CycleDetectedError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Dependency graph contains a cycle involving: {exc.cycle_nodes}",
        )
    deps = graph_svc.list_dependencies()
    return GraphOut(
        execution_order=order,
        dependencies=[DependencyOut.model_validate(d) for d in deps],
    )
