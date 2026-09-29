"""Scenario ↔ Baseline comparison — D19.

Deterministic comparison of two EXACT GraphRuns (baseline run id, scenario run id). Reads
the persisted Results of each run (never re-executes, never bypasses the result system) and
produces per-metric deltas keyed by (dataset_id, field).

Numeric metrics expose baseline, scenario, absolute delta, relative delta (only where
mathematically valid), unit, and direction/sign. Boolean/other metrics expose baseline,
scenario, and a neutral changed flag. There is intentionally no better/worse, winner, score,
ranking, recommendation, or optimization — only descriptive differences.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from backend.app.services.data_contract_manager import DataContractManager
from backend.app.services.results_lineage import ResultsLineageService


class ComparisonRunNotFoundError(Exception):
    """A referenced run produced no results (unknown or failed run)."""


@dataclass
class MetricDelta:
    dataset_id: str
    field: str
    kind: str                       # "numeric" | "boolean" | "other"
    baseline: Any
    scenario: Any
    unit: str | None = None
    absolute_delta: float | None = None
    relative_delta: float | None = None   # None when undefined (e.g. baseline == 0)
    baseline_zero: bool = False
    direction: str | None = None    # "increase" | "decrease" | "none" (numeric only)
    changed: bool = False


@dataclass
class ComparisonResult:
    baseline_run_id: str
    scenario_run_id: str
    metrics: list[MetricDelta] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline_run_id": self.baseline_run_id,
            "scenario_run_id": self.scenario_run_id,
            "metrics": [asdict(m) for m in self.metrics],
        }


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class ScenarioComparisonService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._results = ResultsLineageService(db)
        self._contracts = DataContractManager(db)
        self._unit_cache: dict[str, dict[str, str | None]] = {}

    # ------------------------------------------------------------------

    def _values_by_field(self, run_id: str) -> dict[tuple[str, str], Any]:
        """Flatten a run's Results into {(dataset_id, field): value}."""
        out: dict[tuple[str, str], Any] = {}
        for r in self._results.list_results_for_run(run_id):
            record = json.loads(r.value_json)
            if isinstance(record, dict):
                for fname, val in record.items():
                    out[(r.dataset_id, fname)] = val
        return out

    def _units_for(self, dataset_id: str) -> dict[str, str | None]:
        if dataset_id not in self._unit_cache:
            schema = self._contracts.get_active_schema(dataset_id)
            units: dict[str, str | None] = {}
            if schema is not None:
                for fld in schema.fields:
                    units[fld.name] = getattr(fld, "unit", None)
            self._unit_cache[dataset_id] = units
        return self._unit_cache[dataset_id]

    # ------------------------------------------------------------------

    def compare(self, baseline_run_id: str, scenario_run_id: str) -> ComparisonResult:
        baseline_vals = self._values_by_field(baseline_run_id)
        scenario_vals = self._values_by_field(scenario_run_id)
        if not baseline_vals:
            raise ComparisonRunNotFoundError(
                f"Baseline run {baseline_run_id!r} has no results to compare."
            )
        if not scenario_vals:
            raise ComparisonRunNotFoundError(
                f"Scenario run {scenario_run_id!r} has no results to compare."
            )

        keys = sorted(set(baseline_vals) & set(scenario_vals))
        metrics: list[MetricDelta] = []
        for dataset_id, fname in keys:
            b = baseline_vals[(dataset_id, fname)]
            s = scenario_vals[(dataset_id, fname)]
            unit = self._units_for(dataset_id).get(fname)
            metrics.append(self._delta(dataset_id, fname, b, s, unit))
        return ComparisonResult(baseline_run_id, scenario_run_id, metrics)

    @staticmethod
    def _delta(dataset_id: str, fname: str, b: Any, s: Any, unit: str | None) -> MetricDelta:
        if _is_number(b) and _is_number(s):
            absolute = float(s) - float(b)
            baseline_zero = float(b) == 0.0
            relative = None if baseline_zero else absolute / float(b)
            changed = not math.isclose(absolute, 0.0, abs_tol=0.0)
            direction = "increase" if absolute > 0 else ("decrease" if absolute < 0 else "none")
            return MetricDelta(
                dataset_id=dataset_id, field=fname, kind="numeric",
                baseline=b, scenario=s, unit=unit,
                absolute_delta=absolute, relative_delta=relative,
                baseline_zero=baseline_zero, direction=direction, changed=changed,
            )
        if isinstance(b, bool) and isinstance(s, bool):
            return MetricDelta(
                dataset_id=dataset_id, field=fname, kind="boolean",
                baseline=b, scenario=s, unit=unit, changed=(b != s),
            )
        # Mixed / None / non-numeric — neutral changed flag, no arithmetic.
        return MetricDelta(
            dataset_id=dataset_id, field=fname, kind="other",
            baseline=b, scenario=s, unit=unit, changed=(b != s),
        )
