"""D23 — Decision-Support Product UI completion tests.

D23 is a UI/product-completion phase built on existing APIs. TestClient does not run page
JavaScript, so UI assertions check served HTML structure/anchors, and the "product workflow"
is validated through the same JSON endpoints the page consumes (decision-config → scenarios →
comparison → executions → governance/exposed-outputs). Real SQLite; no mocks; no fabricated
data (every business number is read back from the API).
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d23_tmp")

from backend.app.api.deps import get_db  # noqa: E402
from backend.app.main import api  # noqa: E402
from backend.app.persistence.database import Base  # noqa: E402
from backend.app.ui import decision_state, demo_state  # noqa: E402
from backend.app.ui.port_decision_seed import seed_port_decision  # noqa: E402


def _engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    return eng


def _db_override(SF):
    def _get_db():
        s = SF()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
    return _get_db


@pytest.fixture
def decision_client():
    eng = _engine()
    SF = sessionmaker(bind=eng)
    db = SF()
    cfg = seed_port_decision(db)
    db.commit()
    db.close()
    api.dependency_overrides[get_db] = _db_override(SF)
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
# Product structure — new D23 sections + navigation
# ===========================================================================

class TestProductStructure:
    def test_page_loads(self, decision_client):
        client, _ = decision_client
        res = client.get("/ui")
        assert res.status_code == 200 and "text/html" in res.headers["content-type"]

    def test_has_product_navigation(self, decision_client):
        client, _ = decision_client
        html = client.get("/ui").text
        assert 'id="product-nav"' in html
        for label in ("Overview", "Comparison", "Impact &amp; why",
                      "Sources &amp; lineage", "Execution &amp; governance"):
            assert label in html, label

    def test_has_new_d23_sections(self, decision_client):
        client, _ = decision_client
        html = client.get("/ui").text
        for anchor in ('id="sources-section"', "Sources &amp; provenance",
                       'id="execution-governance-section"', "Execution &amp; governance",
                       'id="exec-readable-content"', 'id="governance-panel-content"',
                       'id="sources-content"'):
            assert anchor in html, anchor

    def test_technical_zone_relegated(self, decision_client):
        client, _ = decision_client
        html = client.get("/ui").text
        # Technical detail is anchored/relegated after the product workflow.
        assert 'id="advanced-zone"' in html
        assert html.index('id="execution-governance-section"') < html.index('id="advanced-zone"')

    def test_loading_and_empty_state_hooks_present(self, decision_client):
        client, _ = decision_client
        html = client.get("/ui").text
        assert "Loading sources" in html and "Loading executions" in html
        # Empty/placeholder + error rendering paths exist in the client code.
        assert "placeholder" in html and "alert alert-error" in html

    def test_governance_link_present(self, decision_client):
        client, _ = decision_client
        assert "/ui/governance" in client.get("/ui").text


# ===========================================================================
# Backward compatibility — D12/D14/D20 anchors must survive
# ===========================================================================

class TestLegacyAnchorsPreserved:
    def test_d20_decision_anchors(self, decision_client):
        client, _ = decision_client
        html = client.get("/ui").text
        for anchor in ('id="decision-overview"', "Decision overview", 'id="comparison-section"',
                       "Scenario comparison", 'id="kpi-cards"', 'id="impact-path-section"',
                       'id="assumptions-section"', 'id="why-section"', "/api/decision-config"):
            assert anchor in html, anchor

    def test_d12_d14_legacy_anchors(self, decision_client):
        client, _ = decision_client
        html = client.get("/ui").text
        for anchor in ("Federation graph", 'id="graph-container"', 'id="model-detail"',
                       'id="impact-content"', "Impact summary", 'id="results-section"',
                       'id="lineage-section"', "Lineage", ".affected",
                       "dataset_values", "input_dataset_id"):
            assert anchor in html, anchor
        assert "version_to_output_dataset" not in html

    def test_d12_word_anchors(self, decision_client):
        client, _ = decision_client
        html = client.get("/ui").text
        for word in ("Federation", "Graph", "Execute", "Propagate", "executions"):
            assert word in html


# ===========================================================================
# Decision workflow — API smoke (the data the product page consumes)
# ===========================================================================

class TestDecisionWorkflowSmoke:
    def test_full_workflow_endpoints(self, decision_client):
        client, cfg = decision_client
        # decision-config
        dc = client.get("/api/decision-config").json()
        assert dc["scenario_id"] == cfg["scenario_id"]
        # scenario + baseline
        scen = client.get(f"/api/scenarios/{cfg['scenario_id']}").json()
        base = client.get(f"/api/baselines/{cfg['baseline_id']}").json()
        assert scen["scenario_run_id"] and base["baseline_run_id"]
        # comparison drives KPI cards / comparison table / variance
        cmp = client.get(f"/api/scenarios/{cfg['scenario_id']}/comparison").json()
        assert cmp["metrics"], "comparison must yield metrics"
        # overrides drive the assumption-change panel
        ov = client.get(f"/api/scenarios/{cfg['scenario_id']}/overrides").json()
        assert len(ov) >= 1

    def test_execution_history_readable_context(self, decision_client):
        client, cfg = decision_client
        runs = client.get("/api/executions?limit=20").json()
        run_ids = {r["id"] for r in runs}
        # Both the baseline and scenario runs are present for the readable exec cards.
        assert cfg["baseline_run_id"] in run_ids
        assert cfg["scenario_run_id"] in run_ids
        # Executor field (in-process vs airflow) is available for the card.
        assert all("executor" in r for r in runs)

    def test_scenario_run_flagged_and_isolated(self, decision_client):
        client, cfg = decision_client
        runs = {r["id"]: r for r in client.get("/api/executions?limit=20").json()}
        scen_run = runs[cfg["scenario_run_id"]]
        # D19 isolation preserved: scenario run carries scenario_id; baseline does not.
        assert scen_run["scenario_id"] == cfg["scenario_id"]
        assert runs[cfg["baseline_run_id"]]["scenario_id"] is None

    def test_sources_provenance_available(self, decision_client):
        client, cfg = decision_client
        events = client.get(f"/api/datasets/{cfg['assumptions_dataset_id']}/change-events").json()
        # Real provenance exists for the baseline assumptions (seeded source).
        assert events and events[0]["source_type"]

    def test_numbers_come_from_api_not_fabricated(self, decision_client):
        client, cfg = decision_client
        cmp = client.get(f"/api/scenarios/{cfg['scenario_id']}/comparison").json()
        out = cfg["decision_output_dataset_id"]
        m = {x["field"]: x for x in cmp["metrics"] if x["dataset_id"] == out}
        # The documented acceptance delta is produced by the domain, surfaced via API.
        assert m["annual_fuel_cost_usd"]["absolute_delta"] == pytest.approx(3_000_000.0)
        assert m["annual_fuel_cost_usd"]["changed"] is True


# ===========================================================================
# Governance visibility (D22 data surfaced in the decision product)
# ===========================================================================

class TestGovernanceVisibility:
    def test_scenario_outputs_not_auto_approved(self, decision_client):
        client, _ = decision_client
        # After seeding baseline+scenario, nothing is approved automatically.
        summary = client.get("/api/governance/summary").json()
        assert summary["effective_approvals"] == 0
        assert client.get("/api/exposed-outputs").json() == []

    def test_approved_output_surfaces_real_value(self, decision_client):
        client, cfg = decision_client
        out_ds = cfg["decision_output_dataset_id"]
        # Register a participant and approve one real decision-output field.
        pid = client.post("/api/participants",
                          json={"participant_key": "port-authority", "name": "Port Authority"}).json()["id"]
        r = client.post("/api/approved-outputs",
                        json={"participant_id": pid, "dataset_id": out_ds,
                              "field_name": "annual_fuel_cost_usd", "purpose": "board report"})
        assert r.status_code == 201
        exposed = client.get("/api/exposed-outputs").json()
        assert len(exposed) == 1
        row = exposed[0]
        assert row["participant_key"] == "port-authority"
        assert row["field_name"] == "annual_fuel_cost_usd"
        # The exposed value is the real published baseline value (not fabricated).
        assert row["value_present"] is True and isinstance(row["value"], (int, float))

    def test_summary_endpoint_shape(self, decision_client):
        client, _ = decision_client
        s = client.get("/api/governance/summary").json()
        for k in ("participants", "effective_approvals", "revoked_approvals"):
            assert k in s


# ===========================================================================
# Empty-state behavior when no decision demo is loaded
# ===========================================================================

class TestEmptyState:
    def test_decision_config_empty_renders_gracefully(self, decision_client):
        client, _ = decision_client
        orig = decision_state.config
        decision_state.config = {}
        try:
            assert client.get("/api/decision-config").json() == {}
            # Page still serves (JS renders the empty state client-side).
            assert client.get("/ui").status_code == 200
        finally:
            decision_state.config = orig
