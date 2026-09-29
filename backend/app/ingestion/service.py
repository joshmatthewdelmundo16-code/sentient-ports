"""Ingestion service — D18.

Orchestrates: Excel connector → named mapping → per-dataset record → D16
DatasetValueService.write_external (contracts + change detection) → D16
ChangePropagationService.propagate (GraphRun). Every attempt — success, no-op, or
rejection — is persisted as an IngestionRun. Rejections publish no dataset value.

D16/D17 dataset-writing and propagation logic is reused unchanged.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from sqlalchemy.orm import Session

from backend.app.ingestion.errors import (
    FileTooLargeError,
    IngestionError,
    UnknownMappingError,
    UnsupportedFileTypeError,
)
from backend.app.ingestion.excel_connector import XLSX_SUFFIX, read_cells, sha256_hex
from backend.app.ingestion.mappings import SourceMapping, get_mapping
from backend.app.persistence.database import IngestionRun
from backend.app.persistence.dataset_repository import DatasetRepository
from backend.app.persistence.exceptions import NotFoundError
from backend.app.persistence.ingestion_run_repository import IngestionRunRepository
from backend.app.services.change_propagation import ChangePropagationService
from backend.app.services.contract_validation import ContractViolationError
from backend.app.services.data_contract_manager import DataContractManager
from backend.app.services.dataset_values import (
    DatasetOwnershipError,
    DatasetValueSerializationError,
    DatasetValueService,
)
from backend.app.services.federation import FederationService

MAX_WORKBOOK_BYTES = 5 * 1024 * 1024  # 5 MiB

# Rejections the service catches and records (never partially publishes).
_REJECTIONS = (
    IngestionError, ContractViolationError, DatasetOwnershipError, DatasetValueSerializationError,
)


class IngestionResult(NamedTuple):
    ingestion_id: str
    status: str                       # "ingested" | "unchanged" | "rejected"
    content_sha256: str | None
    mapping_key: str
    source_name: str
    changed: bool
    dataset_ids: list[str]
    change_event_id: str | None
    graph_run_id: str | None
    error: str | None
    values: dict[str, Any] | None     # {dataset_name: record} on success


class IngestionService:
    def __init__(self, db: Session, adapter_registry=None) -> None:
        self._db = db
        self._runs = IngestionRunRepository(db)
        self._datasets = DatasetRepository(db)
        self._values = DatasetValueService(db)
        self._contracts = DataContractManager(db)
        self._federation = FederationService(db)
        # Adapter registry resolves persisted adapters during propagation (no in-memory config).
        from backend.app.services.adapter_registry import AdapterRegistry
        self._propagation = ChangePropagationService(db, adapter_registry or AdapterRegistry(db))

    def ingest_excel(self, *, source_name: str, workbook_bytes: bytes,
                     mapping_key: str) -> IngestionResult:
        run = self._runs.add(IngestionRun(
            source_name=source_name, mapping_key=mapping_key, status="received",
        ))
        try:
            content_sha256, dataset_ids, values, change_event_id, graph_run_id, changed = \
                self._do_ingest(run, source_name, workbook_bytes, mapping_key)
        except _REJECTIONS as exc:
            run.status = "rejected"
            run.error = str(exc)
            self._db.flush()
            return IngestionResult(
                ingestion_id=run.id, status="rejected", content_sha256=run.content_sha256,
                mapping_key=mapping_key, source_name=source_name, changed=False,
                dataset_ids=[], change_event_id=None, graph_run_id=None,
                error=str(exc), values=None,
            )

        run.status = "ingested" if changed else "unchanged"
        run.dataset_ids_json = json.dumps(dataset_ids)
        run.change_event_id = change_event_id
        run.graph_run_id = graph_run_id
        self._db.flush()
        return IngestionResult(
            ingestion_id=run.id, status=run.status, content_sha256=content_sha256,
            mapping_key=mapping_key, source_name=source_name, changed=changed,
            dataset_ids=dataset_ids, change_event_id=change_event_id,
            graph_run_id=graph_run_id, error=None, values=values,
        )

    # ------------------------------------------------------------------

    def _do_ingest(self, run: IngestionRun, source_name: str, workbook_bytes: bytes,
                   mapping_key: str):
        if not source_name.lower().endswith(XLSX_SUFFIX):
            raise UnsupportedFileTypeError(
                f"Only {XLSX_SUFFIX} files are accepted; got {source_name!r}."
            )
        if len(workbook_bytes) > MAX_WORKBOOK_BYTES:
            raise FileTooLargeError(
                f"Workbook is {len(workbook_bytes)} bytes; limit is {MAX_WORKBOOK_BYTES}."
            )
        mapping = get_mapping(mapping_key)
        if mapping is None:
            raise UnknownMappingError(f"Unknown mapping key {mapping_key!r}.")

        content_sha256 = sha256_hex(workbook_bytes)
        run.content_sha256 = content_sha256
        self._db.flush()

        cells = read_cells(workbook_bytes, mapping)          # may raise IngestionError
        records = self._assemble(mapping, cells)             # {dataset_name: record}

        # Resolve datasets and pre-validate everything before any write (no partial publish).
        resolved: list[tuple[str, str, dict[str, Any]]] = []  # (name, id, record)
        for name, record in records.items():
            try:
                ds = self._datasets.get_by_name(name)
            except NotFoundError as exc:
                raise IngestionError(f"Mapping targets unknown dataset {name!r}.") from exc
            if self._federation.producers_of(ds.id):
                raise DatasetOwnershipError(
                    f"Dataset {name!r} is produced by a model and cannot be ingested into."
                )
            self._contracts.validate_record(ds.id, record, phase="write")  # raises on bad
            resolved.append((name, ds.id, record))

        source_ref = f"{source_name}@sha256:{content_sha256[:12]}"
        dataset_ids: list[str] = []
        change_event_id: str | None = None
        graph_run_id: str | None = None
        changed = False
        for name, ds_id, record in resolved:
            write = self._values.write_external(
                ds_id, record, source_type="excel", source_ref=source_ref,
                triggered_by=f"ingestion:{run.id}",
            )
            dataset_ids.append(ds_id)
            if write.changed:
                changed = True
                if change_event_id is None:
                    change_event_id = write.event.id
                prop = self._propagation.propagate(write.event.id)
                if graph_run_id is None:
                    graph_run_id = prop.run_id

        return content_sha256, dataset_ids, records, change_event_id, graph_run_id, changed

    def _assemble(self, mapping: SourceMapping,
                  cells: dict[tuple[str, str], Any]) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for cm in mapping.cells:
            records.setdefault(cm.target_dataset, {})[cm.target_field] = cells[(cm.worksheet, cm.cell)]
        return records

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_run(self, ingestion_id: str) -> IngestionRun:
        return self._runs.get(ingestion_id)

    def list_runs(self, limit: int = 50) -> list[IngestionRun]:
        return self._runs.list_recent(limit=limit)
