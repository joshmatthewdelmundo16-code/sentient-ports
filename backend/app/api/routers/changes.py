"""Change Propagation API — D11, dataset-driven since D16."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from backend.app.api.deps import get_propagation_service
from backend.app.api.errors import MAPPED_ERRORS, to_http
from backend.app.api.schemas import PropagationOut, PropagationRequest
from backend.app.services.change_propagation import ChangePropagationService

router = APIRouter(prefix="/api/changes", tags=["Changes"])


@router.post(
    "/propagate",
    response_model=PropagationOut,
    status_code=201,
    summary="Write a source dataset value and propagate the change downstream",
)
def propagate_change(
    body: PropagationRequest,
    response: Response,
    prop_svc: ChangePropagationService = Depends(get_propagation_service),
) -> PropagationOut:
    """
    The server computes the old value, hash comparison, affected models, execution order,
    output datasets and results. An unchanged value is a no-op (200, changed=false) unless
    the current value was never successfully propagated, in which case that is retried.
    """
    try:
        change = prop_svc.apply_dataset_change(
            body.dataset_id, body.value,
            triggered_by=body.triggered_by or "api", source_ref=body.source_ref,
        )
    except MAPPED_ERRORS as exc:
        raise to_http(exc)

    prop = change.propagation
    if not change.changed:
        response.status_code = 200
    outcome = prop.graph_outcome if prop else None
    return PropagationOut(
        changed=change.changed,
        change_event_id=change.change_event_id or (prop.change_event_id if prop else None),
        source_dataset_id=change.dataset_id,
        source_version_id=prop.source_version_id if prop else None,
        old_value=change.old_value,
        new_value=change.new_value,
        graph_run_id=prop.run_id if prop else None,
        affected_version_ids=prop.affected_version_ids if prop else [],
        execution_order=prop.execution_order if prop else [],
        success=prop.success if prop else True,
        error=prop.error if prop else None,
        already_processed=prop.already_processed if prop else False,
        recorded_result_ids=list(outcome.result_ids) if outcome else [],
    )
