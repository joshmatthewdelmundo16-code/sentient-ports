"""D12 — Browser UI tests.

Tests the /ui HTML page and the /api/demo-config endpoint, plus end-to-end
integration covering the demo graph execution and propagation flow.

All test classes use an isolated in-memory SQLite database (StaticPool) seeded by
the production demo seed; execution resolves adapters from persisted configuration.

Acceptance criteria tested:
  AC-01  /ui page loads (200 OK, HTML content)
  AC-02  Models can be rendered from API data
  AC-03  Graph data can be rendered
  AC-04  Execute action calls the D11 graph-execution endpoint
  AC-05  Results are available after execution
  AC-06  Lineage is returned by D11
  AC-07  Change/propagation action calls the correct D11 endpoint
  AC-08  New execution/results are available after propagation
  AC-09  API failure is surfaced via standard JSON error (not a crash)
  AC-11  /api/demo-config returns expected structure
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.app.api.deps import get_db
from backend.app.main import api
from backend.app.ui import demo_state
from tests.demo_support import (
    db_override, make_engine, propagate_body, run_body, seed_demo, version_ids,
)

_NAMES = {"Fuel Price", "Shipping Cost", "Operations Cost", "Emissions"}


@pytest.fixture
def demo():
    eng = make_engine()
    SF = sessionmaker(bind=eng)
    cfg = seed_demo(SF)
    api.dependency_overrides[get_db] = db_override(SF)
    orig = demo_state.config
    demo_state.config = cfg
    yield cfg
    demo_state.config = orig
    api.dependency_overrides.clear()
    eng.dispose()


def _client():
    return TestClient(api, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# AC-01 / AC-11  UI page and demo-config
# ---------------------------------------------------------------------------

class TestUIPage:
    def test_ui_page_loads(self, demo):
        with _client() as client:
            res = client.get("/ui")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    def test_ui_page_has_nav_tabs(self, demo):
        with _client() as client:
            html = client.get("/ui").text
        for word in ("Federation", "Graph", "Execute", "Propagate", "Executions"):
            assert word in html

    def test_ui_page_has_demo_scripts(self, demo):
        with _client() as client:
            html = client.get("/ui").text
        assert "<script>" in html
        assert "API.get" in html

    def test_demo_config_structure(self, demo):
        with _client() as client:
            data = client.get("/api/demo-config").json()
        for key in ("models", "terminal_version_id", "fuel_price_version_id",
                    "input_dataset_id", "input_field"):
            assert key in data
        # The client no longer receives (or needs) a version→dataset routing map.
        assert "version_to_output_dataset" not in data

    def test_demo_config_has_four_chain_models(self, demo):
        with _client() as client:
            data = client.get("/api/demo-config").json()
        assert {m["name"] for m in data["models"]} == _NAMES


# ---------------------------------------------------------------------------
# AC-02 / AC-03  Models and graph from API data
# ---------------------------------------------------------------------------

class TestModelsAndGraphFromAPI:
    def test_models_api_returns_four_models(self, demo):
        with _client() as client:
            models = client.get("/api/models").json()
        assert {m["name"] for m in models} == _NAMES

    def test_model_versions_have_persisted_adapter(self, demo):
        with _client() as client:
            for m in client.get("/api/models").json():
                (v,) = client.get(f"/api/models/{m['id']}/versions").json()
                assert v["semver"] == "1.0.0" and v["is_active"] is True
                assert v["adapter_type"] == "synthetic"
                assert "scalar" in json.loads(v["adapter_config"])

    def test_graph_api_returns_execution_order(self, demo):
        with _client() as client:
            data = client.get("/api/graph").json()
        order = data["execution_order"]
        vids = version_ids(demo)
        assert [v for v in order if v in vids] == vids
        assert len(data["dependencies"]) == 3


# ---------------------------------------------------------------------------
# AC-04 / AC-05  Execute and results
# ---------------------------------------------------------------------------

class TestGraphExecution:
    def test_graph_execution_succeeds(self, demo):
        with _client() as client:
            res = client.post("/api/graph-executions", json=run_body(demo, 100))
        assert res.status_code == 201
        data = res.json()
        assert data["success"] is True
        assert data["status"] == "succeeded"
        assert data["execution_order"] == version_ids(demo)

    def test_graph_execution_produces_correct_output_values(self, demo):
        with _client() as client:
            data = client.post("/api/graph-executions", json=run_body(demo, 100)).json()
        values = [data["step_outcomes"][v]["outputs"]["value"] for v in version_ids(demo)]
        assert values == [100.0, 200.0, 300.0, 150.0]

    def test_graph_execution_persists_results(self, demo):
        with _client() as client:
            outcome = client.post("/api/graph-executions", json=run_body(demo, 100)).json()
            results = client.get(f"/api/executions/{outcome['graph_run_id']}/results").json()
        assert len(results) == 4
        assert sorted(r["id"] for r in results) == sorted(outcome["recorded_result_ids"])

    def test_execution_history_lists_one_graph_run(self, demo):
        with _client() as client:
            outcome = client.post("/api/graph-executions", json=run_body(demo, 100)).json()
            history = client.get("/api/executions?limit=10").json()
        assert [r["id"] for r in history] == [outcome["graph_run_id"]]
        assert history[0]["run_kind"] == "graph"


# ---------------------------------------------------------------------------
# AC-06  Lineage
# ---------------------------------------------------------------------------

class TestLineage:
    def test_lineage_available_for_persisted_result(self, demo):
        with _client() as client:
            outcome = client.post("/api/graph-executions", json=run_body(demo, 100)).json()
            results = client.get(f"/api/executions/{outcome['graph_run_id']}/results").json()
            emissions = next(r for r in results if r["model_version_id"] == demo["terminal_version_id"])
            lin = client.get(f"/api/lineage/{emissions['id']}").json()
        assert lin["result_id"] == emissions["id"]
        assert len(lin["edges_to"]) == 1
        assert lin["edges_to"][0]["run_id"] == outcome["graph_run_id"]


# ---------------------------------------------------------------------------
# AC-07 / AC-08  Propagation
# ---------------------------------------------------------------------------

class TestPropagation:
    def test_propagation_succeeds(self, demo):
        with _client() as client:
            res = client.post("/api/changes/propagate", json=propagate_body(demo, 120))
        assert res.status_code == 201
        data = res.json()
        assert data["success"] is True and data["changed"] is True

    def test_propagation_affects_all_downstream(self, demo):
        with _client() as client:
            data = client.post("/api/changes/propagate", json=propagate_body(demo, 120)).json()
        assert data["execution_order"] == version_ids(demo)

    def test_propagation_produces_updated_values(self, demo):
        with _client() as client:
            data = client.post("/api/changes/propagate", json=propagate_body(demo, 120)).json()
            values = {}
            for rid in data["recorded_result_ids"]:
                r = client.get(f"/api/results/{rid}").json()
                values[r["model_version_id"]] = json.loads(r["value_json"])["value"]
        assert [values[v] for v in version_ids(demo)] == [120.0, 240.0, 360.0, 180.0]


# ---------------------------------------------------------------------------
# AC-09  API failure surfaced cleanly
# ---------------------------------------------------------------------------

class TestAPIErrors:
    def test_unknown_model_returns_404(self, demo):
        with TestClient(api, raise_server_exceptions=False) as client:
            res = client.get("/api/models/nonexistent-id")
        assert res.status_code == 404
        assert "detail" in res.json()

    def test_unknown_result_returns_404(self, demo):
        with TestClient(api, raise_server_exceptions=False) as client:
            assert client.get("/api/results/nonexistent-id").status_code == 404

    def test_graph_execution_with_unknown_target_returns_404(self, demo):
        with TestClient(api, raise_server_exceptions=False) as client:
            res = client.post("/api/graph-executions", json={"target_version_id": "nonexistent"})
        assert res.status_code == 404

    def test_demo_config_empty_when_not_seeded(self, demo):
        with _client() as client:
            orig = demo_state.config
            demo_state.config = {}
            try:
                res = client.get("/api/demo-config")
            finally:
                demo_state.config = orig
        assert res.status_code == 200
        assert res.json() == {}
