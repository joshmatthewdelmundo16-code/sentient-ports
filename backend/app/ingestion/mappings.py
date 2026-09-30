"""Named source mappings — D18.

Mappings are explicit, code-defined data (never hard-coded cell addresses inside logic and,
per the approved scope, not a database table). Each cell mapping declares:
  worksheet · cell · target dataset (by name) · target field · optional expected source type.

Cells targeting the same dataset are assembled into one record before it is written through
the D16 dataset/contract/change-detection path.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.ui.port_seed import ASSUMPTIONS_DATASET


@dataclass(frozen=True)
class CellMapping:
    worksheet: str
    cell: str
    target_dataset: str          # dataset name (resolved to id at ingest time)
    target_field: str
    expected_type: str = "number"  # "number" | "string" | "boolean"


@dataclass(frozen=True)
class SourceMapping:
    key: str
    description: str
    cells: tuple[CellMapping, ...]

    def datasets(self) -> list[str]:
        seen: list[str] = []
        for c in self.cells:
            if c.target_dataset not in seen:
                seen.append(c.target_dataset)
        return seen


# The first acceptance mapping: seven B-column cells → the seven D17 port assumptions.
# The workbook uses D18 spec field labels on the left; target_field is the D17 contract
# field name, so the mapping bridges the two naming conventions.
PORT_ASSUMPTIONS_V1 = SourceMapping(
    key="port_assumptions_v1",
    description="Port assumptions workbook (Assumptions sheet, cells B2–B8)",
    cells=(
        CellMapping("Assumptions", "B2", ASSUMPTIONS_DATASET, "bunker_price"),
        CellMapping("Assumptions", "B3", ASSUMPTIONS_DATASET, "annual_vessel_calls"),
        CellMapping("Assumptions", "B4", ASSUMPTIONS_DATASET, "average_teu_per_call"),
        CellMapping("Assumptions", "B5", ASSUMPTIONS_DATASET, "average_berth_hours_per_call"),
        CellMapping("Assumptions", "B6", ASSUMPTIONS_DATASET, "number_of_berths"),
        CellMapping("Assumptions", "B7", ASSUMPTIONS_DATASET, "fuel_burned_in_port_per_call"),
        CellMapping("Assumptions", "B8", ASSUMPTIONS_DATASET, "emission_factor"),
    ),
)

_MAPPINGS: dict[str, SourceMapping] = {m.key: m for m in (PORT_ASSUMPTIONS_V1,)}


def get_mapping(key: str) -> SourceMapping | None:
    return _MAPPINGS.get(key)


def list_mappings() -> list[SourceMapping]:
    return list(_MAPPINGS.values())
