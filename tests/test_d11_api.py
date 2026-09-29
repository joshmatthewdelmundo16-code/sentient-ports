"""
D11 API / Application Layer tests.

Uses FastAPI TestClient with per-class isolated in-memory SQLite engines.
Each test class seeds its own data and overrides get_db / get_adapter_registry.
No mocks — real service layer, real SQLite.

Key fixture pattern: IDs are captured as plain strings before closing the seed
session (after commit, SQLAlchemy expires attributes; after close they can't refresh).
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d11_tmp")

from backend.app.api.deps import get_adapter_registry  # noqa: E402
from backend.app.adapters.synthetic import SyntheticAdapter  # noqa: E402
from backend.app.main import api  # noqa: E402
from backend.app.persistence.database import (  # noqa: E402
    Base,
    DataContract,
    Dataset,
    Dependency,
    Model,
    ModelVersion,
    Result,
    get_db,
)
from backend.app.services.adapter_registry import AdapterRegistry  # noqa: E402
from backend.app.services.federation import FederationService  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ctr = {"n": 0}


def _uid(prefix: str = "") -> str:
    _ctr["n"] += 1
    return f"{prefix}{_ctr['n']}"


def _make_engine():
    # StaticPool: all sessions share one in-memory connection so the schema
    # created by create_all() is visible to every test request.
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    return eng


def _make_sf(eng):
    return sessionmaker(bind=eng, autocommit=False, autoflush=False)


def _db_override(SF):
    """FastAPI get_db override: commit on success, rollback on error."""
    def _get_db():
        db = SF()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    return _get_db


# Seed helpers — return ID strings (never rely on detached ORM attrs)

def _seed_model(db: Session, name: str | None = None) -> str:
    m = Model(name=name or _uid("M-"), owner="test", model_type="python")
    db.add(m)
    db.flush()
    return m.id  # read while session open


def _seed_version(db: Session, model_id: str, semver: str = "1.0.0") -> str:
    v = ModelVersion(model_id=model_id, semver=semver, is_active=True)
    db.add(v)
    db.flush()
    return v.id


def _seed_dataset(db: Session, name: str | None = None) -> str:
    d = Dataset(name=name or _uid("DS-"))
    db.add(d)
    db.flush()
    return d.id


def _seed_dep(db: Session, producer_id: str, consumer_id: str, dataset_id: str) -> str:
    dep = Dependency(
        producer_version_id=producer_id,
        consumer_version_id=consumer_id,
        output_dataset_id=dataset_id,
        input_dataset_id=dataset_id,
    )
    db.add(dep)
    db.flush()
    return dep.id


def _seed_contract(db: Session, dataset_id: str) -> str:
    c = DataContract(
        dataset_id=dataset_id,
        schema_json=json.dumps({"fields": [{"name": "value", "type": "float"}]}),
        semver="1.0.0",
    )
    db.add(c)
    db.flush()
    return c.id


def _build_registry(SF, version_ids_scalars: list[tuple[str, float]]) -> AdapterRegistry:
    """Create an AdapterRegistry with SyntheticAdapters for the given (version_id, scalar) pairs."""
    db = SF()
    reg = AdapterRegistry(db)
    for vid, scalar in version_ids_scalars:
        reg.register_adapter(SyntheticAdapter(_uid("adp-"), vid, scalar=scalar))
    db.commit()
    db.close()
    return reg


# ---------------------------------------------------------------------------
# AC-01: /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_200(self):
        with TestClient(api) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_health_fields(self):
        with TestClient(api) as client:
            data = client.get("/health").json()
        for field in ("ok", "version", "environment", "db", "storage", "timestamp"):
            assert field in data


# ---------------------------------------------------------------------------
# AC-02: Model listing / lookup
# ---------------------------------------------------------------------------

class TestModelsAPI:
    @pytest.fixture(scope="class")
    def ctx(self):
        eng = _make_engine()
        SF = _make_sf(eng)
        db = SF()
        mid = _seed_model(db, name=_uid("APIModel-"))
        vid = _seed_version(db, mid)
        db.commit()
        db.close()

        api.dependency_overrides[get_db] = _db_override(SF)
        yield {"mid": mid, "vid": vid}
        api.dependency_overrides.clear()
        eng.dispose()

    def test_list_models_200(self, ctx):
        with TestClient(api) as c:
            resp = c.get("/api/models")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_models_has_seeded(self, ctx):
        with TestClient(api) as c:
            resp = c.get("/api/models")
        assert any(m["id"] == ctx["mid"] for m in resp.json())

    def test_get_model_200(self, ctx):
        with TestClient(api) as c:
            resp = c.get(f"/api/models/{ctx['mid']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == ctx["mid"]

    def test_get_model_404(self, ctx):
        with TestClient(api) as c:
            assert c.get("/api/models/no-such-id").status_code == 404

    def test_list_versions_200(self, ctx):
        with TestClient(api) as c:
            resp = c.get(f"/api/models/{ctx['mid']}/versions")
        assert resp.status_code == 200
        assert any(v["id"] == ctx["vid"] for v in resp.json())

    def test_list_versions_unknown_model_404(self, ctx):
        with TestClient(api) as c:
            assert c.get("/api/models/unknown/versions").status_code == 404


# ---------------------------------------------------------------------------
# AC-03: Data Contracts
# ---------------------------------------------------------------------------

class TestContractsAPI:
    @pytest.fixture(scope="class")
    def ctx(self):
        eng = _make_engine()
        SF = _make_sf(eng)
        db = SF()
        dsid = _seed_dataset(db)
        cid = _seed_contract(db, dsid)
        db.commit()
        db.close()

        api.dependency_overrides[get_db] = _db_override(SF)
        yield {"cid": cid}
        api.dependency_overrides.clear()
        eng.dispose()

    def test_list_contracts_200(self, ctx):
        with TestClient(api) as c:
            resp = c.get("/api/contracts")
        assert resp.status_code == 200

    def test_get_contract_200(self, ctx):
        with TestClient(api) as c:
            resp = c.get(f"/api/contracts/{ctx['cid']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == ctx["cid"]

    def test_get_contract_404(self, ctx):
        with TestClient(api) as c:
            assert c.get("/api/contracts/no-such-id").status_code == 404


# ---------------------------------------------------------------------------
# AC-04: Dependency graph
# ---------------------------------------------------------------------------

class TestGraphAPI:
    @pytest.fixture(scope="class")
    def ctx(self):
        eng = _make_engine()
        SF = _make_sf(eng)
        db = SF()
        ma_id = _seed_model(db, name=_uid("GrA-"))
        mb_id = _seed_model(db, name=_uid("GrB-"))
        va_id = _seed_version(db, ma_id)
        vb_id = _seed_version(db, mb_id)
        ds_id = _seed_dataset(db)
        _seed_dep(db, va_id, vb_id, ds_id)
        db.commit()
        db.close()

        api.dependency_overrides[get_db] = _db_override(SF)
        yield {"va_id": va_id, "vb_id": vb_id}
        api.dependency_overrides.clear()
        eng.dispose()

    def test_graph_200(self, ctx):
        with TestClient(api) as c:
            assert c.get("/api/graph").status_code == 200

    def test_graph_has_order(self, ctx):
        with TestClient(api) as c:
            data = c.get("/api/graph").json()
        assert "execution_order" in data
        assert isinstance(data["execution_order"], list)

    def test_graph_a_before_b(self, ctx):
        with TestClient(api) as c:
            order = c.get("/api/graph").json()["execution_order"]
        assert order.index(ctx["va_id"]) < order.index(ctx["vb_id"])

    def test_graph_has_dependency(self, ctx):
        with TestClient(api) as c:
            deps = c.get("/api/graph").json()["dependencies"]
        assert any(d["producer_version_id"] == ctx["va_id"] for d in deps)


# ---------------------------------------------------------------------------
# AC-05: Single-model execution
# ---------------------------------------------------------------------------

class TestSingleExecutionAPI:
    @pytest.fixture(scope="class")
    def ctx(self):
        eng = _make_engine()
        SF = _make_sf(eng)
        db = SF()
        mid = _seed_model(db, name=_uid("ExM-"))
        vid = _seed_version(db, mid)
        dsid = _seed_dataset(db)
        FederationService(db).bind_output(vid, dsid)
        db.commit()
        db.close()

        reg = _build_registry(SF, [(vid, 3.0)])
        api.dependency_overrides[get_db] = _db_override(SF)
        api.dependency_overrides[get_adapter_registry] = lambda: reg
        yield {"vid": vid, "dsid": dsid}
        api.dependency_overrides.clear()
        eng.dispose()

    def test_post_execution_201(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/executions", json={"version_id": ctx["vid"], "input_data": {"x": 1.0}})
        assert resp.status_code == 201

    def test_execution_succeeded(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/executions", json={"version_id": ctx["vid"], "input_data": {"x": 2.0}})
        assert resp.json()["status"] == "succeeded"

    def test_execution_outputs(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/executions", json={"version_id": ctx["vid"], "input_data": {"price": 10.0}})
        assert resp.json()["outputs"]["price"] == pytest.approx(30.0)  # 10 * 3

    def test_execution_unknown_version_404(self, ctx):
        with TestClient(api) as c:
            assert c.post("/api/executions", json={"version_id": "no-such", "input_data": {}}).status_code == 404

    def test_execution_missing_body_field_422(self, ctx):
        with TestClient(api) as c:
            assert c.post("/api/executions", json={"input_data": {}}).status_code == 422

    def test_execution_records_result(self, ctx):
        """The result dataset is derived from the version's declared output, not the request."""
        with TestClient(api) as c:
            resp = c.post("/api/executions", json={"version_id": ctx["vid"], "input_data": {"q": 5.0}})
        assert resp.json()["status"] == "succeeded"
        run_id = resp.json()["run_id"]
        with TestClient(api) as c:
            results = c.get(f"/api/executions/{run_id}/results").json()
        assert len(results) == 1
        assert results[0]["run_id"] == run_id
        assert results[0]["dataset_id"] == ctx["dsid"]
        assert resp.json()["recorded_result_ids"] == [results[0]["id"]]

    def test_client_supplied_output_dataset_rejected(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/executions", json={
                "version_id": ctx["vid"], "input_data": {}, "output_dataset_id": ctx["dsid"],
            })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# AC-06: Graph execution
# ---------------------------------------------------------------------------

class TestGraphExecutionAPI:
    @pytest.fixture(scope="class")
    def ctx(self):
        eng = _make_engine()
        SF = _make_sf(eng)
        db = SF()
        ma_id = _seed_model(db, name=_uid("GExA-"))
        mb_id = _seed_model(db, name=_uid("GExB-"))
        va_id = _seed_version(db, ma_id)
        vb_id = _seed_version(db, mb_id)
        ds_id = _seed_dataset(db)
        ds_b_id = _seed_dataset(db)
        _seed_dep(db, va_id, vb_id, ds_id)
        FederationService(db).bind_output(vb_id, ds_b_id)
        db.commit()
        db.close()

        reg = _build_registry(SF, [(va_id, 2.0), (vb_id, 3.0)])
        api.dependency_overrides[get_db] = _db_override(SF)
        api.dependency_overrides[get_adapter_registry] = lambda: reg
        yield {"va_id": va_id, "vb_id": vb_id, "ds_a": ds_id, "ds_b": ds_b_id}
        api.dependency_overrides.clear()
        eng.dispose()

    def test_graph_exec_201(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/graph-executions", json={
                "target_version_id": ctx["vb_id"],
                "input_data": {"x": 1.0},
            })
        assert resp.status_code == 201

    def test_graph_exec_success(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/graph-executions", json={
                "target_version_id": ctx["vb_id"],
                "input_data": {"y": 5.0},
            })
        assert resp.json()["success"] is True

    def test_graph_exec_step_outcomes(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/graph-executions", json={
                "target_version_id": ctx["vb_id"],
                "input_data": {"v": 2.0},
            })
        so = resp.json()["step_outcomes"]
        assert ctx["va_id"] in so and ctx["vb_id"] in so

    def test_graph_exec_records_results(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/graph-executions", json={
                "target_version_id": ctx["vb_id"],
                "input_data": {"z": 3.0},
            })
        data = resp.json()
        assert len(data["recorded_result_ids"]) == 2
        with TestClient(api) as c:
            results = c.get(f"/api/executions/{data['graph_run_id']}/results").json()
        assert {r["dataset_id"] for r in results} == {ctx["ds_a"], ctx["ds_b"]}

    def test_client_supplied_mapping_rejected(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/graph-executions", json={
                "target_version_id": ctx["vb_id"],
                "input_data": {"z": 3.0},
                "version_to_output_dataset": {ctx["vb_id"]: ctx["ds_b"]},
            })
        assert resp.status_code == 422

    def test_graph_exec_unknown_target_404(self, ctx):
        with TestClient(api) as c:
            assert c.post("/api/graph-executions", json={
                "target_version_id": "no-such", "input_data": {}
            }).status_code == 404


# ---------------------------------------------------------------------------
# AC-07: Change propagation
# ---------------------------------------------------------------------------

class TestPropagationAPI:
    @pytest.fixture(scope="class")
    def ctx(self):
        eng = _make_engine()
        SF = _make_sf(eng)
        db = SF()
        ma_id = _seed_model(db, name=_uid("PropA-"))
        mb_id = _seed_model(db, name=_uid("PropB-"))
        va_id = _seed_version(db, ma_id)
        vb_id = _seed_version(db, mb_id)
        ds_link = _seed_dataset(db)
        ds_trigger = _seed_dataset(db)
        ds_b = _seed_dataset(db)
        _seed_dep(db, va_id, vb_id, ds_link)
        fed = FederationService(db)
        fed.bind_input(va_id, ds_trigger)     # source dataset feeding A
        fed.bind_output(vb_id, ds_b)
        db.commit()
        db.close()

        reg = _build_registry(SF, [(va_id, 2.0), (vb_id, 3.0)])
        api.dependency_overrides[get_db] = _db_override(SF)
        api.dependency_overrides[get_adapter_registry] = lambda: reg
        yield {
            "va_id": va_id, "vb_id": vb_id,
            "ds_trigger": ds_trigger, "ds_a": ds_link, "ds_b": ds_b,
        }
        api.dependency_overrides.clear()
        eng.dispose()

    def test_propagate_201(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/changes/propagate", json={
                "dataset_id": ctx["ds_trigger"], "value": {"fuel": 100.0},
            })
        assert resp.status_code == 201
        assert resp.json()["changed"] is True

    def test_propagate_success(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/changes/propagate", json={
                "dataset_id": ctx["ds_trigger"], "value": {"fuel": 110.0},
            })
        assert resp.json()["success"] is True

    def test_propagate_records_results(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/changes/propagate", json={
                "dataset_id": ctx["ds_trigger"], "value": {"fuel": 120.0},
            })
        data = resp.json()
        assert data["success"] is True
        assert len(data["recorded_result_ids"]) == 2
        assert data["old_value"] == {"fuel": 110.0}
        assert data["new_value"] == {"fuel": 120.0}

    def test_propagate_unknown_dataset_404(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/changes/propagate", json={
                "dataset_id": "no-such-dataset", "value": {"fuel": 1.0},
            })
        assert resp.status_code == 404

    def test_propagate_model_produced_dataset_409(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/changes/propagate", json={
                "dataset_id": ctx["ds_b"], "value": {"fuel": 1.0},
            })
        assert resp.status_code == 409

    def test_propagate_missing_dataset_id_422(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/changes/propagate", json={"value": {"fuel": 1.0}})
        assert resp.status_code == 422

    def test_propagate_legacy_client_fields_422(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/changes/propagate", json={
                "dataset_id": ctx["ds_trigger"], "value": {"fuel": 1.0},
                "source_version_id": ctx["va_id"],
            })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# AC-08: Execution history
# ---------------------------------------------------------------------------

class TestExecutionHistoryAPI:
    @pytest.fixture(scope="class")
    def ctx(self):
        eng = _make_engine()
        SF = _make_sf(eng)
        db = SF()
        mid = _seed_model(db, name=_uid("HistM-"))
        vid = _seed_version(db, mid)
        db.commit()
        db.close()

        reg = _build_registry(SF, [(vid, 1.0)])
        api.dependency_overrides[get_db] = _db_override(SF)
        api.dependency_overrides[get_adapter_registry] = lambda: reg

        # Pre-execute one run to populate history
        with TestClient(api) as c:
            resp = c.post("/api/executions", json={"version_id": vid, "input_data": {"h": 1.0}})
        run_id = resp.json()["run_id"]

        yield {"vid": vid, "run_id": run_id}
        api.dependency_overrides.clear()
        eng.dispose()

    def test_list_executions_200(self, ctx):
        with TestClient(api) as c:
            resp = c.get("/api/executions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_executions_has_run(self, ctx):
        with TestClient(api) as c:
            resp = c.get("/api/executions")
        assert any(r["id"] == ctx["run_id"] for r in resp.json())

    def test_get_execution_200(self, ctx):
        with TestClient(api) as c:
            resp = c.get(f"/api/executions/{ctx['run_id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run"]["id"] == ctx["run_id"]
        assert isinstance(data["steps"], list)

    def test_get_execution_404(self, ctx):
        with TestClient(api) as c:
            assert c.get("/api/executions/no-such-run").status_code == 404


# ---------------------------------------------------------------------------
# AC-09: Result retrieval
# ---------------------------------------------------------------------------

class TestResultsAPI:
    @pytest.fixture(scope="class")
    def ctx(self):
        eng = _make_engine()
        SF = _make_sf(eng)
        db = SF()
        mid = _seed_model(db, name=_uid("ResM-"))
        vid = _seed_version(db, mid)
        dsid = _seed_dataset(db)
        FederationService(db).bind_output(vid, dsid)
        db.commit()
        db.close()

        reg = _build_registry(SF, [(vid, 4.0)])
        api.dependency_overrides[get_db] = _db_override(SF)
        api.dependency_overrides[get_adapter_registry] = lambda: reg

        # Execute; the result is recorded server-side for the declared output
        with TestClient(api) as c:
            exec_resp = c.post("/api/executions", json={
                "version_id": vid,
                "input_data": {"r": 3.0},
            })
        run_id = exec_resp.json()["run_id"]

        with TestClient(api) as c:
            results = c.get(f"/api/executions/{run_id}/results").json()
        result_id = results[0]["id"] if results else None

        yield {"vid": vid, "mid": mid, "run_id": run_id, "result_id": result_id}
        api.dependency_overrides.clear()
        eng.dispose()

    def test_get_result_200(self, ctx):
        assert ctx["result_id"], "Setup failed to record a result"
        with TestClient(api) as c:
            resp = c.get(f"/api/results/{ctx['result_id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == ctx["result_id"]

    def test_get_result_404(self, ctx):
        with TestClient(api) as c:
            assert c.get("/api/results/no-such-result").status_code == 404

    def test_run_results_200(self, ctx):
        with TestClient(api) as c:
            resp = c.get(f"/api/executions/{ctx['run_id']}/results")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_model_results_200(self, ctx):
        with TestClient(api) as c:
            resp = c.get(f"/api/models/{ctx['mid']}/results")
        assert resp.status_code == 200

    def test_result_value_json(self, ctx):
        assert ctx["result_id"]
        with TestClient(api) as c:
            data = c.get(f"/api/results/{ctx['result_id']}").json()
        val = json.loads(data["value_json"])
        assert val["r"] == pytest.approx(12.0)  # 3.0 * 4.0


# ---------------------------------------------------------------------------
# AC-10: Lineage retrieval
# ---------------------------------------------------------------------------

class TestLineageAPI:
    @pytest.fixture(scope="class")
    def ctx(self):
        eng = _make_engine()
        SF = _make_sf(eng)
        db = SF()
        ma_id = _seed_model(db, name=_uid("LinA-"))
        mb_id = _seed_model(db, name=_uid("LinB-"))
        va_id = _seed_version(db, ma_id)
        vb_id = _seed_version(db, mb_id)
        ds_id = _seed_dataset(db)
        ds_b_id = _seed_dataset(db)
        _seed_dep(db, va_id, vb_id, ds_id)
        FederationService(db).bind_output(vb_id, ds_b_id)
        db.commit()
        db.close()

        reg = _build_registry(SF, [(va_id, 2.0), (vb_id, 1.0)])
        api.dependency_overrides[get_db] = _db_override(SF)
        api.dependency_overrides[get_adapter_registry] = lambda: reg

        # Execute; results + lineage are recorded server-side
        with TestClient(api) as c:
            resp = c.post("/api/graph-executions", json={
                "target_version_id": vb_id,
                "input_data": {"x": 5.0},
            })
        recorded_ids = resp.json().get("recorded_result_ids", [])

        # Find the B result ID (target of lineage)
        check_db = SF()
        result_id_b = None
        for r in check_db.query(Result).all():
            if r.model_version_id == vb_id:
                result_id_b = r.id
        check_db.close()

        yield {"result_id_b": result_id_b, "recorded_ids": recorded_ids}
        api.dependency_overrides.clear()
        eng.dispose()

    def test_get_lineage_200(self, ctx):
        if not ctx["result_id_b"]:
            pytest.skip("No result recorded")
        with TestClient(api) as c:
            assert c.get(f"/api/lineage/{ctx['result_id_b']}").status_code == 200

    def test_lineage_has_incoming_edge(self, ctx):
        if not ctx["result_id_b"]:
            pytest.skip("No result recorded")
        with TestClient(api) as c:
            data = c.get(f"/api/lineage/{ctx['result_id_b']}").json()
        assert len(data["edges_to"]) >= 1

    def test_lineage_unknown_result_404(self, ctx):
        with TestClient(api) as c:
            assert c.get("/api/lineage/no-such-result").status_code == 404


# ---------------------------------------------------------------------------
# AC-11: Domain failure → sensible HTTP response
# ---------------------------------------------------------------------------

class TestDomainErrors:
    @pytest.fixture(scope="class")
    def ctx(self):
        eng = _make_engine()
        SF = _make_sf(eng)
        db = SF()
        mid = _seed_model(db, name=_uid("ErrM-"))
        vid = _seed_version(db, mid)
        db.commit()
        db.close()

        # Empty registry — no adapters
        empty_reg = AdapterRegistry.__new__(AdapterRegistry)
        empty_reg._db = None
        empty_reg._by_id = {}
        empty_reg._by_version = {}

        api.dependency_overrides[get_db] = _db_override(SF)
        api.dependency_overrides[get_adapter_registry] = lambda: empty_reg
        yield {"vid": vid}
        api.dependency_overrides.clear()
        eng.dispose()

    def test_no_adapter_422(self, ctx):
        with TestClient(api) as c:
            resp = c.post("/api/executions", json={"version_id": ctx["vid"], "input_data": {}})
        assert resp.status_code == 422

    def test_unknown_run_404(self, ctx):
        with TestClient(api) as c:
            assert c.get("/api/executions/no-such-run").status_code == 404

    def test_unknown_run_results_404(self, ctx):
        with TestClient(api) as c:
            assert c.get("/api/executions/no-such-run/results").status_code == 404
