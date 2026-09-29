"""Seeds the demo federation (synthetic arithmetic, not a domain model).

    fuel_price_input (source dataset, contract: value ≥ 0 USD/t)
        → Fuel Price (×1.0)      → fuel_price_output
        → Shipping Cost (×2.0)   → shipping_cost_output
        → Operations Cost (×1.5) → ops_cost_output
        → Emissions (×0.5)       → emissions_output

Everything needed to execute the chain is persisted: adapter type/config on each
version, explicit input/output bindings, dependency edges and dataset contracts.
Idempotent "ensure" semantics: each entity is created only if missing, so a database
seeded by an earlier phase is upgraded in place.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from backend.app.persistence.database import Dataset, ModelVersion
from backend.app.persistence.dataset_repository import DatasetRepository
from backend.app.persistence.exceptions import NotFoundError
from backend.app.persistence.model_version_repository import ModelVersionRepository
from backend.app.services.data_contract_manager import DataContractManager
from backend.app.services.dataset_values import DatasetValueService
from backend.app.services.federation import FederationService
from backend.app.services.model_registry import ModelNotFoundError, ModelRegistry

DEMO_OWNER = "demo"
INPUT_DATASET = "fuel_price_input"
INITIAL_FUEL_PRICE = 100.0

_CHAIN = [
    {"name": "Fuel Price",      "scalar": 1.0, "dataset": "fuel_price_output",    "unit": "USD/t"},
    {"name": "Shipping Cost",   "scalar": 2.0, "dataset": "shipping_cost_output", "unit": "synthetic index"},
    {"name": "Operations Cost", "scalar": 1.5, "dataset": "ops_cost_output",      "unit": "synthetic index"},
    {"name": "Emissions",       "scalar": 0.5, "dataset": "emissions_output",     "unit": "synthetic index"},
]


def _value_contract(unit: str) -> str:
    return json.dumps({
        "fields": [{"name": "value", "type": "float", "nullable": False, "min": 0, "unit": unit}],
        "additional_fields": False,
    })


def _ensure_dataset(db: Session, name: str, description: str, unit: str) -> Dataset:
    repo = DatasetRepository(db)
    try:
        ds = repo.get_by_name(name)
    except NotFoundError:
        ds = repo.add(Dataset(name=name, description=description))
    contracts = DataContractManager(db)
    if contracts.get_active_contract(ds.id) is None:
        contracts.register_contract(dataset_id=ds.id, schema_json=_value_contract(unit), semver="1.0.0")
    return ds


def _ensure_version(db: Session, registry: ModelRegistry, name: str, scalar: float) -> ModelVersion:
    try:
        model = registry.get_model_by_name(name=name, owner=DEMO_OWNER)
    except ModelNotFoundError:
        model = registry.register_model(
            name=name, owner=DEMO_OWNER, model_type="synthetic",
            description=f"Demo model — {name} (synthetic ×{scalar}, not a domain model)",
            status="active",
        )
    versions = ModelVersionRepository(db)
    try:
        version = versions.get_by_model_and_semver(model.id, "1.0.0")
    except NotFoundError:
        version = registry.register_model_version(
            model_id=model.id, semver="1.0.0",
            inputs_spec='[{"name": "value", "type": "float"}]',
            outputs_spec='[{"name": "value", "type": "float"}]',
            adapter_type="synthetic", adapter_config={"scalar": scalar},
        )
    if version.adapter_type is None:
        version.adapter_type = "synthetic"
        version.adapter_config = json.dumps({"scalar": scalar})
    if not version.is_active:
        registry.activate_version(version.id)
    db.flush()
    return version


def seed_demo_data(db: Session) -> dict:
    """Ensure the demo federation exists; return the demo config (IDs for the UI)."""
    registry = ModelRegistry(db)
    federation = FederationService(db)

    source = _ensure_dataset(db, INPUT_DATASET, "Fuel price input (source dataset)", "USD/t")
    versions: list[ModelVersion] = []
    outputs: list[Dataset] = []
    for entry in _CHAIN:
        versions.append(_ensure_version(db, registry, entry["name"], entry["scalar"]))
        outputs.append(_ensure_dataset(db, entry["dataset"], f"Output of {entry['name']}", entry["unit"]))

    federation.ensure_binding(versions[0].id, source.id, "input")
    for i, (version, out) in enumerate(zip(versions, outputs)):
        federation.ensure_binding(version.id, out.id, "output")
        if i + 1 < len(versions):
            federation.connect(
                producer_version_id=version.id, dataset_id=out.id,
                consumer_version_id=versions[i + 1].id,
            )

    if source.current_value is None:
        DatasetValueService(db).write_external(
            source.id, {"value": INITIAL_FUEL_PRICE},
            source_type="seed", source_ref="demo_seed", triggered_by="seed",
        )
    db.flush()
    return build_demo_config(versions, outputs, source)


def build_demo_config(versions: list[ModelVersion], outputs: list[Dataset], source: Dataset) -> dict:
    return {
        "models": [
            {"name": entry["name"], "version_id": v.id, "dataset_id": ds.id,
             "dataset_name": ds.name, "unit": entry["unit"]}
            for entry, v, ds in zip(_CHAIN, versions, outputs)
        ],
        "terminal_version_id": versions[-1].id,
        "fuel_price_version_id": versions[0].id,
        "input_dataset_id": source.id,
        "input_dataset_name": source.name,
        "input_field": "value",
        "chain": [c["name"] for c in _CHAIN],
    }
