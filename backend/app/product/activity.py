"""Semantic activity feed (D25).

Turns execution runs, dataset change events and ingestion runs into business-readable
activity items. Every part of an item is derived from records:

  kind       baseline_run | scenario_run | dataset_change | upload | model_run | plan_run
  title      "Scenario run" — from the kind
  subject    "Higher bunker price" — the scenario/baseline name, the changed fields' labels,
             the workbook's file name, or the target model's name
  context    "Port assumptions" — the dataset label, or what the run was compared against
  headline   one metric with before → after (or a single value) and its unit, picked from
             the recorded comparison: the terminal metric that moved most, else the first
             terminal field in contract order
  technical  every identifier involved (run, event, ingestion, scenario, baseline, hash)

Changes that were propagated fold their propagation run in as `effect`, and committed
uploads fold into the dataset change they caused, so one user action reads as one row.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.persistence.database import (
    Baseline,
    ChangeEvent,
    ExecutionRun,
    IngestionRun,
    Scenario,
)
from backend.app.product.catalog import CatalogService, LabelIndex
from backend.app.product.explain import ExplanationService
from backend.app.product.util import iso, loads as _loads
from backend.app.services.scenario_comparison import ComparisonRunNotFoundError

_EXTERNAL_SOURCES = {"external", "excel", "seed", "network_share", "connector", "api",
                     "csv", "rest", "webhook", "telemetry", "simulated"}


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def pick_headline(metrics: list[dict[str, Any]], *, prefer_changed: bool) -> dict[str, Any] | None:
    """The single most informative metric of a labelled comparison."""
    if not metrics:
        return None
    terminal = [m for m in metrics if m.get("terminal")] or metrics
    if prefer_changed:
        moved = [m for m in terminal if m.get("changed")] or [m for m in metrics if m.get("changed")]
        if moved:
            def rank(m: dict[str, Any]) -> tuple[float, int]:
                rel = m.get("relative_delta")
                weight = abs(rel) if isinstance(rel, (int, float)) else 0.0
                # Round so float noise cannot beat contract order on an exact tie.
                return (-round(weight, 9), m.get("field_order", 0))
            best = sorted(moved, key=rank)[0]
            return {
                "dataset_id": best["dataset_id"], "field": best["field"],
                "label": best["field_label"], "unit": best.get("unit"),
                "from": best["baseline"], "to": best["scenario"],
                "relative_delta": best.get("relative_delta"),
            }
    first = sorted(terminal, key=lambda m: m.get("field_order", 0))[0]
    return {
        "dataset_id": first["dataset_id"], "field": first["field"],
        "label": first["field_label"], "unit": first.get("unit"),
        "value": first["scenario"],
    }


class ActivityService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._catalog = CatalogService(db)
        self._explain = ExplanationService(db)

    # ------------------------------------------------------------------

    def _previous_run(self, run: ExecutionRun) -> ExecutionRun | None:
        """The last successful authoritative run of the same target before this one."""
        return self._db.execute(
            select(ExecutionRun)
            .where(
                ExecutionRun.target_version_id == run.target_version_id,
                ExecutionRun.status == "succeeded",
                ExecutionRun.scenario_id.is_(None),
                ExecutionRun.created_at < run.created_at,
                ExecutionRun.id != run.id,
                ExecutionRun.trigger_type.notin_(("plan", "optimization")),
            )
            .order_by(ExecutionRun.created_at.desc())
            .limit(1)
        ).scalars().first()

    def _compare(self, base: str, target: str, idx: LabelIndex) -> list[dict[str, Any]]:
        try:
            return self._explain.compare_runs(base, target, idx)["metrics"]
        except ComparisonRunNotFoundError:
            return []

    def _run_values(self, run_id: str, idx: LabelIndex) -> list[dict[str, Any]]:
        """A run's own results, shaped like comparison metrics (for single-value headlines)."""
        try:
            return self._explain.compare_runs(run_id, run_id, idx)["metrics"]
        except ComparisonRunNotFoundError:
            return []

    # ------------------------------------------------------------------

    def feed(self, *, limit: int = 40) -> list[dict[str, Any]]:
        idx = self._catalog.label_index()
        items: list[dict[str, Any]] = []
        runs = list(self._db.execute(
            select(ExecutionRun).order_by(ExecutionRun.created_at.desc()).limit(limit * 2)
        ).scalars())
        scenarios = {s.id: s for s in self._db.execute(select(Scenario)).scalars()}
        baselines = list(self._db.execute(select(Baseline)).scalars())
        baseline_by_run = {b.baseline_run_id: b for b in baselines if b.baseline_run_id}
        ingestions = list(self._db.execute(
            select(IngestionRun).order_by(IngestionRun.created_at.desc()).limit(limit)
        ).scalars())
        ingestion_by_id = {i.id: i for i in ingestions}

        # Change events that entered from outside the model graph.
        events = [
            e for e in self._db.execute(
                select(ChangeEvent).order_by(ChangeEvent.created_at.desc()).limit(limit * 2)
            ).scalars()
            if (e.source_type or "external") in _EXTERNAL_SOURCES
        ]
        runs_folded: set[str] = set()
        run_kind = {r.id: r.trigger_type for r in runs}
        for e in events:
            item = self._change_item(e, idx, ingestion_by_id)
            # Only a propagation run belongs to the change that caused it. A baseline run
            # also marks the seed event it consumed as propagated, but it is its own action.
            if e.run_id and run_kind.get(e.run_id, "dataset_change") == "dataset_change":
                runs_folded.add(e.run_id)
            items.append(item)
        folded_ingestions = {it["technical"].get("ingestion_id") for it in items}

        for r in runs:
            if r.id in runs_folded or r.trigger_type in ("plan", "optimization"):
                continue
            items.append(self._run_item(r, idx, scenarios, baselines, baseline_by_run))

        for ing in ingestions:
            if ing.id in folded_ingestions:
                continue
            items.append(self._upload_item(ing))

        items.sort(key=lambda it: it["occurred_at"] or "", reverse=True)
        return items[:limit]

    # ------------------------------------------------------------------
    # Item builders
    # ------------------------------------------------------------------

    def _run_item(self, r: ExecutionRun, idx: LabelIndex, scenarios: dict[str, Scenario],
                  baselines: list[Baseline], baseline_by_run: dict[str, Baseline]) -> dict[str, Any]:
        technical: dict[str, Any] = {"run_id": r.id, "executor": r.executor,
                                     "trigger_type": r.trigger_type, "triggered_by": r.triggered_by,
                                     "target_version_id": r.target_version_id}
        headline = None
        context = None
        if r.scenario_id:
            kind, title = "scenario_run", "Scenario run"
            sc = scenarios.get(r.scenario_id)
            subject = sc.name if sc else "Deleted scenario"
            technical["scenario_id"] = r.scenario_id
            base = next((b for b in baselines if sc and b.id == sc.baseline_id), None)
            if base is not None and base.baseline_run_id and r.status == "succeeded":
                technical["baseline_id"] = base.id
                technical["compared_with_run_id"] = base.baseline_run_id
                context = f"Compared with {base.name}"
                headline = pick_headline(self._compare(base.baseline_run_id, r.id, idx),
                                         prefer_changed=True)
        elif r.trigger_type == "baseline":
            kind, title = "baseline_run", "Baseline run"
            base = baseline_by_run.get(r.id)
            if base is None:
                same_target = [b for b in baselines if b.target_version_id == r.target_version_id]
                base = same_target[0] if len(same_target) == 1 else None
            subject = base.name if base else "Baseline"
            if base is not None:
                technical["baseline_id"] = base.id
                context = "Pinned as the baseline" if base.baseline_run_id == r.id else "Earlier baseline run"
            if r.status == "succeeded":
                headline = pick_headline(self._run_values(r.id, idx), prefer_changed=False)
        else:
            kind, title = "model_run", "Model run"
            subject = idx.model(r.target_version_id)
            context = {"api": "Started from the API", "ui": "Started from the workspace",
                       "dataset_change": "Triggered by a dataset change"}.get(r.trigger_type or "")
            if r.status == "succeeded":
                prev = self._previous_run(r)
                if prev is not None:
                    technical["compared_with_run_id"] = prev.id
                    headline = pick_headline(self._compare(prev.id, r.id, idx), prefer_changed=True)
                if headline is None:
                    headline = pick_headline(self._run_values(r.id, idx), prefer_changed=False)
        return {
            "id": f"run:{r.id}",
            "kind": kind,
            "title": title,
            "subject": subject,
            "context": context,
            "status": r.status,
            "occurred_at": iso(r.finished_at or r.started_at or r.created_at),
            "headline": headline,
            "error": r.error_message,
            "links": {"run_id": r.id, "scenario_id": r.scenario_id},
            "technical": technical,
        }

    def _change_item(self, e: ChangeEvent, idx: LabelIndex,
                     ingestion_by_id: dict[str, IngestionRun]) -> dict[str, Any]:
        old = _loads(e.old_value_json)
        new = _loads(e.new_value_json)
        changed: list[str] = []
        if isinstance(new, dict):
            order = idx.field_order.get(e.dataset_id) or list(new)
            keys = [k for k in order if k in new] + [k for k in new if k not in order]
            changed = [k for k in keys if not isinstance(old, dict) or old.get(k) != new.get(k)]
        initial = old is None
        labels = [idx.field(e.dataset_id, f).label for f in changed]
        if initial:
            title, subject = "Dataset initialised", idx.dataset(e.dataset_id)
        else:
            title = "Dataset change"
            subject = ", ".join(labels[:2]) + (f" and {len(labels) - 2} more" if len(labels) > 2 else "")
            subject = subject or idx.dataset(e.dataset_id)

        headline = None
        if changed and not initial:
            f = changed[0]
            meta = idx.field(e.dataset_id, f)
            headline = {"dataset_id": e.dataset_id, "field": f, "label": meta.label,
                        "unit": meta.unit, "from": old.get(f), "to": new.get(f)}
            if _is_num(old.get(f)) and _is_num(new.get(f)) and old.get(f):
                headline["relative_delta"] = (new[f] - old[f]) / old[f]

        via: dict[str, Any] = {"source_type": e.source_type or "external", "source_ref": e.source_ref}
        technical: dict[str, Any] = {"change_event_id": e.id, "dataset_id": e.dataset_id,
                                     "old_hash": e.old_hash, "new_hash": e.new_hash,
                                     "source_ref": e.source_ref, "triggered_by": e.triggered_by}
        trig = e.triggered_by or ""
        if trig.startswith("ingestion:"):
            ing = ingestion_by_id.get(trig.split(":", 1)[1])
            technical["ingestion_id"] = trig.split(":", 1)[1]
            if ing is not None:
                via.update({"kind": "upload", "file_name": ing.source_name,
                            "content_sha256": ing.content_sha256, "mapping_key": ing.mapping_key})

        effect = None
        status = "recorded"
        if e.run_id:
            run = self._db.get(ExecutionRun, e.run_id)
            technical["propagation_run_id"] = e.run_id
            if run is not None:
                status = run.status
                if run.status == "succeeded":
                    prev = self._previous_run(run)
                    if prev is not None:
                        technical["compared_with_run_id"] = prev.id
                        metrics = self._compare(prev.id, run.id, idx)
                        effect = pick_headline(
                            [m for m in metrics if (m["dataset_id"], m["field"]) !=
                             (e.dataset_id, changed[0] if changed else None)],
                            prefer_changed=True,
                        )
                        if effect is not None and "from" not in effect:
                            effect = None
        elif initial:
            status = "succeeded"

        return {
            "id": f"change:{e.id}",
            "kind": "dataset_change",
            "title": title,
            "subject": subject,
            "context": None if initial else idx.dataset(e.dataset_id),
            "status": status,
            "occurred_at": iso(e.created_at),
            "headline": headline,
            "effect": effect,
            "changed_fields": [{"field": f, "label": idx.field(e.dataset_id, f).label} for f in changed],
            "via": via,
            "links": {"run_id": e.run_id, "dataset_id": e.dataset_id,
                      "ingestion_id": technical.get("ingestion_id")},
            "technical": technical,
        }

    def _upload_item(self, ing: IngestionRun) -> dict[str, Any]:
        return {
            "id": f"upload:{ing.id}",
            "kind": "upload",
            "title": "Workbook upload",
            "subject": ing.source_name,
            "context": {"unchanged": "Values matched the current data — nothing changed",
                        "rejected": "Rejected — nothing was written"}.get(ing.status),
            "status": ing.status,
            "occurred_at": iso(ing.created_at),
            "headline": None,
            "error": ing.error,
            "links": {"ingestion_id": ing.id},
            "technical": {"ingestion_id": ing.id, "content_sha256": ing.content_sha256,
                          "mapping_key": ing.mapping_key},
        }
