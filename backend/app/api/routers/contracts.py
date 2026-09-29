"""Data Contract API — D11."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.deps import get_contract_manager
from backend.app.api.schemas import ContractOut
from backend.app.services.data_contract_manager import (
    ContractNotFoundError,
    DataContractManager,
)

router = APIRouter(prefix="/api/contracts", tags=["Contracts"])


@router.get("", response_model=list[ContractOut], summary="List all data contracts")
def list_contracts(
    manager: DataContractManager = Depends(get_contract_manager),
) -> list[ContractOut]:
    contracts = manager.list_contracts()
    return [ContractOut.model_validate(c) for c in contracts]


@router.get("/{contract_id}", response_model=ContractOut, summary="Get a contract by ID")
def get_contract(
    contract_id: str,
    manager: DataContractManager = Depends(get_contract_manager),
) -> ContractOut:
    try:
        contract = manager.get_contract(contract_id)
    except ContractNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ContractOut.model_validate(contract)
