"""D27 extension of the synthetic network demo (SQLite only; called by network_seed).

Adds, idempotently:
  * hub federations (Hub network performance pack) in the regional and national hubs, each
    hub's aggregate of approved member outputs, and the hubs approving their own aggregates
    upward to the national hub;
  * in Northbay: the port capacity planning, port-city and live operations packs, a base
    master plan and an expansion branch (both evaluated), an optimization study (run and
    verified through the engine), a port-city baseline and a "storm season" scenario, and
    connector sources — a CSV source, a labelled simulated berth-sensor feed (run once), a
    webhook source and a REST source (defined, not polled: no allowed host is configured);
  * a collaboration case hosted by the Northern regional hub with Northbay and Eastmouth,
    into which Northbay explicitly shares one scenario result.

Everything is synthetic and labelled. The session is committed between phases because the
governed cross-organization reads (hub views, cases) see committed data only.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.library.packs import install_pack
from backend.app.persistence.database import (
    ApprovedOutput,
    Baseline,
    CollaborationCase,
    ConnectorSource,
    Dataset,
    MasterPlan,
    OptimizationStudy,
    Organization,
    Participant,
    Scenario,
)
from backend.app.security.tenancy import tenant_scope

SYNTHETIC = {"synthetic": True, "disclaimer": "Synthetic demonstration record."}
HUB_AUTHORITIES = {
    "demo-north-hub": ("northern-hub-office", "Northern regional hub office"),
    "demo-south-hub": ("southern-hub-office", "Southern regional hub office"),
    "demo-national": ("national-hub-office", "National executive hub office"),
}


def _participant(db: Session, key: str, name: str) -> Participant:
    p = db.execute(select(Participant).where(Participant.participant_key == key)).scalars().first()
    if p is None:
        p = Participant(participant_key=key, name=name, status="active",
                        description="Approves what this hub shares upward. Synthetic.",
                        meta_json=json.dumps(SYNTHETIC))
        db.add(p)
        db.flush()
    return p


def _approve(db: Session, participant: Participant, dataset: Dataset, field: str, *, audience: str | None,
             purpose: str, case_id: str | None = None, source_run_id: str | None = None) -> None:
    exists = db.execute(select(ApprovedOutput).where(
        ApprovedOutput.participant_id == participant.id, ApprovedOutput.dataset_id == dataset.id,
        ApprovedOutput.field_name == field, ApprovedOutput.status == "active",
        ApprovedOutput.collaboration_case_id.is_(None) if case_id is None else ApprovedOutput.collaboration_case_id == case_id,
    )).scalars().first()
    if exists is None:
        from backend.app.services.governance import GovernanceService
        GovernanceService(db).create_approval(
            participant_id=participant.id, dataset_id=dataset.id, field_name=field, purpose=purpose,
            audience_organization_id=audience, collaboration_case_id=case_id, source_run_id=source_run_id,
            metadata=SYNTHETIC)


def _hubs(db: Session, orgs: dict[str, Organization]) -> dict[str, Any]:
    from backend.app.network.service import NetworkService

    out = {}
    for key in ("demo-north-hub", "demo-south-hub", "demo-national"):
        with tenant_scope(db, orgs[key].id):
            install_pack(db, "hub_network_performance", owner=key)
            _participant(db, *HUB_AUTHORITIES[key])
    db.commit()
    for key in ("demo-north-hub", "demo-south-hub"):
        with tenant_scope(db, orgs[key].id):
            out[key] = NetworkService(db).materialize(orgs[key].id, triggered_by="network-seed")
            perf = db.execute(select(Dataset).where(Dataset.name == "network_performance_output")).scalars().first()
            part = _participant(db, *HUB_AUTHORITIES[key])
            for field in ("network_throughput_teu", "emissions_intensity_tco2_per_teu", "teu_coverage_pct"):
                _approve(db, part, perf, field, audience=orgs["demo-national"].id,
                         purpose="National executive reporting")
    db.commit()
    return out


def _northbay(db: Session, orgs: dict[str, Organization]) -> dict[str, Any]:
    from backend.app.api.routers.planning import default_plan_spec
    from backend.app.connectors.sources import simulate
    from backend.app.planning.optimize import OptimizationService, default_study
    from backend.app.planning.plans import MasterPlanService
    from backend.app.services.scenarios import ScenarioService

    org = orgs["port-northbay"]
    result: dict[str, Any] = {}
    with tenant_scope(db, org.id):
        planning = install_pack(db, "port_capacity_planning", owner=org.org_key)
        city = install_pack(db, "port_city_cross_domain", owner=org.org_key)
        live = install_pack(db, "live_operations", owner=org.org_key)
        plans = MasterPlanService(db)
        base = db.execute(select(MasterPlan).where(MasterPlan.name == "Base plan 2026–2035")).scalars().first()
        if base is None:
            base = plans.create(name="Base plan 2026–2035", spec=default_plan_spec(),
                                description="Today's terminal, 5% demand growth, a 2028 trade dip. Synthetic.")
            plans.evaluate(base.id, triggered_by="network-seed")
        branch = db.execute(select(MasterPlan).where(MasterPlan.name == "Expansion: berth 5 and shore power")).scalars().first()
        if branch is None:
            spec = default_plan_spec()
            branch = plans.branch(base.id, name="Expansion: berth 5 and shore power", changes={"investments": [
                {"name": "Berth 5", "year": 2029, "capex_usd": 120_000_000,
                 "changes": [{"field": "berths", "op": "add", "value": 1}]},
                {"name": "Shore power, half of calls", "year": 2028, "capex_usd": 30_000_000,
                 "changes": [{"field": "shore_power_share", "op": "set", "value": 0.5}]},
            ], "horizon": spec["horizon"]})
            plans.evaluate(branch.id, triggered_by="network-seed")
        study = db.execute(select(OptimizationStudy).where(
            OptimizationStudy.name == "Capacity, cost and emissions trade-off")).scalars().first()
        if study is None:
            opt = OptimizationService(db)
            study = opt.create(name="Capacity, cost and emissions trade-off", base_plan_id=base.id,
                               spec=default_study(default_plan_spec()["horizon"]))
            opt.run(study.id)

        svc = ScenarioService(db)
        city_base = db.execute(select(Baseline).where(Baseline.name == "Port city today")).scalars().first()
        if city_base is None:
            city_base = svc.create_baseline(name="Port city today", target_version_id=city["terminal_version_id"],
                                            description="Cross-domain port-city federation on its synthetic defaults.")
            svc.execute_baseline(city_base.id, triggered_by="network-seed")
        storm = db.execute(select(Scenario).where(Scenario.baseline_id == city_base.id)).scalars().first()
        if storm is None:
            storm = svc.create_scenario(baseline_id=city_base.id, name="Storm season and larger ships",
                                        description="30 storm days a year and a 14.5 m design draft.")
            nat = city["source_datasets"]["natural_inputs"]
            svc.set_override(storm.id, nat, "storm_days_per_year", 30.0)
            svc.set_override(storm.id, nat, "design_draft_m", 14.5)
            svc.execute_scenario(storm.id, triggered_by="network-seed")

        def source(name: str, kind: str, dataset_id: str, config: dict[str, Any], interval: int | None = None) -> ConnectorSource:
            s = db.execute(select(ConnectorSource).where(ConnectorSource.name == name)).scalars().first()
            if s is None:
                s = ConnectorSource(name=name, kind=kind, target_dataset_id=dataset_id,
                                    config_json=json.dumps(config), poll_interval_s=interval)
                db.add(s)
                db.flush()
            return s

        live_in = live["source_datasets"]["live_operations_inputs"]
        sim = source("Berth sensor feed (simulated)", "simulated", live_in, {
            "window_minutes": 60,
            "fields": {"berth_occupancy_pct": {"metric": "berth_occupancy", "agg": "mean"},
                       "vessel_arrivals_per_hour": {"metric": "vessel_arrival", "agg": "count", "per_hour": True},
                       "observed_crane_moves_per_hour": {"metric": "crane_moves", "agg": "mean"}},
            "simulate": {"berth_occupancy": {"mean": 62, "spread": 6},
                         "crane_moves": {"mean": 25.5, "spread": 1.8},
                         "vessel_arrival": {"kind": "events", "rate_per_minute": 0.004}},
        })
        if sim.last_run_at is None:
            result["simulated"] = simulate(db, sim, minutes=60, seed=2026)
        source("Planning inputs (CSV)", "csv_upload", planning["source_datasets"]["planning_inputs"], {})
        source("Terminal telemetry (webhook)", "webhook", live_in, {})
        source("Port authority open data (REST)", "rest_poll", live_in, {
            "url": "https://data.example.org/ports/northbay/berths.json",
            "fields": {"berth_occupancy_pct": "summary.occupancy_pct"}}, interval=900)
        result.update({"base_plan_id": base.id, "branch_plan_id": branch.id, "study_id": study.id,
                       "city_baseline_id": city_base.id, "storm_scenario_id": storm.id})
    db.commit()
    return result


def _case(db: Session, orgs: dict[str, Organization]) -> str | None:
    from backend.app.network.cases import CaseService

    hub = orgs["demo-north-hub"]
    with tenant_scope(db, hub.id):
        case = db.execute(select(CollaborationCase).where(
            CollaborationCase.title == "Shared berth capacity study 2030")).scalars().first()
        if case is None:
            created = CaseService(db).create(
                title="Shared berth capacity study 2030",
                purpose="Decide jointly whether the northern ports need new berth capacity before 2030, "
                        "using results each port chooses to share into this case.",
                member_org_ids=[orgs["port-northbay"].id, orgs["port-eastmouth"].id],
                closes_at=None, user_id=None)
            case_id = created["id"]
        else:
            case_id = case.id
    db.commit()
    with tenant_scope(db, orgs["port-northbay"].id):
        summary = db.execute(select(Dataset).where(Dataset.name == "decision_summary_output")).scalars().first()
        part = db.execute(select(Participant).where(Participant.participant_key == "northbay-port-authority")).scalars().first()
        scen = db.execute(select(Scenario).where(Scenario.name == "Higher bunker price")).scalars().first()
        if summary and part and scen and scen.scenario_run_id:
            _approve(db, part, summary, "annual_fuel_cost_usd", audience=None, case_id=case_id,
                     source_run_id=scen.scenario_run_id,
                     purpose="Joint look at a 25% higher bunker price (scenario result, not today's value)")
    db.commit()
    return case_id


def seed_d27_demo(db: Session, orgs: dict[str, Organization]) -> dict[str, Any]:
    db.commit()                       # D26 part must be visible to cross-organization reads
    hubs = _hubs(db, orgs)
    northbay = _northbay(db, orgs)
    case_id = _case(db, orgs)
    return {"hubs": {k: v.get("run_id") for k, v in hubs.items()}, "northbay": northbay, "case_id": case_id}
