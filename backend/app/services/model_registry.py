"""Model Registry service — D3.

Application-layer service for registering and retrieving models and
model versions. Calls D2 repositories only; contains no raw SQL.

Business rules enforced here:
  - Duplicate stable identity (owner + name) is rejected.
  - A model version must reference an existing model.
  - All persistence goes through the repository layer.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from backend.app.persistence.database import Model, ModelVersion
from backend.app.persistence.exceptions import DuplicateError, NotFoundError
from backend.app.persistence.model_repository import ModelRepository
from backend.app.persistence.model_version_repository import ModelVersionRepository
from backend.app.services.adapter_registry import validate_adapter_config

MODEL_STATUSES = frozenset({"draft", "active", "deprecated", "retired"})


# ---------------------------------------------------------------------------
# Service-level errors
# ---------------------------------------------------------------------------

class ModelNotFoundError(Exception):
    """No model with the given identity exists in the registry."""


class ModelAlreadyExistsError(Exception):
    """A model with this owner+name is already registered."""


class ModelVersionNotFoundError(Exception):
    """No model version with the given identity exists."""


class InvalidParentModelError(Exception):
    """The referenced parent model does not exist."""


class RegistryPersistenceError(Exception):
    """Unexpected persistence failure during a registry operation."""


class ActiveVersionConflictError(Exception):
    """A model may have at most one active version."""


# ---------------------------------------------------------------------------
# Registry service
# ---------------------------------------------------------------------------

class ModelRegistry:
    """
    Coordinates model and model-version registration and lookup.

    One instance per request/use-case: constructed with an open SQLAlchemy
    session; the caller owns commit/rollback.
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._models = ModelRepository(db)
        self._versions = ModelVersionRepository(db)

    # ------------------------------------------------------------------
    # Model registration
    # ------------------------------------------------------------------

    def register_model(
        self,
        *,
        name: str,
        owner: str,
        model_type: str = "python",
        description: str | None = None,
        status: str = "draft",
    ) -> Model:
        """
        Register a new model.

        Raises ModelAlreadyExistsError if (owner, name) is already taken.
        Raises RegistryPersistenceError on unexpected DB failure.
        """
        # Identity check via repository (no direct SQL)
        try:
            self._models.get_by_name(name=name, owner=owner)
            # If we reach here the model already exists
            raise ModelAlreadyExistsError(
                f"Model owner={owner!r} name={name!r} is already registered."
            )
        except NotFoundError:
            pass  # expected — model does not yet exist

        entity = Model(
            name=name,
            owner=owner,
            model_type=model_type,
            description=description,
            status=status,
        )
        try:
            return self._models.add(entity)
        except DuplicateError as exc:
            raise ModelAlreadyExistsError(str(exc)) from exc
        except Exception as exc:
            raise RegistryPersistenceError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Model lookup
    # ------------------------------------------------------------------

    def get_model(self, model_id: str) -> Model:
        """Return a model by primary-key ID; raise ModelNotFoundError if absent."""
        try:
            return self._models.get(model_id)
        except NotFoundError as exc:
            raise ModelNotFoundError(str(exc)) from exc

    def get_model_by_name(self, *, name: str, owner: str) -> Model:
        """Return a model by stable (owner, name) identity."""
        try:
            return self._models.get_by_name(name=name, owner=owner)
        except NotFoundError as exc:
            raise ModelNotFoundError(str(exc)) from exc

    def list_models(self, *, limit: int = 100, offset: int = 0) -> list[Model]:
        """List all registered models."""
        return self._models.list(limit=limit, offset=offset)

    # ------------------------------------------------------------------
    # Model version registration
    # ------------------------------------------------------------------

    def register_model_version(
        self,
        *,
        model_id: str,
        semver: str,
        inputs_spec: str | None = None,
        outputs_spec: str | None = None,
        execution_entrypoint: str | None = None,
        is_active: bool = False,
        adapter_type: str | None = None,
        adapter_config: dict[str, Any] | None = None,
    ) -> ModelVersion:
        """
        Record a new version for an existing model.

        Raises InvalidParentModelError if the model does not exist.
        Raises ModelAlreadyExistsError if (model_id, semver) is already registered.
        Raises ActiveVersionConflictError if is_active=True while another version is active
            (use activate_version to switch).
        Raises AdapterConfigError if adapter_type/adapter_config cannot build an adapter.
        Raises RegistryPersistenceError on unexpected DB failure.
        """
        # Parent model must exist
        try:
            self._models.get(model_id)
        except NotFoundError as exc:
            raise InvalidParentModelError(
                f"Cannot register version: model_id={model_id!r} does not exist."
            ) from exc

        if is_active and self._versions.list_active(model_id):
            raise ActiveVersionConflictError(
                f"Model {model_id!r} already has an active version; use activate_version()."
            )
        if adapter_type is not None:
            validate_adapter_config(adapter_type, adapter_config or {})

        entity = ModelVersion(
            model_id=model_id,
            semver=semver,
            inputs_spec=inputs_spec,
            outputs_spec=outputs_spec,
            execution_entrypoint=execution_entrypoint,
            is_active=is_active,
            adapter_type=adapter_type,
            adapter_config=json.dumps(adapter_config) if adapter_type is not None else None,
        )
        try:
            return self._versions.add(entity)
        except DuplicateError as exc:
            raise ModelAlreadyExistsError(
                f"Version semver={semver!r} already exists for model_id={model_id!r}."
            ) from exc
        except Exception as exc:
            raise RegistryPersistenceError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Model version lookup
    # ------------------------------------------------------------------

    def get_model_version(self, version_id: str) -> ModelVersion:
        """Return a version by primary-key ID."""
        try:
            return self._versions.get(version_id)
        except NotFoundError as exc:
            raise ModelVersionNotFoundError(str(exc)) from exc

    def get_model_version_by_semver(self, *, model_id: str, semver: str) -> ModelVersion:
        """Return a version by (model_id, semver)."""
        try:
            return self._versions.get_by_model_and_semver(model_id=model_id, semver=semver)
        except NotFoundError as exc:
            raise ModelVersionNotFoundError(str(exc)) from exc

    def list_model_versions(self, model_id: str) -> list[ModelVersion]:
        """List all versions for a model, ordered by semver."""
        return self._versions.list_by_model(model_id)

    # ------------------------------------------------------------------
    # Active version (D16)
    # ------------------------------------------------------------------

    def activate_version(self, version_id: str) -> ModelVersion:
        """Make `version_id` the model's only active version."""
        version = self.get_model_version(version_id)
        for other in self._versions.list_active(version.model_id):
            if other.id != version.id:
                other.is_active = False
        version.is_active = True
        self._db.flush()
        return version

    def deactivate_version(self, version_id: str) -> ModelVersion:
        version = self.get_model_version(version_id)
        version.is_active = False
        self._db.flush()
        return version

    def get_active_version(self, model_id: str) -> ModelVersion:
        """The model's single active version; raises if none or (legacy data) several."""
        actives = self._versions.list_active(model_id)
        if not actives:
            raise ModelVersionNotFoundError(f"Model {model_id!r} has no active version.")
        if len(actives) > 1:
            raise ActiveVersionConflictError(
                f"Model {model_id!r} has {len(actives)} active versions; activate exactly one."
            )
        return actives[0]

    def set_model_status(self, model_id: str, status: str) -> Model:
        if status not in MODEL_STATUSES:
            raise ValueError(f"Unknown model status {status!r}; expected one of {sorted(MODEL_STATUSES)}.")
        model = self.get_model(model_id)
        model.status = status
        self._db.flush()
        return model
