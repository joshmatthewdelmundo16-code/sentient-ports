"""Connector API — D27.

Session-authenticated management and ingestion (/api/connectors/*), plus one route that is
NOT session-authenticated: POST /api/webhooks/{source_id}, which is authenticated by an HMAC
signature instead and scoped to the source's own organization.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.connectors.base import CONFIGURABLE, KINDS, ConnectorError, load_config
from backend.app.connectors import airbyte
from backend.app.connectors import sources as src
from backend.app.persistence.database import ConnectorSource, Dataset, IngestionRun, get_db
from backend.app.product.util import iso
from backend.app.security import audit
from backend.app.security.auth import RequestContext, request_context
from backend.app.security.tenancy import get_scope, tenant_scope

router = APIRouter(prefix="/api/connectors", tags=["Connectors"])
public = APIRouter(tags=["Connectors"])


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceCreate(_Req):
    name: str = Field(..., max_length=255)
    kind: str
    target_dataset_id: str
    config: dict[str, Any] = Field(default_factory=dict)
    poll_interval_s: int | None = Field(None, ge=30, le=86400)


class Readings(_Req):
    readings: list[dict[str, Any]] = Field(..., min_length=1, max_length=5000)


def _out(s: ConnectorSource, db: Session) -> dict[str, Any]:
    ds = db.get(Dataset, s.target_dataset_id) if s.target_dataset_id else None
    runs = db.execute(select(IngestionRun).where(IngestionRun.connector_source_id == s.id)
                      .order_by(IngestionRun.created_at.desc()).limit(5)).scalars().all()
    cfg = load_config(s)
    return {
        "id": s.id, "name": s.name, "kind": s.kind, "kind_label": KINDS.get(s.kind, {}).get("label", s.kind),
        "mode": KINDS.get(s.kind, {}).get("mode"), "status": s.status,
        "simulated": s.kind == "simulated",
        "target_dataset_id": s.target_dataset_id,
        "target_dataset": (ds.display_name or ds.name) if ds else None,
        "config": {k: v for k, v in cfg.items() if k != "secret_env"} | ({"secret_env": "configured"} if cfg.get("secret_env") else {}),
        "poll_interval_s": s.poll_interval_s, "last_run_at": iso(s.last_run_at),
        "last_status": s.last_status, "last_error": s.last_error,
        "recent_runs": [{"id": r.id, "status": r.status, "at": iso(r.created_at), "error": r.error,
                         "graph_run_id": r.graph_run_id, "source_name": r.source_name} for r in runs],
    }


def _source(db: Session, source_id: str) -> ConnectorSource:
    s = db.execute(select(ConnectorSource).where(ConnectorSource.id == source_id)).scalars().first()
    if s is None:
        raise HTTPException(status_code=404, detail="Connector source not found in this organization.")
    return s


def _record_run(db: Session, request: Request, s: ConnectorSource, status: str, ident: str | None) -> None:
    audit.record(db, action="connector.run", request=request, target_type="connector", target_id=s.id,
                 outcome="failure" if status == "rejected" else "success",
                 summary=f"{KINDS.get(s.kind, {}).get('label', s.kind)} · {s.name} · {status}",
                 detail={"ingestion_id": ident})


@router.get("/kinds", summary="Connector kinds and their honest implementation status")
def kinds() -> dict[str, Any]:
    return {"kinds": KINDS, "configurable": list(CONFIGURABLE),
            "webhooks_enabled": bool(settings.CONNECTOR_SIGNING_KEY),
            "rest_allowed_hosts": settings.CONNECTOR_ALLOWED_HOSTS,
            "airbyte_api_configured": bool(settings.AIRBYTE_API_URL),
            "airbyte_staging_schema": settings.AIRBYTE_STAGING_SCHEMA,
            "poll_scheduler_enabled": settings.POLL_SCHEDULER_ENABLED}


@router.get("", summary="Connector sources of this organization")
def list_sources(db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    rows = db.execute(select(ConnectorSource).order_by(ConnectorSource.created_at)).scalars().all()
    return [_out(s, db) for s in rows]


@router.post("", status_code=201, summary="Define a connector source (admins)")
def create_source(body: SourceCreate, request: Request, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    if body.kind not in CONFIGURABLE:
        raise HTTPException(status_code=422, detail=f"kind must be one of {list(CONFIGURABLE)}; "
                                                    f"{KINDS.get(body.kind, {}).get('note', 'unknown kind')}")
    ds = db.execute(select(Dataset).where(Dataset.id == body.target_dataset_id)).scalars().first()
    if ds is None:
        raise HTTPException(status_code=422, detail="Target dataset not found in this organization.")
    if body.kind == "rest_poll":
        if not body.config.get("url") or not isinstance(body.config.get("fields"), dict):
            raise HTTPException(status_code=422, detail='REST sources need "url" and "fields".')
        if body.config.get("secret_env") and not src.SECRET_ENV.match(str(body.config["secret_env"])):
            raise HTTPException(status_code=422, detail="secret_env must be named CONNECTOR_SECRET_<NAME>.")
    if body.kind in ("telemetry", "simulated") and not isinstance(body.config.get("fields"), dict):
        raise HTTPException(status_code=422, detail='Telemetry sources need "fields": {field: {metric, agg}}.')
    if body.kind == airbyte.KIND:
        scope = get_scope(db)
        try:
            airbyte.validate_config(db, scope.org_id if scope else None, body.config)
        except ConnectorError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    s = ConnectorSource(name=body.name.strip(), kind=body.kind, target_dataset_id=ds.id,
                        config_json=json.dumps(body.config), poll_interval_s=body.poll_interval_s)
    db.add(s)
    db.flush()
    audit.record(db, action="connector.created", request=request, target_type="connector", target_id=s.id,
                 summary=f"Defined {KINDS[body.kind]['label']} · {s.name}")
    return _out(s, db)


@router.post("/{source_id}/csv", summary="Upload a CSV of field,value rows to this source")
def upload_csv(source_id: str, request: Request, file: UploadFile = File(...),
               db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    s = _source(db, source_id)
    if s.kind != "csv_upload":
        raise HTTPException(status_code=409, detail="This source is not a CSV upload source.")
    data = file.file.read(src.MAX_CSV_BYTES + 1)
    run = src.ingest_csv(db, s, filename=file.filename or "upload.csv", data=data)
    _record_run(db, request, s, run.status, run.id)
    return {"ingestion_id": run.id, "status": run.status, "error": run.error, "graph_run_id": run.graph_run_id}


@router.post("/{source_id}/poll", summary="Poll a REST source now")
def poll_now(source_id: str, request: Request, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    s = _source(db, source_id)
    if s.kind != "rest_poll":
        raise HTTPException(status_code=409, detail="This source is not a REST polling source.")
    run = src.poll_rest(db, s)
    _record_run(db, request, s, run.status, run.id)
    return {"ingestion_id": run.id, "status": run.status, "error": run.error, "graph_run_id": run.graph_run_id}


def _airbyte_source(db: Session, source_id: str) -> ConnectorSource:
    s = _source(db, source_id)
    if s.kind != airbyte.KIND:
        raise HTTPException(status_code=409, detail="This source is not an Airbyte source.")
    return s


@router.post("/{source_id}/airbyte/ingest", summary="Ingest the rows Airbyte last synced for this source")
def airbyte_ingest(source_id: str, request: Request, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    s = _airbyte_source(db, source_id)
    run = airbyte.ingest(db, s)
    _record_run(db, request, s, run.status, run.id)
    return {"ingestion_id": run.id, "status": run.status, "error": run.error, "graph_run_id": run.graph_run_id,
            "source_ref": run.source_name}


@router.post("/{source_id}/airbyte/sync", summary="Trigger this source's Airbyte connection sync")
def airbyte_sync(source_id: str, request: Request, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    s = _airbyte_source(db, source_id)
    conn = load_config(s).get("connection_id")
    if not conn:
        raise HTTPException(status_code=409, detail='This Airbyte source has no "connection_id" to sync.')
    try:
        out = airbyte.AirbyteClient.from_env().trigger_sync(str(conn))
    except airbyte.AirbyteConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    audit.record(db, action="connector.airbyte_sync", request=request, target_type="connector", target_id=s.id,
                 summary=f"Triggered Airbyte sync · {s.name}", detail={"job_id": out.get("job_id")})
    return out


@router.get("/{source_id}/airbyte/jobs/{job_id}", summary="Status of an Airbyte sync job")
def airbyte_job(source_id: str, job_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    _airbyte_source(db, source_id)
    try:
        return airbyte.AirbyteClient.from_env().job(job_id)
    except airbyte.AirbyteConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/{source_id}/readings", summary="Send telemetry readings (idempotent per key)")
def readings(source_id: str, body: Readings, request: Request,
             db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    s = _source(db, source_id)
    if s.kind != "telemetry":
        raise HTTPException(status_code=409, detail="This source is not a telemetry source.")
    try:
        out = src.ingest_readings(db, s, body.readings)
    except (ConnectorError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _record_run(db, request, s, out["status"], out["ingestion_id"])
    return out


@router.post("/{source_id}/simulate", summary="Generate LABELLED simulated readings through the telemetry path")
def simulate(source_id: str, request: Request, minutes: int = 30, seed: int | None = None,
             db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    s = _source(db, source_id)
    if s.kind != "simulated":
        raise HTTPException(status_code=409, detail="Only a simulated source can generate simulated readings.")
    try:
        out = src.simulate(db, s, minutes=minutes, seed=seed)
    except ConnectorError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _record_run(db, request, s, out["status"], out["ingestion_id"])
    return out


@router.get("/{source_id}/webhook-secret", summary="Signing details for a webhook source (admins)")
def webhook_secret(source_id: str, ctx: RequestContext = Depends(request_context),
                   db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    s = _source(db, source_id)
    if s.kind != "webhook":
        raise HTTPException(status_code=409, detail="This source is not a webhook source.")
    try:
        key = src.signing_key(s.id).hex()
    except ConnectorError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"url": f"/api/webhooks/{s.id}", "signing_key_hex": key,
            "headers": {"X-Platform-Timestamp": "<unix seconds>", "X-Platform-Delivery": "<unique id>",
                        "X-Platform-Signature": "sha256=<hex HMAC-SHA256 of '<timestamp>.<body>'>"},
            "body": {"values": {"<field>": "<value>"}}}


@router.post("/{source_id}/{action}", summary="Pause or resume a source (admins)")
def pause_resume(source_id: str, action: str, request: Request,
                 db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    if action not in ("pause", "resume"):
        raise HTTPException(status_code=404, detail="Unknown action.")
    s = _source(db, source_id)
    s.status = "paused" if action == "pause" else "active"
    db.flush()
    audit.record(db, action=f"connector.{action}d", request=request, target_type="connector", target_id=s.id)
    return _out(s, db)


# ---------------------------------------------------------------------------
# Signed webhook (no session; HMAC-authenticated; scoped to the source's organization)
# ---------------------------------------------------------------------------

@public.post("/api/webhooks/{source_id}", summary="Signed webhook delivery (HMAC; no session)")
async def webhook(source_id: str, request: Request, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    body = await request.body()
    source = db.get(ConnectorSource, source_id)
    ts = request.headers.get("x-platform-timestamp")
    sig = request.headers.get("x-platform-signature")
    delivery = (request.headers.get("x-platform-delivery") or "")[:128]
    try:
        if source is None or source.kind != "webhook" or source.status != "active":
            raise ConnectorError("Unknown or inactive webhook source.")
        src.verify_webhook(source.id, ts, sig, body)
        if not delivery:
            raise ConnectorError("Missing X-Platform-Delivery.")
    except ConnectorError as exc:
        audit.defer(request, db, action="webhook.rejected", outcome="denied",
                    organization_id=source.organization_id if source else None,
                    target_type="connector", target_id=source_id, summary=str(exc))
        raise HTTPException(status_code=401 if source is not None else 404, detail=str(exc))
    import anyio

    def _ingest() -> dict[str, Any]:
        with tenant_scope(db, source.organization_id):
            run = src.receive_webhook(db, source, delivery_id=delivery, body=body)
            audit.record(db, action="connector.run", target_type="connector", target_id=source.id,
                         organization_id=source.organization_id, actor_label=f"webhook:{source.name}",
                         outcome="failure" if run.status == "rejected" else "success",
                         summary=f"Webhook delivery {delivery} · {run.status}")
            return {"ingestion_id": run.id, "status": run.status, "error": run.error,
                    "graph_run_id": run.graph_run_id}

    return await anyio.to_thread.run_sync(_ingest)
