"""Federation service — D16.

Owns the explicit input/output declarations that route data through datasets.

A model version's declared inputs are:
  1. its ModelIOBinding rows with direction="input" (explicit, optional field_map), then
  2. every Dependency where it is the consumer, for input_dataset_id values not already
     covered by (1) — a Dependency row is itself a declaration that the consumer reads
     that dataset (identity mapping).
Declared outputs are defined symmetrically (bindings with direction="output", then
Dependency.output_dataset_id for edges where the version is the producer).

Executability rules:
  - ModelVersion.is_active must be True.
  - Model.status must be one of EXECUTABLE_MODEL_STATUSES ("draft", "active");
    "deprecated" and "retired" models cannot execute.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from backend.app.persistence.database import Dependency, ModelIOBinding, ModelVersion
from backend.app.persistence.dataset_repository import DatasetRepository
from backend.app.persistence.dependency_repository import DependencyRepository
from backend.app.persistence.exceptions import DuplicateError, NotFoundError
from backend.app.persistence.io_binding_repository import ModelIOBindingRepository
from backend.app.persistence.model_repository import ModelRepository
from backend.app.persistence.model_version_repository import ModelVersionRepository

EXECUTABLE_MODEL_STATUSES = frozenset({"draft", "active"})
DIRECTIONS = ("input", "output")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class BindingError(Exception):
    """An I/O binding is invalid (unknown version/dataset, bad map, duplicate)."""


class OutputOwnershipError(BindingError):
    """Another model already produces this dataset."""


class VersionNotExecutableError(Exception):
    """The model version (or its model) is not in an executable state."""

    def __init__(self, version_id: str, reason: str) -> None:
        super().__init__(f"Model version {version_id!r} cannot execute: {reason}")
        self.version_id = version_id
        self.reason = reason


class InputAssemblyError(Exception):
    """Inputs for a model could not be assembled from its declared datasets."""


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IODeclaration:
    dataset_id: str
    field_map: dict[str, str] | None
    source: str  # "binding" | "dependency"


def _parse_field_map(raw: str | None) -> dict[str, str] | None:
    return json.loads(raw) if raw else None


def _check_field_map(field_map: dict[str, str] | None) -> None:
    if field_map is None:
        return
    if not isinstance(field_map, dict) or not field_map:
        raise BindingError("field_map must be a non-empty object of {from: to} strings.")
    for k, v in field_map.items():
        if not isinstance(k, str) or not isinstance(v, str) or not k or not v:
            raise BindingError("field_map keys and values must be non-empty strings.")
    if len(set(field_map.values())) != len(field_map):
        raise BindingError("field_map maps two fields to the same target name.")


def apply_field_map(record: dict[str, Any], field_map: dict[str, str] | None) -> dict[str, Any]:
    """Rename/select fields. Raises InputAssemblyError when a mapped source field is absent."""
    if field_map is None:
        return dict(record)
    missing = [src for src in field_map if src not in record]
    if missing:
        raise InputAssemblyError(f"mapped field(s) {missing} not present in record {sorted(record)}")
    return {dst: record[src] for src, dst in field_map.items()}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class FederationService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._bindings = ModelIOBindingRepository(db)
        self._deps = DependencyRepository(db)
        self._versions = ModelVersionRepository(db)
        self._models = ModelRepository(db)
        self._datasets = DatasetRepository(db)

    # -- registration --------------------------------------------------------

    def bind_input(self, version_id: str, dataset_id: str,
                   field_map: dict[str, str] | None = None) -> ModelIOBinding:
        return self._bind(version_id, dataset_id, "input", field_map)

    def bind_output(self, version_id: str, dataset_id: str,
                    field_map: dict[str, str] | None = None) -> ModelIOBinding:
        return self._bind(version_id, dataset_id, "output", field_map)

    def _bind(self, version_id: str, dataset_id: str, direction: str,
              field_map: dict[str, str] | None) -> ModelIOBinding:
        try:
            version = self._versions.get(version_id)
        except NotFoundError as exc:
            raise BindingError(f"Model version {version_id!r} not found.") from exc
        try:
            self._datasets.get(dataset_id)
        except NotFoundError as exc:
            raise BindingError(f"Dataset {dataset_id!r} not found.") from exc
        _check_field_map(field_map)
        if self._bindings.find(version_id, dataset_id, direction) is not None:
            raise BindingError(
                f"{direction} binding version={version_id!r} dataset={dataset_id!r} already exists."
            )

        if direction == "output":
            for pid in self.producers_of(dataset_id):
                if pid == version_id:
                    continue
                other = self._versions.get(pid)
                if other.model_id != version.model_id:
                    raise OutputOwnershipError(
                        f"Dataset {dataset_id!r} is already produced by model version {pid!r}."
                    )

        binding = ModelIOBinding(
            model_version_id=version_id,
            dataset_id=dataset_id,
            direction=direction,
            field_map=json.dumps(field_map) if field_map else None,
        )
        try:
            return self._bindings.add(binding)
        except DuplicateError as exc:
            raise BindingError(
                f"{direction} binding version={version_id!r} dataset={dataset_id!r} already exists."
            ) from exc

    def ensure_binding(self, version_id: str, dataset_id: str, direction: str,
                       field_map: dict[str, str] | None = None) -> ModelIOBinding:
        existing = self._bindings.find(version_id, dataset_id, direction)
        if existing is not None:
            return existing
        return self._bind(version_id, dataset_id, direction, field_map)

    def connect(self, *, producer_version_id: str, dataset_id: str, consumer_version_id: str,
                input_field_map: dict[str, str] | None = None,
                output_field_map: dict[str, str] | None = None) -> Dependency:
        """Declare producer → dataset → consumer: output binding, input binding and graph edge."""
        from backend.app.services.dependency_graph import DependencyGraphService

        self.ensure_binding(producer_version_id, dataset_id, "output", output_field_map)
        self.ensure_binding(consumer_version_id, dataset_id, "input", input_field_map)
        for dep in self._deps.list_by_consumer(consumer_version_id):
            if dep.producer_version_id == producer_version_id and dep.input_dataset_id == dataset_id:
                return dep
        return DependencyGraphService(self._db).register_dependency(
            producer_version_id=producer_version_id,
            output_dataset_id=dataset_id,
            consumer_version_id=consumer_version_id,
            input_dataset_id=dataset_id,
            dependency_kind="data",
        )

    # -- declarations --------------------------------------------------------

    def declared_inputs(self, version_id: str) -> list[IODeclaration]:
        decls = [
            IODeclaration(b.dataset_id, _parse_field_map(b.field_map), "binding")
            for b in self._bindings.list_by_version(version_id, "input")
        ]
        covered = {d.dataset_id for d in decls}
        for dep in self._deps.list_by_consumer(version_id):
            if dep.input_dataset_id not in covered:
                decls.append(IODeclaration(dep.input_dataset_id, None, "dependency"))
                covered.add(dep.input_dataset_id)
        return decls

    def declared_outputs(self, version_id: str) -> list[IODeclaration]:
        decls = [
            IODeclaration(b.dataset_id, _parse_field_map(b.field_map), "binding")
            for b in self._bindings.list_by_version(version_id, "output")
        ]
        covered = {d.dataset_id for d in decls}
        for dep in self._deps.list_by_producer(version_id):
            if dep.output_dataset_id not in covered:
                decls.append(IODeclaration(dep.output_dataset_id, None, "dependency"))
                covered.add(dep.output_dataset_id)
        return decls

    def producers_of(self, dataset_id: str) -> set[str]:
        ids = {b.model_version_id for b in self._bindings.list_by_dataset(dataset_id, "output")}
        ids |= {d.producer_version_id for d in self._deps.list_by_output_dataset(dataset_id)}
        return ids

    def consumers_of(self, dataset_id: str) -> set[str]:
        ids = {b.model_version_id for b in self._bindings.list_by_dataset(dataset_id, "input")}
        ids |= {d.consumer_version_id for d in self._deps.list_by_input_dataset(dataset_id)}
        return ids

    # -- executability -------------------------------------------------------

    def assert_executable(self, version: ModelVersion) -> None:
        if not version.is_active:
            raise VersionNotExecutableError(version.id, "version is not active")
        model = self._models.get(version.model_id)
        if model.status not in EXECUTABLE_MODEL_STATUSES:
            raise VersionNotExecutableError(
                version.id, f"model {model.name!r} has status {model.status!r}"
            )
