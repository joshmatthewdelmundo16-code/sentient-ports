"""Interconnected port city pack — D27 (cross-domain demonstration).

The smallest federation that spans five domains so the architecture can be shown end to end:

  natural        weather downtime and tidal access (idealised sinusoidal tide)
  operational    port throughput limited by physical access
  economic       direct and indirect jobs, gross value added
  environmental  road-freight NOₓ and PM2.5 inside the city
  social         community exposure and road traffic load

Every equation is deliberately simple and stated in full; every parameter is synthetic. These
are ILLUSTRATIVE models that demonstrate federation across domains — they are not validated
environmental, economic or social science and must not be used for real decisions.
"""

from __future__ import annotations

import math
from typing import Any


def natural_access(*, storm_days_per_year: float, storm_downtime_share: float, tidal_range_m: float,
                   mean_channel_depth_m: float, design_draft_m: float,
                   underkeel_clearance_m: float) -> dict[str, Any]:
    """
    weather_availability = 1 − storm_days × downtime_share / 365
    tide: depth(t) = mean + (range/2)·sin(ωt); a vessel needs draft + underkeel clearance.
    tidal_access_share  = share of the cycle with enough depth
                        = 0.5 − asin((required − mean) / amplitude) / π   (clipped to 0…1)
    access_availability = weather_availability × tidal_access_share
    """
    weather = max(0.0, 1.0 - storm_days_per_year * storm_downtime_share / 365.0)
    amplitude = tidal_range_m / 2.0
    required = design_draft_m + underkeel_clearance_m
    if amplitude <= 0:
        tidal = 1.0 if mean_channel_depth_m >= required else 0.0
    elif required <= mean_channel_depth_m - amplitude:
        tidal = 1.0
    elif required >= mean_channel_depth_m + amplitude:
        tidal = 0.0
    else:
        tidal = 0.5 - math.asin((required - mean_channel_depth_m) / amplitude) / math.pi
    return {
        "weather_availability": weather,
        "tidal_access_share": tidal,
        "access_availability": weather * tidal,
    }


def city_port_operations(*, city_demand_teu: float, nominal_capacity_teu: float,
                         access_availability: float, city_truck_share: float,
                         city_teu_per_truck: float) -> dict[str, Any]:
    """
    effective_capacity = nominal_capacity × access_availability
    handled            = min(demand, effective_capacity)
    truck trips / day  = handled × truck_share / TEU per truck / 365
    (Treats tide- and weather-restricted time as lost capacity — a pessimistic bound; real
    operations schedule some calls around tidal windows.)
    """
    effective = nominal_capacity_teu * access_availability
    handled = min(city_demand_teu, effective)
    trips = handled * city_truck_share / city_teu_per_truck / 365.0 if city_teu_per_truck > 0 else 0.0
    return {
        "effective_capacity_teu": effective,
        "city_handled_teu": handled,
        "city_unmet_teu": max(0.0, city_demand_teu - effective),
        "truck_trips_per_day": trips,
    }


def city_economy(*, city_handled_teu: float, teu_per_direct_job: float, employment_multiplier: float,
                 gva_usd_per_teu: float) -> dict[str, Any]:
    """direct jobs = handled / TEU per job;  total = direct × multiplier;  GVA = handled × GVA/TEU"""
    direct = city_handled_teu / teu_per_direct_job if teu_per_direct_job > 0 else 0.0
    return {
        "direct_jobs": direct,
        "total_jobs": direct * employment_multiplier,
        "gross_value_added_usd": city_handled_teu * gva_usd_per_teu,
    }


def city_air_quality(*, truck_trips_per_day: float, urban_km_per_trip: float, nox_g_per_km: float,
                     pm25_g_per_km: float) -> dict[str, Any]:
    """vehicle-km/year = trips/day × 365 × km;  pollutant (t) = vehicle-km × g/km / 10⁶"""
    vkm = truck_trips_per_day * 365.0 * urban_km_per_trip
    return {
        "truck_vehicle_km": vkm,
        "nox_t": vkm * nox_g_per_km / 1e6,
        "pm25_t": vkm * pm25_g_per_km / 1e6,
    }


def city_social(*, pm25_t: float, exposed_population: float, urban_area_km2: float,
                truck_trips_per_day: float, road_capacity_trucks_per_day: float) -> dict[str, Any]:
    """
    exposure_index    = PM2.5 (t) × exposed population / urban area (km²) / 1000   (relative index)
    traffic_load_pct  = truck trips per day / road capacity for trucks × 100
    """
    exposure = pm25_t * exposed_population / urban_area_km2 / 1000.0 if urban_area_km2 > 0 else None
    return {
        "exposure_index": exposure,
        "traffic_load_pct": truck_trips_per_day / road_capacity_trucks_per_day * 100.0
        if road_capacity_trucks_per_day > 0 else None,
    }


def city_summary(**kpis: Any) -> dict[str, Any]:
    return {k: kpis.get(k) for k in CITY_SUMMARY_FIELDS}


CITY_SUMMARY_FIELDS = (
    "access_availability", "city_handled_teu", "city_unmet_teu", "total_jobs",
    "gross_value_added_usd", "nox_t", "pm25_t", "exposure_index", "traffic_load_pct",
)


# ---------------------------------------------------------------------------
# Hub performance (used by regional / national hubs over APPROVED outputs only)
# ---------------------------------------------------------------------------

def network_performance(*, total_annual_teu: float, total_annual_emissions_tco2: float,
                        teu_where_emissions_reported: float, mean_berth_utilization_pct: float | None,
                        ports_in_scope: float, ports_reporting_teu: float,
                        ports_reporting_emissions: float) -> dict[str, Any]:
    """
    emissions intensity = Σ emissions / Σ TEU, over ports that shared BOTH figures only
    coverage            = ports that shared the figure / ports in the hub × 100
    Nothing is imputed for a port that did not share: it is counted as missing.
    """
    intensity = (total_annual_emissions_tco2 / teu_where_emissions_reported
                 if teu_where_emissions_reported > 0 else None)
    return {
        "emissions_intensity_tco2_per_teu": intensity,
        "teu_coverage_pct": ports_reporting_teu / ports_in_scope * 100.0 if ports_in_scope > 0 else None,
        "emissions_coverage_pct": ports_reporting_emissions / ports_in_scope * 100.0 if ports_in_scope > 0 else None,
        "network_throughput_teu": total_annual_teu,
        "mean_berth_utilization_pct": mean_berth_utilization_pct,
    }
