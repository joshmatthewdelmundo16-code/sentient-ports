"""Sign-in, session and organization membership API — D26.

/api/auth/login and /api/session are the only data endpoints reachable without a session
(neither returns platform data). Everything else goes through security.auth.request_context.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.persistence.database import AppUser, Membership, Organization, get_db
from backend.app.security import audit
from backend.app.security.auth import (
    ROLES,
    RequestContext,
    clear_session_cookies,
    client_ip,
    create_session,
    default_org_of,
    local_default_org,
    effective_auth_mode,
    login_account_limiter,
    login_ip_limiter,
    memberships_of,
    request_context,
    resolve_session,
    revoke_user_sessions,
    set_session_cookies,
)
from backend.app.security.passwords import (
    burn_equivalent_time,
    check_password_policy,
    hash_password,
    needs_rehash,
    verify_password,
)

router = APIRouter(tags=["Authentication"])


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(_Req):
    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=256)


class PasswordChange(_Req):
    current_password: str = Field(..., max_length=256)
    new_password: str = Field(..., max_length=256)


class MemberCreate(_Req):
    email: str = Field(..., max_length=320)
    display_name: str = Field(..., max_length=255)
    role: str = Field("viewer")
    initial_password: str | None = Field(None, max_length=256,
                                         description="Required when the user does not exist yet")


def _org_out(o: Organization) -> dict:
    meta = json.loads(o.meta_json) if o.meta_json else {}
    return {"id": o.id, "key": o.org_key, "name": o.name, "kind": o.kind, "parent_id": o.parent_id,
            "synthetic": bool(meta.get("synthetic"))}


def _demo_accounts() -> list[dict] | None:
    """Only for the local SQLite demo: the seeded accounts, so a reviewer can sign in."""
    if not (settings.IS_SQLITE and settings.DEMO_SEED_ENABLED and not settings.IS_PRODUCTION):
        return None
    from backend.app.ui.network_seed import DEMO_ACCOUNTS
    return [{"email": a["email"], "role": a["role"], "organization": a["org_name"],
             "password": a["password"]} for a in DEMO_ACCOUNTS]


def _session_payload(db: Session, *, authenticated: bool, mode: str, user: AppUser | None,
                     roles: dict[str, str], default_org: str | None = None) -> dict:
    orgs = db.execute(select(Organization).where(Organization.status == "active")
                      .order_by(Organization.name)).scalars().all()
    by_id = {o.id: o for o in orgs}
    memberships = [{"organization": _org_out(by_id[o]), "role": r} for o, r in roles.items() if o in by_id]
    memberships.sort(key=lambda m: m["organization"]["name"])
    return {
        "authenticated": authenticated,
        "auth_mode": mode,
        "user": ({"id": user.id, "email": user.email, "display_name": user.display_name}
                 if user else None),
        "memberships": memberships,
        # Organization names and hierarchy are network metadata every member may see (so a
        # hub, an audience or a case participant can be named). Data never is.
        "organizations": [_org_out(o) for o in orgs] if authenticated else [],
        "active_organization_id": default_org or (memberships[0]["organization"]["id"] if memberships else None),
        "demo_accounts": _demo_accounts(),
    }


@router.get("/api/session", summary="Who is signed in, and which organizations they can act in")
def get_session(request: Request, db: Session = Depends(get_db, scope="function")) -> dict:
    mode = effective_auth_mode()
    if mode == "local":
        orgs = db.execute(select(Organization.id).where(Organization.status == "active")).scalars().all()
        return _session_payload(db, authenticated=True, mode=mode, user=None,
                                roles={o: "admin" for o in orgs}, default_org=local_default_org(db))
    resolved = resolve_session(db, request.cookies.get(settings.SESSION_COOKIE_NAME))
    if resolved is None:
        return _session_payload(db, authenticated=False, mode=mode, user=None, roles={})
    _, user = resolved
    return _session_payload(db, authenticated=True, mode=mode, user=user,
                            roles=memberships_of(db, user.id), default_org=default_org_of(db, user.id))


@router.post("/api/auth/login", summary="Sign in with email and password")
def login(body: LoginRequest, request: Request, response: Response,
          db: Session = Depends(get_db, scope="function")) -> dict:
    mode = effective_auth_mode()
    if mode == "local":
        raise HTTPException(status_code=409, detail="Sign-in is not used in local mode.")
    email = body.email.strip().lower()
    ip = client_ip(request)
    wait = max(login_ip_limiter.blocked_for(ip), login_account_limiter.blocked_for(email))
    if wait > 0:
        audit.defer(request, db, action="auth.login_throttled", outcome="denied",
                    actor_label=email, summary="Too many failed sign-in attempts")
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.",
                            headers={"Retry-After": str(int(wait) + 1)})
    user = db.execute(select(AppUser).where(AppUser.email == email)).scalars().first()
    ok = False
    if user is None or not user.password_hash:
        burn_equivalent_time(body.password)
    else:
        ok = verify_password(body.password, user.password_hash)
    if not ok or user is None or user.status != "active":
        login_ip_limiter.hit(ip)
        login_account_limiter.hit(email)
        audit.defer(request, db, action="auth.login_failed", outcome="denied",
                    actor_user_id=user.id if user else None, actor_label=email,
                    summary="Sign-in failed",
                    organization_id=default_org_of(db, user.id) if user else None)
        # One message for every cause: no account enumeration.
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    login_account_limiter.reset(email)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(timezone.utc)
    token, row = create_session(db, user, request)
    set_session_cookies(response, token, row.csrf_token)
    audit.record(db, action="auth.login", request=request, actor_user_id=user.id,
                 actor_label=user.display_name, summary="Signed in",
                 organization_id=default_org_of(db, user.id))
    return _session_payload(db, authenticated=True, mode=mode, user=user,
                            roles=memberships_of(db, user.id), default_org=default_org_of(db, user.id))


@router.post("/api/auth/logout", summary="Sign out (revokes this session)")
def logout(request: Request, response: Response, db: Session = Depends(get_db, scope="function")) -> dict:
    resolved = resolve_session(db, request.cookies.get(settings.SESSION_COOKIE_NAME))
    if resolved is not None:
        row, user = resolved
        row.revoked_at = datetime.now(timezone.utc)
        audit.record(db, action="auth.logout", request=request, actor_user_id=user.id,
                     actor_label=user.display_name, summary="Signed out",
                     organization_id=default_org_of(db, user.id))
    clear_session_cookies(response)
    return {"signed_out": True}


@router.post("/api/auth/password", summary="Change your password (signs out other sessions)")
def change_password(body: PasswordChange, request: Request, response: Response,
                    ctx: RequestContext = Depends(request_context),
                    db: Session = Depends(get_db, scope="function")) -> dict:
    if ctx.principal.kind != "user" or ctx.principal.user_id is None:
        raise HTTPException(status_code=409, detail="Only signed-in users have a password.")
    user = db.get(AppUser, ctx.principal.user_id)
    if user is None or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=403, detail="The current password is incorrect.")
    reason = check_password_policy(body.new_password)
    if reason:
        raise HTTPException(status_code=422, detail=reason)
    user.password_hash = hash_password(body.new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    keep = ctx.principal.session.id if ctx.principal.session else None
    revoked = revoke_user_sessions(db, user.id, keep=keep)
    audit.record(db, action="auth.password_changed", request=request,
                 summary=f"Password changed; {revoked} other session(s) signed out")
    return {"changed": True, "other_sessions_revoked": revoked}


# ---------------------------------------------------------------------------
# Organizations and members
# ---------------------------------------------------------------------------

@router.get("/api/organizations", summary="Organizations of the network (names and hierarchy only)",
            dependencies=[Depends(request_context)])
def list_organizations(db: Session = Depends(get_db, scope="function")) -> list[dict]:
    orgs = db.execute(select(Organization).where(Organization.status == "active")
                      .order_by(Organization.name)).scalars().all()
    return [_org_out(o) for o in orgs]


def _require_admin_of(ctx: RequestContext, org_id: str) -> None:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=403, detail="Switch to that organization to manage its members.")


@router.get("/api/organizations/{org_id}/members", summary="Members of an organization (admins)")
def list_members(org_id: str, ctx: RequestContext = Depends(request_context),
                 db: Session = Depends(get_db, scope="function")) -> list[dict]:
    _require_admin_of(ctx, org_id)
    rows = db.execute(select(Membership, AppUser).join(AppUser, AppUser.id == Membership.user_id)
                      .where(Membership.organization_id == org_id)).all()
    return [{"user_id": u.id, "email": u.email, "display_name": u.display_name, "role": m.role,
             "status": u.status} for m, u in rows]


@router.post("/api/organizations/{org_id}/members", status_code=201,
             summary="Add a member (creating the account if needed)")
def add_member(org_id: str, body: MemberCreate, request: Request,
               ctx: RequestContext = Depends(request_context),
               db: Session = Depends(get_db, scope="function")) -> dict:
    _require_admin_of(ctx, org_id)
    if body.role not in ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {list(ROLES)}.")
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    user = db.execute(select(AppUser).where(AppUser.email == email)).scalars().first()
    if user is None:
        if not body.initial_password:
            raise HTTPException(status_code=422, detail="initial_password is required for a new account.")
        reason = check_password_policy(body.initial_password)
        if reason:
            raise HTTPException(status_code=422, detail=reason)
        user = AppUser(email=email, display_name=body.display_name.strip() or email,
                       password_hash=hash_password(body.initial_password), status="active")
        db.add(user)
        db.flush()
    existing = db.execute(select(Membership).where(Membership.user_id == user.id,
                                                   Membership.organization_id == org_id)).scalars().first()
    if existing is not None:
        existing.role = body.role
    else:
        db.add(Membership(user_id=user.id, organization_id=org_id, role=body.role))
    db.flush()
    audit.record(db, action="member.added", request=request, target_type="user", target_id=user.id,
                 summary=f"{email} is now {body.role}")
    return {"user_id": user.id, "email": email, "role": body.role}


@router.delete("/api/organizations/{org_id}/members/{user_id}", status_code=204,
               summary="Remove a member from an organization")
def remove_member(org_id: str, user_id: str, request: Request,
                  ctx: RequestContext = Depends(request_context),
                  db: Session = Depends(get_db, scope="function")) -> None:
    _require_admin_of(ctx, org_id)
    if user_id == ctx.principal.user_id:
        raise HTTPException(status_code=409, detail="You cannot remove yourself.")
    m = db.execute(select(Membership).where(Membership.user_id == user_id,
                                            Membership.organization_id == org_id)).scalars().first()
    if m is None:
        raise HTTPException(status_code=404, detail="Not a member.")
    db.delete(m)
    audit.record(db, action="member.removed", request=request, target_type="user", target_id=user_id)


@router.get("/api/audit", summary="Audit trail of this organization (approvers and admins)")
def list_audit(limit: int = 100, ctx: RequestContext = Depends(request_context),
               db: Session = Depends(get_db, scope="function")) -> list[dict]:
    from backend.app.persistence.database import AuditEvent

    q = select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(max(1, min(limit, 500)))
    if ctx.org_id is not None:
        q = q.where(AuditEvent.organization_id == ctx.org_id)
    out = []
    for e in db.execute(q).scalars():
        out.append({
            "id": e.id, "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            "action": e.action, "outcome": e.outcome, "actor_label": e.actor_label,
            "target_type": e.target_type, "target_id": e.target_id, "summary": e.summary,
            "detail": json.loads(e.detail_json) if e.detail_json else None,
        })
    return out
