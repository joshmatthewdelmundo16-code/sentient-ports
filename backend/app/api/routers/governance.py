"""Governance API — D22 (Participants & Approved Outputs).

Small REST surface for federation governance. No authentication (participants are federation
actors, not users). Approved outputs are governance metadata; the read-only exposure view is
the safe output boundary — it returns only approved, non-expired fields of active
participants, never whole datasets and never scenario outputs.

  POST   /api/participants                      register a participant
  GET    /api/participants[/{id}]               list / get participants
  PATCH  /api/participants/{id}                  update name/description/status/metadata
  POST   /api/approved-outputs                   approve a dataset field for exposure
  GET    /api/approved-outputs                   list approvals (filterable)
  GET    /api/approved-outputs/{id}              get one approval
  POST   /api/approved-outputs/{id}/revoke       revoke an approval
  GET    /api/exposed-outputs                     read-only approved-output exposure view
  PUT    /api/datasets/{id}/owner                 set/clear dataset owner
  PUT    /api/model-versions/{id}/owner           set/clear model-version owner
  GET    /api/governance/summary                  small governance overview
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.api.deps import get_governance_service
from backend.app.api.errors import MAPPED_ERRORS, to_http
from backend.app.api.schemas import (
    ApprovedOutputCreate,
    ApprovedOutputOut,
    ExposedFieldOut,
    GovernanceSummaryOut,
    OwnerUpdate,
    ParticipantCreate,
    ParticipantOut,
    ParticipantUpdate,
)
from backend.app.persistence.database import ApprovedOutput, Participant
from backend.app.services.governance import ExposedField, GovernanceService, load_meta

router = APIRouter(prefix="/api", tags=["Governance"])


# ---------------------------------------------------------------------------
# Serialization helpers (ORM → schema, decoding meta_json)
# ---------------------------------------------------------------------------

def _participant_out(p: Participant) -> ParticipantOut:
    return ParticipantOut(
        id=p.id, participant_key=p.participant_key, name=p.name,
        description=p.description, status=p.status, metadata=load_meta(p.meta_json),
        created_at=p.created_at, updated_at=p.updated_at,
    )


def _approval_out(a: ApprovedOutput) -> ApprovedOutputOut:
    return ApprovedOutputOut(
        id=a.id, participant_id=a.participant_id, dataset_id=a.dataset_id,
        field_name=a.field_name, purpose=a.purpose, status=a.status,
        approved_at=a.approved_at, expires_at=a.expires_at, revoked_at=a.revoked_at,
        metadata=load_meta(a.meta_json), created_at=a.created_at, updated_at=a.updated_at,
    )


def _exposed_out(e: ExposedField) -> ExposedFieldOut:
    return ExposedFieldOut(
        participant_id=e.participant_id, participant_key=e.participant_key,
        dataset_id=e.dataset_id, dataset_name=e.dataset_name, field_name=e.field_name,
        purpose=e.purpose, value=e.value, value_present=e.value_present,
        approved_at=e.approved_at, expires_at=e.expires_at,
    )


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------

@router.post("/participants", response_model=ParticipantOut, status_code=201,
             summary="Register a federation participant")
def create_participant(
    body: ParticipantCreate, svc: GovernanceService = Depends(get_governance_service),
) -> ParticipantOut:
    try:
        p = svc.register_participant(
            participant_key=body.participant_key, name=body.name,
            description=body.description, status=body.status, metadata=body.metadata,
        )
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    return _participant_out(p)


@router.get("/participants", response_model=list[ParticipantOut], summary="List participants")
def list_participants(
    limit: int = 100, status: str | None = None,
    svc: GovernanceService = Depends(get_governance_service),
) -> list[ParticipantOut]:
    return [_participant_out(p) for p in svc.list_participants(limit=limit, status=status)]


@router.get("/participants/{participant_id}", response_model=ParticipantOut,
            summary="Get a participant")
def get_participant(
    participant_id: str, svc: GovernanceService = Depends(get_governance_service),
) -> ParticipantOut:
    try:
        return _participant_out(svc.get_participant(participant_id))
    except MAPPED_ERRORS as exc:
        raise to_http(exc)


@router.patch("/participants/{participant_id}", response_model=ParticipantOut,
              summary="Update a participant")
def update_participant(
    participant_id: str, body: ParticipantUpdate,
    svc: GovernanceService = Depends(get_governance_service),
) -> ParticipantOut:
    try:
        p = svc.update_participant(
            participant_id, name=body.name, description=body.description,
            status=body.status, metadata=body.metadata,
        )
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    return _participant_out(p)


# ---------------------------------------------------------------------------
# Approved outputs
# ---------------------------------------------------------------------------

@router.post("/approved-outputs", response_model=ApprovedOutputOut, status_code=201,
             summary="Approve a dataset field for external/output exposure")
def create_approved_output(
    body: ApprovedOutputCreate, svc: GovernanceService = Depends(get_governance_service),
) -> ApprovedOutputOut:
    try:
        a = svc.create_approval(
            participant_id=body.participant_id, dataset_id=body.dataset_id,
            field_name=body.field_name, purpose=body.purpose,
            expires_at=body.expires_at, metadata=body.metadata,
        )
    except MAPPED_ERRORS as exc:
        raise to_http(exc)
    return _approval_out(a)


@router.get("/approved-outputs", response_model=list[ApprovedOutputOut],
            summary="List approved outputs (filter by participant/dataset/status)")
def list_approved_outputs(
    participant_id: str | None = None, dataset_id: str | None = None,
    status: str | None = None, limit: int = 500,
    svc: GovernanceService = Depends(get_governance_service),
) -> list[ApprovedOutputOut]:
    rows = svc.list_approvals(
        participant_id=participant_id, dataset_id=dataset_id, status=status, limit=limit,
    )
    return [_approval_out(a) for a in rows]


@router.get("/approved-outputs/{approval_id}", response_model=ApprovedOutputOut,
            summary="Get one approved output")
def get_approved_output(
    approval_id: str, svc: GovernanceService = Depends(get_governance_service),
) -> ApprovedOutputOut:
    try:
        return _approval_out(svc.get_approval(approval_id))
    except MAPPED_ERRORS as exc:
        raise to_http(exc)


@router.post("/approved-outputs/{approval_id}/revoke", response_model=ApprovedOutputOut,
             summary="Revoke an approved output")
def revoke_approved_output(
    approval_id: str, svc: GovernanceService = Depends(get_governance_service),
) -> ApprovedOutputOut:
    try:
        return _approval_out(svc.revoke_approval(approval_id))
    except MAPPED_ERRORS as exc:
        raise to_http(exc)


# ---------------------------------------------------------------------------
# Read-only exposure view (the safe output boundary)
# ---------------------------------------------------------------------------

@router.get("/exposed-outputs", response_model=list[ExposedFieldOut],
            summary="Read-only view of approved, non-expired output fields")
def list_exposed_outputs(
    participant_id: str | None = None, dataset_id: str | None = None,
    svc: GovernanceService = Depends(get_governance_service),
) -> list[ExposedFieldOut]:
    view = svc.approved_outputs_view(participant_id=participant_id, dataset_id=dataset_id)
    return [_exposed_out(e) for e in view]


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

@router.put("/datasets/{dataset_id}/owner", status_code=204,
            summary="Set or clear a dataset's owning participant")
def set_dataset_owner(
    dataset_id: str, body: OwnerUpdate,
    svc: GovernanceService = Depends(get_governance_service),
) -> None:
    try:
        svc.set_dataset_owner(dataset_id, body.participant_id)
    except MAPPED_ERRORS as exc:
        raise to_http(exc)


@router.put("/model-versions/{version_id}/owner", status_code=204,
            summary="Set or clear a model version's owning participant")
def set_model_version_owner(
    version_id: str, body: OwnerUpdate,
    svc: GovernanceService = Depends(get_governance_service),
) -> None:
    try:
        svc.set_model_version_owner(version_id, body.participant_id)
    except MAPPED_ERRORS as exc:
        raise to_http(exc)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

@router.get("/governance/summary", response_model=GovernanceSummaryOut,
            summary="Small governance overview (counts)")
def governance_summary(
    svc: GovernanceService = Depends(get_governance_service),
) -> GovernanceSummaryOut:
    return GovernanceSummaryOut(**svc.summary())
