"""Append-only audit trail — D26.

Two ways to record:

  record(db, …)            in the caller's transaction — for successful actions, so the
                           audit row commits atomically with the change it describes.
  defer(request, db, …)    for denials and failures. The request's own transaction is
                           rolled back when it raises, which would take a same-transaction
                           audit row with it; deferred events are written by the middleware
                           after the response, in a fresh session on the same database.

Nothing here stores secrets: no passwords, tokens, cookies or request bodies.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from backend.app.persistence.database import AuditEvent

log = logging.getLogger("platform.audit")

_DEFERRED = "deferred_audit"


def _event(*, action: str, outcome: str, organization_id: str | None, actor_user_id: str | None,
           actor_label: str | None, target_type: str | None, target_id: str | None,
           summary: str | None, detail: dict[str, Any] | None, ip: str | None,
           request_id: str | None) -> AuditEvent:
    return AuditEvent(
        action=action, outcome=outcome, organization_id=organization_id,
        actor_user_id=actor_user_id, actor_label=actor_label, target_type=target_type,
        target_id=(target_id or None) and str(target_id)[:64], summary=summary,
        detail_json=json.dumps(detail, default=str) if detail else None,
        ip=ip, request_id=request_id,
    )


def _request_bits(request: Request | None) -> dict[str, Any]:
    if request is None:
        return {"ip": None, "request_id": None}
    ctx = getattr(request.state, "ctx", None)
    return {
        "ip": request.client.host if request.client else None,
        "request_id": getattr(request.state, "request_id", None),
        "ctx": ctx,
    }


def record(db: Session, *, action: str, outcome: str = "success", request: Request | None = None,
           organization_id: str | None = None, target_type: str | None = None,
           target_id: str | None = None, summary: str | None = None,
           detail: dict[str, Any] | None = None, actor_user_id: str | None = None,
           actor_label: str | None = None) -> None:
    bits = _request_bits(request)
    ctx = bits.get("ctx")
    if ctx is not None:
        actor_user_id = actor_user_id or ctx.principal.user_id
        actor_label = actor_label or ctx.principal.label
        organization_id = organization_id or ctx.org_id
    db.add(_event(action=action, outcome=outcome, organization_id=organization_id,
                  actor_user_id=actor_user_id, actor_label=actor_label, target_type=target_type,
                  target_id=target_id, summary=summary, detail=detail, ip=bits["ip"],
                  request_id=bits["request_id"]))
    db.flush()


def defer(request: Request, db: Session | None, *, action: str, outcome: str,
          organization_id: str | None = None, target_type: str | None = None,
          target_id: str | None = None, summary: str | None = None,
          detail: dict[str, Any] | None = None, actor_user_id: str | None = None,
          actor_label: str | None = None) -> None:
    bits = _request_bits(request)
    ctx = bits.get("ctx")
    if ctx is not None:
        actor_user_id = actor_user_id or ctx.principal.user_id
        actor_label = actor_label or ctx.principal.label
    pending = getattr(request.state, _DEFERRED, None)
    if pending is None:
        pending = []
        setattr(request.state, _DEFERRED, pending)
    pending.append((db.get_bind() if db is not None else None, dict(
        action=action, outcome=outcome, organization_id=organization_id,
        actor_user_id=actor_user_id, actor_label=actor_label, target_type=target_type,
        target_id=target_id, summary=summary, detail=detail, ip=bits["ip"],
        request_id=bits["request_id"],
    )))
    log.info("audit %s %s org=%s target=%s:%s", action, outcome, organization_id, target_type, target_id)


def flush_deferred(state: Any) -> None:
    """Write deferred events. Called by the middleware after the response is produced."""
    pending = getattr(state, _DEFERRED, None) if state is not None else None
    if not pending:
        return
    from backend.app.persistence.database import engine as default_engine

    for bind, fields in pending:
        db = Session(bind=bind or default_engine)
        try:
            db.add(_event(**fields))
            db.commit()
        except Exception:  # noqa: BLE001 — auditing must never break a response
            db.rollback()
            log.exception("Could not write audit event %s", fields.get("action"))
        finally:
            db.close()
    pending.clear()
