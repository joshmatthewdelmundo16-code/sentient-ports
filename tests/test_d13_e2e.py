"""D13 — End-to-end demo scenario through the real HTTP API (updated for D16).

API → Change Propagation (D9) → GraphRun orchestration (D8) → dataset-aware step
execution (D7) → persisted synthetic adapters (D6) → results + lineage (D10) →
dependency ordering (D5), on the production demo seed.

Scenario (tests run in order and share one database):
  Initial run  (fuel_price_input = 100):  100 → 200 → 300 → 150
  Propagation  (fuel_price_input = 120):  120 → 240 → 360 → 180
  Re-submitting 120 is a no-op (no new ChangeEvent, no new run).
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


class TestDemoScenarioE2E:
    state: dict = {}

    @pytest.fixture(scope="class", autouse=True)
    def _setup(self):
        eng = make_engine()
        SF = sessionmaker(bind=eng)
        cfg = seed_demo(SF)
        api.dependency_overrides[get_db] = db_override(SF)
        type(self).state = {"cfg": cfg, "vids": version_ids(cfg)}
        yield
        api.dependency_overrides.clear()
        eng.dispose()

    @property
    def cfg(self):
        return self.state["cfg"]

    @property
    def vids(self):
        return self.state["vids"]

    def _client(self):
        return TestClient(api, raise_server_exceptions=True)

    def _values(self, client, result_ids):
        by_version = {}
        for rid in result_ids:
            r = client.get(f"/api/results/{rid}").json()
            by_version[r["model_version_id"]] = json.loads(r["value_json"])["value"]
        return [by_version[v] for v in self.vids]

    # ── Topology ────────────────────────────────────────────────────────────

    def test_e2e_01_models_registered(self):
        with self._client() as client:
            names = {m["name"] for m in client.get("/api/models").json()}
        assert names == {"Fuel price", "Shipping cost", "Operations cost", "Emissions"}

    def test_e2e_02_graph_topology_is_deterministic(self):
        with self._client() as client:
            g = client.get("/api/graph").json()
        assert g["execution_order"] == self.vids
        assert len(g["dependencies"]) == 3

    # ── Initial execution ───────────────────────────────────────────────────

    def test_e2e_03_initial_graph_execution(self):
        with self._client() as client:
            res = client.post("/api/graph-executions", json=run_body(self.cfg, 100))
            data = res.json()
            assert res.status_code == 201 and data["success"] is True
            self.state["run1"] = data
            assert self._values(client, data["recorded_result_ids"]) == [100.0, 200.0, 300.0, 150.0]

    def test_e2e_04_one_graph_run_with_four_steps(self):
        run_id = self.state["run1"]["graph_run_id"]
        with self._client() as client:
            detail = client.get(f"/api/executions/{run_id}").json()
            history = client.get("/api/executions?limit=20").json()
        assert detail["run"]["run_kind"] == "graph"
        assert detail["run"]["status"] == "succeeded"
        assert [s["model_version_id"] for s in detail["steps"]] == self.vids
        assert all(s["status"] == "succeeded" for s in detail["steps"])
        assert [r["id"] for r in history] == [run_id]

    def test_e2e_05_results_and_lineage_belong_to_the_run(self):
        run_id = self.state["run1"]["graph_run_id"]
        with self._client() as client:
            results = client.get(f"/api/executions/{run_id}/results").json()
            lineage = client.get(f"/api/executions/{run_id}/lineage").json()
        assert len(results) == 4
        # fuel_price_input → Fuel Price, then three model-to-model edges
        assert len(lineage) == 4
        assert {e["run_id"] for e in lineage} == {run_id}

    def test_e2e_06_datasets_published(self):
        with self._client() as client:
            datasets = {d["name"]: d for d in client.get("/api/datasets").json()}
        assert datasets["fuel_price_input"]["current_value"] == {"value": 100.0}
        assert datasets["emissions_output"]["current_value"] == {"value": 150.0}
        assert datasets["emissions_output"]["contract_semver"] == "1.0.0"

    # ── Change propagation ──────────────────────────────────────────────────

    def test_e2e_07_propagation_updates_full_chain(self):
        with self._client() as client:
            res = client.post("/api/changes/propagate", json=propagate_body(self.cfg, 120))
            data = res.json()
            assert res.status_code == 201
            assert data["changed"] is True and data["success"] is True
            assert data["old_value"] == {"value": 100.0} and data["new_value"] == {"value": 120.0}
            assert data["execution_order"] == self.vids
            assert self._values(client, data["recorded_result_ids"]) == [120.0, 240.0, 360.0, 180.0]
        self.state["prop1"] = data

    def test_e2e_08_duplicate_propagation_is_a_no_op(self):
        with self._client() as client:
            res = client.post("/api/changes/propagate", json=propagate_body(self.cfg, 120))
            data = res.json()
            history = client.get("/api/executions?limit=20").json()
        assert res.status_code == 200
        assert data["changed"] is False
        assert data["graph_run_id"] is None
        assert len(history) == 2  # initial run + one propagation run

    # ── UI layer ────────────────────────────────────────────────────────────

    def test_e2e_09_ui_page_loads(self):
        with self._client() as client:
            res = client.get("/ui")
        assert res.status_code == 200
        assert "Federated model orchestration" in res.text

    def test_e2e_10_demo_config_matches_seed(self):
        with self._client() as client:
            orig = demo_state.config
            demo_state.config = self.cfg
            try:
                cfg = client.get("/api/demo-config").json()
            finally:
                demo_state.config = orig
        assert cfg["terminal_version_id"] == self.vids[-1]
        assert cfg["fuel_price_version_id"] == self.vids[0]
        assert len(cfg["models"]) == 4
