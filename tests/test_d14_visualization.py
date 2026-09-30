"""D14 — Client-Grade Federation Visualization tests (updated for D16).

Checks that the UI renders, calls the correct endpoints, and that the data those
views display (graph, results, lineage, impact) is served by the API. Uses an
isolated in-memory SQLite database seeded by the production demo seed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.app.api.deps import get_db
from backend.app.main import api
from backend.app.ui import demo_state
from tests.demo_support import (
    db_override, make_engine, propagate_body, run_body, seed_demo, version_ids,
)


@pytest.fixture()
def seeded_client():
    eng = make_engine()
    SF = sessionmaker(bind=eng)
    cfg = seed_demo(SF)
    api.dependency_overrides[get_db] = db_override(SF)
    with TestClient(api, raise_server_exceptions=True) as client:
        # Set AFTER startup, which seeds the app database and overwrites demo_state.
        orig = demo_state.config
        demo_state.config = cfg
        try:
            yield client, cfg
        finally:
            demo_state.config = orig
    api.dependency_overrides.clear()
    eng.dispose()


class TestD14UIPage:
    def test_page_loads_200(self, seeded_client):
        client, _ = seeded_client
        res = client.get("/ui")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    def test_page_contains_federation_title(self, seeded_client):
        client, _ = seeded_client
        assert "Federated model orchestration" in client.get("/ui").text

    def test_page_uses_dataset_driven_api(self, seeded_client):
        client, _ = seeded_client
        html = client.get("/ui").text
        assert "dataset_values" in html          # graph run writes the source dataset
        assert "input_dataset_id" in html        # propagation targets the source dataset
        assert "version_to_output_dataset" not in html


class TestD14FederationSummary:
    def test_page_has_graph_section(self, seeded_client):
        client, _ = seeded_client
        assert "Federation graph" in client.get("/ui").text

    def test_models_endpoint_returns_4(self, seeded_client):
        client, _ = seeded_client
        assert len(client.get("/api/models").json()) == 4

    def test_demo_config_has_chain(self, seeded_client):
        client, cfg = seeded_client
        data = client.get("/api/demo-config").json()
        assert len(data["models"]) == 4
        assert data["terminal_version_id"] == cfg["terminal_version_id"]


class TestD14Graph:
    def test_graph_has_4_nodes_3_edges(self, seeded_client):
        client, _ = seeded_client
        g = client.get("/api/graph").json()
        assert len(g["execution_order"]) == 4
        assert len(g["dependencies"]) == 3

    def test_graph_deterministic_order(self, seeded_client):
        client, cfg = seeded_client
        assert client.get("/api/graph").json()["execution_order"] == version_ids(cfg)

    def test_page_has_graph_container(self, seeded_client):
        client, _ = seeded_client
        assert 'id="graph-container"' in client.get("/ui").text


class TestD14ModelDetail:
    def test_page_has_model_detail_container(self, seeded_client):
        client, _ = seeded_client
        assert 'id="model-detail"' in client.get("/ui").text

    def test_model_versions_available(self, seeded_client):
        client, _ = seeded_client
        for m in client.get("/api/models").json():
            vs = client.get(f"/api/models/{m['id']}/versions").json()
            assert len(vs) == 1 and vs[0]["is_active"] is True


class TestD14Execute:
    def test_graph_execution_succeeds(self, seeded_client):
        client, cfg = seeded_client
        res = client.post("/api/graph-executions", json=run_body(cfg, 100))
        assert res.status_code == 201
        data = res.json()
        assert data["success"] is True
        assert len(data["execution_order"]) == 4
        assert len(data["recorded_result_ids"]) == 4

    def test_execution_output_values(self, seeded_client):
        client, cfg = seeded_client
        data = client.post("/api/graph-executions", json=run_body(cfg, 100)).json()
        vals = [data["step_outcomes"][v]["outputs"]["value"] for v in version_ids(cfg)]
        assert vals == [100.0, 200.0, 300.0, 150.0]


class TestD14Propagation:
    def test_propagation_succeeds(self, seeded_client):
        client, cfg = seeded_client
        res = client.post("/api/changes/propagate", json=propagate_body(cfg, 120))
        assert res.status_code == 201
        data = res.json()
        assert data["success"] is True
        assert len(data["recorded_result_ids"]) == 4

    def test_propagation_returns_affected_versions(self, seeded_client):
        client, cfg = seeded_client
        data = client.post("/api/changes/propagate", json=propagate_body(cfg, 120)).json()
        assert data["affected_version_ids"] == sorted(version_ids(cfg))
        assert data["execution_order"] == version_ids(cfg)

    def test_propagation_has_all_impact_fields(self, seeded_client):
        client, cfg = seeded_client
        data = client.post("/api/changes/propagate", json=propagate_body(cfg, 120)).json()
        for key in ("success", "changed", "old_value", "new_value", "affected_version_ids",
                    "execution_order", "recorded_result_ids", "change_event_id", "graph_run_id"):
            assert key in data

    def test_page_has_affected_css_class(self, seeded_client):
        client, _ = seeded_client
        assert ".affected" in client.get("/ui").text


class TestD14ImpactSummary:
    def test_page_has_impact_panel(self, seeded_client):
        client, _ = seeded_client
        html = client.get("/ui").text
        assert 'id="impact-content"' in html
        assert "Impact summary" in html


class TestD14Results:
    def test_results_retrievable_by_id(self, seeded_client):
        client, cfg = seeded_client
        data = client.post("/api/graph-executions", json=run_body(cfg, 100)).json()
        for rid in data["recorded_result_ids"]:
            r = client.get(f"/api/results/{rid}").json()
            assert r["value_json"] is not None
            assert r["model_version_id"] in version_ids(cfg)

    def test_page_has_results_section(self, seeded_client):
        client, _ = seeded_client
        assert 'id="results-section"' in client.get("/ui").text


class TestD14Lineage:
    def test_lineage_retrievable(self, seeded_client):
        client, cfg = seeded_client
        data = client.post("/api/graph-executions", json=run_body(cfg, 100)).json()
        results = client.get(f"/api/executions/{data['graph_run_id']}/results").json()
        emissions = next(r for r in results if r["model_version_id"] == cfg["terminal_version_id"])
        lin = client.get(f"/api/lineage/{emissions['id']}").json()
        assert lin["result_id"] == emissions["id"]
        assert len(lin["edges_to"]) == 1

    def test_page_has_lineage_section(self, seeded_client):
        client, _ = seeded_client
        html = client.get("/ui").text
        assert 'id="lineage-section"' in html and "Lineage" in html


class TestD14APIFailure:
    def test_invalid_graph_execution_returns_error(self, seeded_client):
        client, _ = seeded_client
        res = client.post("/api/graph-executions", json={"target_version_id": "nonexistent"})
        assert res.status_code == 404

    def test_contract_violation_returns_422(self, seeded_client):
        client, cfg = seeded_client
        res = client.post("/api/changes/propagate", json=propagate_body(cfg, -5))
        assert res.status_code == 422
        assert res.json()["detail"]["violations"][0]["field"] == "value"

    def test_invalid_result_returns_404(self, seeded_client):
        client, _ = seeded_client
        assert client.get("/api/results/nonexistent-id").status_code == 404

    def test_page_still_loads_after_bad_request(self, seeded_client):
        client, _ = seeded_client
        client.post("/api/graph-executions", json={"target_version_id": "bad"})
        assert client.get("/ui").status_code == 200


class TestD14Regression:
    def test_health_endpoint(self, seeded_client):
        client, _ = seeded_client
        res = client.get("/health")
        assert res.status_code == 200 and res.json()["ok"] is True

    def test_demo_config_endpoint(self, seeded_client):
        client, _ = seeded_client
        res = client.get("/api/demo-config")
        assert res.status_code == 200 and "models" in res.json()
