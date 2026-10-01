"""Port network and collaboration API — D27.

Everything here is behind request_context: the caller's organization is the scope, and the
network service decides — from approvals only — what of other organizations it may see.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.network.cases import CaseError, CaseNotFound, CaseService
from backend.app.network.service import ExposureDenied, NetworkError, NetworkService
from backend.app.persistence.database import get_db
from backend.app.security import audit
from backend.app.security.auth import RequestContext, request_context
from backend.app.services.governance import GovernanceError, GovernanceService

router = APIRouter(prefix="/api", tags=["Network"])


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseCreate(_Req):
    title: str = Field(..., max_length=255)
    purpose: str = Field(..., max_length=4000)
    member_organization_ids: list[str] = Field(..., min_length=1, max_length=50)
    closes_at: datetime | None = None


class CaseMemberAdd(_Req):
    organization_id: str
    role: str = "participant"


class CaseShare(_Req):
    participant_id: str
    dataset_id: str
    field_name: str
    purpose: str = Field(..., min_length=3, max_length=2000)
    source_run_id: str | None = None
    expires_at: datetime | None = None


def _org(ctx: RequestContext) -> str:
    if ctx.org_id is None:
        raise HTTPException(status_code=409, detail="Choose an organization scope first.")
    return ctx.org_id


@router.get("/network/hierarchy", summary="The network's organizations (names and structure only)")
def hierarchy(db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    return NetworkService(db).hierarchy()


@router.get("/network/shared-with-me", summary="Outputs other organizations have approved for you")
def shared_with_me(ctx: RequestContext = Depends(request_context),
                   db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    return [asdict(v) for v in NetworkService(db).shared_with(_org(ctx))]


@router.get("/network/hub", summary="Hub view: members, what each shared, and aggregates with coverage")
def hub_view(ctx: RequestContext = Depends(request_context),
             db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    try:
        return NetworkService(db).hub_overview(_org(ctx))
    except NetworkError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/network/hub/materialize",
             summary="Write this hub's aggregate of approved outputs into its own dataset and propagate")
def materialize(request: Request, ctx: RequestContext = Depends(request_context),
                db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    try:
        out = NetworkService(db).materialize(_org(ctx), triggered_by=f"hub-share:{ctx.principal.label}")
    except NetworkError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    audit.record(db, action="share.materialized", request=request, target_type="dataset",
                 summary=f"Aggregated {len(out['approvals_used'])} approved outputs; "
                         f"{len(out['not_shared'])} indicator(s) not shared",
                 detail={"approvals": out["approvals_used"], "run_id": out["run_id"],
                         "not_shared": out["not_shared"]})
    return out


@router.get("/network/exposure", summary="Ask for one field of another organization (audited)")
def request_exposure(
    request: Request,
    provider: str = Query(..., description="Providing organization id"),
    dataset: str = Query(..., description="Dataset name"),
    field: str = Query(...),
    ctx: RequestContext = Depends(request_context),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    org = _org(ctx)
    try:
        v = NetworkService(db).request_exposure(org, provider, dataset, field)
    except ExposureDenied as exc:
        audit.defer(request, db, action="exposure.denied", outcome="denied", organization_id=org,
                    target_type="field", target_id=f"{dataset}.{field}"[:64],
                    summary=f"Asked for {dataset}.{field} from another organization — not shared",
                    detail={"provider_org": provider})
        raise HTTPException(status_code=403, detail=str(exc))
    audit.record(db, action="exposure.granted", request=request, target_type="field",
                 target_id=f"{dataset}.{field}"[:64], summary=f"Read shared {v.field_label} from {v.provider_org_name}",
                 detail={"approval_id": v.approval_id, "purpose": v.purpose})
    return asdict(v)


# ---------------------------------------------------------------------------
# Collaboration cases
# ---------------------------------------------------------------------------

def _case_http(exc: Exception) -> HTTPException:
    if isinstance(exc, CaseNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/cases", summary="Collaboration cases this organization hosts or takes part in")
def list_cases(ctx: RequestContext = Depends(request_context),
               db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    _org(ctx)
    return CaseService(db).list()


@router.post("/cases", status_code=201, summary="Open a collaboration case hosted by this organization")
def create_case(body: CaseCreate, request: Request, ctx: RequestContext = Depends(request_context),
                db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    _org(ctx)
    try:
        case = CaseService(db).create(title=body.title, purpose=body.purpose,
                                      member_org_ids=body.member_organization_ids,
                                      closes_at=body.closes_at, user_id=ctx.principal.user_id)
    except CaseError as exc:
        raise _case_http(exc)
    audit.record(db, action="case.created", request=request, target_type="case", target_id=case["id"],
                 summary=f"Opened “{case['title']}” with {len(case['members']) - 1} other organization(s)")
    return case


@router.get("/cases/{case_id}", summary="One case: members, shared outputs, audit")
def get_case(case_id: str, ctx: RequestContext = Depends(request_context),
             db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    _org(ctx)
    try:
        return CaseService(db).get(case_id)
    except CaseError as exc:
        raise _case_http(exc)


@router.post("/cases/{case_id}/members", summary="Invite another organization (host only)")
def add_case_member(case_id: str, body: CaseMemberAdd, request: Request,
                    ctx: RequestContext = Depends(request_context),
                    db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    _org(ctx)
    try:
        case = CaseService(db).add_member(case_id, body.organization_id, body.role)
    except CaseError as exc:
        raise _case_http(exc)
    audit.record(db, action="case.member_added", request=request, target_type="case", target_id=case_id,
                 detail={"organization_id": body.organization_id, "role": body.role})
    return case


@router.post("/cases/{case_id}/close", summary="Close a case (host only) — its shared outputs stop being visible")
def close_case(case_id: str, request: Request, ctx: RequestContext = Depends(request_context),
               db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    _org(ctx)
    try:
        case = CaseService(db).close(case_id)
    except CaseError as exc:
        raise _case_http(exc)
    audit.record(db, action="case.closed", request=request, target_type="case", target_id=case_id,
                 summary=f"Closed “{case['title']}”; case-limited sharing ended")
    return case


@router.post("/cases/{case_id}/outputs", status_code=201,
             summary="Share one output into a case (visible to case members only, while it is open)")
def share_into_case(case_id: str, body: CaseShare, request: Request,
                    ctx: RequestContext = Depends(request_context),
                    db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    _org(ctx)
    try:
        a = GovernanceService(db).create_approval(
            participant_id=body.participant_id, dataset_id=body.dataset_id, field_name=body.field_name,
            purpose=body.purpose, expires_at=body.expires_at, source_run_id=body.source_run_id,
            collaboration_case_id=case_id, approved_by_user_id=ctx.principal.user_id)
    except GovernanceError as exc:
        audit.defer(request, db, action="approval.denied", outcome="denied", organization_id=ctx.org_id,
                    target_type="case", target_id=case_id, summary=f"Sharing into case refused: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))
    audit.record(db, action="case.output_shared", request=request, target_type="case", target_id=case_id,
                 summary=f"Shared {a.field_name} into the case"
                         + (" (a specific run's result)" if a.source_run_id else ""),
                 detail={"approval_id": a.id, "source_run_id": a.source_run_id})
    return {"approval_id": a.id, "case_id": case_id, "field_name": a.field_name,
            "source_run_id": a.source_run_id}
