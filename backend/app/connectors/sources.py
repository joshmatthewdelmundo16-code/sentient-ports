"""Concrete connectors — D27: CSV upload, REST polling, signed webhooks, telemetry, simulator.

Security properties, each enforced here rather than assumed:

  CSV          ≤ 1 MiB, ≤ 1000 rows, UTF-8, only fields the target contract declares.
  REST poll    HTTPS only; host must be on CONNECTOR_ALLOWED_HOSTS; every resolved address
               must be public (no loopback, private, link-local, multicast or reserved
               ranges) unless CONNECTOR_ALLOW_PRIVATE_HOSTS is on for local testing;
               redirects are not followed; 10 s timeout; ≤ 1 MiB JSON. A secret header may
               only come from an environment variable named CONNECTOR_SECRET_* — so a source
               definition can never exfiltrate DATABASE_URL or any other server secret.
               Residual risk, stated: DNS could change between the address check and the
               connection (rebinding); the exact-host allowlist bounds that to hosts an
               operator already trusts.
  Webhook      HMAC-SHA256 over "<timestamp>.<body>" with a per-source key derived from
               CONNECTOR_SIGNING_KEY (no per-source secret is stored); ±5 minute timestamp
               window; each delivery ID is accepted once.
  Telemetry    readings are idempotent per (source, key); a window aggregate is written to the
               target dataset only through the governed path.
  Simulator    produces readings through the SAME telemetry path, stamped simulated=True and
               source_type="simulated" everywhere they appear.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import ipaddress
import json
import os
import random
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.connectors.base import (
    ConnectorError,
    ingest_record,
    load_config,
    sha256_hex,
    target_dataset,
)
from backend.app.persistence.database import (
    ConnectorSource,
    DataContract,
    Dataset,
    IngestionRun,
    TelemetryReading,
)
from backend.app.services.contract_validation import parse_schema

MAX_CSV_BYTES = 1024 * 1024
MAX_CSV_ROWS = 1000
MAX_REST_BYTES = 1024 * 1024
WEBHOOK_WINDOW_S = 300
SECRET_ENV = re.compile(r"^CONNECTOR_SECRET_[A-Z0-9_]{1,64}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _contract_fields(db: Session, dataset: Dataset) -> dict[str, Any]:
    c = db.execute(select(DataContract).where(DataContract.dataset_id == dataset.id)
                   .order_by(DataContract.created_at.desc())).scalars().first()
    if c is None:
        return {}
    return {f.name: f for f in parse_schema(json.loads(c.schema_json)).fields}


def _coerce(spec: Any, raw: Any) -> Any:
    t = getattr(spec, "type", "number")
    if t in ("number", "integer"):
        if isinstance(raw, bool):
            raise ValueError("expected a number")
        v = float(raw)
        return int(v) if t == "integer" and v.is_integer() else v
    if t == "boolean":
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("true", "1", "yes"):
            return True
        if s in ("false", "0", "no"):
            return False
        raise ValueError("expected true or false")
    return str(raw)


# ---------------------------------------------------------------------------
# CSV upload
# ---------------------------------------------------------------------------

def ingest_csv(db: Session, source: ConnectorSource, *, filename: str, data: bytes) -> IngestionRun:
    dataset = target_dataset(db, source)
    digest = sha256_hex(data)
    ref = f"{filename}@sha256:{digest[:12]}"
    try:
        if not filename.lower().endswith(".csv"):
            raise ConnectorError("Only .csv files are accepted.")
        if len(data) > MAX_CSV_BYTES:
            raise ConnectorError(f"CSV is larger than {MAX_CSV_BYTES} bytes.")
        text = data.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        if rows and [c.strip().lower() for c in rows[0][:2]] == ["field", "value"]:
            rows = rows[1:]
        if len(rows) > MAX_CSV_ROWS:
            raise ConnectorError(f"CSV has more than {MAX_CSV_ROWS} rows.")
        spec = _contract_fields(db, dataset)
        record: dict[str, Any] = {}
        for i, row in enumerate(rows, start=1):
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) != 2:
                raise ConnectorError(f"Row {i}: expected exactly two columns, field and value.")
            field, raw = row[0].strip(), row[1].strip()
            if field not in spec:
                raise ConnectorError(f"Row {i}: {field!r} is not a field of {dataset.name!r}.")
            if field in record:
                raise ConnectorError(f"Row {i}: {field!r} appears twice.")
            try:
                record[field] = _coerce(spec[field], raw)
            except ValueError as exc:
                raise ConnectorError(f"Row {i}: {field}: {exc}") from exc
        if not record:
            raise ConnectorError("The CSV contains no field,value rows.")
    except (ConnectorError, UnicodeDecodeError, csv.Error) as exc:
        return _rejected(db, source, "csv_upload", filename, str(exc), digest)
    return ingest_record(db, kind="csv_upload", dataset=dataset, record=record, source_name=filename,
                         source_ref=ref, content_sha256=digest, source=source, source_type="csv")


def _rejected(db: Session, source: ConnectorSource, kind: str, name: str, error: str,
              digest: str | None = None) -> IngestionRun:
    run = IngestionRun(source_name=name[:512], mapping_key=f"connector:{kind}", status="rejected",
                       error=error[:2000], connector_kind=kind, connector_source_id=source.id,
                       content_sha256=digest)
    db.add(run)
    source.last_run_at, source.last_status, source.last_error = _now(), "rejected", error[:2000]
    db.flush()
    return run


# ---------------------------------------------------------------------------
# REST polling
# ---------------------------------------------------------------------------

def check_url(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    allow_private = settings.CONNECTOR_ALLOW_PRIVATE_HOSTS
    if parts.scheme != "https" and not (allow_private and parts.scheme == "http"):
        raise ConnectorError("REST sources must use https.")
    host = (parts.hostname or "").lower()
    if not host:
        raise ConnectorError("The URL has no host.")
    if parts.username or parts.password:
        raise ConnectorError("Credentials in the URL are not allowed; use a CONNECTOR_SECRET_* header.")
    if host not in settings.CONNECTOR_ALLOWED_HOSTS:
        raise ConnectorError(f"{host!r} is not on CONNECTOR_ALLOWED_HOSTS.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ConnectorError(f"{host!r} does not resolve.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not allow_private and not ip.is_global:
            raise ConnectorError(f"{host!r} resolves to a non-public address; refused.")
    return host, port


def extract(doc: Any, path: str) -> Any:
    cur = doc
    for part in path.split("."):
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            if idx >= len(cur):
                raise ConnectorError(f"Path {path!r}: index {idx} out of range.")
            cur = cur[idx]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise ConnectorError(f"Path {path!r} not found in the response.")
    return cur


def poll_rest(db: Session, source: ConnectorSource, *, transport: Any = None) -> IngestionRun:
    import httpx

    dataset = target_dataset(db, source)
    cfg = load_config(source)
    url = str(cfg.get("url", ""))
    fields = cfg.get("fields") or {}
    try:
        if not isinstance(fields, dict) or not fields:
            raise ConnectorError("Configure at least one field: {\"field\": \"json.path\"}.")
        check_url(url)
        headers = {"Accept": "application/json", "User-Agent": "port-decision-platform/connector"}
        env_name = cfg.get("secret_env")
        if env_name:
            if not SECRET_ENV.match(str(env_name)):
                raise ConnectorError("secret_env must be named CONNECTOR_SECRET_<NAME>.")
            secret = os.environ.get(str(env_name))
            if not secret:
                raise ConnectorError(f"{env_name} is not set on the server.")
            headers[str(cfg.get("secret_header", "Authorization"))] = (
                f"Bearer {secret}" if cfg.get("secret_scheme", "bearer") == "bearer" else secret)
        client = httpx.Client(timeout=10.0, follow_redirects=False, transport=transport)
        with client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code != 200:
                    raise ConnectorError(f"The source answered HTTP {resp.status_code}.")
                if "json" not in resp.headers.get("content-type", ""):
                    raise ConnectorError("The source did not return JSON.")
                body = b""
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > MAX_REST_BYTES:
                        raise ConnectorError("The response is larger than 1 MiB.")
        doc = json.loads(body)
        spec = _contract_fields(db, dataset)
        record: dict[str, Any] = {}
        for field, path in fields.items():
            if field not in spec:
                raise ConnectorError(f"{field!r} is not a field of {dataset.name!r}.")
            record[field] = _coerce(spec[field], extract(doc, str(path)))
    except (ConnectorError, ValueError, httpx.HTTPError) as exc:
        return _rejected(db, source, "rest_poll", urlsplit(url).netloc or source.name, str(exc))
    digest = sha256_hex(body)
    return ingest_record(db, kind="rest_poll", dataset=dataset, record=record,
                         source_name=f"GET {urlsplit(url).netloc}{urlsplit(url).path}",
                         source_ref=f"{url}@sha256:{digest[:12]}", content_sha256=digest,
                         source=source, source_type="rest")


# ---------------------------------------------------------------------------
# Signed webhooks
# ---------------------------------------------------------------------------

def signing_key(source_id: str) -> bytes:
    master = settings.CONNECTOR_SIGNING_KEY
    if not master:
        raise ConnectorError("Webhooks are disabled: CONNECTOR_SIGNING_KEY is not set on the server.")
    return hmac.new(master.encode(), f"webhook:{source_id}".encode(), hashlib.sha256).digest()


def sign(source_id: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(signing_key(source_id), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def verify_webhook(source_id: str, timestamp: str | None, signature: str | None, body: bytes) -> None:
    if not timestamp or not signature:
        raise ConnectorError("Missing X-Platform-Timestamp or X-Platform-Signature.")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise ConnectorError("X-Platform-Timestamp must be Unix seconds.") from exc
    if abs(time.time() - ts) > WEBHOOK_WINDOW_S:
        raise ConnectorError("The timestamp is outside the 5-minute window (possible replay).")
    if not hmac.compare_digest(sign(source_id, timestamp, body), signature):
        raise ConnectorError("The signature does not match.")


def receive_webhook(db: Session, source: ConnectorSource, *, delivery_id: str, body: bytes) -> IngestionRun:
    dataset = target_dataset(db, source)
    ref = f"delivery:{delivery_id}"
    dup = db.execute(select(IngestionRun).where(IngestionRun.connector_source_id == source.id,
                                                IngestionRun.source_name == ref)).scalars().first()
    if dup is not None:
        return dup
    try:
        payload = json.loads(body)
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, dict) or not values:
            raise ConnectorError('Send {"values": {"field": value, …}}.')
        spec = _contract_fields(db, dataset)
        record = {}
        for field, raw in values.items():
            if field not in spec:
                raise ConnectorError(f"{field!r} is not a field of {dataset.name!r}.")
            record[field] = _coerce(spec[field], raw)
    except (ConnectorError, ValueError) as exc:
        return _rejected(db, source, "webhook", ref, str(exc), sha256_hex(body))
    return ingest_record(db, kind="webhook", dataset=dataset, record=record, source_name=ref,
                         source_ref=f"webhook:{source.name}:{delivery_id}", content_sha256=sha256_hex(body),
                         source=source, source_type="webhook")


# ---------------------------------------------------------------------------
# Telemetry (and the labelled simulator)
# ---------------------------------------------------------------------------

def _window_record(db: Session, source: ConnectorSource, cfg: dict[str, Any]) -> dict[str, Any]:
    window = timedelta(minutes=float(cfg.get("window_minutes", 60)))
    since = _now() - window
    readings = db.execute(select(TelemetryReading).where(
        TelemetryReading.source_id == source.id, TelemetryReading.observed_at >= since)).scalars().all()
    record: dict[str, Any] = {}
    for field, rule in (cfg.get("fields") or {}).items():
        vals = [r.value for r in readings if r.metric == rule.get("metric")]
        if not vals:
            continue
        agg = rule.get("agg", "mean")
        if agg == "count":
            hours = window.total_seconds() / 3600.0
            record[field] = len(vals) / hours if rule.get("per_hour") else float(len(vals))
        elif agg == "last":
            record[field] = vals[-1]
        elif agg == "max":
            record[field] = max(vals)
        else:
            record[field] = sum(vals) / len(vals)
    return record


def ingest_readings(db: Session, source: ConnectorSource, readings: list[dict[str, Any]], *,
                    simulated: bool = False) -> dict[str, Any]:
    if not isinstance(readings, list) or not readings or len(readings) > 5000:
        raise ConnectorError("Send 1–5000 readings.")
    cfg = load_config(source)
    metrics = {rule.get("metric") for rule in (cfg.get("fields") or {}).values()}
    accepted = duplicates = 0
    for r in readings:
        metric = str(r.get("metric", ""))
        if metric not in metrics:
            raise ConnectorError(f"Unknown metric {metric!r}; configured: {sorted(m for m in metrics if m)}.")
        value = float(r["value"])
        observed = r.get("observed_at")
        at = datetime.fromisoformat(observed) if isinstance(observed, str) else _now()
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        key = str(r.get("key") or hashlib.sha256(f"{metric}|{at.isoformat()}|{value}".encode()).hexdigest()[:40])
        exists = db.execute(select(TelemetryReading.id).where(
            TelemetryReading.source_id == source.id, TelemetryReading.idempotency_key == key)).first()
        if exists:
            duplicates += 1
            continue
        db.add(TelemetryReading(source_id=source.id, metric=metric, value=value, observed_at=at,
                                idempotency_key=key[:128], simulated=simulated))
        accepted += 1
    db.flush()
    record = _window_record(db, source, cfg)
    run = None
    if record:
        run = ingest_record(db, kind="simulated" if simulated else "telemetry",
                            dataset=target_dataset(db, source), record=record,
                            source_name=f"{'Simulated' if simulated else 'Telemetry'} window · {source.name}",
                            source_ref=f"{source.kind}:{source.name}:window={cfg.get('window_minutes', 60)}m",
                            source=source, source_type="simulated" if simulated else "telemetry")
    return {"accepted": accepted, "duplicates": duplicates, "window": record,
            "ingestion_id": run.id if run else None, "status": run.status if run else "no_window",
            "graph_run_id": run.graph_run_id if run else None, "simulated": simulated}


def simulate(db: Session, source: ConnectorSource, *, minutes: int = 30, seed: int | None = None) -> dict[str, Any]:
    """Synthetic berth-sensor readings. NOT real data — every reading is stamped simulated."""
    cfg = load_config(source)
    rng = random.Random(seed if seed is not None else int(time.time()))
    profile = cfg.get("simulate") or {}
    now = _now()
    readings = []
    for i in range(max(1, min(minutes, 240))):
        at = (now - timedelta(minutes=minutes - i)).isoformat()
        for metric, spec in profile.items():
            mean, spread = float(spec.get("mean", 0)), float(spec.get("spread", 0))
            if spec.get("kind") == "events":
                if rng.random() < float(spec.get("rate_per_minute", 0.05)):
                    readings.append({"metric": metric, "value": 1.0, "observed_at": at,
                                     "key": f"sim-{metric}-{at}-{rng.random():.6f}"})
            else:
                readings.append({"metric": metric, "value": max(0.0, rng.gauss(mean, spread)),
                                 "observed_at": at, "key": f"sim-{metric}-{at}-{rng.random():.6f}"})
    if not readings:
        raise ConnectorError("The simulator profile produced no readings.")
    return ingest_readings(db, source, readings, simulated=True)
