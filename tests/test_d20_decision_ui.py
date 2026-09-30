"""D20 — Decision-Support UI tests.

Covers the evolved /ui decision workspace, the two read-only API additions
(ExecutionRunOut.scenario_id, GET /api/decision-config), the guarded decision seed, and
that the decision surface's data source (the D19 comparison API) yields the acceptance
figures — asserted through the API, never hard-coded in templates/JS. Also guards the
retained D12/D14 anchors against regression.

Real SQLite (and PostgreSQL when TEST_DATABASE_URL is set). TestClient does not execute the
page JavaScript, so UI assertions check served HTML anchors + the JSON the JS consumes.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.deps import get_db
from backend.app.config.settings import _normalize_db_url
from backend.app.main import api
from backend.app.persistence.database import Base, ExecutionRun
from backend.app.services.adapter_registry import AdapterRegistry
from backend.app.services.scenarios import ScenarioService
from backend.app.ui import decision_state, demo_state
from backend.app.ui.port_decision_seed import (
    SCENARIO_BUNKER_PRICE, is_sqlite, seed_port_decision,
)
from backend.app.ui.port_seed import BUNKER_PRICE
from tests.demo_support import db_override, seed_demo

EXP_FUEL_COST_DELTA = 3_000_000.0
EXP_COST_PER_TEU_DELTA = 0.15


def _sqlite_engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture
def decision_client():
    eng = _sqlite_engine()
    SF = sessionmaker(bind=eng)
    db = SF()
    cfg = seed_port_decision(db)
    db.commit()
    db.close()
    api.dependency_overrides[get_db] = db_override(SF)
    with TestClient(api, raise_server_exceptions=True) as client:
        orig_dec, orig_demo = decision_state.config, demo_state.config
        decision_state.config = cfg
        try:
            yield client, cfg
        finally:
            decision_state.config, demo_state.config = orig_dec, orig_demo
    api.dependency_overrides.clear()
    eng.dispose()


# ===========================================================================
# Guarded seed
# ===========================================================================

class TestDecisionSeed:
    def test_seed_creates_baseline_and_scenario(self):
        eng = _sqlite_engine()
        db = sessionmaker(bind=eng)()
        cfg = seed_port_decision(db)
        assert cfg["baseline_id"] and cfg["scenario_id"]
        assert cfg["baseline_run_id"] and cfg["scenario_run_id"]
        assert cfg["decision_output_dataset_id"]
        assert cfg["overrides"] and cfg["overrides"][0]["field_name"] == BUNKER_PRICE
        db.close(); eng.dispose()

    def test_seed_is_idempotent(self):
        eng = _sqlite_engine()
        db = sessionmaker(bind=eng)()
        first = seed_port_decision(db); db.commit()
        second = seed_port_decision(db); db.commit()
        assert first["baseline_id"] == second["baseline_id"]
        assert first["scenario_id"] == second["scenario_id"]
        assert first["baseline_run_id"] == second["baseline_run_id"]
        assert first["scenario_run_id"] == second["scenario_run_id"]
        db.close(); eng.dispose()

    def test_seed_noop_when_scenario_tables_absent(self):
        # A DB where the D19 tables were never created must safely no-op (no writes).
        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
        # Create only the pre-D19 core tables, not baseline/scenario/scenario_override.
        only = [t for n, t in Base.metadata.tables.items()
                if n not in ("baseline", "scenario", "scenario_override")]
        Base.metadata.create_all(bind=eng, tables=only)
        db = sessionmaker(bind=eng)()
        assert seed_port_decision(db) == {}
        db.close(); eng.dispose()

    def test_is_sqlite_true_for_sqlite(self):
        eng = _sqlite_engine()
        db = sessionmaker(bind=eng)()
        assert is_sqlite(db) is True
        db.close(); eng.dispose()


# ===========================================================================
# /api/decision-config
# ===========================================================================

class TestDecisionConfig:
    def test_decision_config_returns_ids(self, decision_client):
        client, cfg = decision_client
        data = client.get("/api/decision-config").json()
        assert data["baseline_id"] == cfg["baseline_id"]
        assert data["scenario_id"] == cfg["scenario_id"]
        assert data["decision_output_dataset_id"] == cfg["decision_output_dataset_id"]
        assert {"models", "datasets", "overrides", "assumptions_dataset_id"} <= set(data)

    def test_decision_config_empty_when_unseeded(self, decision_client):
        client, _ = decision_client
        orig = decision_state.config
        decision_state.config = {}
        try:
            assert client.get("/api/decision-config").json() == {}
        finally:
            decision_state.config = orig


# ===========================================================================
# Decision workspace HTML (anchors only — JS is not executed by TestClient)
# ===========================================================================

class TestDecisionUIPage:
    def test_page_loads(self, decision_client):
        client, _ = decision_client
        res = client.get("/ui")
        assert res.status_code == 200 and "text/html" in res.headers["content-type"]

    def test_page_has_decision_sections(self, decision_client):
        client, _ = decision_client
        html = client.get("/ui").text
        for anchor in ('id="decision-overview"', "Decision overview", 'id="comparison-section"',
                       "Scenario comparison", 'id="kpi-cards"', 'id="impact-path-section"',
                       'id="assumptions-section"', 'id="why-section"', "/api/decision-config"):
            assert anchor in html, anchor

    def test_page_retains_legacy_anchors(self, decision_client):
        """Regression: D12/D14 anchors must survive the redesign."""
        client, _ = decision_client
        html = client.get("/ui").text
        for anchor in ("Federation graph", 'id="graph-container"', 'id="model-detail"',
                       'id="impact-content"', "Impact summary", 'id="results-section"',
                       'id="lineage-section"', "Lineage", ".affected",
                       "dataset_values", "input_dataset_id"):
            assert anchor in html, anchor
        assert "version_to_output_dataset" not in html


# ===========================================================================
# Data source: D19 comparison API drives the UI (values from API, not hard-coded)
# ===========================================================================

class TestComparisonDataSource:
    def _metrics(self, client, cfg):
        cmp = client.get(f"/api/scenarios/{cfg['scenario_id']}/comparison").json()
        out = cfg["decision_output_dataset_id"]
        return {m["field"]: m for m in cmp["metrics"] if m["dataset_id"] == out}

    def test_acceptance_deltas_available_from_api(self, decision_client):
        client, cfg = decision_client
        m = self._metrics(client, cfg)
        assert m["annual_fuel_cost_usd"]["absolute_delta"] == pytest.approx(EXP_FUEL_COST_DELTA)
        assert m["fuel_cost_per_teu"]["absolute_delta"] == pytest.approx(EXP_COST_PER_TEU_DELTA)
        assert m["annual_fuel_cost_usd"]["changed"] is True

    def test_unaffected_metrics_zero_delta(self, decision_client):
        client, cfg = decision_client
        m = self._metrics(client, cfg)
        for field in ("annual_teu", "annual_emissions_tco2", "emissions_per_teu",
                      "berth_utilization_pct"):
            assert m[field]["absolute_delta"] == pytest.approx(0.0)
            assert m[field]["changed"] is False

    def test_metrics_carry_units(self, decision_client):
        client, cfg = decision_client
        m = self._metrics(client, cfg)
        assert m["annual_fuel_cost_usd"]["unit"] == "USD/year"

    def test_overrides_endpoint_exposes_bunker(self, decision_client):
        client, cfg = decision_client
        overrides = client.get(f"/api/scenarios/{cfg['scenario_id']}/overrides").json()
        assert any(o["field_name"] == BUNKER_PRICE for o in overrides)


# ===========================================================================
# ExecutionRunOut.scenario_id (read-only addition)
# ===========================================================================

class TestExecutionScenarioId:
    def test_scenario_run_carries_scenario_id(self, decision_client):
        client, cfg = decision_client
        runs = client.get("/api/executions?limit=50").json()
        by_id = {r["id"]: r for r in runs}
        assert "scenario_id" in next(iter(by_id.values()))
        assert by_id[cfg["scenario_run_id"]]["scenario_id"] == cfg["scenario_id"]
        assert by_id[cfg["baseline_run_id"]]["scenario_id"] is None


# ===========================================================================
# Human-readable resolution data is available (no UUID-only UI)
# ===========================================================================

class TestNameResolution:
    def test_datasets_expose_names_units_current_value(self, decision_client):
        client, cfg = decision_client
        datasets = client.get("/api/datasets").json()
        assumptions = next(d for d in datasets if d["id"] == cfg["assumptions_dataset_id"])
        assert assumptions["name"]
        assert isinstance(assumptions["current_value"], dict)
        assert assumptions["current_value"][BUNKER_PRICE] != SCENARIO_BUNKER_PRICE  # baseline untouched
        assert any(f["unit"] for f in assumptions["contract_fields"])

    def test_decision_config_models_have_readable_names(self, decision_client):
        client, cfg = decision_client
        data = client.get("/api/decision-config").json()
        assert all(m.get("name") and m.get("version_id") for m in data["models"])


# ===========================================================================
# PostgreSQL (skipped unless TEST_DATABASE_URL is set; rolled back — DB untouched)
# ===========================================================================

_PG_URL = _normalize_db_url(os.environ.get("TEST_DATABASE_URL", ""))


@pytest.mark.skipif(not _PG_URL.startswith("postgresql"), reason="TEST_DATABASE_URL not set")
class TestD20OnPostgreSQL:
    @pytest.fixture()
    def pg_db(self):
        eng = create_engine(_PG_URL, connect_args={"prepare_threshold": None})
        conn = eng.connect()
        trans = conn.begin()
        Base.metadata.create_all(bind=conn)
        session = Session(bind=conn, join_transaction_mode="create_savepoint", autoflush=False)
        yield session
        session.close()
        trans.rollback()
        conn.close()
        eng.dispose()

    def test_decision_seed_noops_on_postgres(self, pg_db):
        # Safety invariant: startup seed must never create demo data on a shared PG database.
        assert is_sqlite(pg_db) is False
        assert seed_port_decision(pg_db) == {}

    def test_scenario_run_scenario_id_on_pg(self, pg_db):
        from backend.app.ui.port_seed import seed_port_domain
        port = seed_port_domain(pg_db)
        svc = ScenarioService(pg_db, AdapterRegistry(pg_db))
        b = svc.create_baseline(name="B", target_version_id=port["terminal_version_id"])
        svc.execute_baseline(b.id)
        s = svc.create_scenario(baseline_id=b.id, name="S")
        svc.set_override(s.id, port["assumptions_dataset_id"], BUNKER_PRICE, SCENARIO_BUNKER_PRICE)
        go = svc.execute_scenario(s.id)
        run = pg_db.query(ExecutionRun).filter_by(id=go.run_id).one()
        assert run.scenario_id == s.id
