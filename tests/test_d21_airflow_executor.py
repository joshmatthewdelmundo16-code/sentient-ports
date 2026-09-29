"""D21 — Airflow optional-executor tests.

Airflow is NOT installed/configured in this environment, so every test uses a fake transport
or exercises the platform side directly. No test requires Docker/Kubernetes/a running Airflow.

Coverage:
  - executor selection (no silent fallback when 'airflow' is explicitly chosen)  [correction #1]
  - Airflow config/auth handling without real secrets
  - submit request construction: minimal conf, correlation ids                    [correction #3]
  - GraphRun ↔ Airflow dag_run_id correlation round-trip
  - status mapping + failure mapping + reconcile (incl. failure before callback)
  - callback execution + idempotent re-callback (no duplicate results)            [correction #2]
  - callback hardening at the API layer (404 / 409 correlation / wrong executor)  [correction #2]
  - InProcessExecutor regression (default path unchanged)
  - D19 scenario isolation preserved; scenarios stay in-process                   [correction #4]
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d21_tmp")

from backend.app.config import settings  # noqa: E402
from backend.app.adapters.synthetic import SyntheticAdapter  # noqa: E402
from backend.app.execution.airflow_client import (  # noqa: E402
    AirflowConfig,
    AirflowConfigError,
    AirflowTransportError,
)
from backend.app.execution.airflow_executor import (  # noqa: E402
    AIRFLOW_STATE_TO_PLATFORM,
    AirflowExecutor,
    dag_run_id_for,
)
from backend.app.execution.executor import (  # noqa: E402
    ExecutorSelectionError,
    InProcessExecutor,
    is_terminal,
)
from backend.app.execution.factory import select_executor  # noqa: E402
from backend.app.persistence.database import (  # noqa: E402
    Base,
    DataContract,
    Dataset,
    ExecutionRun,
    Model,
    ModelVersion,
    Result,
    get_db,
)
from backend.app.services.adapter_registry import AdapterRegistry  # noqa: E402
from backend.app.services.federation import FederationService  # noqa: E402
from backend.app.services.orchestration import GraphOrchestrationService  # noqa: E402
from backend.app.services.scenarios import ScenarioService  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------

class FakeTransport:
    """In-memory Airflow transport — records triggers, returns a scripted dag-run state."""

    def __init__(self, state: str = "success", fail_trigger: bool = False) -> None:
        self.triggered: list[dict] = []
        self.state = state
        self.fail_trigger = fail_trigger

    def trigger_dag_run(self, dag_id, dag_run_id, conf):
        if self.fail_trigger:
            raise AirflowTransportError("simulated unreachable Airflow")
        self.triggered.append({"dag_id": dag_id, "dag_run_id": dag_run_id, "conf": conf})
        return {"dag_run_id": dag_run_id, "state": "queued"}

    def get_dag_run_state(self, dag_id, dag_run_id):
        return self.state


def _valid_config(dag_id: str = "platform_graphrun") -> AirflowConfig:
    return AirflowConfig(
        enabled=True, base_url="http://airflow.example", dag_id=dag_id,
        auth_token="TESTONLY-not-a-real-token", username="", password="",
        timeout_s=5.0, verify_tls=True,
    )


_ctr = {"n": 0}


def _uid(prefix: str = "") -> str:
    _ctr["n"] += 1
    return f"{prefix}{_ctr['n']}"


def _engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    return eng


def _contract(db: Session, dataset_id: str) -> None:
    db.add(DataContract(
        dataset_id=dataset_id,
        schema_json=json.dumps({"fields": [{"name": "value", "type": "float"}]}),
        semver="1.0.0",
    ))
    db.flush()


def _seed_single_node_graph(db: Session, scalar: float = 2.0) -> dict[str, str]:
    """Source dataset S (value=10) → model V (×scalar) → output dataset O. Returns ids."""
    model = Model(name=_uid("M-"), owner="test", model_type="python", status="active")
    db.add(model)
    db.flush()
    version = ModelVersion(model_id=model.id, semver="1.0.0", is_active=True)
    db.add(version)
    db.flush()

    src = Dataset(name=_uid("S-"), current_value=json.dumps({"value": 10.0}))
    out = Dataset(name=_uid("O-"))
    db.add_all([src, out])
    db.flush()
    _contract(db, src.id)
    _contract(db, out.id)

    fed = FederationService(db)
    fed.bind_input(version.id, src.id)
    fed.bind_output(version.id, out.id)
    db.flush()
    return {"version_id": version.id, "src_id": src.id, "out_id": out.id}


def _registry_with_adapter(db: Session, version_id: str, scalar: float = 2.0) -> AdapterRegistry:
    reg = AdapterRegistry(db)
    reg.register_adapter(SyntheticAdapter(_uid("adp-"), version_id, scalar=scalar))
    return reg


@pytest.fixture()
def graph():
    eng = _engine()
    SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    db = SF()
    ids = _seed_single_node_graph(db)
    reg = _registry_with_adapter(db, ids["version_id"])
    db.commit()
    yield {"db": db, "SF": SF, "ids": ids, "reg": reg}
    db.close()
    eng.dispose()


# ---------------------------------------------------------------------------
# Executor selection — correction #1 (no silent fallback)
# ---------------------------------------------------------------------------

class TestExecutorSelection:
    def test_unspecified_defaults_to_in_process(self, monkeypatch):
        monkeypatch.setattr(settings, "AIRFLOW_ENABLED", False)
        ex = select_executor(None)
        assert ex.name == "in_process" and ex.is_external is False

    def test_explicit_in_process(self):
        ex = select_executor("in_process")
        assert isinstance(ex, InProcessExecutor)

    def test_explicit_airflow_with_valid_config(self, monkeypatch):
        monkeypatch.setattr(settings, "AIRFLOW_ENABLED", True)
        monkeypatch.setattr(settings, "AIRFLOW_BASE_URL", "http://airflow.example")
        monkeypatch.setattr(settings, "AIRFLOW_AUTH_TOKEN", "TESTONLY")
        ex = select_executor("airflow")
        assert ex.name == "airflow" and ex.is_external is True

    def test_explicit_airflow_missing_config_raises(self, monkeypatch):
        # Disabled / unconfigured must NOT silently fall back to in-process.
        monkeypatch.setattr(settings, "AIRFLOW_ENABLED", False)
        monkeypatch.setattr(settings, "AIRFLOW_BASE_URL", "")
        monkeypatch.setattr(settings, "AIRFLOW_AUTH_TOKEN", "")
        with pytest.raises(AirflowConfigError):
            select_executor("airflow")

    def test_unknown_executor_raises(self):
        with pytest.raises(ExecutorSelectionError):
            select_executor("spark")


# ---------------------------------------------------------------------------
# Airflow config / auth (no real secrets)
# ---------------------------------------------------------------------------

class TestAirflowConfig:
    def test_validate_reports_all_missing(self):
        cfg = AirflowConfig(False, "", "", "", "", "", 5.0, True)
        with pytest.raises(AirflowConfigError) as exc:
            cfg.validate()
        msg = str(exc.value)
        assert "AIRFLOW_ENABLED" in msg and "AIRFLOW_BASE_URL" in msg

    def test_valid_config_passes(self):
        _valid_config().validate()  # does not raise

    def test_bearer_auth_header(self):
        hdr = _valid_config().auth_header()
        assert hdr["Authorization"].startswith("Bearer ")

    def test_basic_auth_header(self):
        cfg = AirflowConfig(True, "http://a", "d", "", "user", "pw", 5.0, True)
        assert cfg.auth_header()["Authorization"].startswith("Basic ")

    def test_has_auth_false_when_none(self):
        cfg = AirflowConfig(True, "http://a", "d", "", "", "", 5.0, True)
        assert cfg.has_auth() is False


# ---------------------------------------------------------------------------
# Submit: request construction + correlation — correction #3
# ---------------------------------------------------------------------------

class TestSubmitAndCorrelation:
    def test_submit_triggers_minimal_payload(self, graph):
        transport = FakeTransport(state="queued")
        executor = AirflowExecutor(_valid_config(dag_id="platform_graphrun"), transport)
        orch = GraphOrchestrationService(graph["db"], graph["reg"])

        out = orch.submit_graph(graph["ids"]["version_id"], executor=executor)

        assert out.status == "running"
        assert out.external_ref == dag_run_id_for(out.run_id)
        assert len(transport.triggered) == 1
        call = transport.triggered[0]
        assert call["dag_id"] == "platform_graphrun"
        assert call["dag_run_id"] == out.external_ref
        # Minimal conf: only the correlation id, nothing else (no logical_date, no creds).
        assert call["conf"] == {"graph_run_id": out.run_id}
        assert set(call["conf"].keys()) == {"graph_run_id"}

    def test_submit_marks_run_airflow_and_pending(self, graph):
        executor = AirflowExecutor(_valid_config(), FakeTransport())
        orch = GraphOrchestrationService(graph["db"], graph["reg"])
        out = orch.submit_graph(graph["ids"]["version_id"], executor=executor)

        run = graph["db"].get(ExecutionRun, out.run_id)
        assert run.executor == "airflow"
        assert run.status == "running" and not is_terminal(run.status)
        # No results before the callback carries out the run.
        assert graph["db"].query(Result).filter_by(run_id=out.run_id).count() == 0

    def test_external_ref_round_trip(self, graph):
        executor = AirflowExecutor(_valid_config(), FakeTransport())
        orch = GraphOrchestrationService(graph["db"], graph["reg"])
        out = orch.submit_graph(graph["ids"]["version_id"], executor=executor)
        run = graph["db"].get(ExecutionRun, out.run_id)
        assert AirflowExecutor.read_external_ref(run) == out.external_ref

    def test_submit_transport_failure_propagates(self, graph):
        executor = AirflowExecutor(_valid_config(), FakeTransport(fail_trigger=True))
        orch = GraphOrchestrationService(graph["db"], graph["reg"])
        with pytest.raises(AirflowTransportError):
            orch.submit_graph(graph["ids"]["version_id"], executor=executor)


# ---------------------------------------------------------------------------
# Callback execution + idempotency — correction #2
# ---------------------------------------------------------------------------

class TestCallbackExecution:
    def test_execute_submitted_run_records_results_and_publishes(self, graph):
        executor = AirflowExecutor(_valid_config(), FakeTransport())
        orch = GraphOrchestrationService(graph["db"], graph["reg"])
        submitted = orch.submit_graph(graph["ids"]["version_id"], executor=executor)

        out = orch.execute_submitted_run(submitted.run_id)
        assert out.success is True and out.status == "succeeded"
        assert graph["db"].query(Result).filter_by(run_id=submitted.run_id).count() == 1
        # publish=True: the output dataset was written (10 × 2 = 20).
        out_ds = graph["db"].get(Dataset, graph["ids"]["out_id"])
        assert json.loads(out_ds.current_value) == {"value": 20.0}

    def test_re_callback_is_idempotent(self, graph):
        executor = AirflowExecutor(_valid_config(), FakeTransport())
        orch = GraphOrchestrationService(graph["db"], graph["reg"])
        submitted = orch.submit_graph(graph["ids"]["version_id"], executor=executor)

        first = orch.execute_submitted_run(submitted.run_id)
        count_after_first = graph["db"].query(Result).filter_by(run_id=submitted.run_id).count()
        second = orch.execute_submitted_run(submitted.run_id)  # repeated callback
        count_after_second = graph["db"].query(Result).filter_by(run_id=submitted.run_id).count()

        assert first.status == second.status == "succeeded"
        assert count_after_first == count_after_second == 1  # no duplicate results


# ---------------------------------------------------------------------------
# Status mapping + reconcile
# ---------------------------------------------------------------------------

class TestStatusMappingAndReconcile:
    @pytest.mark.parametrize("airflow_state,expected", [
        ("queued", "running"), ("running", "running"),
        ("success", "succeeded"), ("failed", "failed"),
    ])
    def test_status_mapping_table(self, airflow_state, expected):
        assert AIRFLOW_STATE_TO_PLATFORM[airflow_state] == expected

    def test_unknown_state_maps_to_running(self, graph):
        executor = AirflowExecutor(_valid_config(), FakeTransport(state="up_for_retry"))
        orch = GraphOrchestrationService(graph["db"], graph["reg"])
        out = orch.submit_graph(graph["ids"]["version_id"], executor=executor)
        run = graph["db"].get(ExecutionRun, out.run_id)
        assert executor.poll_status(run) == "running"

    def test_reconcile_marks_failed_when_airflow_failed(self, graph):
        # Simulates the DAG failing before it ever calls back.
        executor = AirflowExecutor(_valid_config(), FakeTransport(state="failed"))
        orch = GraphOrchestrationService(graph["db"], graph["reg"])
        out = orch.submit_graph(graph["ids"]["version_id"], executor=executor)

        run = orch.reconcile_run(out.run_id, executor)
        assert run.status == "failed" and run.error_message

    def test_reconcile_terminal_is_sticky(self, graph):
        executor = AirflowExecutor(_valid_config(), FakeTransport())
        orch = GraphOrchestrationService(graph["db"], graph["reg"])
        submitted = orch.submit_graph(graph["ids"]["version_id"], executor=executor)
        orch.execute_submitted_run(submitted.run_id)  # → succeeded

        executor._transport.state = "failed"  # later Airflow poll disagrees
        run = orch.reconcile_run(submitted.run_id, executor)
        assert run.status == "succeeded"  # terminal status never overwritten

    def test_reconcile_in_process_is_noop(self, graph):
        orch = GraphOrchestrationService(graph["db"], graph["reg"])
        out = orch.execute_graph(graph["ids"]["version_id"])
        run = orch.reconcile_run(out.run_id, InProcessExecutor())
        assert run.status == out.status == "succeeded"


# ---------------------------------------------------------------------------
# InProcessExecutor regression (default path unchanged)
# ---------------------------------------------------------------------------

class TestInProcessRegression:
    def test_submit_graph_in_process_runs_inline(self, graph):
        orch = GraphOrchestrationService(graph["db"], graph["reg"])
        out = orch.submit_graph(graph["ids"]["version_id"], executor=InProcessExecutor())
        assert out.success is True and out.status == "succeeded"
        run = graph["db"].get(ExecutionRun, out.run_id)
        assert run.executor == "in_process"
        assert graph["db"].query(Result).filter_by(run_id=out.run_id).count() == 1

    def test_execute_graph_unchanged(self, graph):
        orch = GraphOrchestrationService(graph["db"], graph["reg"])
        out = orch.execute_graph(graph["ids"]["version_id"])
        assert out.success is True and out.status == "succeeded"
        assert out.external_ref is None


# ---------------------------------------------------------------------------
# D19 scenario isolation — correction #4 (scenarios stay in-process)
# ---------------------------------------------------------------------------

class TestScenarioStaysInProcess:
    def test_scenario_runs_in_process_and_does_not_publish(self, graph):
        db, ids, reg = graph["db"], graph["ids"], graph["reg"]
        svc = ScenarioService(db, reg)
        baseline = svc.create_baseline(name=_uid("B-"), target_version_id=ids["version_id"])
        scenario = svc.create_scenario(baseline_id=baseline.id, name=_uid("SC-"))
        svc.set_override(scenario.id, ids["src_id"], "value", 25.0)

        out = svc.execute_scenario(scenario.id)
        assert out.success is True

        run = db.get(ExecutionRun, out.run_id)
        # D19: scenario execution is NEVER routed through Airflow.
        assert run.executor == "in_process"
        assert run.scenario_id == scenario.id
        # D19 read-only invariant: output dataset is not published by a scenario run.
        out_ds = db.get(Dataset, ids["out_id"])
        assert out_ds.current_value is None
        # Results are recorded and stamped with the scenario id.
        results = db.query(Result).filter_by(run_id=out.run_id).all()
        assert results and all(r.scenario_id == scenario.id for r in results)


# ---------------------------------------------------------------------------
# API callback hardening — correction #2
# ---------------------------------------------------------------------------

class TestApiCallbackHardening:
    @pytest.fixture()
    def client_ctx(self):
        from fastapi.testclient import TestClient
        from backend.app.api.deps import get_adapter_registry
        from backend.app.main import api
        from backend.app.persistence.database import ExecutionRun, ExecutionStep

        eng = _engine()
        SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        db = SF()
        ids = _seed_single_node_graph(db)
        reg = _registry_with_adapter(db, ids["version_id"])

        # An already-submitted Airflow run, awaiting its callback.
        run = ExecutionRun(
            run_kind="graph", executor="airflow", status="requested",
            target_version_id=ids["version_id"],
            subgraph_json=json.dumps([ids["version_id"]]),
            input_snapshot=json.dumps({}),
            audit_json=json.dumps({"executor": "airflow",
                                   "airflow_dag_run_id": "graphrun__wf1"}),
        )
        db.add(run)
        db.flush()
        db.add(ExecutionStep(run_id=run.id, model_version_id=ids["version_id"],
                             step_order=0, status="pending"))
        # An in-process run (must be rejected by the callback).
        inproc = ExecutionRun(run_kind="graph", executor="in_process", status="requested",
                              subgraph_json=json.dumps([ids["version_id"]]),
                              input_snapshot=json.dumps({}))
        db.add(inproc)
        db.flush()
        run_id, inproc_id = run.id, inproc.id
        db.commit()

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

        api.dependency_overrides[get_db] = _get_db
        api.dependency_overrides[get_adapter_registry] = lambda: reg
        yield {"api": api, "run_id": run_id, "inproc_id": inproc_id}
        api.dependency_overrides.clear()
        db.close()
        eng.dispose()

    def test_callback_success(self, client_ctx):
        from fastapi.testclient import TestClient
        with TestClient(client_ctx["api"]) as c:
            resp = c.post(f"/api/executions/{client_ctx['run_id']}/airflow-callback",
                          json={"dag_run_id": "graphrun__wf1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True and body["status"] == "succeeded"
        assert body["already_terminal"] is False

    def test_callback_idempotent(self, client_ctx):
        from fastapi.testclient import TestClient
        with TestClient(client_ctx["api"]) as c:
            first = c.post(f"/api/executions/{client_ctx['run_id']}/airflow-callback",
                           json={"dag_run_id": "graphrun__wf1"})
            second = c.post(f"/api/executions/{client_ctx['run_id']}/airflow-callback",
                            json={"dag_run_id": "graphrun__wf1"})
        assert first.status_code == second.status_code == 200
        assert second.json()["already_terminal"] is True
        # Same recorded results, not duplicated.
        assert first.json()["recorded_result_ids"] == second.json()["recorded_result_ids"]

    def test_callback_correlation_mismatch_409(self, client_ctx):
        from fastapi.testclient import TestClient
        with TestClient(client_ctx["api"]) as c:
            resp = c.post(f"/api/executions/{client_ctx['run_id']}/airflow-callback",
                          json={"dag_run_id": "graphrun__WRONG"})
        assert resp.status_code == 409

    def test_callback_wrong_executor_409(self, client_ctx):
        from fastapi.testclient import TestClient
        with TestClient(client_ctx["api"]) as c:
            resp = c.post(f"/api/executions/{client_ctx['inproc_id']}/airflow-callback",
                          json={"dag_run_id": "graphrun__wf1"})
        assert resp.status_code == 409

    def test_callback_unknown_run_404(self, client_ctx):
        from fastapi.testclient import TestClient
        with TestClient(client_ctx["api"]) as c:
            resp = c.post("/api/executions/no-such-run/airflow-callback",
                          json={"dag_run_id": "graphrun__wf1"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API graph-execution executor selection (error paths need no Airflow)
# ---------------------------------------------------------------------------

class TestApiExecutorSelection:
    @pytest.fixture()
    def client_ctx(self, monkeypatch):
        from backend.app.api.deps import get_adapter_registry
        from backend.app.main import api

        eng = _engine()
        SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        db = SF()
        ids = _seed_single_node_graph(db)
        reg = _registry_with_adapter(db, ids["version_id"])
        db.commit()

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

        api.dependency_overrides[get_db] = _get_db
        api.dependency_overrides[get_adapter_registry] = lambda: reg
        yield {"api": api, "ids": ids}
        api.dependency_overrides.clear()
        db.close()
        eng.dispose()

    def test_airflow_requested_but_unconfigured_returns_503(self, client_ctx, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setattr(settings, "AIRFLOW_ENABLED", False)
        monkeypatch.setattr(settings, "AIRFLOW_BASE_URL", "")
        monkeypatch.setattr(settings, "AIRFLOW_AUTH_TOKEN", "")
        with TestClient(client_ctx["api"]) as c:
            resp = c.post("/api/graph-executions", json={
                "target_version_id": client_ctx["ids"]["version_id"], "executor": "airflow",
            })
        assert resp.status_code == 503

    def test_unknown_executor_returns_422(self, client_ctx):
        from fastapi.testclient import TestClient
        with TestClient(client_ctx["api"]) as c:
            resp = c.post("/api/graph-executions", json={
                "target_version_id": client_ctx["ids"]["version_id"], "executor": "spark",
            })
        assert resp.status_code == 422

    def test_default_in_process_succeeds(self, client_ctx):
        from fastapi.testclient import TestClient
        with TestClient(client_ctx["api"]) as c:
            resp = c.post("/api/graph-executions", json={
                "target_version_id": client_ctx["ids"]["version_id"],
            })
        assert resp.status_code == 201
        assert resp.json()["executor"] == "in_process"
