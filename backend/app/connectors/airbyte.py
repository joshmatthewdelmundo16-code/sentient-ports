"""Airbyte connector — D28.

Airbyte is the data mover; the platform stays the only writer of datasets.

    Airbyte source (Excel on SharePoint / Google Drive / S3, a database, a SaaS API …)
      → Airbyte connection → Postgres destination → staging schema of THIS database
      → "airbyte" connector reads the synced rows → governed path (contract → dataset →
        change event → propagation → results + lineage), exactly like every other connector.

Airbyte never touches a platform table and never bypasses contract validation. The platform can
also trigger the Airbyte connection's sync through Airbyte's public API and follow the job.

Row layouts supported (set per source as ``layout``):
  wide         one row per sync, one column per contract field (``fields`` optionally maps
               contract field → column). The newest row by ``_airbyte_extracted_at`` wins.
  field_value  a two-column sheet: ``field_column`` / ``value_column`` (default field / value) —
               the same shape as the CSV connector. Newest value per field wins.

Security properties, enforced here:
  * the table name is a plain identifier, and must start with the source organization's stream
    prefix ``<org_key>__`` (Airbyte's per-connection "stream prefix"), so one organization's
    source cannot read another organization's synced data;
  * the table must carry Airbyte's ``_airbyte_extracted_at`` column — it must really be an
    Airbyte destination table, not an arbitrary platform table;
  * identifiers are resolved by SQLAlchemy reflection, never interpolated into SQL;
  * at most MAX_ROWS rows are read; only fields the target contract declares are accepted;
  * the Airbyte API credential comes from the environment only and is never returned or logged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import MetaData, Table, select
from sqlalchemy.exc import NoSuchTableError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.connectors.base import ConnectorError, ingest_record, load_config, sha256_hex, target_dataset
from backend.app.persistence.database import ConnectorSource, IngestionRun, Organization

KIND = "airbyte"
EXTRACTED_AT = "_airbyte_extracted_at"
MAX_ROWS = 1000
LAYOUTS = ("wide", "field_value")
IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# Airbyte job status → a plain platform word.
JOB_STATUS = {"pending": "running", "running": "running", "incomplete": "running",
              "succeeded": "succeeded", "failed": "failed", "cancelled": "failed"}


# ---------------------------------------------------------------------------
# Configuration checks
# ---------------------------------------------------------------------------

class AirbyteConfigError(ConnectorError):
    """The platform is not configured to talk to Airbyte's API."""


def stream_prefix(db: Session, organization_id: str | None) -> str:
    """``<org_key>__`` for an organization's source; empty for the unscoped single-tenant demo."""
    if not organization_id:
        return ""
    org = db.get(Organization, organization_id)
    key = org.org_key if org is not None else organization_id
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_") + "__"


def validate_config(db: Session, organization_id: str | None, cfg: dict[str, Any]) -> None:
    table = str(cfg.get("stream_table") or "")
    if not IDENT.match(table):
        raise ConnectorError('Airbyte sources need "stream_table": a lower-case table name.')
    prefix = stream_prefix(db, organization_id)
    if prefix and not table.startswith(prefix):
        raise ConnectorError(f'stream_table must start with this organization\'s stream prefix "{prefix}" '
                             "(set it as the Airbyte connection's destination stream prefix).")
    layout = cfg.get("layout", "wide")
    if layout not in LAYOUTS:
        raise ConnectorError(f"layout must be one of {list(LAYOUTS)}.")
    for key in ("field_column", "value_column"):
        if key in cfg and not IDENT.match(str(cfg[key])):
            raise ConnectorError(f"{key} must be a lower-case column name.")
    fields = cfg.get("fields")
    if fields is not None and (not isinstance(fields, dict)
                               or not all(IDENT.match(str(c)) for c in fields.values())):
        raise ConnectorError('"fields" must map contract fields to lower-case column names.')
    conn = cfg.get("connection_id")
    if conn is not None and not re.fullmatch(r"[0-9a-fA-F-]{8,64}", str(conn)):
        raise ConnectorError("connection_id must be an Airbyte connection UUID.")


# ---------------------------------------------------------------------------
# Reading the staging table
# ---------------------------------------------------------------------------

def _table(db: Session, name: str) -> Table:
    schema = settings.AIRBYTE_STAGING_SCHEMA or None
    try:
        return Table(name, MetaData(), schema=schema, autoload_with=db.connection())
    except NoSuchTableError as exc:
        where = f"{schema}.{name}" if schema else name
        raise ConnectorError(f"Airbyte has not synced {where!r} yet (table not found).") from exc


def _ts(v: Any) -> str:
    return v.isoformat() if isinstance(v, datetime) else str(v)


def read_record(db: Session, source: ConnectorSource) -> tuple[dict[str, Any], str, str]:
    """Return (record, source_ref, digest) from the newest synced rows. Raises ConnectorError."""
    from backend.app.connectors.sources import _coerce, _contract_fields

    cfg = load_config(source)
    validate_config(db, source.organization_id, cfg)
    dataset = target_dataset(db, source)
    spec = _contract_fields(db, dataset)
    table = _table(db, cfg["stream_table"])
    if EXTRACTED_AT not in table.c:
        raise ConnectorError(f"{cfg['stream_table']!r} is not an Airbyte destination table "
                             f"(no {EXTRACTED_AT} column).")
    newest_first = table.c[EXTRACTED_AT].desc()
    conn = db.connection()
    record: dict[str, Any] = {}
    try:
        if cfg.get("layout", "wide") == "wide":
            row = conn.execute(select(table).order_by(newest_first).limit(1)).mappings().first()
            if row is None:
                raise ConnectorError("The Airbyte stream is empty.")
            mapping = cfg.get("fields") or {f: f for f in spec if f in table.c}
            for field, column in mapping.items():
                if field not in spec:
                    raise ConnectorError(f"{field!r} is not a field of {dataset.name!r}.")
                if column not in table.c:
                    raise ConnectorError(f"Column {column!r} is not in the Airbyte stream.")
                if row[column] is not None:
                    record[field] = _coerce(spec[field], row[column])
            extracted = _ts(row[EXTRACTED_AT])
        else:
            fcol, vcol = cfg.get("field_column", "field"), cfg.get("value_column", "value")
            for col in (fcol, vcol):
                if col not in table.c:
                    raise ConnectorError(f"Column {col!r} is not in the Airbyte stream.")
            rows = conn.execute(select(table.c[fcol], table.c[vcol], table.c[EXTRACTED_AT])
                                .order_by(newest_first).limit(MAX_ROWS + 1)).all()
            if len(rows) > MAX_ROWS:
                raise ConnectorError(f"The Airbyte stream has more than {MAX_ROWS} rows.")
            if not rows:
                raise ConnectorError("The Airbyte stream is empty.")
            extracted = _ts(rows[0][2])
            for field, raw, _at in rows:          # newest first: the first value per field wins
                field = str(field).strip() if field is not None else ""
                if not field or field in record:
                    continue
                if field not in spec:
                    raise ConnectorError(f"{field!r} is not a field of {dataset.name!r}.")
                record[field] = _coerce(spec[field], raw)
    except ValueError as exc:
        raise ConnectorError(f"Airbyte value rejected: {exc}") from exc
    except SQLAlchemyError as exc:
        raise ConnectorError(f"Could not read the Airbyte stream: {exc.__class__.__name__}") from exc
    if not record:
        raise ConnectorError("The Airbyte stream contains no values for this dataset's fields.")
    digest = sha256_hex(json.dumps(record, sort_keys=True, default=str).encode())
    schema = settings.AIRBYTE_STAGING_SCHEMA
    ref = f"airbyte:{schema + '.' if schema else ''}{cfg['stream_table']}@{extracted}"
    return record, ref, digest


def ingest(db: Session, source: ConnectorSource) -> IngestionRun:
    """Ingest the newest synced rows through the governed path. Never raises for bad data: the
    rejection is recorded on the returned IngestionRun, like every other connector."""
    from backend.app.connectors.sources import _rejected

    dataset = target_dataset(db, source)
    try:
        record, ref, digest = read_record(db, source)
    except ConnectorError as exc:
        return _rejected(db, source, KIND, f"airbyte:{load_config(source).get('stream_table', '?')}", str(exc), None)
    return ingest_record(db, kind=KIND, dataset=dataset, record=record, source_name=ref.split("@")[0],
                         source_ref=ref, content_sha256=digest, source=source, source_type=KIND)


# ---------------------------------------------------------------------------
# Airbyte public API: trigger a connection sync, follow the job
# ---------------------------------------------------------------------------

class AirbyteTransport(Protocol):
    def post(self, path: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]: ...
    def get(self, path: str, headers: dict[str, str]) -> dict[str, Any]: ...


class HttpxAirbyteTransport:
    def __init__(self, base_url: str, timeout_s: float) -> None:
        self._base, self._timeout = base_url.rstrip("/"), timeout_s

    def _call(self, method: str, path: str, headers: dict[str, str], body: dict[str, Any] | None) -> dict[str, Any]:
        import httpx

        try:
            with httpx.Client(base_url=self._base, timeout=self._timeout, follow_redirects=False) as c:
                resp = c.request(method, path, json=body, headers={"Accept": "application/json", **headers})
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Airbyte unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise ConnectorError(f"Airbyte API refused the request (HTTP {resp.status_code}).")
        return resp.json()

    def post(self, path: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        return self._call("POST", path, headers, body)

    def get(self, path: str, headers: dict[str, str]) -> dict[str, Any]:
        return self._call("GET", path, headers, None)


@dataclass
class AirbyteClient:
    transport: AirbyteTransport

    @classmethod
    def from_env(cls) -> "AirbyteClient":
        if not settings.AIRBYTE_API_URL:
            raise AirbyteConfigError("Airbyte sync triggering needs AIRBYTE_API_URL (Airbyte's own schedule "
                                 "still works without it, and ingestion does not need it).")
        if not (settings.AIRBYTE_API_TOKEN or (settings.AIRBYTE_CLIENT_ID and settings.AIRBYTE_CLIENT_SECRET)):
            raise AirbyteConfigError("Set AIRBYTE_API_TOKEN, or AIRBYTE_CLIENT_ID and AIRBYTE_CLIENT_SECRET.")
        return cls(HttpxAirbyteTransport(settings.AIRBYTE_API_URL, settings.AIRBYTE_TIMEOUT_S))

    def _auth(self) -> dict[str, str]:
        token = settings.AIRBYTE_API_TOKEN
        if not token:
            out = self.transport.post("/v1/applications/token", {
                "client_id": settings.AIRBYTE_CLIENT_ID, "client_secret": settings.AIRBYTE_CLIENT_SECRET}, {})
            token = out.get("access_token") or ""
            if not token:
                raise ConnectorError("Airbyte did not issue an access token.")
        return {"Authorization": f"Bearer {token}"}

    def trigger_sync(self, connection_id: str) -> dict[str, Any]:
        out = self.transport.post("/v1/jobs", {"connectionId": connection_id, "jobType": "sync"}, self._auth())
        return {"job_id": out.get("jobId"), "airbyte_status": out.get("status"),
                "status": JOB_STATUS.get(str(out.get("status")), "running")}

    def job(self, job_id: int | str) -> dict[str, Any]:
        out = self.transport.get(f"/v1/jobs/{int(job_id)}", self._auth())
        return {"job_id": out.get("jobId", job_id), "airbyte_status": out.get("status"),
                "status": JOB_STATUS.get(str(out.get("status")), "running"),
                "rows_synced": out.get("rowsSynced")}
