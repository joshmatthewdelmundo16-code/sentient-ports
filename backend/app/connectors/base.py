"""Connector boundary — D27.

Every connector, whatever the transport, ends in the same governed path:

    source → record → contract validation → dataset write (hash-based change detection)
           → ChangeEvent → propagation GraphRun → results + lineage

and every attempt — accepted, unchanged or rejected — is an IngestionRun carrying the
connector kind and source, so provenance and activity work identically for an Excel upload, a
CSV file, a REST poll, a signed webhook, a telemetry window or a governed network share.

Partial records (a webhook that sends one field) are merged onto the dataset's current value
before validation, so a connector can update a field without restating the whole record.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.persistence.database import ConnectorSource, Dataset, IngestionRun

KINDS: dict[str, dict[str, Any]] = {
    "excel_upload": {"label": "Excel workbook upload", "mode": "upload", "status": "implemented",
                     "note": "Guided workflow in Assumptions & Excel."},
    "csv_upload": {"label": "CSV upload", "mode": "upload", "status": "implemented",
                   "note": "field,value rows mapped onto one dataset."},
    "rest_poll": {"label": "REST API polling", "mode": "poll", "status": "implemented",
                  "note": "HTTPS JSON only, exact host allowlist, no redirects, public addresses only."},
    "webhook": {"label": "Signed webhook", "mode": "push", "status": "implemented",
                "note": "HMAC-SHA256 over timestamp and body; replay-protected delivery IDs."},
    "telemetry": {"label": "Telemetry / event stream", "mode": "stream", "status": "implemented",
                  "note": "Idempotent readings, windowed aggregation into a dataset."},
    "simulated": {"label": "Simulated feed", "mode": "stream", "status": "simulated",
                  "note": "Generates synthetic readings through the telemetry path. Always labelled simulated."},
    "network_share": {"label": "Governed network share", "mode": "event", "status": "implemented",
                      "note": "A hub's aggregate of outputs members approved for it."},
    "airbyte": {"label": "Airbyte sync", "mode": "poll", "status": "implemented",
                "note": "Reads rows an Airbyte connection synced into the staging schema (Excel on SharePoint/Drive/S3, "
                        "databases, SaaS APIs); can trigger the Airbyte sync. Airbyte never writes a dataset."},
    "database": {"label": "External database", "mode": "poll", "status": "not_implemented",
                 "note": "Needs a vetted read-only driver, a credential vault and a query allowlist."},
    "parquet": {"label": "Parquet file", "mode": "upload", "status": "not_implemented",
                "note": "Would add pyarrow; no current source needs it."},
    "object_storage": {"label": "Object storage (S3-compatible)", "mode": "poll", "status": "external_dependency",
                       "note": "Needs a bucket and credentials; not configured."},
}

CONFIGURABLE = ("csv_upload", "rest_poll", "webhook", "telemetry", "simulated", "airbyte")


class ConnectorError(Exception):
    """A source or its configuration is invalid."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest_record(db: Session, *, kind: str, dataset: Dataset, record: dict[str, Any],
                  source_name: str, source_ref: str, content_sha256: str | None = None,
                  source: ConnectorSource | None = None, source_type: str | None = None) -> IngestionRun:
    """Write one (possibly partial) record through the governed path. Never raises for a
    rejected record: the rejection is recorded on the returned IngestionRun."""
    from backend.app.services.adapter_registry import AdapterRegistry
    from backend.app.services.change_propagation import ChangePropagationService
    from backend.app.services.contract_validation import ContractViolationError
    from backend.app.services.data_contract_manager import DataContractManager
    from backend.app.services.dataset_values import (
        DatasetOwnershipError,
        DatasetValueSerializationError,
        DatasetValueService,
    )
    from backend.app.services.federation import FederationService

    run = IngestionRun(source_name=source_name[:512], mapping_key=f"connector:{kind}", status="received",
                       connector_kind=kind, connector_source_id=source.id if source else None,
                       content_sha256=content_sha256)
    db.add(run)
    db.flush()
    try:
        if FederationService(db).producers_of(dataset.id):
            raise ConnectorError(f"{dataset.name!r} is produced by a model and cannot be written by a connector.")
        current = json.loads(dataset.current_value) if dataset.current_value else {}
        merged = {**(current if isinstance(current, dict) else {}), **record}
        DataContractManager(db).validate_record(dataset.id, merged, phase="write")
        write = DatasetValueService(db).write_external(
            dataset.id, merged, source_type=source_type or kind, source_ref=source_ref[:1000],
            triggered_by=f"ingestion:{run.id}")
        run.dataset_ids_json = json.dumps([dataset.id])
        if write.changed:
            run.status = "ingested"
            run.change_event_id = write.event.id
            prop = ChangePropagationService(db, AdapterRegistry(db)).propagate(write.event.id)
            run.graph_run_id = prop.run_id
        else:
            run.status = "unchanged"
    except (ConnectorError, ContractViolationError, DatasetOwnershipError, DatasetValueSerializationError,
            ValueError, TypeError) as exc:
        run.status = "rejected"
        run.error = str(exc)[:2000]
    if source is not None:
        source.last_run_at = _now()
        source.last_status = run.status
        source.last_error = run.error
    db.flush()
    return run


def target_dataset(db: Session, source: ConnectorSource) -> Dataset:
    if not source.target_dataset_id:
        raise ConnectorError("This source has no target dataset.")
    ds = db.execute(select(Dataset).where(Dataset.id == source.target_dataset_id)).scalars().first()
    if ds is None:
        raise ConnectorError("The target dataset no longer exists in this organization.")
    return ds


def load_config(source: ConnectorSource) -> dict[str, Any]:
    try:
        cfg = json.loads(source.config_json or "{}")
    except ValueError as exc:
        raise ConnectorError(f"Invalid configuration: {exc}") from exc
    return cfg if isinstance(cfg, dict) else {}
