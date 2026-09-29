"""Dataset value service — D16.

Dataset.current_value / current_hash are the source of truth for what a dataset holds.
Every write goes through this service:

  normalize (canonical JSON) → hash → compare with current_hash
     unchanged → no-op (no ChangeEvent)
     changed   → ChangeEvent(old, new, provenance) + update current value/hash

External writes are validated against the dataset contract and rejected for datasets
that a model produces (only the producer may write those, via publish_model_output).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, NamedTuple

from sqlalchemy.orm import Session

from backend.app.persistence.change_event_repository import ChangeEventRepository
from backend.app.persistence.database import ChangeEvent, Dataset
from backend.app.persistence.dataset_repository import DatasetRepository
from backend.app.persistence.exceptions import NotFoundError
from backend.app.services.data_contract_manager import DataContractManager
from backend.app.services.federation import FederationService


class DatasetValueNotFoundError(Exception):
    """The dataset does not exist."""


class DatasetOwnershipError(Exception):
    """The dataset is produced by a model and cannot be written externally."""


class DatasetValueSerializationError(Exception):
    """The value cannot be represented as canonical JSON."""


def normalize_value(value: Any) -> str:
    """Canonical JSON: sorted keys, compact separators, no NaN/Infinity."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DatasetValueSerializationError(str(exc)) from exc


def hash_normalized(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class DatasetWriteOutcome(NamedTuple):
    dataset_id: str
    changed: bool
    event: ChangeEvent | None
    old_value: Any
    new_value: Any


class DatasetValueService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._datasets = DatasetRepository(db)
        self._events = ChangeEventRepository(db)
        self._contracts = DataContractManager(db)
        self._federation = FederationService(db)

    # -- reads -----------------------------------------------------------------

    def get_dataset(self, dataset_id: str) -> Dataset:
        try:
            return self._datasets.get(dataset_id)
        except NotFoundError as exc:
            raise DatasetValueNotFoundError(f"Dataset {dataset_id!r} not found.") from exc

    def get_value(self, dataset_id: str) -> Any:
        ds = self.get_dataset(dataset_id)
        return json.loads(ds.current_value) if ds.current_value is not None else None

    def current_value_event(self, dataset_id: str) -> ChangeEvent | None:
        """The ChangeEvent that produced the dataset's current value (provenance)."""
        return self._events.latest_value_event(dataset_id)

    # -- writes ----------------------------------------------------------------

    def write_external(self, dataset_id: str, value: Any, *, source_type: str = "external",
                       source_ref: str | None = None,
                       triggered_by: str | None = None) -> DatasetWriteOutcome:
        ds = self.get_dataset(dataset_id)
        producers = self._federation.producers_of(dataset_id)
        if producers:
            raise DatasetOwnershipError(
                f"Dataset {ds.name!r} is produced by model version(s) {sorted(producers)}; "
                "it can only be written by its producer."
            )
        value = self._contracts.validate_record(dataset_id, value, phase="write")
        return self._apply(
            ds, value,
            source_type=source_type, source_ref=source_ref, triggered_by=triggered_by,
        )

    def publish_model_output(self, dataset_id: str, value: Any, *, run_id: str,
                             result_id: str, version_id: str,
                             propagated_in_run: bool) -> DatasetWriteOutcome:
        """Publish a (contract-validated) model output as the dataset's current value."""
        ds = self.get_dataset(dataset_id)
        return self._apply(
            ds, value,
            source_type="model_output",
            source_ref=f"run:{run_id}",
            triggered_by=f"model_version:{version_id}",
            source_version_id=version_id,
            produced_by_run_id=run_id,
            result_id=result_id,
            run_id=run_id if propagated_in_run else None,
        )

    def _apply(self, ds: Dataset, value: Any, **event_fields: Any) -> DatasetWriteOutcome:
        normalized = normalize_value(value)
        new_hash = hash_normalized(normalized)
        old_value = json.loads(ds.current_value) if ds.current_value is not None else None
        if ds.current_hash == new_hash:
            return DatasetWriteOutcome(ds.id, False, None, old_value, value)

        event = ChangeEvent(
            dataset_id=ds.id,
            old_value_json=ds.current_value,
            old_hash=ds.current_hash,
            new_value_json=normalized,
            new_hash=new_hash,
            **event_fields,
        )
        self._events.add(event)
        ds.current_value = normalized
        ds.current_hash = new_hash
        self._db.flush()
        return DatasetWriteOutcome(ds.id, True, event, old_value, json.loads(normalized))
