"""Data Contract Manager service — D4.

Application-layer service for registering and retrieving DataContracts.
Calls D2 repositories only; contains no raw SQL.

D1 DataContract schema:
  id          UUID PK
  dataset_id  FK → dataset.id (required)
  schema_json JSON field-schema definition (required, non-empty)
  semver      contract version string (default "1.0.0")
  created_at

Identity: (dataset_id, semver) — unique per D1 schema.
Parent reference: dataset must exist before a contract is registered.

D16: the contract with the highest semver is the dataset's active contract and is
enforced on every dataset write and on model inputs/outputs (validate_record).
A dataset with no registered contract is uncontracted and is not validated.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from backend.app.persistence.contract_repository import DataContractRepository
from backend.app.persistence.database import DataContract
from backend.app.persistence.dataset_repository import DatasetRepository
from backend.app.persistence.exceptions import DuplicateError, NotFoundError
from backend.app.services.contract_validation import (
    ContractSchema,
    ContractSchemaError,
    ContractViolationError,
    coerce_record,
    parse_schema,
    semver_key,
    validate_record,
)


# ---------------------------------------------------------------------------
# Service-level errors
# ---------------------------------------------------------------------------

class ContractNotFoundError(Exception):
    """No contract with the given identity exists."""


class DuplicateContractError(Exception):
    """A contract with this (dataset_id, semver) is already registered."""


class DatasetNotFoundError(Exception):
    """The referenced dataset does not exist."""


class InvalidContractError(Exception):
    """The contract data fails basic structural validation."""


class ContractPersistenceError(Exception):
    """Unexpected persistence failure during a contract operation."""


# ---------------------------------------------------------------------------
# Manager service
# ---------------------------------------------------------------------------

class DataContractManager:
    """
    Coordinates DataContract registration and lookup.

    One instance per request/use-case: constructed with an open SQLAlchemy
    session; the caller owns commit/rollback.
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._contracts = DataContractRepository(db)
        self._datasets = DatasetRepository(db)

    # ------------------------------------------------------------------
    # Contract registration
    # ------------------------------------------------------------------

    def register_contract(
        self,
        *,
        dataset_id: str,
        schema_json: str,
        semver: str = "1.0.0",
    ) -> DataContract:
        """
        Register a DataContract for an existing dataset.

        Raises DatasetNotFoundError if dataset_id doesn't exist.
        Raises InvalidContractError if schema_json is empty or not valid JSON.
        Raises DuplicateContractError if (dataset_id, semver) is already registered.
        Raises ContractPersistenceError on unexpected DB failure.
        """
        # Verify parent dataset exists
        try:
            self._datasets.get(dataset_id)
        except NotFoundError as exc:
            raise DatasetNotFoundError(
                f"Cannot register contract: dataset_id={dataset_id!r} does not exist."
            ) from exc

        # Basic structural validation: schema_json must be non-empty valid JSON
        self._validate_schema_json(schema_json)

        # Duplicate check via repository lookup
        try:
            self._contracts.get_by_dataset_and_semver(dataset_id=dataset_id, semver=semver)
            raise DuplicateContractError(
                f"Contract dataset_id={dataset_id!r} semver={semver!r} is already registered."
            )
        except NotFoundError:
            pass  # expected — contract does not yet exist

        entity = DataContract(
            dataset_id=dataset_id,
            schema_json=schema_json,
            semver=semver,
        )
        try:
            return self._contracts.add(entity)
        except DuplicateError as exc:
            raise DuplicateContractError(str(exc)) from exc
        except Exception as exc:
            raise ContractPersistenceError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Contract lookup
    # ------------------------------------------------------------------

    def get_contract(self, contract_id: str) -> DataContract:
        """Return a contract by primary-key ID."""
        try:
            return self._contracts.get(contract_id)
        except NotFoundError as exc:
            raise ContractNotFoundError(str(exc)) from exc

    def get_contract_by_dataset_and_semver(
        self, *, dataset_id: str, semver: str
    ) -> DataContract:
        """Return a contract by (dataset_id, semver) — the D2 lookup path."""
        try:
            return self._contracts.get_by_dataset_and_semver(
                dataset_id=dataset_id, semver=semver
            )
        except NotFoundError as exc:
            raise ContractNotFoundError(str(exc)) from exc

    def list_contracts(self, *, limit: int = 100, offset: int = 0) -> list[DataContract]:
        """List all contracts."""
        return self._contracts.list(limit=limit, offset=offset)

    def list_contracts_by_dataset(self, dataset_id: str) -> list[DataContract]:
        """List all contracts for a given dataset, ordered by semver."""
        return self._contracts.list_by_dataset(dataset_id)

    # ------------------------------------------------------------------
    # Enforcement (D16)
    # ------------------------------------------------------------------

    def get_active_contract(self, dataset_id: str) -> DataContract | None:
        """Highest-semver contract for the dataset, or None if uncontracted."""
        contracts = self._contracts.list_by_dataset(dataset_id)
        if not contracts:
            return None
        return max(contracts, key=lambda c: semver_key(c.semver))

    def get_active_schema(self, dataset_id: str) -> ContractSchema | None:
        contract = self.get_active_contract(dataset_id)
        return parse_schema(json.loads(contract.schema_json)) if contract else None

    def validate_record(self, dataset_id: str, record: Any, *, phase: str) -> Any:
        """
        Validate `record` against the dataset's active contract and return its canonical form
        (number fields as floats). Uncontracted datasets return the record unchanged.

        Raises ContractViolationError listing every violation.
        """
        contract = self.get_active_contract(dataset_id)
        if contract is None:
            return record
        schema = parse_schema(json.loads(contract.schema_json))
        violations = validate_record(record, schema)
        if violations:
            dataset = self._datasets.get(dataset_id)
            raise ContractViolationError(
                dataset_id=dataset_id,
                dataset_name=dataset.name,
                contract_semver=contract.semver,
                phase=phase,
                violations=violations,
            )
        return coerce_record(record, schema)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_schema_json(schema_json: str) -> None:
        """Raise InvalidContractError if schema_json is empty, not JSON, or malformed."""
        if not schema_json or not schema_json.strip():
            raise InvalidContractError("schema_json must not be empty.")
        try:
            doc = json.loads(schema_json)
        except (json.JSONDecodeError, ValueError) as exc:
            raise InvalidContractError(
                f"schema_json is not valid JSON: {exc}"
            ) from exc
        try:
            parse_schema(doc)
        except ContractSchemaError as exc:
            raise InvalidContractError(f"Invalid contract schema: {exc}") from exc
