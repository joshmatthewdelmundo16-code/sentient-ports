"""D27 — model packs, model library, dynamic master planning, optimization, capability registry."""

from __future__ import annotations

import json
import math

import pytest
from sqlalchemy import func, select

from backend.app.library import port_city, port_planning
from backend.app.library.packs import CITY, PACKS, PLANNING, evaluate_in_memory
from backend.app.persistence.database import ChangeEvent, Dataset, ExecutionRun, MasterPlan, PlanPeriod
from backend.app.planning.optimize import (
    OptimizationError,
    default_study,
    pareto,
    run_study,
    validate_study,
)
from backend.app.planning.plans import (
    PlanError,
    capex_in_period,
    evaluate_spec_in_memory,
    period_inputs,
    validate_spec,
)
from tests.d27_support import network_client

BASE = PLANNING.defaults["planning_inputs"]


@pytest.fixture(scope="module")
def net():
    with network_client() as n:
        yield n


# ---------------------------------------------------------------------------
# Model maths
# ---------------------------------------------------------------------------

class TestModels:
    def test_erlang_c_matches_mm1_and_mm2_closed_forms(self):
        # M/M/1: P(wait) = ρ.  M/M/2 with a = λ/μ: P(wait) = a² / (2 + a)… for ρ = a/2 (Erlang C).
        assert port_planning.erlang_c(1, 0.6) == pytest.approx(0.6)
        a = 1.2
        assert port_planning.erlang_c(2, a) == pytest.approx(a ** 2 / (2 + a))

    def test_berth_queueing_mm1_waiting_time(self):
        out = port_planning.berth_operations(
            vessel_calls=0.5 * 8760, average_call_size_teu=100, berths=1, cranes_per_berth=1,
            crane_moves_per_hour=100, teu_per_move=1, call_overhead_hours=0, berth_availability=1.0,
            target_berth_occupancy=0.9)
        lam, mu = 0.5, 1.0                                  # calls/hour; service 1 h
        assert out["berth_utilization_pct"] == pytest.approx(50.0)
        assert out["waiting_time_h"] == pytest.approx((lam / mu) / (mu - lam))   # ρ/(μ−λ)
        assert out["turnaround_h"] == pytest.approx(out["waiting_time_h"] + 1.0)

    def test_overloaded_berth_reports_unbounded_waiting_not_a_number(self):
        out = port_planning.berth_operations(
            vessel_calls=2 * 8760, average_call_size_teu=100, berths=1, cranes_per_berth=1,
            crane_moves_per_hour=100, teu_per_move=1, call_overhead_hours=0, berth_availability=1.0,
            target_berth_occupancy=0.7)
        assert out["berth_overloaded"] is True and out["waiting_time_h"] is None and out["turnaround_h"] is None

    def test_tidal_access_analytic_points(self):
        common = dict(storm_days_per_year=0, storm_downtime_share=0, tidal_range_m=2.0, underkeel_clearance_m=0.0)
        assert port_city.natural_access(mean_channel_depth_m=15, design_draft_m=13.9, **common)["tidal_access_share"] == 1.0
        assert port_city.natural_access(mean_channel_depth_m=15, design_draft_m=16.1, **common)["tidal_access_share"] == 0.0
        assert port_city.natural_access(mean_channel_depth_m=15, design_draft_m=15.0, **common)["tidal_access_share"] == pytest.approx(0.5)

    def test_binding_constraint_and_unmet_demand(self):
        out = port_planning.capacity_resilience(demand_teu=100, berth_capacity_teu=80, yard_capacity_teu=90,
                                                gate_capacity_teu=200, average_call_size_teu=10,
                                                disruption_days=0, berth_availability=1)
        assert out["binding_constraint"] == "berth" and out["unmet_teu"] == 20 and out["handled_teu"] == 80

    def test_formulas_shown_are_the_code_docstrings(self):
        m = next(x for x in PLANNING.models if x.key == "berth_operations")
        import inspect
        assert m.formula == inspect.cleandoc(port_planning.berth_operations.__doc__).strip()
        assert m.formula.startswith("handling_rate") and "Erlang C" in m.formula

    def test_cross_domain_pack_spans_five_domains(self):
        assert {m.domain for m in CITY.models} >= {"natural", "operational", "economic", "environmental", "social"}
        base = evaluate_in_memory(CITY, CITY.defaults)["city_summary_output"]
        storm = evaluate_in_memory(CITY, {**CITY.defaults, "natural_inputs": {
            **CITY.defaults["natural_inputs"], "storm_days_per_year": 30.0, "design_draft_m": 14.5}})["city_summary_output"]
        assert base["city_unmet_teu"] == 0 and storm["city_unmet_teu"] > 0
        assert storm["total_jobs"] < base["total_jobs"] and storm["pm25_t"] < base["pm25_t"]


# ---------------------------------------------------------------------------
# Library and packs (through the API)
# ---------------------------------------------------------------------------

class TestLibrary:
    def test_cards_carry_contracts_units_provenance_and_compatibility(self, net):
        net.login("demo@platform.example")
        cards = {c["name"]: c for c in net.get("/api/model-library", "port-northbay").json()}
        berth = cards["Berth and crane operations"]
        assert berth["provider_kind"] == "internal" and berth["calibration"] == "uncalibrated"
        assert berth["domain"] == "operational" and "Erlang C" in berth["formula"]
        v = berth["versions"][0]
        assert v["compatible"] and v["execution_method"].startswith("In-process Python function")
        fields = {f["name"]: f for i in v["inputs"] for f in i["fields"]}
        assert fields["crane_moves_per_hour"]["unit"] == "moves/hour"
        assert all(ver["compatible"] for c in cards.values() for ver in c["versions"])

    def test_pack_install_is_admin_only_and_idempotent(self, net):
        net.login("analyst@northbay.example")
        assert net.post("/api/model-library/packs/port_city_cross_domain/install").status_code == 403
        net.login("demo@platform.example")
        db = net.SF()
        before = db.execute(select(func.count()).select_from(Dataset)).scalar_one()
        db.close()
        r = net.post("/api/model-library/packs/port_city_cross_domain/install", "port-northbay")
        assert r.status_code == 200
        db = net.SF()
        after = db.execute(select(func.count()).select_from(Dataset)).scalar_one()
        db.close()
        assert before == after
        assert net.post("/api/model-library/packs/nope/install", "port-northbay").status_code == 404

    def test_unknown_pack_model_cannot_be_executed(self):
        from backend.app.persistence.database import ModelVersion
        from backend.app.services.adapter_registry import AdapterConfigError, build_persisted_adapter
        v = ModelVersion(id="x", model_id="m", semver="1", adapter_type="model_pack",
                         adapter_config=json.dumps({"pack": "port_capacity_planning", "model": "os.system"}))
        with pytest.raises(AdapterConfigError):
            build_persisted_adapter(v)


# ---------------------------------------------------------------------------
# Master planning
# ---------------------------------------------------------------------------

SPEC = {"horizon": [2026, 2027, 2028, 2030], "base_year": 2026, "discount_rate": 0.08,
        "assumptions": {"demand_teu": {"type": "growth", "rate": 0.05},
                        "electricity_price_usd_per_mwh": {"type": "points", "points": {"2026": 120, "2030": 100}}},
        "shocks": [{"name": "Dip", "field": "demand_teu", "year": 2028, "factor": 0.9}],
        "investments": [{"name": "Berth", "year": 2028, "capex_usd": 100.0,
                         "changes": [{"field": "berths", "op": "add", "value": 1}]}]}


class TestPlanMaths:
    def test_time_dependent_inputs(self):
        spec = validate_spec(SPEC, BASE)
        y27 = period_inputs(BASE, spec, 2027)
        assert y27["demand_teu"] == pytest.approx(BASE["demand_teu"] * 1.05)
        assert period_inputs(BASE, spec, 2028)["demand_teu"] == pytest.approx(BASE["demand_teu"] * 1.05 ** 2 * 0.9)
        assert period_inputs(BASE, spec, 2028)["electricity_price_usd_per_mwh"] == pytest.approx(110.0)
        assert y27["berths"] == BASE["berths"] and period_inputs(BASE, spec, 2030)["berths"] == BASE["berths"] + 1

    def test_capex_lands_in_the_period_that_represents_its_year(self):
        spec = validate_spec(SPEC, BASE)
        assert capex_in_period(spec, 2028) == 100.0 and capex_in_period(spec, 2027) == 0.0

    def test_invalid_specs_are_refused(self):
        with pytest.raises(PlanError):
            validate_spec({**SPEC, "horizon": []}, BASE)
        with pytest.raises(PlanError):
            validate_spec({**SPEC, "assumptions": {"nope": {"type": "growth"}}}, BASE)
        with pytest.raises(PlanError):
            validate_spec({**SPEC, "investments": [{"name": "x", "year": 2027, "changes": [{"field": "berths", "op": "multiply", "value": 2}]}]}, BASE)


class TestPlansThroughTheEngine:
    def test_every_period_is_a_read_only_engine_run_matching_in_memory(self, net):
        net.login("demo@platform.example")
        created = net.post("/api/plans", "port-northbay", json={"name": "Test plan", "spec": SPEC})
        assert created.status_code == 201, created.text
        db = net.SF()
        inputs_before = db.execute(select(Dataset.current_hash).where(
            Dataset.organization_id == net.orgs["port-northbay"], Dataset.name == "planning_inputs")).scalar_one()
        events_before = db.execute(select(func.count()).select_from(ChangeEvent)).scalar_one()
        db.close()
        plan = net.post(f"/api/plans/{created.json()['id']}/evaluate", "port-northbay").json()
        assert [p["year"] for p in plan["periods"]] == [2026, 2027, 2028, 2030]
        db = net.SF()
        runs = [db.get(ExecutionRun, p["run_id"]) for p in plan["periods"]]
        assert all(r.trigger_type == "plan" and r.status == "succeeded" for r in runs)
        # Read-only: the shared planning inputs and change events are untouched.
        assert db.execute(select(Dataset.current_hash).where(
            Dataset.organization_id == net.orgs["port-northbay"], Dataset.name == "planning_inputs")).scalar_one() == inputs_before
        assert db.execute(select(func.count()).select_from(ChangeEvent)).scalar_one() == events_before
        db.close()
        mem = evaluate_spec_in_memory("port_capacity_planning", BASE, validate_spec(SPEC, BASE))
        for eng, m in zip(plan["periods"], mem["periods"]):
            for k, v in m["outputs"].items():
                assert eng["outputs"][k] == (pytest.approx(v) if isinstance(v, float) else v), (eng["year"], k)
        assert plan["totals"]["npv_cost_usd"] == pytest.approx(mem["totals"]["npv_cost_usd"])

    def test_branch_and_compare(self, net):
        net.login("demo@platform.example")
        plans = {p["name"]: p["id"] for p in net.get("/api/plans", "port-northbay").json()}
        cmp_ = net.get(f"/api/plans/compare?a={plans['Base plan 2026–2035']}&b={plans['Expansion: berth 5 and shore power']}",
                       "port-northbay").json()
        assert cmp_["totals"]["cumulative_unmet_teu"]["a"] > 0 and cmp_["totals"]["cumulative_unmet_teu"]["b"] == 0
        assert cmp_["totals"]["total_capex_usd"]["b"] == 150_000_000
        y2035 = next(r for r in cmp_["periods"] if r["year"] == 2035)
        assert y2035["capacity_teu"]["b"] > y2035["capacity_teu"]["a"]
        assert "step approximation" in cmp_["method_note"]

    def test_plans_need_the_pack(self, net):
        net.login("hub@north.example")                     # the hub has no planning pack
        assert net.post("/api/plans", json={"name": "x", "spec": SPEC}).status_code == 422
        assert net.get("/api/plans/defaults").json()["installed"] is False


# ---------------------------------------------------------------------------
# Optimization
# ---------------------------------------------------------------------------

class TestOptimization:
    @pytest.fixture(scope="class")
    def result(self):
        spec = validate_spec({**SPEC, "horizon": [2026, 2027, 2028, 2029, 2030, 2035], "investments": [], "shocks": []}, BASE)
        study = validate_study(default_study(spec["horizon"]), BASE)
        return spec, study, run_study("port_capacity_planning", BASE, spec, study)

    def test_feasible_alternatives_satisfy_every_constraint(self, result):
        spec, study, res = result
        assert res["feasible"] > 0 and res["evaluated"] <= res["grid_size"]
        for alt in res["alternatives"]:
            evaluated = evaluate_spec_in_memory("port_capacity_planning", BASE,
                                                {**spec, "investments": alt["investments"]})
            if alt["feasible"]:
                assert all(p["outputs"]["unmet_teu"] <= 1e-9 for p in evaluated["periods"])
                assert all(p["outputs"]["berth_utilization_pct"] <= 70 + 1e-9 for p in evaluated["periods"])
                assert evaluated["totals"]["total_capex_usd"] <= 450_000_000
            else:
                assert alt["violations"]

    def test_pareto_set_is_non_dominated(self, result):
        _, study, res = result
        feasible = [a for a in res["alternatives"] if a["feasible"]]
        front = [a for a in feasible if a.get("pareto")]
        objs = study["objectives"]
        for f in front:
            for other in feasible:
                better_or_equal = all(other["metrics"][o["metric"]] <= f["metrics"][o["metric"]] for o in objs)
                strictly = any(other["metrics"][o["metric"]] < f["metrics"][o["metric"]] for o in objs)
                assert not (better_or_equal and strictly)
        assert pareto([{"metrics": {"x": 1, "y": 1}}, {"metrics": {"x": 2, "y": 2}}],
                      [{"metric": "x", "sense": "min"}, {"metric": "y", "sense": "min"}]) == {0}

    def test_weighted_pick_is_attributed_to_declared_weights(self, result):
        _, _, res = result
        pick = res["alternatives"][res["weighted_pick"]]
        assert pick["feasible"] and pick.get("pareto")
        assert "weights" in res["weighting_note"] and "Not a policy recommendation" in res["disclaimer"]
        assert res["method"].startswith("Exhaustive enumeration")

    def test_single_objective_bests(self, result):
        _, _, res = result
        feasible = [a for a in res["alternatives"] if a["feasible"]]
        cheapest = min(a["metrics"]["npv_cost_usd"] for a in feasible)
        assert res["alternatives"][res["best_by_objective"]["npv_cost_usd"]]["metrics"]["npv_cost_usd"] == cheapest

    def test_invalid_studies_are_refused(self):
        with pytest.raises(OptimizationError):
            validate_study({"variables": [], "objectives": [{"metric": "npv_cost_usd", "sense": "min"}]}, BASE)
        big = {"variables": [{"key": f"v{i}", "values": list(range(10)), "apply": {"field": "berths", "op": "add"}}
                             for i in range(4)], "objectives": [{"metric": "npv_cost_usd", "sense": "min"}]}
        with pytest.raises(OptimizationError, match="limit"):
            validate_study(big, BASE)
        with pytest.raises(OptimizationError):
            validate_study({**default_study([2026, 2027, 2028]), "objectives": [{"metric": "profit", "sense": "max"}]}, BASE)

    def test_seeded_study_was_verified_through_the_engine(self, net):
        net.login("demo@platform.example")
        study = net.get("/api/optimization", "port-northbay").json()[0]
        detail = net.get(f"/api/optimization/{study['id']}", "port-northbay").json()
        ver = detail["result"]["verification"]
        assert ver["matches"] is True and ver["max_relative_difference"] < 1e-9
        db = net.SF()
        plan = db.get(MasterPlan, ver["plan_id"])
        periods = db.execute(select(PlanPeriod).where(PlanPeriod.plan_id == plan.id)).scalars().all()
        db.close()
        assert len(periods) == len(ver["run_ids"]) and all(p.run_id for p in periods)


# ---------------------------------------------------------------------------
# Capability registry
# ---------------------------------------------------------------------------

class TestCapabilities:
    def test_registry_counts_and_honest_labels(self, net):
        net.login("demo@platform.example")
        caps = net.get("/api/capabilities", "port-northbay").json()
        by = {c["key"]: c for c in caps["capabilities"]}
        assert by["third_party"]["status"] == "not_implemented"
        assert by["cross_domain"]["status"] == "illustrative"
        assert by["deployment"]["status"] == "external_dependency"
        assert by["app_supply_chain"]["status"] == "not_implemented"
        assert caps["counts"]["unverified"] == 0
        assert "Not a reproduction" in caps["statement"]

    def test_capability_is_downgraded_when_its_route_is_not_served(self, monkeypatch):
        from backend.app import capabilities as capmod
        from backend.app.main import api

        fake = capmod._c("ghost", "X", "Ghost", "implemented", "ref", "impl", routes=("/api/does-not-exist",))
        monkeypatch.setattr(capmod, "CAPABILITIES", capmod.CAPABILITIES + [fake])
        rows = {c["key"]: c for c in capmod.registry(api)["capabilities"]}
        assert rows["ghost"]["status"] == "unverified"
