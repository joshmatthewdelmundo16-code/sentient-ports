"""Optimization — D27.  Decision variables + constraints + objectives → feasible alternatives.

This is NOT scenario evaluation. A study declares:

  variables    discrete choices (e.g. new berths ∈ {0,1,2}, opening year ∈ {2028, 2030},
               shore-power share ∈ {0, 0.5, 1}), each mapped to a plan investment with a
               capital cost
  constraints  limits that every alternative must satisfy (per period or for the whole plan)
  objectives   what to minimise or maximise, each with a declared weight

Method: EXHAUSTIVE ENUMERATION of the declared grid. Every combination is evaluated with the
same models the plans use (in memory, for speed). The result is therefore exact for the
declared grid — not an approximation, and nothing outside the grid is claimed. No solver is
needed at this size; a MILP/metaheuristic would only be warranted for much larger spaces.

Outputs: counts (evaluated, feasible, infeasible by reason), the Pareto-efficient set across all
objectives, the best alternative for each single objective, and a weighted pick that is
explicitly attributed to the declared weights (min-max normalised over the feasible set). The
weighted pick is then re-run through the real GraphRun engine as a plan branch, and the
engine's numbers are compared with the in-memory numbers.

These results are model-derived under the declared objective function. They are not a policy
recommendation and do not weigh anything that is not in the model.
"""

from __future__ import annotations

import copy
import itertools
import json
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.persistence.database import OptimizationStudy
from backend.app.planning.plans import (
    MasterPlanService,
    PlanError,
    evaluate_spec_in_memory,
    validate_spec,
)
from backend.app.product.util import iso

PLAN_METRICS = ("npv_cost_usd", "total_capex_usd", "cumulative_unmet_teu", "cumulative_co2_t",
                "max_berth_utilization_pct", "max_yard_utilization_pct", "max_waiting_time_h")
PERIOD_METRICS = ("berth_utilization_pct", "yard_utilization_pct", "gate_utilization_pct",
                  "unmet_teu", "waiting_time_h", "co2_t", "capacity_headroom_pct",
                  "resilience_headroom_pct")
OPS = {"<=": lambda a, b: a <= b + 1e-9, ">=": lambda a, b: a >= b - 1e-9}
MAX_CANDIDATES = 5000
DISCLAIMER = ("Model-derived result under the declared variables, constraints, objectives and "
              "weights. Synthetic, uncalibrated models. Not a policy recommendation.")


class OptimizationError(Exception):
    pass


def validate_study(spec: dict[str, Any], base_inputs: dict[str, Any]) -> dict[str, Any]:
    variables = spec.get("variables") or []
    if not variables:
        raise OptimizationError("Declare at least one decision variable.")
    size = 1
    keys = set()
    for v in variables:
        if not v.get("key") or not isinstance(v.get("values"), list) or not v["values"]:
            raise OptimizationError(f"Variable {v.get('key')!r} needs a key and a list of values.")
        keys.add(v["key"])
        size *= len(v["values"])
        apply = v.get("apply")
        if apply and (apply.get("field") not in base_inputs or apply.get("op") not in ("add", "set")):
            raise OptimizationError(f"Variable {v['key']!r}: invalid apply {apply}.")
    if size > MAX_CANDIDATES:
        raise OptimizationError(f"The grid has {size} combinations; the limit is {MAX_CANDIDATES}.")
    for v in variables:
        if v.get("year_variable") and v["year_variable"] not in keys:
            raise OptimizationError(f"Variable {v['key']!r} refers to unknown year variable {v['year_variable']!r}.")
    for c in spec.get("constraints") or []:
        metric = c.get("metric")
        scope = c.get("when", "plan")
        allowed = PERIOD_METRICS if scope == "all_periods" else PLAN_METRICS
        if metric not in allowed or c.get("op") not in OPS:
            raise OptimizationError(f"Invalid constraint {c}.")
    objectives = spec.get("objectives") or []
    if not objectives:
        raise OptimizationError("Declare at least one objective.")
    for o in objectives:
        if o.get("metric") not in PLAN_METRICS or o.get("sense") not in ("min", "max"):
            raise OptimizationError(f"Invalid objective {o}.")
        if float(o.get("weight", 1)) < 0:
            raise OptimizationError("Objective weights must be non-negative.")
    return spec


def candidate_investments(variables: list[dict[str, Any]], choice: dict[str, Any],
                          base_inputs: dict[str, Any], default_year: int) -> list[dict[str, Any]]:
    """Turn one combination of variable values into plan investments with capital costs."""
    investments = []
    for v in variables:
        apply = v.get("apply")
        if not apply:
            continue                       # e.g. a year variable used by another variable
        value = choice[v["key"]]
        base = float(base_inputs[apply["field"]])
        delta = float(value) if apply["op"] == "add" else max(0.0, float(value) - base)
        if apply["op"] == "add" and float(value) == 0:
            continue
        if apply["op"] == "set" and float(value) == base:
            continue
        scale = 1.0
        if v.get("capex_scale_field"):
            scale_field = v["capex_scale_field"]
            scale = float(base_inputs[scale_field]) + sum(
                float(choice[o["key"]]) for o in variables
                if o.get("apply", {}).get("field") == scale_field and o.get("apply", {}).get("op") == "add")
        year = int(choice[v["year_variable"]]) if v.get("year_variable") else int(v.get("year", default_year))
        investments.append({
            "name": f"{v.get('label', v['key'])}: {value}",
            "year": year,
            "capex_usd": float(v.get("capex_per_unit_usd", 0)) * delta * scale,
            "changes": [{"field": apply["field"], "op": apply["op"], "value": float(value)}],
            "variable": v["key"],
        })
    return investments


def _violations(spec: dict[str, Any], evaluated: dict[str, Any]) -> list[str]:
    out = []
    for c in spec.get("constraints") or []:
        op, limit, metric = OPS[c["op"]], float(c["value"]), c["metric"]
        if c.get("when") == "all_periods":
            for p in evaluated["periods"]:
                val = p["outputs"].get(metric)
                if val is None:
                    if metric == "waiting_time_h":
                        out.append(f"{metric} unbounded in {p['year']}")
                        break
                    continue
                if not op(float(val), limit):
                    out.append(f"{metric} {c['op']} {limit:g} broken in {p['year']}")
                    break
        else:
            val = evaluated["totals"].get(metric)
            if val is None or not op(float(val), limit):
                out.append(f"{metric} {c['op']} {limit:g}")
    return out


def pareto(points: list[dict[str, Any]], objectives: list[dict[str, Any]]) -> set[int]:
    def vec(p):
        return [(p["metrics"][o["metric"]] if o["sense"] == "min" else -p["metrics"][o["metric"]])
                for o in objectives]
    vecs = [vec(p) for p in points]
    front = set()
    for i, a in enumerate(vecs):
        dominated = any(all(bj <= aj for aj, bj in zip(a, b)) and any(bj < aj for aj, bj in zip(a, b))
                        for j, b in enumerate(vecs) if j != i)
        if not dominated:
            front.add(i)
    return front


def run_study(pack_key: str, base_inputs: dict[str, Any], plan_spec: dict[str, Any],
              study: dict[str, Any]) -> dict[str, Any]:
    variables = study["variables"]
    objectives = study["objectives"]
    grid = list(itertools.product(*[v["values"] for v in variables]))
    seen: set[str] = set()
    alternatives = []
    reasons: dict[str, int] = {}
    for combo in grid:
        choice = {v["key"]: val for v, val in zip(variables, combo)}
        invs = candidate_investments(variables, choice, base_inputs, plan_spec["horizon"][0])
        signature = json.dumps(sorted((i["variable"], i["year"], i["changes"][0]["value"]) for i in invs))
        if signature in seen:
            continue                       # e.g. "0 new berths" in two different years
        seen.add(signature)
        spec = copy.deepcopy(plan_spec)
        spec["investments"] = list(spec.get("investments") or []) + invs
        evaluated = evaluate_spec_in_memory(pack_key, base_inputs, spec)
        violations = _violations(study, evaluated)
        for r in violations:
            key = r.split(" broken")[0]
            reasons[key] = reasons.get(key, 0) + 1
        metrics = {m: evaluated["totals"].get(m) for m in PLAN_METRICS}
        alternatives.append({
            "index": len(alternatives), "choice": choice, "investments": invs, "metrics": metrics,
            "feasible": not violations, "violations": violations,
        })
    feasible = [a for a in alternatives
                if a["feasible"] and all(a["metrics"][o["metric"]] is not None for o in objectives)]
    result: dict[str, Any] = {
        "method": "Exhaustive enumeration of the declared grid (exact for that grid).",
        "evaluated": len(alternatives), "grid_size": len(grid), "feasible": len(feasible),
        "infeasible_reasons": reasons, "objectives": objectives, "constraints": study.get("constraints") or [],
        "variables": variables, "alternatives": alternatives, "disclaimer": DISCLAIMER,
    }
    if not feasible:
        result.update({"pareto": [], "best_by_objective": {}, "weighted_pick": None})
        return result
    front = pareto(feasible, objectives)
    for i, a in enumerate(feasible):
        a["pareto"] = i in front
    best_by = {}
    for o in objectives:
        pick = (min if o["sense"] == "min" else max)(feasible, key=lambda a: a["metrics"][o["metric"]])
        best_by[o["metric"]] = pick["index"]
    total_w = sum(float(o.get("weight", 1)) for o in objectives) or 1.0
    ranges = {}
    for o in objectives:
        vals = [a["metrics"][o["metric"]] for a in feasible]
        ranges[o["metric"]] = (min(vals), max(vals))
    def score(a):
        s = 0.0
        for o in objectives:
            lo, hi = ranges[o["metric"]]
            norm = 0.0 if hi == lo else (a["metrics"][o["metric"]] - lo) / (hi - lo)
            if o["sense"] == "max":
                norm = 1.0 - norm
            s += float(o.get("weight", 1)) / total_w * norm
        return s
    for a in feasible:
        a["weighted_score"] = score(a)
    pick = min(feasible, key=lambda a: (a["weighted_score"], a["index"]))
    result.update({
        "pareto": sorted(a["index"] for a in feasible if a.get("pareto")),
        "best_by_objective": best_by,
        "weighted_pick": pick["index"],
        "weighting_note": ("Weighted pick = lowest weighted sum of objectives, each min-max normalised over "
                           f"the {len(feasible)} feasible alternatives; weights "
                           + ", ".join(f"{o['metric']} {float(o.get('weight', 1)) / total_w:.2f}" for o in objectives)
                           + ". Change the weights and the pick can change; the Pareto set does not."),
    })
    return result


class OptimizationService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._plans = MasterPlanService(db)

    def create(self, *, name: str, base_plan_id: str, spec: dict[str, Any]) -> OptimizationStudy:
        plan = self._plans.get(base_plan_id)
        validate_study(spec, self._plans.base_inputs())
        study = OptimizationStudy(name=(name or "Optimization study").strip(), base_plan_id=plan.id,
                                  spec_json=json.dumps(spec), status="draft")
        self._db.add(study)
        self._db.flush()
        return study

    def get(self, study_id: str) -> OptimizationStudy:
        s = self._db.execute(select(OptimizationStudy).where(OptimizationStudy.id == study_id)).scalars().first()
        if s is None:
            raise OptimizationError("Study not found in this organization.")
        return s

    def run(self, study_id: str, *, verify: bool = True) -> dict[str, Any]:
        study = self.get(study_id)
        spec = json.loads(study.spec_json)
        base_plan = self._plans.get(study.base_plan_id)
        plan_spec = json.loads(base_plan.spec_json)
        base_inputs = self._plans.base_inputs()
        plan_spec = validate_spec(plan_spec, base_inputs)
        validate_study(spec, base_inputs)
        result = run_study("port_capacity_planning", base_inputs, plan_spec, spec)
        if verify and result.get("weighted_pick") is not None:
            pick = result["alternatives"][result["weighted_pick"]]
            try:
                branch = self._plans.branch(
                    base_plan.id, name=f"{study.name} · weighted pick",
                    changes={"investments": list(plan_spec.get("investments") or []) + pick["investments"]})
                engine = self._plans.evaluate(branch.id, triggered_by="optimization")
                diffs = []
                for m in PLAN_METRICS:
                    a, b = pick["metrics"].get(m), (engine["totals"] or {}).get(m)
                    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                        diffs.append(abs(a - b) / max(1.0, abs(a)))
                max_diff = max(diffs) if diffs else math.inf
                result["verification"] = {
                    "plan_id": branch.id, "run_ids": [p["run_id"] for p in engine["periods"]],
                    "max_relative_difference": max_diff, "matches": max_diff < 1e-9,
                    "note": "The weighted pick was re-run through the GraphRun engine as a plan branch; "
                            "these are the engine's numbers compared with the optimizer's in-memory numbers.",
                }
                study.verification_plan_id = branch.id
            except PlanError as exc:
                result["verification"] = {"matches": False, "error": str(exc)}
        study.result_json = json.dumps(result)
        study.status = "evaluated"
        study.evaluated_at = datetime.now(timezone.utc)
        self._db.flush()
        return self.detail(study.id)

    def detail(self, study_id: str) -> dict[str, Any]:
        s = self.get(study_id)
        return {"id": s.id, "name": s.name, "status": s.status, "base_plan_id": s.base_plan_id,
                "spec": json.loads(s.spec_json), "result": json.loads(s.result_json) if s.result_json else None,
                "verification_plan_id": s.verification_plan_id, "evaluated_at": iso(s.evaluated_at)}

    def list(self) -> list[dict[str, Any]]:
        rows = self._db.execute(select(OptimizationStudy).order_by(OptimizationStudy.created_at)).scalars().all()
        return [{"id": s.id, "name": s.name, "status": s.status, "base_plan_id": s.base_plan_id,
                 "evaluated_at": iso(s.evaluated_at)} for s in rows]


def default_study(horizon: list[int]) -> dict[str, Any]:
    """A reviewable default study for the demo port (every number is declared, none hidden)."""
    later = [y for y in horizon if y > horizon[0]]
    return {
        "variables": [
            {"key": "new_berths", "label": "New berths", "values": [0, 1, 2],
             "apply": {"field": "berths", "op": "add"}, "capex_per_unit_usd": 120_000_000,
             "year_variable": "berth_year"},
            {"key": "berth_year", "label": "Berth opening year", "values": later[1:3] or later[:1]},
            {"key": "cranes_per_berth", "label": "Cranes per berth", "values": [3, 4],
             "apply": {"field": "cranes_per_berth", "op": "set"}, "capex_per_unit_usd": 12_000_000,
             "capex_scale_field": "berths", "year": later[0] if later else horizon[0]},
            {"key": "yard_slots_added", "label": "Yard slots added", "values": [0, 3000],
             "apply": {"field": "yard_ground_slots", "op": "add"}, "capex_per_unit_usd": 13_000,
             "year": later[1] if len(later) > 1 else horizon[0]},
            {"key": "shore_power_share", "label": "Calls on shore power", "values": [0.0, 0.5, 1.0],
             "apply": {"field": "shore_power_share", "op": "set"}, "capex_per_unit_usd": 60_000_000,
             "year": later[1] if len(later) > 1 else horizon[0]},
        ],
        "constraints": [
            {"metric": "unmet_teu", "op": "<=", "value": 0, "when": "all_periods"},
            {"metric": "berth_utilization_pct", "op": "<=", "value": 70, "when": "all_periods"},
            {"metric": "yard_utilization_pct", "op": "<=", "value": 85, "when": "all_periods"},
            {"metric": "total_capex_usd", "op": "<=", "value": 450_000_000, "when": "plan"},
        ],
        "objectives": [
            {"metric": "npv_cost_usd", "sense": "min", "weight": 0.5, "label": "Discounted cost"},
            {"metric": "cumulative_co2_t", "sense": "min", "weight": 0.3, "label": "Cumulative CO₂"},
            {"metric": "max_waiting_time_h", "sense": "min", "weight": 0.2, "label": "Worst waiting time"},
        ],
    }
