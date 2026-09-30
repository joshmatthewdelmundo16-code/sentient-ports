"""Seeds the D17 toy port domain federation (synthetic, illustrative — not real port data).

Two-level dependency DAG built entirely on the D16 federation engine:

    port_assumptions (source: 7 contracted inputs)
        ├── Throughput ── throughput_output(annual_teu) ─┬── Port Fuel Cost ─┐
        │                                                └── Port Emissions ─┤
        └── Berth Utilization ─────────────────────────────────────────────┤
      Throughput · Berth Utilization · Port Fuel Cost · Port Emissions ──────┴── Decision Summary

Execution behaviour is persisted on each version (adapter_type="port_domain",
adapter_config={"model": ...}); no business logic lives here and no in-memory adapter
registry is built. Idempotent "ensure" semantics: safe to re-run against an existing DB
(SQLite or PostgreSQL) — nothing is dropped.

This seed is additive and independent of the D0–D16 fuel-price demo (different owner and
model names), so both federations can coexist in one database.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from backend.app.persistence.database import Dataset, ModelVersion
from backend.app.persistence.dataset_repository import DatasetRepository
from backend.app.persistence.exceptions import NotFoundError
from backend.app.persistence.model_version_repository import ModelVersionRepository
from backend.app.services.data_contract_manager import DataContractManager
from backend.app.services.dataset_values import DatasetValueService
from backend.app.services.federation import FederationService
from backend.app.services.model_registry import ModelNotFoundError, ModelRegistry

PORT_OWNER = "port-demo"
ASSUMPTIONS_DATASET = "port_assumptions"
CONGESTION_THRESHOLD_PCT = 70.0

# Assumption field names
BUNKER_PRICE = "bunker_price"
CALLS = "annual_vessel_calls"
TEU_PER_CALL = "average_teu_per_call"
BERTH_HOURS = "average_berth_hours_per_call"
BERTHS = "number_of_berths"
FUEL_PER_CALL = "fuel_burned_in_port_per_call"
EMISSION_FACTOR = "emission_factor"

DEFAULT_ASSUMPTIONS: dict[str, float] = {
    BUNKER_PRICE: 600.0,
    CALLS: 10000.0,
    TEU_PER_CALL: 2000.0,
    BERTH_HOURS: 2.0,
    BERTHS: 6.0,
    FUEL_PER_CALL: 2.0,
    EMISSION_FACTOR: 3.114,
}

_ILLUSTRATIVE = "Illustrative/synthetic — not calibrated real-world data or Sentient IP."


# ---------------------------------------------------------------------------
# Contract-field helpers
# ---------------------------------------------------------------------------

# D25: business-readable, sentence-case field labels. Presentation metadata stored with the
# contract (FieldSpec.label) — never consulted by validation.
FIELD_LABELS: dict[str, str] = {
    BUNKER_PRICE: "Bunker price",
    CALLS: "Annual vessel calls",
    TEU_PER_CALL: "Average TEU per call",
    BERTH_HOURS: "Average berth hours per call",
    BERTHS: "Number of berths",
    FUEL_PER_CALL: "Fuel burned in port per call",
    EMISSION_FACTOR: "Emission factor",
    "annual_teu": "Annual throughput",
    "utilization_pct": "Berth utilization",
    "congestion": "Congestion",
    "annual_fuel_t": "Annual fuel burned",
    "annual_fuel_cost_usd": "Annual fuel cost",
    "fuel_cost_per_teu": "Fuel cost per TEU",
    "annual_emissions_tco2": "Annual CO₂ emissions",
    "emissions_per_teu": "CO₂ emissions per TEU",
    "berth_utilization_pct": "Berth utilization",
    "congestion_status": "Congestion status",
}

DATASET_LABELS: dict[str, str] = {
    ASSUMPTIONS_DATASET: "Port assumptions",
    "throughput_output": "Throughput results",
    "berth_utilization_output": "Berth utilization results",
    "port_fuel_cost_output": "Port fuel cost results",
    "port_emissions_output": "Port emissions results",
    "decision_summary_output": "Decision summary",
}


def _num(name: str, unit: str, *, nullable: bool = False, minimum: float | None = 0.0,
         maximum: float | None = None) -> dict[str, Any]:
    field: dict[str, Any] = {"name": name, "type": "float", "nullable": nullable,
                             "required": True, "unit": unit}
    if name in FIELD_LABELS:
        field["label"] = FIELD_LABELS[name]
    if minimum is not None:
        field["min"] = minimum
    if maximum is not None:
        field["max"] = maximum
    return field


def _boolean(name: str) -> dict[str, Any]:
    field: dict[str, Any] = {"name": name, "type": "boolean", "nullable": False, "required": True}
    if name in FIELD_LABELS:
        field["label"] = FIELD_LABELS[name]
    return field


_ASSUMPTION_FIELDS = [
    _num(BUNKER_PRICE, "USD/t"),
    _num(CALLS, "calls/year"),
    _num(TEU_PER_CALL, "TEU/call"),
    _num(BERTH_HOURS, "hours/call"),
    _num(BERTHS, "berths", minimum=1.0),          # zero/negative berths rejected at the boundary
    _num(FUEL_PER_CALL, "t fuel/call"),
    _num(EMISSION_FACTOR, "tCO2/t fuel"),
]

_DATASETS: dict[str, list[dict[str, Any]]] = {
    ASSUMPTIONS_DATASET: _ASSUMPTION_FIELDS,
    "throughput_output": [_num("annual_teu", "TEU/year")],
    "berth_utilization_output": [_num("utilization_pct", "%"), _boolean("congestion")],
    "port_fuel_cost_output": [
        _num("annual_fuel_t", "t/year"),
        _num("annual_fuel_cost_usd", "USD/year"),
        _num("fuel_cost_per_teu", "USD/TEU", nullable=True),
    ],
    "port_emissions_output": [
        _num("annual_emissions_tco2", "tCO2/year"),
        _num("emissions_per_teu", "tCO2/TEU", nullable=True),
    ],
    "decision_summary_output": [
        _num("annual_teu", "TEU/year"),
        _num("berth_utilization_pct", "%"),
        _num("annual_fuel_cost_usd", "USD/year"),
        _num("fuel_cost_per_teu", "USD/TEU", nullable=True),
        _num("annual_emissions_tco2", "tCO2/year"),
        _num("emissions_per_teu", "tCO2/TEU", nullable=True),
        _boolean("congestion_status"),
    ],
}

# Model cards: purpose, formula, assumptions, I/O with units, adapter config.
_MODELS = [
    {
        "key": "throughput", "domain": "operational", "name": "Throughput",
        "purpose": "Annual container throughput from vessel calls.",
        "formula": "annual_teu = annual_vessel_calls * average_teu_per_call",
        "config": {"model": "throughput"},
        "inputs": [(CALLS, "calls/year"), (TEU_PER_CALL, "TEU/call")],
        "outputs": [("annual_teu", "TEU/year")],
        "output_dataset": "throughput_output",
    },
    {
        "key": "berth_utilization", "domain": "operational", "name": "Berth utilization",
        "purpose": "Share of annual berth-hour capacity consumed by vessel calls.",
        "formula": "utilization_pct = (annual_vessel_calls * average_berth_hours_per_call) "
                   "/ (number_of_berths * 8760) * 100",
        "config": {"model": "berth_utilization", "congestion_threshold_pct": CONGESTION_THRESHOLD_PCT},
        "inputs": [(CALLS, "calls/year"), (BERTH_HOURS, "hours/call"), (BERTHS, "berths")],
        "outputs": [("utilization_pct", "%"), ("congestion", "bool")],
        "output_dataset": "berth_utilization_output",
    },
    {
        "key": "port_fuel_cost", "domain": "economic", "name": "Port fuel cost",
        "purpose": "Annual in-port bunker fuel cost and cost intensity per TEU.",
        "formula": "annual_fuel_t = annual_vessel_calls * fuel_burned_in_port_per_call; "
                   "annual_fuel_cost_usd = annual_fuel_t * bunker_price; "
                   "fuel_cost_per_teu = annual_fuel_cost_usd / annual_teu",
        "config": {"model": "port_fuel_cost"},
        "inputs": [(CALLS, "calls/year"), (FUEL_PER_CALL, "t fuel/call"),
                   (BUNKER_PRICE, "USD/t"), ("annual_teu", "TEU/year")],
        "outputs": [("annual_fuel_t", "t/year"), ("annual_fuel_cost_usd", "USD/year"),
                    ("fuel_cost_per_teu", "USD/TEU")],
        "output_dataset": "port_fuel_cost_output",
    },
    {
        "key": "port_emissions", "domain": "environmental", "name": "Port emissions",
        "purpose": "Annual in-port CO2 emissions and emission intensity per TEU.",
        "formula": "annual_emissions_tco2 = annual_vessel_calls * fuel_burned_in_port_per_call "
                   "* emission_factor; emissions_per_teu = annual_emissions_tco2 / annual_teu",
        "config": {"model": "port_emissions"},
        "inputs": [(CALLS, "calls/year"), (FUEL_PER_CALL, "t fuel/call"),
                   (EMISSION_FACTOR, "tCO2/t fuel"), ("annual_teu", "TEU/year")],
        "outputs": [("annual_emissions_tco2", "tCO2/year"), ("emissions_per_teu", "tCO2/TEU")],
        "output_dataset": "port_emissions_output",
    },
    {
        "key": "decision_summary", "domain": "decision", "name": "Decision summary",
        "purpose": "Fan-in of the four models into one decision-support KPI view.",
        "formula": "collect: annual_teu, berth_utilization_pct, annual_fuel_cost_usd, "
                   "fuel_cost_per_teu, annual_emissions_tco2, emissions_per_teu, congestion_status",
        "config": {"model": "decision_summary"},
        "inputs": [("annual_teu", "TEU/year"), ("berth_utilization_pct", "%"),
                   ("congestion", "bool"), ("annual_fuel_cost_usd", "USD/year"),
                   ("fuel_cost_per_teu", "USD/TEU"), ("annual_emissions_tco2", "tCO2/year"),
                   ("emissions_per_teu", "tCO2/TEU")],
        "outputs": [(f, "") for f in (
            "annual_teu", "berth_utilization_pct", "annual_fuel_cost_usd", "fuel_cost_per_teu",
            "annual_emissions_tco2", "emissions_per_teu", "congestion_status")],
        "output_dataset": "decision_summary_output",
    },
]


# ---------------------------------------------------------------------------
# Ensure helpers
# ---------------------------------------------------------------------------

def _ensure_dataset(db: Session, name: str, fields: list[dict[str, Any]]) -> Dataset:
    repo = DatasetRepository(db)
    try:
        ds = repo.get_by_name(name)
    except NotFoundError:
        ds = repo.add(Dataset(name=name, description=f"Port domain dataset — {name}"))
    if not ds.display_name and name in DATASET_LABELS:
        ds.display_name = DATASET_LABELS[name]
    contracts = DataContractManager(db)
    if contracts.get_active_contract(ds.id) is None:
        contracts.register_contract(
            dataset_id=ds.id,
            schema_json=json.dumps({"fields": fields, "additional_fields": False}),
            semver="1.0.0",
        )
    return ds


def _ensure_version(db: Session, registry: ModelRegistry, spec: dict[str, Any]) -> ModelVersion:
    description = (
        f"{spec['purpose']} | Formula: {spec['formula']} | "
        f"Inputs: {', '.join(f'{n} ({u})' for n, u in spec['inputs'])} | {_ILLUSTRATIVE}"
    )
    try:
        model = registry.get_model_by_name(name=spec["name"], owner=PORT_OWNER)
    except ModelNotFoundError:
        model = registry.register_model(
            name=spec["name"], owner=PORT_OWNER, model_type="port_domain",
            description=description, status="active",
        )
    if not model.card_json:
        model.card_json = json.dumps(model_card(spec))
    versions = ModelVersionRepository(db)
    try:
        version = versions.get_by_model_and_semver(model.id, "1.0.0")
    except NotFoundError:
        version = registry.register_model_version(
            model_id=model.id, semver="1.0.0",
            inputs_spec=json.dumps([{"name": n, "unit": u} for n, u in spec["inputs"]]),
            outputs_spec=json.dumps([{"name": n, "unit": u} for n, u in spec["outputs"]]),
            adapter_type="port_domain", adapter_config=spec["config"],
        )
    if version.adapter_type is None:
        version.adapter_type = "port_domain"
        version.adapter_config = json.dumps(spec["config"])
    if not version.is_active:
        registry.activate_version(version.id)
    db.flush()
    return version


def model_card(spec: dict[str, Any]) -> dict[str, Any]:
    """Model-library card for one toy port model (D25). Honest by construction: every card
    states the model is illustrative and uncalibrated."""
    return {
        "purpose": spec["purpose"],
        "formula": spec["formula"],
        "domain": spec.get("domain"),
        "provider": "Platform (internal)",
        "provider_kind": "internal",
        "execution_method": "In-process Python function (port_domain adapter)",
        "calibration": "illustrative",
        "provenance": "Synthetic textbook arithmetic authored for this platform. "
                      "Not calibrated against real port data.",
        "governance_status": "reviewed",
        "inputs": [{"name": n, "unit": u} for n, u in spec["inputs"]],
        "outputs": [{"name": n, "unit": u} for n, u in spec["outputs"]],
        "disclaimer": _ILLUSTRATIVE,
    }


def _identity_map(names: list[str]) -> dict[str, str]:
    return {n: n for n in names}


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

def seed_port_domain(db: Session) -> dict:
    """Ensure the D17 port-domain federation exists; return its config (IDs for tests/UI)."""
    registry = ModelRegistry(db)
    fed = FederationService(db)
    values = DatasetValueService(db)

    datasets = {name: _ensure_dataset(db, name, fields) for name, fields in _DATASETS.items()}
    versions = {spec["key"]: _ensure_version(db, registry, spec) for spec in _MODELS}
    assumptions = datasets[ASSUMPTIONS_DATASET]

    v = versions
    d = datasets

    # Source (Assumptions) → model input bindings, selecting only the fields each model uses.
    fed.ensure_binding(v["throughput"].id, assumptions.id, "input",
                       _identity_map([CALLS, TEU_PER_CALL]))
    fed.ensure_binding(v["berth_utilization"].id, assumptions.id, "input",
                       _identity_map([CALLS, BERTH_HOURS, BERTHS]))
    fed.ensure_binding(v["port_fuel_cost"].id, assumptions.id, "input",
                       _identity_map([CALLS, FUEL_PER_CALL, BUNKER_PRICE]))
    fed.ensure_binding(v["port_emissions"].id, assumptions.id, "input",
                       _identity_map([CALLS, FUEL_PER_CALL, EMISSION_FACTOR]))

    # Throughput → Port Fuel Cost / Port Emissions (annual_teu consumed, never recomputed).
    fed.connect(producer_version_id=v["throughput"].id, dataset_id=d["throughput_output"].id,
                consumer_version_id=v["port_fuel_cost"].id, input_field_map=_identity_map(["annual_teu"]))
    fed.connect(producer_version_id=v["throughput"].id, dataset_id=d["throughput_output"].id,
                consumer_version_id=v["port_emissions"].id, input_field_map=_identity_map(["annual_teu"]))

    # All four models → Decision Summary (fan-in).
    fed.connect(producer_version_id=v["throughput"].id, dataset_id=d["throughput_output"].id,
                consumer_version_id=v["decision_summary"].id, input_field_map=_identity_map(["annual_teu"]))
    fed.connect(producer_version_id=v["berth_utilization"].id, dataset_id=d["berth_utilization_output"].id,
                consumer_version_id=v["decision_summary"].id,
                input_field_map={"utilization_pct": "berth_utilization_pct", "congestion": "congestion"})
    fed.connect(producer_version_id=v["port_fuel_cost"].id, dataset_id=d["port_fuel_cost_output"].id,
                consumer_version_id=v["decision_summary"].id,
                input_field_map=_identity_map(["annual_fuel_cost_usd", "fuel_cost_per_teu"]))
    fed.connect(producer_version_id=v["port_emissions"].id, dataset_id=d["port_emissions_output"].id,
                consumer_version_id=v["decision_summary"].id,
                input_field_map=_identity_map(["annual_emissions_tco2", "emissions_per_teu"]))

    # Decision Summary terminal output.
    fed.ensure_binding(v["decision_summary"].id, d["decision_summary_output"].id, "output")

    if assumptions.current_value is None:
        values.write_external(assumptions.id, dict(DEFAULT_ASSUMPTIONS),
                              source_type="seed", source_ref="port_domain_seed", triggered_by="seed")
    db.flush()
    return build_port_config(versions, datasets)


def build_port_config(versions: dict[str, ModelVersion], datasets: dict[str, Dataset]) -> dict:
    return {
        "domain": "toy_port",
        "input_dataset_id": datasets[ASSUMPTIONS_DATASET].id,
        "input_dataset_name": ASSUMPTIONS_DATASET,
        "assumptions_dataset_id": datasets[ASSUMPTIONS_DATASET].id,
        "default_assumptions": dict(DEFAULT_ASSUMPTIONS),
        "congestion_threshold_pct": CONGESTION_THRESHOLD_PCT,
        "terminal_version_id": versions["decision_summary"].id,
        "models": [
            {"key": spec["key"], "name": spec["name"],
             "version_id": versions[spec["key"]].id,
             "output_dataset_id": datasets[spec["output_dataset"]].id}
            for spec in _MODELS
        ],
        "datasets": {name: ds.id for name, ds in datasets.items()},
    }
