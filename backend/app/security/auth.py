"""Authentication, authorization and request scoping — D26.

Every API request passes through `request_context` (attached to every API router in
main.py). It answers, in order:

  1. Who is calling?    a signed-in user (server-side session cookie), the optional Airflow
                        executor (service bearer token, one route only), or — in local
                        single-developer mode only — a local principal.
  2. Which scope?       the organization named by X-Scope-Org, which must be one the caller
                        belongs to; otherwise the caller's first membership.
  3. May they do this?  the route's required role (POLICY; unsafe methods default to
                        analyst), the CSRF token for cookie-authenticated writes, the Origin
                        of cross-site writes, and the write rate limit.
  4. Scope the session  tenancy.set_scope() — from here on the database session cannot see
                        or write any other organization's records.

Denials return 401/403/429 and are written to the audit trail.

AUTH_MODE=local is refused (at startup and per request) unless the database is SQLite and
ENVIRONMENT is not production: a shared database always requires sign-in.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.persistence.database import (
    AppUser,
    ExecutionRun,
    Membership,
    Organization,
    UserSession,
    get_db,
)
from backend.app.security import audit, tenancy
from backend.app.security.ratelimit import SlidingWindowLimiter

ROLES = ("viewer", "analyst", "approver", "admin")
RANK = {r: i + 1 for i, r in enumerate(ROLES)}
UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Routes that need more than the default. GET/HEAD default to viewer; any other method
# defaults to analyst. "service" = only an external executor's bearer token (or local mode).
POLICY: dict[tuple[str, str], str] = {
    ("POST", "/api/approved-outputs"): "approver",
    ("POST", "/api/approved-outputs/{approval_id}/revoke"): "approver",
    ("POST", "/api/participants"): "admin",
    ("PATCH", "/api/participants/{participant_id}"): "admin",
    ("PUT", "/api/datasets/{dataset_id}/owner"): "admin",
    ("PUT", "/api/model-versions/{version_id}/owner"): "admin",
    ("POST", "/api/organizations/{org_id}/members"): "admin",
    ("DELETE", "/api/organizations/{org_id}/members/{user_id}"): "admin",
    ("GET", "/api/organizations/{org_id}/members"): "admin",
    ("GET", "/api/audit"): "approver",
    ("POST", "/api/executions/{run_id}/airflow-callback"): "service",
    ("POST", "/api/executions/{run_id}/dagster-callback"): "service",  # D28
    # D27
    ("POST", "/api/model-library/packs/{pack_key}/install"): "admin",
    ("POST", "/api/connectors"): "admin",
    ("POST", "/api/connectors/{source_id}/{action}"): "admin",
    ("GET", "/api/connectors/{source_id}/webhook-secret"): "admin",
    ("POST", "/api/cases/{case_id}/close"): "approver",
    ("POST", "/api/cases/{case_id}/members"): "approver",
    ("POST", "/api/cases/{case_id}/outputs"): "approver",
}


class AuthConfigError(RuntimeError):
    """The authentication configuration is unsafe for this deployment."""


def effective_auth_mode() -> str:
    mode = (settings.AUTH_MODE or "required").strip().lower()
    if mode not in ("required", "local"):
        raise AuthConfigError(f"AUTH_MODE must be 'required' or 'local', got {mode!r}.")
    if mode == "local" and (not settings.IS_SQLITE or settings.IS_PRODUCTION):
        raise AuthConfigError(
            "AUTH_MODE=local is only allowed with a local SQLite database outside production. "
            "A shared database always requires sign-in.")
    return mode


def required_role(method: str, template: str) -> str:
    explicit = POLICY.get((method.upper(), template))
    if explicit:
        return explicit
    return "viewer" if method.upper() in ("GET", "HEAD", "OPTIONS") else "analyst"


# ---------------------------------------------------------------------------
# Principal and context
# ---------------------------------------------------------------------------

@dataclass
class Principal:
    kind: str                            # "user" | "local" | "service"
    user_id: str | None
    email: str | None
    label: str
    roles: dict[str, str] = field(default_factory=dict)   # organization_id → role
    session: UserSession | None = None


@dataclass
class RequestContext:
    principal: Principal
    org_id: str | None
    role: str | None

    def can(self, need: str) -> bool:
        if self.principal.kind == "local":
            return True
        return RANK.get(self.role or "", 0) >= RANK.get(need, 99)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: AppUser, request: Request) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(32)
    now = _now()
    row = UserSession(
        token_hash=token_hash(token), user_id=user.id, csrf_token=secrets.token_urlsafe(24),
        created_at=now, last_seen_at=now,
        expires_at=now + timedelta(hours=settings.SESSION_ABSOLUTE_HOURS),
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:512],
    )
    db.add(row)
    db.flush()
    return token, row


def resolve_session(db: Session, token: str | None) -> tuple[UserSession, AppUser] | None:
    if not token or len(token) > 256:
        return None
    row = db.execute(
        select(UserSession).where(UserSession.token_hash == token_hash(token))
    ).scalars().first()
    if row is None or row.revoked_at is not None:
        return None
    now = _now()
    if _aware(row.expires_at) <= now:
        return None
    last = _aware(row.last_seen_at) or _aware(row.created_at) or now
    if now - last > timedelta(minutes=settings.SESSION_IDLE_MINUTES):
        row.revoked_at = now
        db.flush()
        return None
    user = db.get(AppUser, row.user_id)
    if user is None or user.status != "active":
        return None
    if now - last > timedelta(seconds=60):     # touch at most once a minute
        row.last_seen_at = now
        db.flush()
    return row, user


def revoke_user_sessions(db: Session, user_id: str, *, keep: str | None = None) -> int:
    rows = db.execute(select(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None))).scalars().all()
    n = 0
    for r in rows:
        if r.id != keep:
            r.revoked_at = _now()
            n += 1
    db.flush()
    return n


def set_session_cookies(response: Response, token: str, csrf: str) -> None:
    max_age = int(settings.SESSION_ABSOLUTE_HOURS * 3600)
    response.set_cookie(settings.SESSION_COOKIE_NAME, token, max_age=max_age, httponly=True,
                        secure=settings.SESSION_COOKIE_SECURE, samesite="lax", path="/")
    # Readable by the page on purpose (double-submit CSRF). It is not a credential: it is
    # useless without the HttpOnly session cookie, and a cross-site page cannot read it.
    response.set_cookie(settings.CSRF_COOKIE_NAME, csrf, max_age=max_age, httponly=False,
                        secure=settings.SESSION_COOKIE_SECURE, samesite="lax", path="/")


def clear_session_cookies(response: Response) -> None:
    for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
        response.delete_cookie(name, path="/", secure=settings.SESSION_COOKIE_SECURE, samesite="lax")


def memberships_of(db: Session, user_id: str) -> dict[str, str]:
    rows = db.execute(
        select(Membership.organization_id, Membership.role)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id == user_id, Organization.status == "active")
    ).all()
    return {org_id: role for org_id, role in rows}


def default_org_of(db: Session, user_id: str) -> str | None:
    """The membership flagged is_default, else the first by organization name."""
    rows = db.execute(
        select(Membership.organization_id, Membership.is_default, Organization.name)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id == user_id, Organization.status == "active")
    ).all()
    if not rows:
        return None
    flagged = [r for r in rows if r[1]]
    return (flagged or sorted(rows, key=lambda r: r[2]))[0][0]


def local_default_org(db: Session) -> str | None:
    """Local mode without a chosen scope: the organization marked local_default, else the
    first port by name. None when no organizations exist (pre-tenancy databases)."""
    import json as _json

    orgs = db.execute(select(Organization).where(Organization.status == "active")
                      .order_by(Organization.name)).scalars().all()
    for o in orgs:
        try:
            if _json.loads(o.meta_json or "{}").get("local_default"):
                return o.id
        except ValueError:
            pass
    ports = [o for o in orgs if o.kind == "port"]
    return (ports or orgs)[0].id if orgs else None


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

login_ip_limiter = SlidingWindowLimiter(settings.LOGIN_MAX_ATTEMPTS, settings.LOGIN_WINDOW_S)
login_account_limiter = SlidingWindowLimiter(settings.LOGIN_MAX_ATTEMPTS, settings.LOGIN_WINDOW_S)
write_limiter = SlidingWindowLimiter(settings.WRITE_RATE_PER_MINUTE, 60.0)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Origin check for writes
# ---------------------------------------------------------------------------

def _same_origin(request: Request, origin: str) -> bool:
    try:
        o = urlsplit(origin)
    except ValueError:
        return False
    host = request.headers.get("host", "")
    return bool(o.netloc) and o.netloc.lower() == host.lower()


def origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        if not referer:
            return True       # non-browser clients send neither; CSRF token still applies
        parts = urlsplit(referer)
        origin = f"{parts.scheme}://{parts.netloc}"
    if origin == "null":
        return False
    return _same_origin(request, origin) or origin.rstrip("/") in settings.CORS_ALLOWED_ORIGINS


# ---------------------------------------------------------------------------
# The dependency
# ---------------------------------------------------------------------------

def _deny(request: Request, db: Session, status: int, message: str, *, action: str = "access.denied",
          org_id: str | None = None, detail: dict | None = None,
          principal: Principal | None = None) -> HTTPException:
    template = getattr(request.scope.get("route"), "path", request.url.path)
    audit.defer(request, db, action=action, outcome="denied", organization_id=org_id,
                target_type="route", target_id=None,
                summary=f"{request.method} {template}: {message}",
                detail={"status": status, **(detail or {})},
                actor_user_id=principal.user_id if principal else None,
                actor_label=principal.label if principal else None)
    return HTTPException(status_code=status, detail=message)


def _local_principal(db: Session) -> Principal:
    orgs = db.execute(select(Organization.id).where(Organization.status == "active")).scalars().all()
    return Principal(kind="local", user_id=None, email=None, label="Local developer",
                     roles={o: "admin" for o in orgs})


def _service_principal(request: Request) -> Principal | None:
    token = settings.SERVICE_TOKEN
    header = request.headers.get("authorization", "")
    if not token or not header.lower().startswith("bearer "):
        return None
    if not hmac.compare_digest(header[7:].strip().encode(), token.encode()):
        return None
    return Principal(kind="service", user_id=None, email=None, label="External executor")


def request_context(request: Request, db: Session = Depends(get_db, scope="function")) -> RequestContext:
    try:
        mode = effective_auth_mode()
    except AuthConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    route = request.scope.get("route")
    template = getattr(route, "path", request.url.path)
    method = request.method.upper()
    need = required_role(method, template)

    principal: Principal | None = None
    session_row: UserSession | None = None
    if mode == "local":
        principal = _local_principal(db)
    else:
        principal = _service_principal(request)
        if principal is None:
            resolved = resolve_session(db, request.cookies.get(settings.SESSION_COOKIE_NAME))
            if resolved is None:
                raise HTTPException(status_code=401, detail="Sign in to continue.")
            session_row, user = resolved
            principal = Principal(kind="user", user_id=user.id, email=user.email,
                                  label=user.display_name, roles=memberships_of(db, user.id),
                                  session=session_row)

    # --- scope -----------------------------------------------------------------------
    # EventSource cannot send headers, so a `scope` query parameter is accepted too — it is
    # validated against the caller's memberships exactly like the header.
    requested = request.headers.get("x-scope-org") or request.query_params.get("scope")
    org_id: str | None = None
    role: str | None = None
    if principal.kind == "service":
        if need != "service":
            raise _deny(request, db, 403, "The service token is only valid for the executor callback.",
                        principal=principal)
        run_id = request.path_params.get("run_id")
        run = db.get(ExecutionRun, run_id) if run_id else None
        org_id = run.organization_id if run is not None else None
    elif principal.kind == "local":
        if requested:
            if db.get(Organization, requested) is None:
                raise HTTPException(status_code=404, detail="Unknown organization.")
            org_id = requested
        else:
            org_id = local_default_org(db)
        role = "admin"
    else:
        if requested:
            if requested not in principal.roles:
                raise _deny(request, db, 403, "You are not a member of that organization.",
                            org_id=None, detail={"requested_org": requested}, principal=principal)
            org_id = requested
        elif principal.roles:
            org_id = default_org_of(db, principal.user_id)
        else:
            raise _deny(request, db, 403, "Your account is not a member of any organization.",
                        principal=principal)
        role = principal.roles.get(org_id)

    ctx = RequestContext(principal=principal, org_id=org_id, role=role)
    request.state.ctx = ctx

    # --- authorization ------------------------------------------------------------------
    if need == "service":
        if principal.kind not in ("service", "local"):
            raise _deny(request, db, 403, "Only the configured executor may call this endpoint.",
                        org_id=org_id, principal=principal)
    elif principal.kind == "user" and not ctx.can(need):
        raise _deny(request, db, 403, f"This needs the {need} role in this organization.",
                    org_id=org_id, detail={"role": role, "required": need}, principal=principal)

    # --- write protections ---------------------------------------------------------------
    if method in UNSAFE:
        if principal.kind == "user":
            sent = request.headers.get("x-csrf-token", "")
            if not session_row or not hmac.compare_digest(sent.encode(), session_row.csrf_token.encode()):
                raise _deny(request, db, 403, "Missing or invalid CSRF token.", action="csrf.rejected",
                            org_id=org_id, principal=principal)
        if not origin_allowed(request):
            raise _deny(request, db, 403, "Cross-origin write refused.", action="csrf.rejected",
                        org_id=org_id, detail={"origin": request.headers.get("origin")},
                        principal=principal)
        key = principal.user_id or f"ip:{client_ip(request)}"
        if not write_limiter.allow(key):
            raise HTTPException(status_code=429, detail="Too many changes in a short time. Wait a minute.",
                                headers={"Retry-After": "60"})

    # --- scope the session ---------------------------------------------------------------
    if org_id is not None:
        tenancy.set_scope(db, org_id, include_unowned=principal.kind == "local")
    return ctx


def current_context(request: Request) -> RequestContext | None:
    return getattr(request.state, "ctx", None)
