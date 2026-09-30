"""Comparison with labels, and "why did this change" explanations (D25).

Everything is derived from recorded facts:

  * values come from the persisted Results of two exact GraphRuns (D19 comparison service);
  * the value an assumption HAD in a run comes from that run's step input snapshots — what
    the model actually consumed — not from today's dataset value;
  * the causal path comes from the declared input bindings: a model is on the path only if
    its binding selects a changed field (field-level, via the binding's field map), and an
    output is attributed to it only if the comparison shows that output actually moved.

No narrative is invented. The response is structured so the UI can phrase it, and every
statement in it can be traced back to a run, a step, a binding, or a result.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.persistence.database import (
    ExecutionRun,
    ExecutionStep,
    ModelIOBinding,
)
from backend.app.product.catalog import CatalogService, LabelIndex
from backend.app.services.scenario_comparison import ScenarioComparisonService
from backend.app.services.scenarios import ScenarioService


class ExplanationUnavailable(Exception):
    """The scenario (or its baseline) has not produced a run to explain yet."""


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


class ExplanationService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._catalog = CatalogService(db)
        self._comparison = ScenarioComparisonService(db)

    # ------------------------------------------------------------------
    # Labelled comparison of any two runs
    # ------------------------------------------------------------------

    def _terminal_datasets(self, run_id: str) -> set[str]:
        run = self._db.get(ExecutionRun, run_id)
        if run is None or not run.target_version_id:
            return set()
        rows = self._db.execute(
            select(ModelIOBinding.dataset_id).where(
                ModelIOBinding.model_version_id == run.target_version_id,
                ModelIOBinding.direction == "output",
            )
        ).scalars()
        return set(rows)

    def compare_runs(self, base_run_id: str, target_run_id: str,
                     idx: LabelIndex | None = None) -> dict[str, Any]:
        idx = idx or self._catalog.label_index()
        result = self._comparison.compare(base_run_id, target_run_id)
        terminal = self._terminal_datasets(target_run_id)
        metrics = []
        for m in result.metrics:
            meta = idx.field(m.dataset_id, m.field)
            order = idx.field_order.get(m.dataset_id, [])
            metrics.append({
                **asdict(m),
                "unit": meta.unit or m.unit,
                "dataset_label": idx.dataset(m.dataset_id),
                "field_label": meta.label,
                "terminal": m.dataset_id in terminal,
                "field_order": order.index(m.field) if m.field in order else len(order),
            })
        metrics.sort(key=lambda x: (not x["terminal"], x["dataset_label"], x["field_order"]))
        return {
            "base_run_id": base_run_id,
            "target_run_id": target_run_id,
            "metrics": metrics,
            "changed_count": sum(1 for x in metrics if x["changed"]),
        }

    # ------------------------------------------------------------------
    # What each step consumed
    # ------------------------------------------------------------------

    def _inputs_by_version(self, run_id: str) -> dict[str, dict[str, Any]]:
        steps = self._db.execute(
            select(ExecutionStep).where(ExecutionStep.run_id == run_id)
        ).scalars()
        return {s.model_version_id: (_loads(s.input_snapshot) or {}) for s in steps
                if s.model_version_id}

    def _input_bindings(self) -> list[ModelIOBinding]:
        return list(self._db.execute(
            select(ModelIOBinding).where(ModelIOBinding.direction == "input")
        ).scalars())

    def _output_bindings(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for b in self._db.execute(
            select(ModelIOBinding).where(ModelIOBinding.direction == "output")
        ).scalars():
            out.setdefault(b.model_version_id, []).append(b.dataset_id)
        return out

    @staticmethod
    def _reads(binding: ModelIOBinding, field: str) -> str | None:
        """The model-side input name if this binding passes `field`, else None."""
        fmap = _loads(binding.field_map)
        if fmap is None:
            return field
        return fmap.get(field)

    def consumed_value(self, run_id: str, dataset_id: str, field: str) -> tuple[bool, Any]:
        """(found, value) — the value of dataset.field that some step of the run consumed."""
        inputs = self._inputs_by_version(run_id)
        for b in self._input_bindings():
            if b.dataset_id != dataset_id or b.model_version_id not in inputs:
                continue
            name = self._reads(b, field)
            if name is not None and name in inputs[b.model_version_id]:
                return True, inputs[b.model_version_id][name]
        return False, None

    # ------------------------------------------------------------------
    # Scenario explanation
    # ------------------------------------------------------------------

    def explain_scenario(self, scenario_id: str) -> dict[str, Any]:
        svc = ScenarioService(self._db)
        scenario = svc.get_scenario(scenario_id)
        baseline = svc.get_baseline(scenario.baseline_id)
        if not baseline.baseline_run_id or not scenario.scenario_run_id:
            raise ExplanationUnavailable(
                "Run the baseline and the scenario first — there is nothing to explain yet."
            )
        idx = self._catalog.label_index()
        comparison = self.compare_runs(baseline.baseline_run_id, scenario.scenario_run_id, idx)

        # 1. What changed on the input side (the scenario's overrides).
        changes = []
        for ov in svc.list_overrides(scenario_id):
            meta = idx.field(ov.dataset_id, ov.field_name)
            found, was = self.consumed_value(baseline.baseline_run_id, ov.dataset_id, ov.field_name)
            _, now = self.consumed_value(scenario.scenario_run_id, ov.dataset_id, ov.field_name)
            value = json.loads(ov.value_json)
            changes.append({
                "dataset_id": ov.dataset_id,
                "dataset_label": idx.dataset(ov.dataset_id),
                "field": ov.field_name,
                "field_label": meta.label,
                "unit": meta.unit,
                "baseline_value": was if found else None,
                "baseline_value_known": found,
                "scenario_value": value if now is None else now,
            })

        return {
            "scenario": {"id": scenario.id, "name": scenario.name, "run_id": scenario.scenario_run_id},
            "baseline": {"id": baseline.id, "name": baseline.name, "run_id": baseline.baseline_run_id},
            "changes": changes,
            **self._trace(
                [(c["dataset_id"], c["field"]) for c in changes],
                scenario.scenario_run_id,
                comparison,
                idx,
            ),
            "comparison": comparison,
        }

    def _trace(self, seeds: list[tuple[str, str]], run_id: str, comparison: dict[str, Any],
               idx: LabelIndex) -> dict[str, Any]:
        """Field-level causal trace through the models the run executed."""
        run = self._db.get(ExecutionRun, run_id)
        order: list[str] = _loads(run.subgraph_json) if run else []
        order = order or []
        changed_fields: dict[str, set[str]] = {}
        for m in comparison["metrics"]:
            if m["changed"]:
                changed_fields.setdefault(m["dataset_id"], set()).add(m["field"])

        live: set[tuple[str, str]] = set(seeds)   # fields known to differ, as (dataset, field)
        bindings = self._input_bindings()
        outputs = self._output_bindings()
        path: list[dict[str, Any]] = []
        unaffected: list[dict[str, Any]] = []

        for vid in order:
            reads = []
            for b in bindings:
                if b.model_version_id != vid:
                    continue
                for ds, field in sorted(live):
                    if ds == b.dataset_id and self._reads(b, field) is not None:
                        reads.append({"dataset_id": ds, "field": field,
                                      "field_label": idx.field(ds, field).label,
                                      "dataset_label": idx.dataset(ds)})
            if not reads:
                unaffected.append({
                    "version_id": vid, "model_name": idx.model(vid),
                    "reason": "does_not_read_changed_fields",
                })
                continue
            moved = []
            steady = []
            for ds in outputs.get(vid, []):
                for field in idx.field_order.get(ds, []) or sorted(changed_fields.get(ds, set())):
                    entry = {"dataset_id": ds, "field": field,
                             "field_label": idx.field(ds, field).label,
                             "dataset_label": idx.dataset(ds)}
                    if field in changed_fields.get(ds, set()):
                        moved.append(entry)
                        live.add((ds, field))
                    else:
                        steady.append(entry)
            path.append({
                "version_id": vid,
                "model_name": idx.model(vid),
                "reads_changed": reads,
                "outputs_changed": moved,
                "outputs_unchanged": steady,
            })
        return {"path": path, "unaffected": unaffected}
