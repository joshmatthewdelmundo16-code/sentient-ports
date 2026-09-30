"""Human-readable labels derived from platform identifiers (D25).

Resolution order for a dataset field:
  1. the contract field's explicit ``label`` (authored with the contract), then
  2. a deterministic humanisation of the field identifier.

Resolution order for a dataset:
  1. ``Dataset.display_name``, then
  2. a humanisation of ``Dataset.name``.

Humanisation is sentence case, keeps acronyms and units upper-case (TEU, CO₂, USD), and
drops trailing unit tokens that the contract's ``unit`` already carries — so
``annual_emissions_tco2`` reads "Annual emissions" beside its unit "tCO₂/year" rather than
"Annual emissions tco2". It never invents meaning: it only re-spells the identifier.
"""

from __future__ import annotations

import re

# Tokens re-spelled when they appear inside an identifier.
_ACRONYMS: dict[str, str] = {
    "teu": "TEU", "co2": "CO₂", "tco2": "tCO₂", "usd": "USD", "nox": "NOₓ", "pm": "PM",
    "pm25": "PM2.5", "capex": "CAPEX", "opex": "OPEX", "id": "ID", "kpi": "KPI",
    "npv": "NPV", "gdp": "GDP", "lng": "LNG", "kwh": "kWh", "mwh": "MWh", "gwh": "GWh",
}
# Trailing tokens that only restate the unit.
_UNIT_SUFFIXES = {"usd", "tco2", "pct", "t", "kwh", "mwh", "gwh", "h", "hours", "tonnes"}
# Trailing structural tokens with no meaning for a reader.
_STRUCTURAL_SUFFIXES = {"output", "outputs"}

_SPLIT = re.compile(r"[_\-\s]+|(?<=[a-z0-9])(?=[A-Z])")


def humanize(identifier: str | None, *, drop_unit_suffix: bool = True,
             drop_structural_suffix: bool = False) -> str:
    """``annual_fuel_cost_usd`` → ``Annual fuel cost``; ``fuel_cost_per_teu`` → ``Fuel cost per TEU``."""
    if not identifier:
        return ""
    tokens = [t for t in _SPLIT.split(identifier.strip()) if t]
    if not tokens:
        return identifier
    lowered = [t.lower() for t in tokens]
    while drop_unit_suffix and len(lowered) > 1 and lowered[-1] in _UNIT_SUFFIXES:
        lowered.pop()
    while drop_structural_suffix and len(lowered) > 1 and lowered[-1] in _STRUCTURAL_SUFFIXES:
        lowered.pop()
    words = [_ACRONYMS.get(t, t) for t in lowered]
    first = words[0]
    if first == first.lower():
        words[0] = first[:1].upper() + first[1:]
    return " ".join(words)


def display_unit(unit: str | None) -> str | None:
    """Typographic unit for display: ``tCO2/year`` → ``tCO₂/year``. None stays None."""
    if not unit:
        return unit
    return unit.replace("CO2", "CO₂").replace("co2", "CO₂")


def dataset_label(name: str, display_name: str | None = None) -> str:
    if display_name and display_name.strip():
        return display_name.strip()
    return humanize(name, drop_unit_suffix=False, drop_structural_suffix=False)


def field_label(field_name: str, contract_label: str | None = None) -> str:
    if contract_label and contract_label.strip():
        return contract_label.strip()
    return humanize(field_name)
