"""Dynamic master planning — D27.

A master plan is a horizon (e.g. 2026 → 2027 → 2028 → 2029 → 2030 → 2035), time-dependent
assumptions (growth rates, interpolated trajectories, one-off shocks), and investments that
take effect from a commissioning year. Evaluating a plan runs the installed port capacity
federation once per period through the ordinary GraphRun engine — read-only, exactly like a
scenario (publish suppressed, overrides applied in memory) — and pins each period to its run,
so every number in a plan has results and lineage behind it.

Plans can branch (a child plan inherits and changes its parent's spec) and be compared
period by period. The same period-input logic drives the optimizer's in-memory evaluator, so a
plan and an optimization candidate with the same spec produce the same numbers.

Discounting: each evaluated year stands for the years until the next evaluated year (a step
approximation, stated in every result). Capital spending is discounted from its own year.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.library.packs import PACKS, evaluate_in_memory
from backend.app.persistence.database import Dataset, MasterPlan, PlanPeriod, Result
from backend.app.product.util import iso

SUMMARY_DATASET = "planning_summary_output"
INPUTS_DATASET = "planning_inputs"
INVESTMENT_OPS = ("add", "set")


class PlanError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Spec → period inputs (pure; shared with the optimizer)
# ---------------------------------------------------------------------------

def validate_spec(spec: dict[str, Any], base_inputs: dict[str, Any]) -> dict[str, Any]:
    horizon = spec.get("horizon")
    if not isinstance(horizon, list) or not horizon or not all(isinstance(y, int) for y in horizon):
        raise PlanError("horizon must be a list of years, e.g. [2026, 2027, 2030].")
    horizon = sorted(set(horizon))
    if len(horizon) > 40 or horizon[0] < 1990 or horizon[-1] > 2100:
        raise PlanError("horizon must be at most 40 years between 1990 and 2100.")
    base_year = int(spec.get("base_year", horizon[0]))
    rate = float(spec.get("discount_rate", 0.08))
    if not 0 <= rate < 1:
        raise PlanError("discount_rate must be between 0 and 1.")
    for field, rule in (spec.get("assumptions") or {}).items():
        if field not in base_inputs:
            raise PlanError(f"Unknown assumption {field!r}.")
        if rule.get("type") not in ("growth", "points", "constant"):
            raise PlanError(f"{field}: type must be growth, points or constant.")
    for inv in spec.get("investments") or []:
        if not isinstance(inv.get("year"), int):
            raise PlanError(f"Investment {inv.get('name')!r} needs a commissioning year.")
        for ch in inv.get("changes") or []:
            if ch.get("field") not in base_inputs or ch.get("op") not in INVESTMENT_OPS:
                raise PlanError(f"Investment {inv.get('name')!r}: invalid change {ch}.")
    for sh in spec.get("shocks") or []:
        if sh.get("field") not in base_inputs:
            raise PlanError(f"Shock {sh.get('name')!r}: unknown field {sh.get('field')!r}.")
    return {**spec, "horizon": horizon, "base_year": base_year, "discount_rate": rate}


def _interpolate(points: dict[str, Any], year: int) -> float:
    pts = sorted((int(k), float(v)) for k, v in points.items())
    if year <= pts[0][0]:
        return pts[0][1]
    if year >= pts[-1][0]:
        return pts[-1][1]
    for (y0, v0), (y1, v1) in zip(pts, pts[1:]):
        if y0 <= year <= y1:
            return v0 + (v1 - v0) * (year - y0) / (y1 - y0)
    return pts[-1][1]


def period_inputs(base: dict[str, Any], spec: dict[str, Any], year: int) -> dict[str, Any]:
    v = dict(base)
    base_year = spec["base_year"]
    for field, rule in (spec.get("assumptions") or {}).items():
        if rule["type"] == "growth":
            v[field] = float(base[field]) * (1.0 + float(rule.get("rate", 0))) ** (year - base_year)
        elif rule["type"] == "points":
            v[field] = _interpolate(rule.get("points") or {}, year)
    for sh in spec.get("shocks") or []:
        start = int(sh.get("year", sh.get("from", year)))
        end = int(sh.get("to", start))
        if start <= year <= end:
            v[sh["field"]] = float(v[sh["field"]]) * float(sh.get("factor", 1.0))
    for inv in spec.get("investments") or []:
        if year >= inv["year"]:
            for ch in inv.get("changes") or []:
                v[ch["field"]] = (float(v[ch["field"]]) + float(ch["value"])) if ch["op"] == "add" else float(ch["value"])
    v["capex_usd_this_year"] = capex_in_period(spec, year)
    return v


def capex_in_period(spec: dict[str, Any], year: int) -> float:
    """Capital spending attributed to an evaluated year: investments whose year falls in
    [this evaluated year, next evaluated year)."""
    horizon = spec["horizon"]
    nxt = next((y for y in horizon if y > year), None)
    return float(sum(float(inv.get("capex_usd", 0)) for inv in spec.get("investments") or []
                     if inv["year"] >= year and (nxt is None or inv["year"] < nxt)
                     or (year == horizon[0] and inv["year"] < year)))


def represented_years(horizon: list[int], year: int) -> list[int]:
    nxt = next((y for y in horizon if y > year), None)
    return list(range(year, nxt)) if nxt else [year]


def summarize(spec: dict[str, Any], periods: list[dict[str, Any]]) -> dict[str, Any]:
    """Plan-level results from per-period planning summaries (same maths for plans and the optimizer)."""
    r = spec["discount_rate"]
    base = spec["base_year"]
    horizon = spec["horizon"]
    npv_opex = cum_unmet = cum_co2 = 0.0
    max_util = max_wait = max_yard = None
    wait_unbounded = False
    first_short = None
    for p in periods:
        out = p["outputs"]
        yrs = represented_years(horizon, p["year"])
        opex = float(out.get("total_cost_usd") or 0) - float(out.get("capex_usd") or 0)
        npv_opex += sum(opex / (1 + r) ** (t - base) for t in yrs)
        cum_unmet += float(out.get("unmet_teu") or 0) * len(yrs)
        cum_co2 += float(out.get("co2_t") or 0) * len(yrs)
        for key, cur in (("berth_utilization_pct", "util"), ("yard_utilization_pct", "yard")):
            val = out.get(key)
            if isinstance(val, (int, float)):
                if cur == "util":
                    max_util = val if max_util is None else max(max_util, val)
                else:
                    max_yard = val if max_yard is None else max(max_yard, val)
        w = out.get("waiting_time_h")
        if w is None:
            wait_unbounded = True
        elif max_wait is None or w > max_wait:
            max_wait = w
        if first_short is None and float(out.get("unmet_teu") or 0) > 0:
            first_short = p["year"]
    npv_capex = sum(float(inv.get("capex_usd", 0)) / (1 + r) ** (inv["year"] - base)
                    for inv in spec.get("investments") or [])
    return {
        "npv_cost_usd": npv_opex + npv_capex,
        "npv_opex_usd": npv_opex,
        "npv_capex_usd": npv_capex,
        "total_capex_usd": float(sum(float(inv.get("capex_usd", 0)) for inv in spec.get("investments") or [])),
        "cumulative_unmet_teu": cum_unmet,
        "cumulative_co2_t": cum_co2,
        "max_berth_utilization_pct": max_util,
        "max_yard_utilization_pct": max_yard,
        "max_waiting_time_h": None if wait_unbounded else max_wait,
        "waiting_unbounded": wait_unbounded,
        "first_year_short_of_capacity": first_short,
        "method_note": (f"Discounted at {r:.0%} to {base}. Each evaluated year stands for the years until "
                        "the next evaluated year (step approximation); capital is discounted from its own year."),
    }


def evaluate_spec_in_memory(pack_key: str, base_inputs: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    pack = PACKS[pack_key]
    periods = []
    for year in spec["horizon"]:
        inputs = period_inputs(base_inputs, spec, year)
        out = evaluate_in_memory(pack, {INPUTS_DATASET: inputs})[SUMMARY_DATASET]
        periods.append({"year": year, "outputs": out})
    return {"periods": periods, "totals": summarize(spec, periods)}


# ---------------------------------------------------------------------------
# Persisted plans evaluated through the engine
# ---------------------------------------------------------------------------

class MasterPlanService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _federation(self) -> tuple[Dataset, str]:
        inputs = self._db.execute(select(Dataset).where(Dataset.name == INPUTS_DATASET)).scalars().first()
        summary = self._db.execute(select(Dataset).where(Dataset.name == SUMMARY_DATASET)).scalars().first()
        if inputs is None or summary is None or inputs.current_value is None:
            raise PlanError("Install the “Port capacity planning” model pack in this organization first.")
        from backend.app.persistence.database import ModelIOBinding
        terminal = self._db.execute(select(ModelIOBinding.model_version_id).where(
            ModelIOBinding.dataset_id == summary.id, ModelIOBinding.direction == "output")).scalars().first()
        if terminal is None:
            raise PlanError("The planning summary model is missing.")
        return inputs, terminal

    def base_inputs(self) -> dict[str, Any]:
        inputs, _ = self._federation()
        return json.loads(inputs.current_value)

    def create(self, *, name: str, spec: dict[str, Any], description: str | None = None,
               parent_plan_id: str | None = None) -> MasterPlan:
        inputs, terminal = self._federation()
        clean = validate_spec(spec, json.loads(inputs.current_value))
        if not (name or "").strip():
            raise PlanError("A plan needs a name.")
        plan = MasterPlan(name=name.strip(), description=description, spec_json=json.dumps(clean),
                          status="draft", parent_plan_id=parent_plan_id, target_version_id=terminal,
                          inputs_dataset_id=inputs.id)
        self._db.add(plan)
        self._db.flush()
        return plan

    def get(self, plan_id: str) -> MasterPlan:
        plan = self._db.execute(select(MasterPlan).where(MasterPlan.id == plan_id)).scalars().first()
        if plan is None:
            raise PlanError("Plan not found in this organization.")
        return plan

    def branch(self, plan_id: str, *, name: str, changes: dict[str, Any]) -> MasterPlan:
        parent = self.get(plan_id)
        spec = copy.deepcopy(json.loads(parent.spec_json))
        for key in ("horizon", "assumptions", "investments", "shocks", "discount_rate"):
            if key in changes:
                spec[key] = changes[key]
        return self.create(name=name, spec=spec, description=f"Branch of {parent.name}", parent_plan_id=parent.id)

    def evaluate(self, plan_id: str, *, triggered_by: str = "plan") -> dict[str, Any]:
        from backend.app.services.adapter_registry import AdapterRegistry
        from backend.app.services.orchestration import GraphOrchestrationService

        plan = self.get(plan_id)
        spec = json.loads(plan.spec_json)
        inputs_ds, terminal = self._federation()
        base = json.loads(inputs_ds.current_value)
        orch = GraphOrchestrationService(self._db, AdapterRegistry(self._db))
        summary_ds = self._db.execute(select(Dataset).where(Dataset.name == SUMMARY_DATASET)).scalars().first()
        for old in self._db.execute(select(PlanPeriod).where(PlanPeriod.plan_id == plan.id)).scalars():
            self._db.delete(old)
        self._db.flush()
        for year in spec["horizon"]:
            inputs = period_inputs(base, spec, year)
            outcome = orch.execute_graph(
                terminal, triggered_by=f"{triggered_by}:{plan.id}:{year}", trigger_type="plan",
                overrides={inputs_ds.id: inputs}, publish=False)
            outputs = None
            if outcome.success:
                res = self._db.execute(select(Result).where(Result.run_id == outcome.run_id,
                                                            Result.dataset_id == summary_ds.id)).scalars().first()
                outputs = json.loads(res.value_json) if res else None
            self._db.add(PlanPeriod(plan_id=plan.id, year=year, run_id=outcome.run_id,
                                    status="succeeded" if outcome.success else "failed",
                                    inputs_json=json.dumps(inputs), outputs_json=json.dumps(outputs)))
        plan.status = "evaluated"
        plan.evaluated_at = _now()
        self._db.flush()
        return self.detail(plan.id)

    def detail(self, plan_id: str) -> dict[str, Any]:
        plan = self.get(plan_id)
        spec = json.loads(plan.spec_json)
        rows = self._db.execute(select(PlanPeriod).where(PlanPeriod.plan_id == plan.id)
                                .order_by(PlanPeriod.year)).scalars().all()
        periods = [{"year": p.year, "run_id": p.run_id, "status": p.status,
                    "inputs": json.loads(p.inputs_json or "null"), "outputs": json.loads(p.outputs_json or "null")}
                   for p in rows]
        ok = [p for p in periods if p["outputs"]]
        return {
            "id": plan.id, "name": plan.name, "description": plan.description, "status": plan.status,
            "parent_plan_id": plan.parent_plan_id, "spec": spec, "periods": periods,
            "totals": summarize(spec, ok) if ok and len(ok) == len(periods) else None,
            "evaluated_at": iso(plan.evaluated_at), "created_at": iso(plan.created_at),
        }

    def list(self) -> list[dict[str, Any]]:
        plans = self._db.execute(select(MasterPlan).order_by(MasterPlan.created_at)).scalars().all()
        return [{"id": p.id, "name": p.name, "status": p.status, "parent_plan_id": p.parent_plan_id,
                 "description": p.description, "evaluated_at": iso(p.evaluated_at)} for p in plans]

    def compare(self, a_id: str, b_id: str) -> dict[str, Any]:
        a, b = self.detail(a_id), self.detail(b_id)
        if not a["totals"] or not b["totals"]:
            raise PlanError("Evaluate both plans first.")
        years = sorted({p["year"] for p in a["periods"]} & {p["year"] for p in b["periods"]})
        by = lambda d: {p["year"]: p["outputs"] for p in d["periods"]}
        pa, pb = by(a), by(b)
        rows = []
        for y in years:
            row = {"year": y}
            for k in ("demand_teu", "capacity_teu", "unmet_teu", "berth_utilization_pct", "waiting_time_h",
                      "co2_t", "total_cost_usd", "binding_constraint"):
                row[k] = {"a": pa[y].get(k), "b": pb[y].get(k)}
            rows.append(row)
        totals = {k: {"a": a["totals"][k], "b": b["totals"][k]} for k in a["totals"] if k != "method_note"}
        return {"a": {"id": a["id"], "name": a["name"]}, "b": {"id": b["id"], "name": b["name"]},
                "periods": rows, "totals": totals, "method_note": a["totals"]["method_note"]}
