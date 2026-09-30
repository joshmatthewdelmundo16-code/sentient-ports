"""Labelled catalog and federation map (D25).

One read model that turns identifiers into business-readable metadata:

  * every dataset with its label, contract fields (label, unit, type, bounds), current
    value, role (source vs model output), producers/consumers, and the provenance of its
    current value (the latest ChangeEvent: source type, reference, time);
  * the federation map — model versions, the datasets each one reads (with the exact
    fields its binding selects) and writes — derived from ModelIOBinding rows.

Read-only. Built from the same records the engine executes against.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.persistence.database import (
    ChangeEvent,
    DataContract,
    Dataset,
    Model,
    ModelIOBinding,
    ModelVersion,
)
from backend.app.product.labels import dataset_label, display_unit, field_label, humanize
from backend.app.product.util import iso
from backend.app.services.contract_validation import ContractSchemaError, parse_schema


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class FieldMeta:
    name: str
    label: str
    unit: str | None
    type: str | None = None
    nullable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    description: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "label": self.label, "unit": self.unit, "type": self.type,
            "nullable": self.nullable, "min": self.minimum, "max": self.maximum,
            "description": self.description,
        }


class LabelIndex:
    """dataset/field → label + unit lookups for one request."""

    def __init__(self) -> None:
        self.datasets: dict[str, str] = {}
        self.dataset_names: dict[str, str] = {}
        self.fields: dict[tuple[str, str], FieldMeta] = {}
        self.field_order: dict[str, list[str]] = {}
        self.models: dict[str, str] = {}          # version_id → model name
        self.model_ids: dict[str, str] = {}       # version_id → model_id

    def dataset(self, dataset_id: str | None) -> str:
        if not dataset_id:
            return "Unknown dataset"
        return self.datasets.get(dataset_id, "Unknown dataset")

    def field(self, dataset_id: str, name: str) -> FieldMeta:
        meta = self.fields.get((dataset_id, name))
        if meta is not None:
            return meta
        return FieldMeta(name=name, label=humanize(name), unit=None)

    def model(self, version_id: str | None) -> str:
        if not version_id:
            return "Unknown model"
        return self.models.get(version_id, "Unknown model")


class CatalogService:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Contracts
    # ------------------------------------------------------------------

    def _active_contracts(self) -> dict[str, DataContract]:
        """dataset_id → latest contract (by created_at)."""
        latest: dict[str, DataContract] = {}
        rows = self._db.execute(select(DataContract).order_by(DataContract.created_at)).scalars()
        for c in rows:
            latest[c.dataset_id] = c
        return latest

    @staticmethod
    def _fields_for(contract: DataContract | None) -> list[FieldMeta]:
        if contract is None:
            return []
        try:
            schema = parse_schema(json.loads(contract.schema_json))
        except (ContractSchemaError, ValueError, TypeError):
            return []
        return [
            FieldMeta(
                name=f.name, label=field_label(f.name, f.label), unit=display_unit(f.unit),
                type=f.type, nullable=f.nullable, minimum=f.minimum, maximum=f.maximum,
                description=f.description,
            )
            for f in schema.fields
        ]

    # ------------------------------------------------------------------
    # Label index
    # ------------------------------------------------------------------

    def label_index(self) -> LabelIndex:
        idx = LabelIndex()
        contracts = self._active_contracts()
        for ds in self._db.execute(select(Dataset)).scalars():
            idx.datasets[ds.id] = dataset_label(ds.name, ds.display_name)
            idx.dataset_names[ds.id] = ds.name
            fields = self._fields_for(contracts.get(ds.id))
            idx.field_order[ds.id] = [f.name for f in fields]
            for f in fields:
                idx.fields[(ds.id, f.name)] = f
        rows = self._db.execute(
            select(ModelVersion.id, ModelVersion.model_id, Model.name)
            .join(Model, Model.id == ModelVersion.model_id)
        ).all()
        for vid, mid, name in rows:
            idx.models[vid] = name
            idx.model_ids[vid] = mid
        return idx

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------

    def _latest_events(self) -> dict[str, ChangeEvent]:
        sub = (
            select(ChangeEvent.dataset_id, func.max(ChangeEvent.created_at).label("latest"))
            .group_by(ChangeEvent.dataset_id)
            .subquery()
        )
        rows = self._db.execute(
            select(ChangeEvent).join(
                sub,
                (ChangeEvent.dataset_id == sub.c.dataset_id)
                & (ChangeEvent.created_at == sub.c.latest),
            )
        ).scalars()
        return {e.dataset_id: e for e in rows}

    def _bindings(self) -> list[ModelIOBinding]:
        return list(self._db.execute(select(ModelIOBinding)).scalars())

    def datasets(self) -> list[dict[str, Any]]:
        idx = self.label_index()
        contracts = self._active_contracts()
        events = self._latest_events()
        producers: dict[str, list[str]] = {}
        consumers: dict[str, list[str]] = {}
        for b in self._bindings():
            target = producers if b.direction == "output" else consumers
            target.setdefault(b.dataset_id, []).append(b.model_version_id)

        out: list[dict[str, Any]] = []
        for ds in self._db.execute(select(Dataset).order_by(Dataset.name)).scalars():
            ev = events.get(ds.id)
            contract = contracts.get(ds.id)
            out.append({
                "id": ds.id,
                "name": ds.name,
                "label": idx.dataset(ds.id),
                "description": ds.description,
                "role": "model_output" if producers.get(ds.id) else "source",
                "fields": [f.as_dict() for f in self._fields_for(contract)],
                "contract": ({"id": contract.id, "semver": contract.semver}
                             if contract else None),
                "value": _loads(ds.current_value),
                "value_hash": ds.current_hash,
                "updated_at": iso(ds.updated_at),
                "owner_participant_id": ds.owner_participant_id,
                "produced_by": [{"version_id": v, "model_name": idx.model(v)}
                                for v in producers.get(ds.id, [])],
                "consumed_by": [{"version_id": v, "model_name": idx.model(v)}
                                for v in consumers.get(ds.id, [])],
                "current_source": ({
                    "change_event_id": ev.id,
                    "source_type": ev.source_type,
                    "source_ref": ev.source_ref,
                    "triggered_by": ev.triggered_by,
                    "produced_by_run_id": ev.produced_by_run_id,
                    "at": iso(ev.created_at),
                } if ev else None),
            })
        return out

    # ------------------------------------------------------------------
    # Federation map
    # ------------------------------------------------------------------

    def federation_map(self) -> dict[str, Any]:
        idx = self.label_index()
        bindings = self._bindings()
        bound_versions = {b.model_version_id for b in bindings}
        models: list[dict[str, Any]] = []
        rows = self._db.execute(
            select(ModelVersion, Model).join(Model, Model.id == ModelVersion.model_id)
        ).all()
        for version, model in rows:
            if version.id not in bound_versions:
                continue
            card = _loads(model.card_json) or {}
            models.append({
                "version_id": version.id,
                "model_id": model.id,
                "name": model.name,
                "semver": version.semver,
                "is_active": version.is_active,
                "status": model.status,
                "adapter_type": version.adapter_type,
                "owner": model.owner,
                "purpose": card.get("purpose"),
                "formula": card.get("formula") or _formula_from_description(model.description),
                "domain": card.get("domain"),
                "calibration": card.get("calibration"),
            })
        reads: list[dict[str, Any]] = []
        writes: list[dict[str, Any]] = []
        for b in bindings:
            field_map = _loads(b.field_map)
            if b.direction == "input":
                fields = sorted(field_map) if isinstance(field_map, dict) else None
                reads.append({"version_id": b.model_version_id, "dataset_id": b.dataset_id,
                              "fields": fields})
            else:
                fields = sorted(field_map.values()) if isinstance(field_map, dict) else None
                writes.append({"version_id": b.model_version_id, "dataset_id": b.dataset_id,
                               "fields": fields})
        dataset_ids = {r["dataset_id"] for r in reads} | {w["dataset_id"] for w in writes}
        datasets = [
            {"id": d, "name": idx.dataset_names.get(d), "label": idx.dataset(d),
             "role": "model_output" if any(w["dataset_id"] == d for w in writes) else "source"}
            for d in sorted(dataset_ids, key=lambda x: idx.dataset(x))
        ]
        return {"models": models, "datasets": datasets, "reads": reads, "writes": writes}


def _formula_from_description(description: str | None) -> str | None:
    """Pre-D25 seeds packed 'purpose | Formula: … | Inputs: …' into the description."""
    if not description:
        return None
    for part in description.split("|"):
        part = part.strip()
        if part.lower().startswith("formula:"):
            return part.split(":", 1)[1].strip()
    return None
