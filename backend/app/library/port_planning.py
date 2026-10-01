"""Port capacity planning pack — D27.

Parameterised, per-period port planning models built from standard textbook relations
(berth occupancy and M/M/c queueing as used in port planning guides; static yard capacity from
ground slots, stacking height and dwell; gate throughput from lanes and lane rate). They are
ILLUSTRATIVE and UNCALIBRATED: the structure is defensible, the parameters are synthetic, and
none of the outputs should be read as a forecast for a real port.

Known simplifications, stated so nobody mistakes this for more than it is:
  * M/M/c assumes Poisson arrivals and exponential service; berth service is usually less
    variable (M/Ek/c), so waiting times here are pessimistic.
  * Waiting at anchorage is unbounded when utilization reaches 100%: the model returns None for
    waiting and turnaround and flags `overloaded` rather than inventing a number.
  * Yard and gate are steady-state annual averages with no peaking factor.

Every function is pure: named floats in, named outputs out. Output names are unique across the
pack so an in-memory evaluator (used by the optimizer) and the federation engine (used for
plans) compute exactly the same thing from the same functions.
"""

from __future__ import annotations

import math
from typing import Any

HOURS_PER_YEAR = 8760.0


def erlang_c(servers: int, offered_load: float) -> float:
    """Probability an arrival waits in an M/M/c queue (requires offered_load < servers)."""
    term = 1.0
    total = 1.0
    for k in range(1, servers):
        term *= offered_load / k
        total += term
    term_c = term * offered_load / servers                 # a^c / c!
    p_wait = term_c / (1.0 - offered_load / servers)
    return p_wait / (total + p_wait)


def demand_calls(*, demand_teu: float, average_call_size_teu: float) -> dict[str, Any]:
    """vessel_calls = demand_teu / average_call_size_teu"""
    calls = demand_teu / average_call_size_teu if average_call_size_teu > 0 else 0.0
    return {"vessel_calls": calls}


def berth_operations(*, vessel_calls: float, average_call_size_teu: float, berths: float,
                     cranes_per_berth: float, crane_moves_per_hour: float, teu_per_move: float,
                     call_overhead_hours: float, berth_availability: float,
                     target_berth_occupancy: float) -> dict[str, Any]:
    """
    handling_rate   = cranes_per_berth × crane_moves_per_hour × teu_per_move      (TEU/h)
    service_time_h  = average_call_size_teu / handling_rate + call_overhead_hours
    λ = vessel_calls / (8760 × berth_availability);  μ = 1 / service_time_h;  c = ⌊berths⌋
    utilization ρ   = λ / (c μ);  waiting W_q = C(c, λ/μ) / (c μ − λ)   (Erlang C)
    berth_capacity  = c × 8760 × availability × target_occupancy / service_time × call size
    """
    handling_rate = cranes_per_berth * crane_moves_per_hour * teu_per_move
    service_h = (average_call_size_teu / handling_rate if handling_rate > 0 else math.inf) + call_overhead_hours
    servers = max(1, int(math.floor(berths)))
    available_h = HOURS_PER_YEAR * berth_availability
    lam = vessel_calls / available_h if available_h > 0 else math.inf
    mu = 1.0 / service_h if service_h > 0 else 0.0
    rho = lam / (servers * mu) if mu > 0 else math.inf
    overloaded = rho >= 1.0
    if overloaded or not math.isfinite(rho):
        waiting_h = None
        turnaround_h = None
    else:
        waiting_h = erlang_c(servers, lam / mu) / (servers * mu - lam)
        turnaround_h = waiting_h + service_h
    capacity_calls = servers * available_h * target_berth_occupancy / service_h if service_h > 0 else 0.0
    return {
        "service_time_h": service_h,
        "berth_utilization_pct": rho * 100.0 if math.isfinite(rho) else None,
        "waiting_time_h": waiting_h,
        "turnaround_h": turnaround_h,
        "berth_capacity_teu": capacity_calls * average_call_size_teu,
        "berth_congested": bool(rho >= target_berth_occupancy),
        "berth_overloaded": bool(overloaded),
    }


def yard_gate(*, demand_teu: float, yard_ground_slots: float, stacking_height: float,
              dwell_days: float, target_yard_utilization: float, truck_share: float,
              teu_per_truck: float, gate_lanes: float, trucks_per_lane_hour: float,
              gate_hours_per_day: float) -> dict[str, Any]:
    """
    yard static capacity   = ground slots × stacking height                        (TEU)
    yard_capacity_teu      = static × target utilization × 365 / dwell_days       (TEU/year)
    yard_utilization_pct   = demand × dwell / 365 / static × 100
    truck trips            = demand × truck_share / teu_per_truck
    gate capacity (trucks) = lanes × trucks per lane-hour × gate hours × 365
    """
    static = yard_ground_slots * stacking_height
    yard_capacity = static * target_yard_utilization * 365.0 / dwell_days if dwell_days > 0 else 0.0
    yard_util = (demand_teu * dwell_days / 365.0) / static * 100.0 if static > 0 else None
    trucks = demand_teu * truck_share / teu_per_truck if teu_per_truck > 0 else 0.0
    gate_trucks = gate_lanes * trucks_per_lane_hour * gate_hours_per_day * 365.0
    gate_capacity = gate_trucks * teu_per_truck / truck_share if truck_share > 0 else gate_trucks * teu_per_truck * 1e6
    return {
        "yard_capacity_teu": yard_capacity,
        "yard_utilization_pct": yard_util,
        "gate_capacity_teu": gate_capacity,
        "gate_utilization_pct": trucks / gate_trucks * 100.0 if gate_trucks > 0 else None,
        "truck_trips": trucks,
    }


def capacity_resilience(*, demand_teu: float, berth_capacity_teu: float, yard_capacity_teu: float,
                        gate_capacity_teu: float, average_call_size_teu: float,
                        disruption_days: float, berth_availability: float) -> dict[str, Any]:
    """
    capacity            = min(berth, yard, gate) — the binding subsystem is reported
    handled_teu         = min(demand, capacity);  unmet_teu = max(0, demand − capacity)
    capacity_headroom   = (capacity − demand) / capacity × 100
    resilience_headroom = same, after losing disruption_days of berth operation
    """
    caps = {"berth": berth_capacity_teu, "yard": yard_capacity_teu, "gate": gate_capacity_teu}
    binding = min(caps, key=lambda k: caps[k])
    capacity = caps[binding]
    handled = min(demand_teu, capacity)
    operating_days = 365.0 * berth_availability
    disrupted = capacity * max(0.0, 1.0 - disruption_days / operating_days) if operating_days > 0 else 0.0
    return {
        "capacity_teu": capacity,
        "binding_constraint": binding,
        "handled_teu": handled,
        "handled_calls": handled / average_call_size_teu if average_call_size_teu > 0 else 0.0,
        "unmet_teu": max(0.0, demand_teu - capacity),
        "capacity_headroom_pct": (capacity - demand_teu) / capacity * 100.0 if capacity > 0 else None,
        "resilience_headroom_pct": (disrupted - demand_teu) / disrupted * 100.0 if disrupted > 0 else None,
    }


def energy_emissions(*, handled_calls: float, handled_teu: float, service_time_h: float,
                     waiting_time_h: float | None, hotelling_fuel_t_per_hour: float,
                     shore_power_share: float, shore_power_mw_per_vessel: float,
                     handling_kwh_per_teu: float, fuel_emission_factor: float,
                     grid_emission_factor: float) -> dict[str, Any]:
    """
    berth hours       = handled_calls × service_time_h;  anchorage hours = calls × waiting
    vessel fuel (t)   = (berth hours × (1 − shore share) + anchorage hours) × hotelling rate
    electricity (MWh) = berth hours × shore share × shore MW + handled TEU × kWh/TEU / 1000
    CO₂ (t)           = fuel × fuel EF + electricity × grid EF
    """
    berth_hours = handled_calls * service_time_h
    anchorage_hours = handled_calls * (waiting_time_h or 0.0)
    fuel = (berth_hours * (1.0 - shore_power_share) + anchorage_hours) * hotelling_fuel_t_per_hour
    electricity = berth_hours * shore_power_share * shore_power_mw_per_vessel + handled_teu * handling_kwh_per_teu / 1000.0
    return {
        "vessel_fuel_t": fuel,
        "electricity_mwh": electricity,
        "co2_t": fuel * fuel_emission_factor + electricity * grid_emission_factor,
        # When the berth is overloaded, waiting is unbounded and was counted as zero here.
        "co2_understated": waiting_time_h is None,
    }


def port_cost(*, handled_teu: float, fixed_opex_usd: float, variable_opex_usd_per_teu: float,
              electricity_mwh: float, electricity_price_usd_per_mwh: float,
              capex_usd_this_year: float) -> dict[str, Any]:
    """opex = fixed + variable × handled + electricity × price;  total = opex + capex"""
    opex = fixed_opex_usd + variable_opex_usd_per_teu * handled_teu + electricity_mwh * electricity_price_usd_per_mwh
    total = opex + capex_usd_this_year
    return {
        "opex_usd": opex,
        "capex_usd": capex_usd_this_year,
        "total_cost_usd": total,
        "cost_per_teu": total / handled_teu if handled_teu > 0 else None,
    }


def planning_summary(**kpis: Any) -> dict[str, Any]:
    """Fan-in: the period's decision KPIs in one record."""
    return {k: kpis.get(k) for k in SUMMARY_FIELDS}


SUMMARY_FIELDS = (
    "demand_teu", "capacity_teu", "handled_teu", "unmet_teu", "binding_constraint",
    "capacity_headroom_pct", "resilience_headroom_pct", "berth_utilization_pct",
    "waiting_time_h", "turnaround_h", "yard_utilization_pct", "gate_utilization_pct",
    "co2_t", "total_cost_usd", "capex_usd", "cost_per_teu",
)


def operations_pulse(*, berth_occupancy_pct: float, vessel_arrivals_per_hour: float,
                     observed_crane_moves_per_hour: float, planned_crane_moves_per_hour: float,
                     congestion_alert_pct: float) -> dict[str, Any]:
    """
    productivity_gap_pct = (planned − observed crane moves) / planned × 100
    congestion_alert     = berth occupancy ≥ alert threshold
    Fed by the telemetry connector's window aggregate (or the labelled simulator).
    """
    gap = ((planned_crane_moves_per_hour - observed_crane_moves_per_hour) / planned_crane_moves_per_hour * 100.0
           if planned_crane_moves_per_hour > 0 else None)
    return {
        "live_berth_occupancy_pct": berth_occupancy_pct,
        "live_arrivals_per_hour": vessel_arrivals_per_hour,
        "productivity_gap_pct": gap,
        "congestion_alert": bool(berth_occupancy_pct >= congestion_alert_pct),
    }
