"""Model packs: trusted, code-reviewed model sets that can be installed into an organization.

A pack declares datasets (with contracts: labels, units, bounds), models (pure functions in
this repository) and exactly which fields each model reads. Installing a pack registers all of
it through the ordinary engine services — model registry, contracts, bindings, dependency
edges — inside the caller's organization, so the installed federation is private to that
organization and runs on the same GraphRun engine as everything else.

Execution method: adapter_type="model_pack", adapter_config={"pack", "model"}. The adapter
resolves the function from the ALLOWLIST below; nothing in the database can name arbitrary
code to execute.

The formula shown in the model library IS the function's docstring, so the displayed formula
and the executed code share one source.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.library import port_city, port_planning


@dataclass(frozen=True)
class FieldDef:
    name: str
    label: str
    unit: str | None = None
    type: str = "number"
    min: float | None = None
    max: float | None = None
    nullable: bool = False

    def contract(self) -> dict[str, Any]:
        f: dict[str, Any] = {"name": self.name, "label": self.label, "type": self.type,
                             "nullable": self.nullable, "required": True}
        if self.unit:
            f["unit"] = self.unit
        if self.min is not None:
            f["min"] = self.min
        if self.max is not None:
            f["max"] = self.max
        return f


@dataclass(frozen=True)
class DatasetDef:
    name: str
    label: str
    fields: tuple[FieldDef, ...]


@dataclass(frozen=True)
class ModelDef:
    key: str
    name: str
    domain: str
    purpose: str
    fn: Callable[..., dict[str, Any]]
    reads: tuple[tuple[str, tuple[str, ...]], ...]     # (dataset name, fields)
    writes: str                                        # output dataset name

    @property
    def formula(self) -> str:
        return inspect.cleandoc(self.fn.__doc__ or "").strip()


@dataclass(frozen=True)
class Pack:
    key: str
    name: str
    description: str
    provider: str
    provider_kind: str              # internal | trusted_third_party | open_source
    calibration: str                # illustrative | uncalibrated | calibrated
    provenance: str
    datasets: tuple[DatasetDef, ...]
    models: tuple[ModelDef, ...]
    defaults: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def terminal(self) -> ModelDef:
        return self.models[-1]

    def dataset(self, name: str) -> DatasetDef:
        return next(d for d in self.datasets if d.name == name)

    @property
    def source_datasets(self) -> list[str]:
        produced = {m.writes for m in self.models}
        return [d.name for d in self.datasets if d.name not in produced]


def _n(name: str, label: str, unit: str | None = None, *, lo: float | None = 0.0, hi: float | None = None,
       nullable: bool = False) -> FieldDef:
    return FieldDef(name=name, label=label, unit=unit, min=lo, max=hi, nullable=nullable)


def _share(name: str, label: str) -> FieldDef:
    return FieldDef(name=name, label=label, unit="share", min=0.0, max=1.0)


# ---------------------------------------------------------------------------
# Port capacity planning
# ---------------------------------------------------------------------------

_PLAN_IN = "planning_inputs"
PLANNING = Pack(
    key="port_capacity_planning",
    name="Port capacity planning",
    description="Demand, vessel calls, berths and cranes (M/M/c queueing), yard, gate, "
                "capacity and resilience, energy and emissions, and cost — per planning period.",
    provider="Platform (internal)",
    provider_kind="internal",
    calibration="uncalibrated",
    provenance="Textbook port-planning relations (berth occupancy, M/M/c queueing, static yard "
               "capacity, gate lane throughput) with synthetic parameters. Not calibrated.",
    datasets=(
        DatasetDef(_PLAN_IN, "Planning inputs", (
            _n("demand_teu", "Container demand", "TEU/year"),
            _n("average_call_size_teu", "Average call size", "TEU/call", lo=1.0),
            _n("berths", "Berths", "berths", lo=1.0),
            _n("cranes_per_berth", "Cranes per berth", "cranes", lo=1.0),
            _n("crane_moves_per_hour", "Crane productivity", "moves/hour", lo=1.0),
            _n("teu_per_move", "TEU per move", "TEU/move", lo=1.0, hi=2.0),
            _n("call_overhead_hours", "Berthing overhead per call", "hours/call"),
            _share("berth_availability", "Berth availability"),
            _share("target_berth_occupancy", "Target berth occupancy"),
            _n("yard_ground_slots", "Yard ground slots", "TEU slots", lo=1.0),
            _n("stacking_height", "Stacking height", "tiers", lo=1.0, hi=8.0),
            _n("dwell_days", "Average dwell time", "days", lo=0.5),
            _share("target_yard_utilization", "Target yard utilization"),
            _share("truck_share", "Share moved by truck"),
            _n("teu_per_truck", "TEU per truck", "TEU/truck", lo=1.0),
            _n("gate_lanes", "Gate lanes", "lanes", lo=1.0),
            _n("trucks_per_lane_hour", "Gate lane rate", "trucks/lane/hour", lo=1.0),
            _n("gate_hours_per_day", "Gate opening hours", "hours/day", lo=1.0, hi=24.0),
            _n("hotelling_fuel_t_per_hour", "Vessel fuel at berth", "t/hour"),
            _share("shore_power_share", "Calls on shore power"),
            _n("shore_power_mw_per_vessel", "Shore power demand", "MW/vessel"),
            _n("handling_kwh_per_teu", "Handling energy", "kWh/TEU"),
            _n("fuel_emission_factor", "Fuel emission factor", "tCO2/t fuel"),
            _n("grid_emission_factor", "Grid emission factor", "tCO2/MWh"),
            _n("fixed_opex_usd", "Fixed operating cost", "USD/year"),
            _n("variable_opex_usd_per_teu", "Variable operating cost", "USD/TEU"),
            _n("electricity_price_usd_per_mwh", "Electricity price", "USD/MWh"),
            _n("capex_usd_this_year", "Capital spending this year", "USD/year"),
            _n("disruption_days", "Disruption days", "days/year", hi=200.0),
        )),
        DatasetDef("vessel_demand_output", "Vessel demand", (
            _n("vessel_calls", "Vessel calls", "calls/year"),)),
        DatasetDef("berth_operations_output", "Berth operations", (
            _n("service_time_h", "Service time per call", "hours/call"),
            _n("berth_utilization_pct", "Berth utilization", "%", hi=None, nullable=True),
            _n("waiting_time_h", "Waiting time per call", "hours/call", nullable=True),
            _n("turnaround_h", "Vessel turnaround", "hours/call", nullable=True),
            _n("berth_capacity_teu", "Berth capacity", "TEU/year"),
            FieldDef("berth_congested", "Berth congested", type="boolean"),
            FieldDef("berth_overloaded", "Berth overloaded", type="boolean"),
        )),
        DatasetDef("yard_gate_output", "Yard and gate", (
            _n("yard_capacity_teu", "Yard capacity", "TEU/year"),
            _n("yard_utilization_pct", "Yard utilization", "%", nullable=True),
            _n("gate_capacity_teu", "Gate capacity", "TEU/year"),
            _n("gate_utilization_pct", "Gate utilization", "%", nullable=True),
            _n("truck_trips", "Truck trips", "trucks/year"),
        )),
        DatasetDef("capacity_resilience_output", "Capacity and resilience", (
            _n("capacity_teu", "Terminal capacity", "TEU/year"),
            FieldDef("binding_constraint", "Binding constraint", type="string"),
            _n("handled_teu", "Handled throughput", "TEU/year"),
            _n("handled_calls", "Handled calls", "calls/year"),
            _n("unmet_teu", "Unmet demand", "TEU/year"),
            _n("capacity_headroom_pct", "Capacity headroom", "%", lo=None, nullable=True),
            _n("resilience_headroom_pct", "Headroom after disruption", "%", lo=None, nullable=True),
        )),
        DatasetDef("energy_emissions_output", "Energy and emissions", (
            _n("vessel_fuel_t", "Vessel fuel in port", "t/year"),
            _n("electricity_mwh", "Electricity", "MWh/year"),
            _n("co2_t", "CO2 emissions", "tCO2/year"),
            FieldDef("co2_understated", "Emissions understated", type="boolean"),
        )),
        DatasetDef("port_cost_output", "Port cost", (
            _n("opex_usd", "Operating cost", "USD/year"),
            _n("capex_usd", "Capital spending", "USD/year"),
            _n("total_cost_usd", "Total cost", "USD/year"),
            _n("cost_per_teu", "Cost per TEU", "USD/TEU", nullable=True),
        )),
        DatasetDef("planning_summary_output", "Planning summary", (
            _n("demand_teu", "Container demand", "TEU/year"),
            _n("capacity_teu", "Terminal capacity", "TEU/year"),
            _n("handled_teu", "Handled throughput", "TEU/year"),
            _n("unmet_teu", "Unmet demand", "TEU/year"),
            FieldDef("binding_constraint", "Binding constraint", type="string"),
            _n("capacity_headroom_pct", "Capacity headroom", "%", lo=None, nullable=True),
            _n("resilience_headroom_pct", "Headroom after disruption", "%", lo=None, nullable=True),
            _n("berth_utilization_pct", "Berth utilization", "%", nullable=True),
            _n("waiting_time_h", "Waiting time per call", "hours/call", nullable=True),
            _n("turnaround_h", "Vessel turnaround", "hours/call", nullable=True),
            _n("yard_utilization_pct", "Yard utilization", "%", nullable=True),
            _n("gate_utilization_pct", "Gate utilization", "%", nullable=True),
            _n("co2_t", "CO2 emissions", "tCO2/year"),
            _n("total_cost_usd", "Total cost", "USD/year"),
            _n("capex_usd", "Capital spending", "USD/year"),
            _n("cost_per_teu", "Cost per TEU", "USD/TEU", nullable=True),
        )),
    ),
    models=(
        ModelDef("demand_calls", "Vessel demand", "operational",
                 "Vessel calls implied by container demand.", port_planning.demand_calls,
                 ((_PLAN_IN, ("demand_teu", "average_call_size_teu")),), "vessel_demand_output"),
        ModelDef("berth_operations", "Berth and crane operations", "operational",
                 "Service time, utilization, queueing delay and capacity of the berths.",
                 port_planning.berth_operations,
                 ((_PLAN_IN, ("average_call_size_teu", "berths", "cranes_per_berth", "crane_moves_per_hour",
                              "teu_per_move", "call_overhead_hours", "berth_availability",
                              "target_berth_occupancy")),
                  ("vessel_demand_output", ("vessel_calls",))), "berth_operations_output"),
        ModelDef("yard_gate", "Yard and gate", "operational",
                 "Static yard capacity from dwell time, and gate throughput.", port_planning.yard_gate,
                 ((_PLAN_IN, ("demand_teu", "yard_ground_slots", "stacking_height", "dwell_days",
                              "target_yard_utilization", "truck_share", "teu_per_truck", "gate_lanes",
                              "trucks_per_lane_hour", "gate_hours_per_day")),), "yard_gate_output"),
        ModelDef("capacity_resilience", "Capacity and resilience", "resilience",
                 "Binding subsystem, handled and unmet demand, headroom with and without disruption.",
                 port_planning.capacity_resilience,
                 ((_PLAN_IN, ("demand_teu", "average_call_size_teu", "disruption_days", "berth_availability")),
                  ("berth_operations_output", ("berth_capacity_teu",)),
                  ("yard_gate_output", ("yard_capacity_teu", "gate_capacity_teu"))), "capacity_resilience_output"),
        ModelDef("energy_emissions", "Energy and emissions", "environmental",
                 "Vessel fuel in port, shore power and handling electricity, and CO₂.",
                 port_planning.energy_emissions,
                 ((_PLAN_IN, ("hotelling_fuel_t_per_hour", "shore_power_share", "shore_power_mw_per_vessel",
                              "handling_kwh_per_teu", "fuel_emission_factor", "grid_emission_factor")),
                  ("berth_operations_output", ("service_time_h", "waiting_time_h")),
                  ("capacity_resilience_output", ("handled_calls", "handled_teu"))), "energy_emissions_output"),
        ModelDef("port_cost", "Port cost", "economic",
                 "Operating cost, capital spending and cost per TEU.", port_planning.port_cost,
                 ((_PLAN_IN, ("fixed_opex_usd", "variable_opex_usd_per_teu", "electricity_price_usd_per_mwh",
                              "capex_usd_this_year")),
                  ("capacity_resilience_output", ("handled_teu",)),
                  ("energy_emissions_output", ("electricity_mwh",))), "port_cost_output"),
        ModelDef("planning_summary", "Planning summary", "decision",
                 "The period's decision indicators in one record.", port_planning.planning_summary,
                 ((_PLAN_IN, ("demand_teu",)),
                  ("berth_operations_output", ("berth_utilization_pct", "waiting_time_h", "turnaround_h")),
                  ("yard_gate_output", ("yard_utilization_pct", "gate_utilization_pct")),
                  ("capacity_resilience_output", ("capacity_teu", "binding_constraint", "handled_teu",
                                                  "unmet_teu", "capacity_headroom_pct", "resilience_headroom_pct")),
                  ("energy_emissions_output", ("co2_t",)),
                  ("port_cost_output", ("total_cost_usd", "capex_usd", "cost_per_teu"))), "planning_summary_output"),
    ),
    defaults={_PLAN_IN: {
        "demand_teu": 1_500_000.0, "average_call_size_teu": 1500.0, "berths": 4.0,
        "cranes_per_berth": 3.0, "crane_moves_per_hour": 28.0, "teu_per_move": 1.5,
        "call_overhead_hours": 4.0, "berth_availability": 0.95, "target_berth_occupancy": 0.65,
        "yard_ground_slots": 12_000.0, "stacking_height": 4.0, "dwell_days": 5.0,
        "target_yard_utilization": 0.8, "truck_share": 0.6, "teu_per_truck": 1.6,
        "gate_lanes": 10.0, "trucks_per_lane_hour": 30.0, "gate_hours_per_day": 20.0,
        # Consistent pair: a 2 MW hotel load at ~0.21 t fuel/MWh ≈ 0.42 t/h burned at berth, or
        # the same 2 MW drawn from shore power when plugged in.
        "hotelling_fuel_t_per_hour": 0.42, "shore_power_share": 0.0, "shore_power_mw_per_vessel": 2.0,
        "handling_kwh_per_teu": 15.0, "fuel_emission_factor": 3.114, "grid_emission_factor": 0.4,
        "fixed_opex_usd": 60_000_000.0, "variable_opex_usd_per_teu": 45.0,
        "electricity_price_usd_per_mwh": 120.0, "capex_usd_this_year": 0.0, "disruption_days": 10.0,
    }},
)


# ---------------------------------------------------------------------------
# Interconnected port city (cross-domain)
# ---------------------------------------------------------------------------

CITY = Pack(
    key="port_city_cross_domain",
    name="Interconnected port city",
    description="Natural access (weather, tide) → port operations → jobs and value added → "
                "city air quality → community exposure and traffic.",
    provider="Platform (internal)",
    provider_kind="internal",
    calibration="illustrative",
    provenance="Deliberately simple equations written for this demonstration (idealised tide, "
               "linear employment and emission factors). Synthetic parameters. Not validated science.",
    datasets=(
        DatasetDef("natural_inputs", "Natural conditions", (
            _n("storm_days_per_year", "Storm days", "days/year", hi=365.0),
            _share("storm_downtime_share", "Operating time lost per storm day"),
            _n("tidal_range_m", "Tidal range", "m"),
            _n("mean_channel_depth_m", "Mean channel depth", "m"),
            _n("design_draft_m", "Design vessel draft", "m"),
            _n("underkeel_clearance_m", "Underkeel clearance", "m"),
        )),
        DatasetDef("city_inputs", "Port and city inputs", (
            _n("city_demand_teu", "Container demand", "TEU/year"),
            _n("nominal_capacity_teu", "Nominal terminal capacity", "TEU/year"),
            _share("city_truck_share", "Share moved by truck"),
            _n("city_teu_per_truck", "TEU per truck", "TEU/truck", lo=1.0),
            _n("teu_per_direct_job", "TEU per direct job", "TEU/job", lo=1.0),
            _n("employment_multiplier", "Employment multiplier", "ratio", lo=1.0),
            _n("gva_usd_per_teu", "Value added per TEU", "USD/TEU"),
            _n("urban_km_per_trip", "Urban distance per truck trip", "km"),
            _n("nox_g_per_km", "Truck NOx factor", "g/km"),
            _n("pm25_g_per_km", "Truck PM2.5 factor", "g/km"),
        )),
        DatasetDef("social_inputs", "Community context", (
            _n("exposed_population", "Population near freight corridors", "people"),
            _n("urban_area_km2", "Corridor area", "km²", lo=0.1),
            _n("road_capacity_trucks_per_day", "Road capacity for trucks", "trucks/day", lo=1.0),
        )),
        DatasetDef("natural_access_output", "Natural access", (
            _share("weather_availability", "Weather availability"),
            _share("tidal_access_share", "Tidal access"),
            _share("access_availability", "Access availability"),
        )),
        DatasetDef("city_operations_output", "Port operations", (
            _n("effective_capacity_teu", "Effective capacity", "TEU/year"),
            _n("city_handled_teu", "Handled throughput", "TEU/year"),
            _n("city_unmet_teu", "Unmet demand", "TEU/year"),
            _n("truck_trips_per_day", "Truck trips", "trucks/day"),
        )),
        DatasetDef("city_economy_output", "Jobs and value added", (
            _n("direct_jobs", "Direct jobs", "jobs"),
            _n("total_jobs", "Total jobs", "jobs"),
            _n("gross_value_added_usd", "Gross value added", "USD/year"),
        )),
        DatasetDef("city_air_output", "City air quality", (
            _n("truck_vehicle_km", "Truck vehicle-km", "km/year"),
            _n("nox_t", "NOx emissions", "t/year"),
            _n("pm25_t", "PM2.5 emissions", "t/year"),
        )),
        DatasetDef("city_social_output", "Community", (
            _n("exposure_index", "Community exposure index", "index", nullable=True),
            _n("traffic_load_pct", "Road traffic load", "%", nullable=True),
        )),
        DatasetDef("city_summary_output", "Port city summary", (
            _share("access_availability", "Access availability"),
            _n("city_handled_teu", "Handled throughput", "TEU/year"),
            _n("city_unmet_teu", "Unmet demand", "TEU/year"),
            _n("total_jobs", "Total jobs", "jobs"),
            _n("gross_value_added_usd", "Gross value added", "USD/year"),
            _n("nox_t", "NOx emissions", "t/year"),
            _n("pm25_t", "PM2.5 emissions", "t/year"),
            _n("exposure_index", "Community exposure index", "index", nullable=True),
            _n("traffic_load_pct", "Road traffic load", "%", nullable=True),
        )),
    ),
    models=(
        ModelDef("natural_access", "Weather and tide access", "natural",
                 "Share of the year vessels can safely reach the berths.", port_city.natural_access,
                 (("natural_inputs", ("storm_days_per_year", "storm_downtime_share", "tidal_range_m",
                                      "mean_channel_depth_m", "design_draft_m", "underkeel_clearance_m")),),
                 "natural_access_output"),
        ModelDef("city_port_operations", "Port operations", "operational",
                 "Throughput the port can handle given physical access.", port_city.city_port_operations,
                 (("city_inputs", ("city_demand_teu", "nominal_capacity_teu", "city_truck_share", "city_teu_per_truck")),
                  ("natural_access_output", ("access_availability",))), "city_operations_output"),
        ModelDef("city_economy", "Jobs and value added", "economic",
                 "Employment and value added supported by handled throughput.", port_city.city_economy,
                 (("city_inputs", ("teu_per_direct_job", "employment_multiplier", "gva_usd_per_teu")),
                  ("city_operations_output", ("city_handled_teu",))), "city_economy_output"),
        ModelDef("city_air_quality", "City air quality", "environmental",
                 "Road-freight NOx and PM2.5 inside the city.", port_city.city_air_quality,
                 (("city_inputs", ("urban_km_per_trip", "nox_g_per_km", "pm25_g_per_km")),
                  ("city_operations_output", ("truck_trips_per_day",))), "city_air_output"),
        ModelDef("city_social", "Community exposure and traffic", "social",
                 "Relative exposure of nearby residents and road traffic load.", port_city.city_social,
                 (("social_inputs", ("exposed_population", "urban_area_km2", "road_capacity_trucks_per_day")),
                  ("city_air_output", ("pm25_t",)),
                  ("city_operations_output", ("truck_trips_per_day",))), "city_social_output"),
        ModelDef("city_summary", "Port city summary", "decision",
                 "The cross-domain indicators in one record.", port_city.city_summary,
                 (("natural_access_output", ("access_availability",)),
                  ("city_operations_output", ("city_handled_teu", "city_unmet_teu")),
                  ("city_economy_output", ("total_jobs", "gross_value_added_usd")),
                  ("city_air_output", ("nox_t", "pm25_t")),
                  ("city_social_output", ("exposure_index", "traffic_load_pct"))), "city_summary_output"),
    ),
    defaults={
        "natural_inputs": {"storm_days_per_year": 12.0, "storm_downtime_share": 0.6, "tidal_range_m": 3.0,
                           "mean_channel_depth_m": 16.0, "design_draft_m": 13.5, "underkeel_clearance_m": 1.0},
        "city_inputs": {"city_demand_teu": 1_500_000.0, "nominal_capacity_teu": 1_800_000.0,
                        "city_truck_share": 0.6, "city_teu_per_truck": 1.6, "teu_per_direct_job": 350.0,
                        "employment_multiplier": 2.1, "gva_usd_per_teu": 160.0, "urban_km_per_trip": 18.0,
                        "nox_g_per_km": 4.5, "pm25_g_per_km": 0.08},
        "social_inputs": {"exposed_population": 85_000.0, "urban_area_km2": 22.0,
                          "road_capacity_trucks_per_day": 4_000.0},
    },
)

# ---------------------------------------------------------------------------
# Hub network performance (installed in regional / national hubs)
# ---------------------------------------------------------------------------

NETWORK = Pack(
    key="hub_network_performance",
    name="Hub network performance",
    description="Aggregates only the outputs member ports have approved for this hub, "
                "with explicit coverage — nothing is imputed for a port that did not share.",
    provider="Platform (internal)",
    provider_kind="internal",
    calibration="illustrative",
    provenance="Sums, means and ratios over governed shared values. Inputs are written only by "
               "the governed-share materialization from approved outputs.",
    datasets=(
        DatasetDef("network_indicators", "Shared port indicators", (
            _n("total_annual_teu", "Throughput of reporting ports", "TEU/year"),
            _n("total_annual_emissions_tco2", "CO2 of reporting ports", "tCO2/year"),
            _n("teu_where_emissions_reported", "Throughput of ports reporting CO2", "TEU/year"),
            _n("mean_berth_utilization_pct", "Mean berth utilization", "%", nullable=True),
            _n("ports_in_scope", "Ports in this hub", "ports"),
            _n("ports_reporting_teu", "Ports sharing throughput", "ports"),
            _n("ports_reporting_emissions", "Ports sharing CO2", "ports"),
        )),
        DatasetDef("network_performance_output", "Network performance", (
            _n("emissions_intensity_tco2_per_teu", "CO2 intensity", "tCO2/TEU", nullable=True),
            _n("teu_coverage_pct", "Throughput coverage", "%", nullable=True),
            _n("emissions_coverage_pct", "CO2 coverage", "%", nullable=True),
            _n("network_throughput_teu", "Shared throughput", "TEU/year"),
            _n("mean_berth_utilization_pct", "Mean berth utilization", "%", nullable=True),
        )),
    ),
    models=(
        ModelDef("network_performance", "Hub network performance", "decision",
                 "Network indicators computed only from governed shared values.",
                 port_city.network_performance,
                 (("network_indicators", ("total_annual_teu", "total_annual_emissions_tco2",
                                          "teu_where_emissions_reported", "mean_berth_utilization_pct",
                                          "ports_in_scope", "ports_reporting_teu",
                                          "ports_reporting_emissions")),),
                 "network_performance_output"),
    ),
    defaults={},
)

LIVE = Pack(
    key="live_operations",
    name="Live operations pulse",
    description="Turns a telemetry window (berth occupancy, arrivals, crane productivity) into a "
                "productivity gap and a congestion alert.",
    provider="Platform (internal)",
    provider_kind="internal",
    calibration="illustrative",
    provenance="Simple ratios over telemetry window aggregates. In the demo the telemetry is "
               "produced by a labelled simulator, not real sensors.",
    datasets=(
        DatasetDef("live_operations_inputs", "Live operations readings", (
            _n("berth_occupancy_pct", "Berth occupancy (window)", "%", hi=100.0),
            _n("vessel_arrivals_per_hour", "Vessel arrivals (window)", "calls/hour"),
            _n("observed_crane_moves_per_hour", "Observed crane productivity", "moves/hour"),
            _n("planned_crane_moves_per_hour", "Planned crane productivity", "moves/hour", lo=1.0),
            _n("congestion_alert_pct", "Congestion alert threshold", "%", hi=100.0),
        )),
        DatasetDef("live_operations_output", "Operations pulse", (
            _n("live_berth_occupancy_pct", "Berth occupancy", "%", hi=100.0),
            _n("live_arrivals_per_hour", "Vessel arrivals", "calls/hour"),
            _n("productivity_gap_pct", "Productivity gap", "%", lo=None, nullable=True),
            FieldDef("congestion_alert", "Congestion alert", type="boolean"),
        )),
    ),
    models=(
        ModelDef("operations_pulse", "Operations pulse", "operational",
                 "Productivity gap and congestion alert from live readings.",
                 port_planning.operations_pulse,
                 (("live_operations_inputs", ("berth_occupancy_pct", "vessel_arrivals_per_hour",
                                              "observed_crane_moves_per_hour", "planned_crane_moves_per_hour",
                                              "congestion_alert_pct")),),
                 "live_operations_output"),
    ),
    defaults={"live_operations_inputs": {
        "berth_occupancy_pct": 55.0, "vessel_arrivals_per_hour": 0.12,
        "observed_crane_moves_per_hour": 27.0, "planned_crane_moves_per_hour": 28.0,
        "congestion_alert_pct": 75.0,
    }},
)

PACKS: dict[str, Pack] = {p.key: p for p in (PLANNING, CITY, NETWORK, LIVE)}


# ---------------------------------------------------------------------------
# Execution (used by the adapter and by the in-memory evaluator)
# ---------------------------------------------------------------------------

def resolve(pack_key: str, model_key: str) -> ModelDef:
    pack = PACKS.get(pack_key)
    if pack is None:
        raise KeyError(f"Unknown model pack {pack_key!r}; known: {sorted(PACKS)}")
    for m in pack.models:
        if m.key == model_key:
            return m
    raise KeyError(f"Pack {pack_key!r} has no model {model_key!r}.")


def compute(model: ModelDef, values: dict[str, Any]) -> dict[str, Any]:
    params = inspect.signature(model.fn).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return model.fn(**values)
    missing = [n for n, p in params.items() if p.default is inspect.Parameter.empty and n not in values]
    if missing:
        raise KeyError(f"model {model.key!r} is missing required input(s): {missing}")
    return model.fn(**{k: values[k] for k in params if k in values})


def evaluate_in_memory(pack: Pack, sources: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Run every model of the pack in declared (dependency) order without the database.

    Same functions, same field selection as the installed federation — used by the optimizer
    to explore many candidates quickly; its chosen candidates are then re-run through the real
    engine and the two results are compared.
    """
    records: dict[str, dict[str, Any]] = {k: dict(v) for k, v in sources.items()}
    for m in pack.models:
        args: dict[str, Any] = {}
        for ds, fields in m.reads:
            for f in fields:
                args[f] = records[ds][f]
        records[m.writes] = compute(m, args)
    return records


# ---------------------------------------------------------------------------
# Installation into the current organization
# ---------------------------------------------------------------------------

def model_card(pack: Pack, m: ModelDef) -> dict[str, Any]:
    return {
        "purpose": m.purpose, "formula": m.formula, "domain": m.domain,
        "provider": pack.provider, "provider_kind": pack.provider_kind,
        "pack": pack.key, "pack_name": pack.name,
        "execution_method": "In-process Python function from a reviewed model pack (model_pack adapter)",
        "calibration": pack.calibration, "provenance": pack.provenance,
        "governance_status": "reviewed",
        "inputs": [{"dataset": ds, "fields": list(fields)} for ds, fields in m.reads],
        "outputs": [{"dataset": m.writes}],
        "disclaimer": "Illustrative/uncalibrated model with synthetic parameters — not a forecast.",
    }


def installed(db: Session, pack_key: str) -> bool:
    from backend.app.persistence.database import Model
    pack = PACKS[pack_key]
    return db.execute(select(Model).where(Model.name == pack.terminal.name)).scalars().first() is not None


def install_pack(db: Session, pack_key: str, *, owner: str,
                 initial: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Idempotently install `pack_key` into the session's current organization."""
    from backend.app.persistence.database import Dataset
    from backend.app.persistence.exceptions import NotFoundError
    from backend.app.persistence.model_version_repository import ModelVersionRepository
    from backend.app.services.data_contract_manager import DataContractManager
    from backend.app.services.dataset_values import DatasetValueService
    from backend.app.services.federation import FederationService
    from backend.app.services.model_registry import ModelNotFoundError, ModelRegistry

    pack = PACKS[pack_key]
    registry = ModelRegistry(db)
    fed = FederationService(db)
    contracts = DataContractManager(db)
    values = DatasetValueService(db)

    datasets: dict[str, Dataset] = {}
    for d in pack.datasets:
        ds = db.execute(select(Dataset).where(Dataset.name == d.name)).scalars().first()
        if ds is None:
            ds = Dataset(name=d.name, display_name=d.label, description=f"{pack.name} — {d.label}")
            db.add(ds)
            db.flush()
        if contracts.get_active_contract(ds.id) is None:
            contracts.register_contract(
                dataset_id=ds.id, semver="1.0.0",
                schema_json=json.dumps({"fields": [f.contract() for f in d.fields], "additional_fields": False}),
            )
        datasets[d.name] = ds

    versions: dict[str, Any] = {}
    producer_of = {m.writes: m.key for m in pack.models}
    for m in pack.models:
        try:
            model = registry.get_model_by_name(name=m.name, owner=owner)
        except ModelNotFoundError:
            model = registry.register_model(name=m.name, owner=owner, model_type="model_pack",
                                            description=m.purpose, status="active")
        model.card_json = json.dumps(model_card(pack, m))
        repo = ModelVersionRepository(db)
        try:
            version = repo.get_by_model_and_semver(model.id, "1.0.0")
        except NotFoundError:
            version = registry.register_model_version(
                model_id=model.id, semver="1.0.0",
                inputs_spec=json.dumps([{"name": f, "dataset": ds} for ds, fs in m.reads for f in fs]),
                outputs_spec=json.dumps([{"dataset": m.writes}]),
                adapter_type="model_pack", adapter_config={"pack": pack.key, "model": m.key},
            )
        if not version.is_active:
            registry.activate_version(version.id)
        versions[m.key] = version
    db.flush()

    for m in pack.models:
        v = versions[m.key]
        for ds_name, fields in m.reads:
            fmap = {f: f for f in fields}
            if ds_name in producer_of:
                fed.connect(producer_version_id=versions[producer_of[ds_name]].id,
                            dataset_id=datasets[ds_name].id, consumer_version_id=v.id,
                            input_field_map=fmap)
            else:
                fed.ensure_binding(v.id, datasets[ds_name].id, "input", fmap)
        fed.ensure_binding(v.id, datasets[m.writes].id, "output")

    start = {**pack.defaults, **(initial or {})}
    for name in pack.source_datasets:
        ds = datasets[name]
        if ds.current_value is None and name in start:
            values.write_external(ds.id, dict(start[name]), source_type="seed",
                                  source_ref=f"pack:{pack.key}", triggered_by="pack-install")
    db.flush()
    return {
        "pack": pack.key,
        "terminal_version_id": versions[pack.terminal.key].id,
        "versions": {k: v.id for k, v in versions.items()},
        "datasets": {k: d.id for k, d in datasets.items()},
        "source_datasets": {k: datasets[k].id for k in pack.source_datasets},
    }
