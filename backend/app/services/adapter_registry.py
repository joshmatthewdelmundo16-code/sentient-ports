"""Adapter Registry service — D6.

Application-layer service for registering and resolving model adapters.

D16: execution behaviour is persisted on ModelVersion (adapter_type + adapter_config
JSON) and built on demand by build_persisted_adapter(). In-memory registration
remains an explicit override (tests, embedded callers) and takes precedence.

Architecture:
    Execution Service
            ↓
    AdapterRegistry.resolve_for_version(version)
            ├─ in-memory override (register_adapter)
            └─ persisted config → ADAPTER_FACTORIES[adapter_type]
            ↓
    ModelAdapter  (adapters/base.py)
"""

from __future__ import annotations

import json
from typing import Any, Callable

from sqlalchemy.orm import Session

from backend.app.adapters.base import ModelAdapter
from backend.app.adapters.synthetic import SyntheticAdapter
from backend.app.persistence.database import ModelVersion
from backend.app.persistence.exceptions import NotFoundError
from backend.app.persistence.model_version_repository import ModelVersionRepository


# ---------------------------------------------------------------------------
# Service-level errors
# ---------------------------------------------------------------------------

class AdapterNotFoundError(Exception):
    """No adapter is registered for the requested model version."""


class DuplicateAdapterError(Exception):
    """An adapter with this adapter_id is already registered."""


class AdapterVersionNotFoundError(Exception):
    """The referenced model version does not exist in the registry."""


class UnsupportedMappingError(Exception):
    """The adapter's version_id does not match any known model version."""


class InvalidAdapterError(Exception):
    """The adapter object is invalid or does not satisfy the required contract."""


class AdapterConfigError(Exception):
    """Persisted adapter configuration is missing, unknown or malformed."""


# ---------------------------------------------------------------------------
# Persisted adapter factories
# ---------------------------------------------------------------------------

def _build_synthetic(version: ModelVersion, config: dict[str, Any]) -> ModelAdapter:
    scalar = config.get("scalar", 1.0)
    if isinstance(scalar, bool) or not isinstance(scalar, (int, float)):
        raise AdapterConfigError(f"synthetic adapter 'scalar' must be numeric, got {scalar!r}")
    return SyntheticAdapter(
        adapter_id=f"persisted:synthetic:{version.id}", version_id=version.id, scalar=float(scalar)
    )


def _build_port_domain(version: ModelVersion, config: dict[str, Any]) -> ModelAdapter:
    from backend.app.adapters.port_domain import PortDomainAdapter
    from backend.app.domain import port_models

    model = config.get("model")
    if model not in port_models.MODEL_FUNCS:
        raise AdapterConfigError(
            f"port_domain 'model' must be one of {sorted(port_models.MODEL_FUNCS)}, got {model!r}"
        )
    params = {k: v for k, v in config.items() if k != "model"}
    for k, v in params.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise AdapterConfigError(
                f"port_domain param {k!r} must be numeric, got {v!r}"
            )
    return PortDomainAdapter(
        adapter_id=f"persisted:port_domain:{version.id}",
        version_id=version.id, model=model, params=params,
    )


def _build_model_pack(version: ModelVersion, config: dict[str, Any]) -> ModelAdapter:
    """D27: a model from a reviewed pack. The (pack, model) pair is resolved against the
    in-repository allowlist — persisted configuration can never name arbitrary code."""
    from backend.app.adapters.base import ModelAdapter as _Base
    from backend.app.library import packs

    try:
        model = packs.resolve(str(config.get("pack")), str(config.get("model")))
    except KeyError as exc:
        raise AdapterConfigError(str(exc)) from exc

    class _PackAdapter(_Base):
        @property
        def adapter_id(self) -> str:
            return f"persisted:model_pack:{version.id}"

        @property
        def version_id(self) -> str:
            return version.id

        def invoke(self, inputs: dict[str, Any]) -> dict[str, Any]:
            return packs.compute(model, inputs)

    return _PackAdapter()


ADAPTER_FACTORIES: dict[str, Callable[[ModelVersion, dict[str, Any]], ModelAdapter]] = {
    "synthetic": _build_synthetic,
    "port_domain": _build_port_domain,
    "model_pack": _build_model_pack,
}


def validate_adapter_config(adapter_type: str, config: dict[str, Any]) -> None:
    """Raise AdapterConfigError if (adapter_type, config) cannot build an adapter."""
    factory = ADAPTER_FACTORIES.get(adapter_type)
    if factory is None:
        raise AdapterConfigError(
            f"Unknown adapter_type {adapter_type!r}; known: {sorted(ADAPTER_FACTORIES)}"
        )
    probe = ModelVersion(id="probe", model_id="probe", semver="0")
    factory(probe, config)


def build_persisted_adapter(version: ModelVersion) -> ModelAdapter:
    if not version.adapter_type:
        raise AdapterNotFoundError(
            f"No adapter registered or persisted for version_id={version.id!r}."
        )
    factory = ADAPTER_FACTORIES.get(version.adapter_type)
    if factory is None:
        raise AdapterConfigError(
            f"Version {version.id!r} has unknown adapter_type {version.adapter_type!r}."
        )
    try:
        config = json.loads(version.adapter_config) if version.adapter_config else {}
    except json.JSONDecodeError as exc:
        raise AdapterConfigError(f"Version {version.id!r} adapter_config is not JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise AdapterConfigError(f"Version {version.id!r} adapter_config must be an object.")
    return factory(version, config)


# ---------------------------------------------------------------------------
# Registry service
# ---------------------------------------------------------------------------

class AdapterRegistry:
    """
    In-memory registry that maps adapter_id → ModelAdapter and
    version_id → ModelAdapter for resolution.

    One instance per application lifetime (or per test session).
    Constructed with a SQLAlchemy session used only for validation
    lookups against the D2 repository layer.
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._versions = ModelVersionRepository(db)
        # adapter_id → adapter
        self._by_id: dict[str, ModelAdapter] = {}
        # version_id → adapter
        self._by_version: dict[str, ModelAdapter] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_adapter(self, adapter: ModelAdapter) -> ModelAdapter:
        """
        Register an adapter against its declared model version.

        Raises InvalidAdapterError if the adapter object is not a ModelAdapter.
        Raises DuplicateAdapterError if adapter_id is already registered.
        Raises AdapterVersionNotFoundError if version_id does not exist in the DB.
        Returns the registered adapter.
        """
        if not isinstance(adapter, ModelAdapter):
            raise InvalidAdapterError(
                f"Object {adapter!r} does not satisfy the ModelAdapter contract."
            )

        if adapter.adapter_id in self._by_id:
            raise DuplicateAdapterError(
                f"Adapter id={adapter.adapter_id!r} is already registered."
            )

        # Validate that the version exists (uses D2 repository, no raw SQL)
        try:
            self._versions.get(adapter.version_id)
        except NotFoundError as exc:
            raise AdapterVersionNotFoundError(
                f"Model version_id={adapter.version_id!r} not found; "
                "register the model version before registering an adapter."
            ) from exc

        self._by_id[adapter.adapter_id] = adapter
        self._by_version[adapter.version_id] = adapter
        return adapter

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve_adapter(self, version_id: str) -> ModelAdapter:
        """
        Return the adapter registered for the given model version ID.

        Raises AdapterNotFoundError if no adapter is registered.
        """
        adapter = self._by_version.get(version_id)
        if adapter is None:
            raise AdapterNotFoundError(
                f"No adapter registered for version_id={version_id!r}."
            )
        return adapter

    def resolve_for_version(self, version: ModelVersion) -> ModelAdapter:
        """In-memory override if registered, otherwise the version's persisted adapter."""
        adapter = self._by_version.get(version.id)
        if adapter is not None:
            return adapter
        return build_persisted_adapter(version)

    def get_adapter_by_id(self, adapter_id: str) -> ModelAdapter:
        """Return an adapter by its stable adapter_id."""
        adapter = self._by_id.get(adapter_id)
        if adapter is None:
            raise AdapterNotFoundError(
                f"No adapter with id={adapter_id!r} is registered."
            )
        return adapter

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_adapters(self) -> list[ModelAdapter]:
        """Return all registered adapters in insertion order."""
        return list(self._by_id.values())
