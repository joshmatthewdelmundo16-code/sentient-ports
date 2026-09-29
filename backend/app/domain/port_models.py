"""Toy port domain models — D17.

Synthetic, clearly-illustrative arithmetic for a port's in-port operations. These are
NOT Sentient Ports proprietary logic and NOT calibrated real-world figures; they exist to
exercise the D16 federation engine (dataset routing, contracts, fan-in, GraphRun) with a
credible multi-model, multi-level dependency graph.

Each function is pure: named float inputs → dict of named float/bool outputs. The per-TEU
metrics return None when annual throughput is zero (division is undefined), which the
dataset contracts model as nullable fields.

Dependency structure (two-level DAG):

    Assumptions ─┬─ Throughput ── annual_teu ─┬─ Port Fuel Cost ─┐
                 │                            └─ Port Emissions ─┤
                 └─ Berth Utilization ───────────────────────────┤
    Throughput, Berth Utilization, Port Fuel Cost, Port Emissions ┴─ Decision Summary

Annual fuel cost and annual emissions use their direct assumption inputs; only the
per-TEU metrics consume Throughput's persisted `annual_teu` (never recomputed here).
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

HOURS_PER_YEAR = 8760.0


# ---------------------------------------------------------------------------
# Pure formulas
# ---------------------------------------------------------------------------

def throughput(*, annual_vessel_calls: float, average_teu_per_call: float) -> dict[str, Any]:
    """annual_teu = annual_vessel_calls × average_teu_per_call"""
    return {"annual_teu": annual_vessel_calls * average_teu_per_call}


def berth_utilization(
    *,
    annual_vessel_calls: float,
    average_berth_hours_per_call: float,
    number_of_berths: float,
    congestion_threshold_pct: float,
) -> dict[str, Any]:
    """
    utilization_pct = (calls × berth_hours) / (berths × 8760) × 100

    Reported as a percentage of annual berth-hour capacity. congestion is True when
    utilization reaches the configured threshold. berths is contract-constrained to ≥ 1;
    the guard here keeps the function total if it is ever called with zero capacity.
    """
    capacity_hours = number_of_berths * HOURS_PER_YEAR
    if capacity_hours <= 0:
        return {"utilization_pct": None, "congestion": False}
    utilization_pct = (annual_vessel_calls * average_berth_hours_per_call) / capacity_hours * 100.0
    return {
        "utilization_pct": utilization_pct,
        "congestion": utilization_pct >= congestion_threshold_pct,
    }


def port_fuel_cost(
    *,
    annual_vessel_calls: float,
    fuel_burned_in_port_per_call: float,
    bunker_price: float,
    annual_teu: float,
) -> dict[str, Any]:
    """
    annual_fuel_t      = calls × fuel_burned_in_port_per_call        (direct assumptions)
    annual_fuel_cost_usd = annual_fuel_t × bunker_price              (direct assumptions)
    fuel_cost_per_teu   = annual_fuel_cost_usd / annual_teu          (consumes Throughput)
    """
    annual_fuel_t = annual_vessel_calls * fuel_burned_in_port_per_call
    annual_fuel_cost_usd = annual_fuel_t * bunker_price
    fuel_cost_per_teu = annual_fuel_cost_usd / annual_teu if annual_teu else None
    return {
        "annual_fuel_t": annual_fuel_t,
        "annual_fuel_cost_usd": annual_fuel_cost_usd,
        "fuel_cost_per_teu": fuel_cost_per_teu,
    }


def port_emissions(
    *,
    annual_vessel_calls: float,
    fuel_burned_in_port_per_call: float,
    emission_factor: float,
    annual_teu: float,
) -> dict[str, Any]:
    """
    annual_emissions_tco2 = calls × fuel_burned_in_port_per_call × emission_factor  (direct)
    emissions_per_teu     = annual_emissions_tco2 / annual_teu          (consumes Throughput)
    """
    annual_emissions_tco2 = annual_vessel_calls * fuel_burned_in_port_per_call * emission_factor
    emissions_per_teu = annual_emissions_tco2 / annual_teu if annual_teu else None
    return {
        "annual_emissions_tco2": annual_emissions_tco2,
        "emissions_per_teu": emissions_per_teu,
    }


def decision_summary(
    *,
    annual_teu: float,
    berth_utilization_pct: float,
    congestion: bool,
    annual_fuel_cost_usd: float,
    fuel_cost_per_teu: float | None,
    annual_emissions_tco2: float,
    emissions_per_teu: float | None,
) -> dict[str, Any]:
    """Fan-in aggregator: collect the four upstream models' KPIs into one decision view."""
    return {
        "annual_teu": annual_teu,
        "berth_utilization_pct": berth_utilization_pct,
        "annual_fuel_cost_usd": annual_fuel_cost_usd,
        "fuel_cost_per_teu": fuel_cost_per_teu,
        "annual_emissions_tco2": annual_emissions_tco2,
        "emissions_per_teu": emissions_per_teu,
        "congestion_status": congestion,
    }


MODEL_FUNCS: dict[str, Callable[..., dict[str, Any]]] = {
    "throughput": throughput,
    "berth_utilization": berth_utilization,
    "port_fuel_cost": port_fuel_cost,
    "port_emissions": port_emissions,
    "decision_summary": decision_summary,
}


def required_params(model_key: str) -> list[str]:
    """Parameter names of the model's pure function (inputs + config params)."""
    fn = MODEL_FUNCS[model_key]
    return list(inspect.signature(fn).parameters)


def compute(model_key: str, values: dict[str, Any]) -> dict[str, Any]:
    """
    Call the model's pure function with the subset of `values` it declares.

    Raises KeyError (with the missing names) if a declared parameter is absent, so the
    execution service surfaces it as a step failure rather than a generic TypeError.
    """
    fn = MODEL_FUNCS[model_key]
    params = inspect.signature(fn).parameters
    missing = [
        name for name, p in params.items()
        if p.default is inspect.Parameter.empty and name not in values
    ]
    if missing:
        raise KeyError(f"model {model_key!r} is missing required input(s): {missing}")
    return fn(**{k: values[k] for k in params if k in values})
