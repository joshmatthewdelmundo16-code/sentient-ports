"""Datasets API — D16 (read-only; writes go through /api/changes/propagate)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.api.schemas import ChangeEventOut, ContractFieldOut, DatasetOut
from backend.app.persistence.change_event_repository import ChangeEventRepository
from backend.app.persistence.database import Dataset, get_db
from backend.app.persistence.dataset_repository import DatasetRepository
from backend.app.persistence.exceptions import NotFoundError
from backend.app.services.data_contract_manager import DataContractManager
from backend.app.services.federation import FederationService

router = APIRouter(prefix="/api/datasets", tags=["Datasets"])


def _to_out(db: Session, ds: Dataset) -> DatasetOut:
    federation = FederationService(db)
    contracts = DataContractManager(db)
    contract = contracts.get_active_contract(ds.id)
    schema = contracts.get_active_schema(ds.id) if contract else None
    return DatasetOut(
        id=ds.id,
        name=ds.name,
        description=ds.description,
        current_value=json.loads(ds.current_value) if ds.current_value is not None else None,
        current_hash=ds.current_hash,
        updated_at=ds.updated_at,
        producer_version_ids=sorted(federation.producers_of(ds.id)),
        consumer_version_ids=sorted(federation.consumers_of(ds.id)),
        contract_semver=contract.semver if contract else None,
        contract_fields=[
            ContractFieldOut(
                name=f.name, type=f.type, nullable=f.nullable, required=f.required,
                min=f.minimum, max=f.maximum, unit=f.unit,
            )
            for f in (schema.fields if schema else ())
        ],
    )


def _get(db: Session, dataset_id: str) -> Dataset:
    try:
        return DatasetRepository(db).get(dataset_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("", response_model=list[DatasetOut], summary="List datasets with current values")
def list_datasets(db: Session = Depends(get_db, scope="function")) -> list[DatasetOut]:
    return [_to_out(db, ds) for ds in DatasetRepository(db).list(limit=500)]


@router.get("/{dataset_id}", response_model=DatasetOut, summary="Get one dataset")
def get_dataset(dataset_id: str, db: Session = Depends(get_db, scope="function")) -> DatasetOut:
    return _to_out(db, _get(db, dataset_id))


@router.get(
    "/{dataset_id}/change-events",
    response_model=list[ChangeEventOut],
    summary="Value history of a dataset (newest first)",
)
def list_dataset_changes(dataset_id: str, db: Session = Depends(get_db, scope="function")) -> list[ChangeEventOut]:
    _get(db, dataset_id)
    events = ChangeEventRepository(db).list_by_dataset(dataset_id)
    return [ChangeEventOut.model_validate(e) for e in events]
