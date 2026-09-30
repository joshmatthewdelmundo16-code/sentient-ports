"""Guarded D20 decision-support seed.

Ensures the port domain (D17) plus one demo Baseline and one demo Scenario exist so the
decision workspace has real baseline↔scenario data to render. Reuses the existing machinery
only — `seed_port_domain` (D17 federation) and `ScenarioService` (D19 baseline/scenario/
override + read-only scenario execution). No new execution engine, no domain changes.

SAFETY (per D20 constraint): this seed is SQLite-only and non-fatal. It refuses to run
against a shared PostgreSQL/Supabase database, so simply starting the app never creates
baseline/scenario GraphRuns there and never modifies shared data. It is idempotent: an
existing baseline/scenario (matched by name) is reused and only executed if it has no run.

The demo scenario overrides bunker_price from the D17 golden baseline (600) to 750 — the
×1.25 change the specification describes; the numeric values are produced by the domain, not
written here.
"""
from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from backend.app.persistence.exceptions import NotFoundError
from backend.app.persistence.scenario_repository import (
    BaselineRepository,
    ScenarioRepository,
)
from backend.app.services.scenarios import ScenarioService
from backend.app.ui.port_seed import BUNKER_PRICE, seed_port_domain

BASELINE_NAME = "Current operations"
SCENARIO_NAME = "Higher bunker price"
# ×1.25 of the D17 golden baseline bunker price (DEFAULT_ASSUMPTIONS bunker_price = 600).
SCENARIO_BUNKER_PRICE = 750.0


def _tables_present(db: Session) -> bool:
    """True only when the D19 scenario tables exist (they do under SQLite create_all).

    Inspects the session's own connection rather than the engine: `inspect(engine)` checks
    a connection out of the pool and rolls it back on return, which would discard any
    uncommitted work already done in this session. Harmless while this is the first seed to
    run, but it is the same hazard that cost the governance seed its datasets, so it is
    closed here too rather than left as a trap for the next caller.
    """
    try:
        insp = inspect(db.connection())
        return insp.has_table("baseline") and insp.has_table("scenario")
    except Exception:
        return False


def is_sqlite(db: Session) -> bool:
    try:
        return db.get_bind().dialect.name == "sqlite"
    except Exception:
        return False


def seed_port_decision(db: Session) -> dict:
    """Idempotently ensure the port domain + demo baseline/scenario exist; return config.

    Returns {} without writing anything when the database is not SQLite or the D19 tables
    are absent, so a shared PostgreSQL/Supabase database is never touched at startup.
    """
    if not is_sqlite(db) or not _tables_present(db):
        return {}

    port = seed_port_domain(db)
    svc = ScenarioService(db)
    baselines = BaselineRepository(db)
    scenarios = ScenarioRepository(db)

    # Baseline (idempotent by unique name) — executed once (publishes normally).
    try:
        baseline = baselines.get_by_name(BASELINE_NAME)
    except NotFoundError:
        baseline = svc.create_baseline(
            name=BASELINE_NAME,
            description="D17 golden port assumptions — the authoritative baseline.",
            target_version_id=port["terminal_version_id"],
        )
    if baseline.baseline_run_id is None:
        svc.execute_baseline(baseline.id, triggered_by="decision-seed")

    # Scenario derived from the baseline, with a single bunker-price override.
    scenario = next(
        (s for s in scenarios.list_by_baseline(baseline.id) if s.name == SCENARIO_NAME),
        None,
    )
    if scenario is None:
        scenario = svc.create_scenario(
            baseline_id=baseline.id,
            name=SCENARIO_NAME,
            description="Considers a higher in-port bunker price against the baseline.",
        )
        svc.set_override(
            scenario.id, port["assumptions_dataset_id"], BUNKER_PRICE, SCENARIO_BUNKER_PRICE
        )
    if scenario.scenario_run_id is None:
        svc.execute_scenario(scenario.id, triggered_by="decision-seed")

    db.flush()
    return build_decision_config(port, baseline, scenario, svc)


def build_decision_config(port: dict, baseline, scenario, svc: ScenarioService) -> dict:
    """Assemble the read-only config the UI uses to render the default comparison."""
    overrides = [
        {"dataset_id": o.dataset_id, "field_name": o.field_name}
        for o in svc.list_overrides(scenario.id)
    ]
    datasets = port.get("datasets", {})
    return {
        "domain": port.get("domain", "toy_port"),
        "baseline_id": baseline.id,
        "baseline_name": baseline.name,
        "baseline_run_id": baseline.baseline_run_id,
        "scenario_id": scenario.id,
        "scenario_name": scenario.name,
        "scenario_run_id": scenario.scenario_run_id,
        "assumptions_dataset_id": port.get("assumptions_dataset_id"),
        "terminal_version_id": port.get("terminal_version_id"),
        "decision_output_dataset_id": datasets.get("decision_summary_output"),
        "models": port.get("models", []),
        "datasets": datasets,
        "overrides": overrides,
    }
