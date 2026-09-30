"""Decision workspace summary and upload impact (D25).

`WorkspaceService.summary()` replaces the demo-only `/api/decision-config` as the React
product's starting point. It is derived from records, so it works the same against a demo
SQLite database and a shared PostgreSQL one:

  * baselines, each with its pinned run and target model;
  * scenarios, each with its overrides labelled and its run;
  * source datasets (assumptions) with labelled fields, current values and provenance;
  * a default selection: the most recently updated scenario that has been executed against
    a baseline that has a run (else the first baseline) — or none, which the UI renders as
    an empty state with next steps.

`upload_impact()` answers "what did this workbook change?" for one ingestion: the fields
that changed (old → new), the propagation run it triggered, which models ran, and the
labelled comparison of that run against the previous authoritative run.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.persistence.database import (
    Baseline,
    ChangeEvent,
    Dataset,
    ExecutionRun,
    IngestionRun,
    Model,
    Scenario,
    ScenarioOverride,
)
from backend.app.product.activity import ActivityService
from backend.app.product.util import iso
from backend.app.product.catalog import CatalogService
from backend.app.product.explain import ExplanationService
from backend.app.services.scenario_comparison import ComparisonRunNotFoundError


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


class WorkspaceService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._catalog = CatalogService(db)
        self._explain = ExplanationService(db)

    def _run_brief(self, run_id: str | None) -> dict[str, Any] | None:
        if not run_id:
            return None
        run = self._db.get(ExecutionRun, run_id)
        if run is None:
            return None
        return {"id": run.id, "status": run.status, "executor": run.executor,
                "finished_at": iso(run.finished_at), "started_at": iso(run.started_at)}

    def summary(self) -> dict[str, Any]:
        idx = self._catalog.label_index()
        baselines = list(self._db.execute(select(Baseline).order_by(Baseline.created_at)).scalars())
        scenarios = list(self._db.execute(
            select(Scenario).order_by(Scenario.updated_at.desc())).scalars())
        overrides: dict[str, list[ScenarioOverride]] = {}
        for ov in self._db.execute(select(ScenarioOverride)).scalars():
            overrides.setdefault(ov.scenario_id, []).append(ov)

        baseline_out = []
        for b in baselines:
            baseline_out.append({
                "id": b.id, "name": b.name, "description": b.description, "status": b.status,
                "target_version_id": b.target_version_id,
                "target_model": idx.model(b.target_version_id) if b.target_version_id else None,
                "run": self._run_brief(b.baseline_run_id),
                "created_at": iso(b.created_at), "updated_at": iso(b.updated_at),
            })

        by_baseline = {b.id: b for b in baselines}
        scenario_out = []
        for s in scenarios:
            base = by_baseline.get(s.baseline_id)
            ovs = []
            for ov in overrides.get(s.id, []):
                meta = idx.field(ov.dataset_id, ov.field_name)
                was = None
                known = False
                if base is not None and base.baseline_run_id:
                    known, was = self._explain.consumed_value(
                        base.baseline_run_id, ov.dataset_id, ov.field_name)
                ovs.append({
                    "id": ov.id, "dataset_id": ov.dataset_id,
                    "dataset_label": idx.dataset(ov.dataset_id), "field": ov.field_name,
                    "field_label": meta.label, "unit": meta.unit,
                    "value": json.loads(ov.value_json),
                    "baseline_value": was if known else None, "baseline_value_known": known,
                })
            scenario_out.append({
                "id": s.id, "name": s.name, "description": s.description, "status": s.status,
                "baseline_id": s.baseline_id, "baseline_name": base.name if base else None,
                "run": self._run_brief(s.scenario_run_id),
                "overrides": ovs,
                "created_at": iso(s.created_at), "updated_at": iso(s.updated_at),
            })

        default_scenario = next(
            (s for s in scenario_out
             if s["run"] and s["run"]["status"] == "succeeded"
             and by_baseline.get(s["baseline_id"]) is not None
             and by_baseline[s["baseline_id"]].baseline_run_id),
            None,
        )
        default_baseline = (
            default_scenario["baseline_id"] if default_scenario
            else next((b["id"] for b in baseline_out if b["run"]), None)
            or (baseline_out[0]["id"] if baseline_out else None)
        )

        datasets = self._catalog.datasets()
        sources = [d for d in datasets if d["role"] == "source" and d["consumed_by"]]
        counts = {
            "models": self._db.execute(select(func.count()).select_from(Model)).scalar_one(),
            "datasets": len(datasets),
            "runs": self._db.execute(select(func.count()).select_from(ExecutionRun)).scalar_one(),
        }
        return {
            "baselines": baseline_out,
            "scenarios": scenario_out,
            "assumptions": sources,
            "default": {"baseline_id": default_baseline,
                        "scenario_id": default_scenario["id"] if default_scenario else None},
            "counts": counts,
        }

    # ------------------------------------------------------------------

    def upload_impact(self, ingestion_id: str) -> dict[str, Any] | None:
        ing = self._db.get(IngestionRun, ingestion_id)
        if ing is None:
            return None
        idx = self._catalog.label_index()
        out: dict[str, Any] = {
            "ingestion": {
                "id": ing.id, "file_name": ing.source_name, "status": ing.status,
                "mapping_key": ing.mapping_key, "content_sha256": ing.content_sha256,
                "error": ing.error, "created_at": iso(ing.created_at),
                "dataset_ids": _loads(ing.dataset_ids_json) or [],
            },
            "changes": [], "run": None, "models_run": [], "comparison": None,
        }
        events = list(self._db.execute(
            select(ChangeEvent).where(ChangeEvent.triggered_by == f"ingestion:{ing.id}")
            .order_by(ChangeEvent.created_at)
        ).scalars())
        for e in events:
            old, new = _loads(e.old_value_json) or {}, _loads(e.new_value_json) or {}
            for f, v in (new.items() if isinstance(new, dict) else []):
                if isinstance(old, dict) and old.get(f) == v:
                    continue
                meta = idx.field(e.dataset_id, f)
                out["changes"].append({
                    "dataset_id": e.dataset_id, "dataset_label": idx.dataset(e.dataset_id),
                    "field": f, "field_label": meta.label, "unit": meta.unit,
                    "from": old.get(f) if isinstance(old, dict) else None, "to": v,
                    "change_event_id": e.id, "old_hash": e.old_hash, "new_hash": e.new_hash,
                })
        if ing.graph_run_id:
            run = self._db.get(ExecutionRun, ing.graph_run_id)
            if run is not None:
                out["run"] = self._run_brief(run.id)
                order = _loads(run.subgraph_json) or []
                out["models_run"] = [{"version_id": v, "model_name": idx.model(v)} for v in order]
                prev = ActivityService(self._db)._previous_run(run)
                if prev is not None and run.status == "succeeded":
                    try:
                        out["comparison"] = self._explain.compare_runs(prev.id, run.id, idx)
                        out["compared_with"] = self._run_brief(prev.id)
                    except ComparisonRunNotFoundError:
                        pass
        return out


def dataset_by_name(db: Session, name: str) -> Dataset | None:
    return db.execute(select(Dataset).where(Dataset.name == name)).scalars().first()
