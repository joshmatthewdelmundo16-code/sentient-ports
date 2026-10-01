"""Live refresh — D27.

GET /api/events/stream is a server-sent-events stream of new activity in the CURRENT
organization: dataset changes, runs, connector ingestions and audit events. It is backed by
database polling (LIVE_STREAM_INTERVAL_S, default 2 s) rather than in-process pub/sub, so it
is correct with several app instances and survives restarts; the React app re-fetches what
changed when an event arrives. Each stream ends after `max_seconds` (≤ 300) and the browser's
EventSource reconnects on its own, so no connection is held indefinitely.

EventSource cannot send custom headers, so the scope arrives as the `scope` query parameter;
request_context validates it against the caller's memberships exactly like X-Scope-Org.

This is live REFRESH of platform activity. It is not a real-time sensor feed: data only
arrives through connectors, and the demo telemetry is simulated and labelled as such.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.persistence.database import AuditEvent, ChangeEvent, ExecutionRun, IngestionRun, get_db
from backend.app.security.auth import RequestContext, request_context
from backend.app.security.tenancy import tenant_scope

router = APIRouter(prefix="/api/events", tags=["Live"])


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def poll_changes(bind: Any, org_id: str | None, since: datetime, include_unowned: bool) -> tuple[list[dict[str, Any]], datetime]:
    """New activity since `since`, read in a fresh session scoped to the organization."""
    events: list[dict[str, Any]] = []
    newest = since
    db = Session(bind=bind)
    try:
        ctx = tenant_scope(db, org_id, include_unowned=include_unowned) if org_id else None
        if ctx:
            ctx.__enter__()
        try:
            for model, kind, col in ((ChangeEvent, "dataset_change", ChangeEvent.created_at),
                                     (ExecutionRun, "run", ExecutionRun.created_at),
                                     (IngestionRun, "ingestion", IngestionRun.created_at)):
                for row in db.execute(select(model).where(col > since).order_by(col).limit(50)).scalars():
                    at = _aware(getattr(row, "created_at"))
                    events.append({"kind": kind, "id": row.id, "at": at.isoformat() if at else None,
                                   "status": getattr(row, "status", None),
                                   "source_type": getattr(row, "source_type", None)})
                    if at and at > newest:
                        newest = at
            q = select(AuditEvent).where(AuditEvent.occurred_at > since)
            if org_id:
                q = q.where(AuditEvent.organization_id == org_id)
            for row in db.execute(q.order_by(AuditEvent.occurred_at).limit(50)).scalars():
                at = _aware(row.occurred_at)
                events.append({"kind": "audit", "id": row.id, "action": row.action, "outcome": row.outcome,
                               "at": at.isoformat() if at else None})
                if at and at > newest:
                    newest = at
        finally:
            if ctx:
                ctx.__exit__(None, None, None)
    finally:
        db.close()
    return events, newest


@router.get("/stream", summary="Server-sent events: new activity in the current organization")
async def stream(request: Request, max_seconds: int = 300, ctx: RequestContext = Depends(request_context),
                 db: Session = Depends(get_db, scope="function")) -> StreamingResponse:
    bind = db.get_bind()
    org_id = ctx.org_id
    include_unowned = ctx.principal.kind == "local"
    limit = max(1, min(int(max_seconds), 300))
    interval = max(0.2, settings.LIVE_STREAM_INTERVAL_S)

    async def gen() -> AsyncIterator[bytes]:
        since = datetime.now(timezone.utc)
        started = anyio.current_time()
        yield b"retry: 3000\n\n"
        yield f"event: ready\ndata: {json.dumps({'scope': org_id, 'interval_s': interval})}\n\n".encode()
        beat = 0.0
        while anyio.current_time() - started < limit:
            if await request.is_disconnected():
                break
            events, since = await anyio.to_thread.run_sync(poll_changes, bind, org_id, since, include_unowned)
            for e in events:
                yield f"event: activity\ndata: {json.dumps(e)}\n\n".encode()
            beat += interval
            if beat >= 15:
                yield b": keep-alive\n\n"
                beat = 0.0
            await anyio.sleep(interval)
        yield b"event: end\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
