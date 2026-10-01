"""D28 — Dagster optional-executor tests.

Dagster is not a platform dependency, so every test here uses a fake transport (or an httpx mock
transport for the GraphQL client). The Dagster code location itself is exercised against a real
Dagster only when Dagster is importable (see TestDefinitions) — otherwise those tests skip.

Coverage mirrors D21: explicit selection without silent fallback, minimal launch payload and
correlation, status mapping and reconcile, hardened idempotent callback at the API layer, the
service-only route policy, and the written-dataset names an orchestrator records as data events.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from backend.app.config import settings
from backend.app.execution import dagster_client
from backend.app.execution.dagster_client import (
    DagsterConfig,
    DagsterConfigError,
    DagsterTransportError,
    HttpxDagsterTransport,
)
from backend.app.execution.dagster_executor import (
    DAGSTER_STATUS_TO_PLATFORM,
    OP_NAME,
    TAG_KEY,
    DagsterExecutor,
    run_config_for,
)
from backend.app.execution.executor import InProcessExecutor
from backend.app.execution.factory import select_executor
from backend.app.persistence.database import Dataset, ExecutionRun, ExecutionStep, get_db
from backend.app.security.auth import required_role
from backend.app.services.orchestration import GraphOrchestrationService
from tests.test_d21_airflow_executor import _engine, _registry_with_adapter, _seed_single_node_graph


class FakeDagster:
    def __init__(self, status: str = "SUCCESS", fail_launch: bool = False) -> None:
        self.launched: list[dict] = []
        self.status = status
        self.fail_launch = fail_launch

    def launch_run(self, selector, run_config, tags):
        if self.fail_launch:
            raise DagsterTransportError("simulated unreachable Dagster")
        self.launched.append({"selector": selector, "run_config": run_config, "tags": tags})
        return f"dg-{len(self.launched)}"

    def get_run_status(self, run_id):
        return self.status


def _cfg(**kw) -> DagsterConfig:
    base = dict(enabled=True, graphql_url="http://dagster.example/graphql", location_name="dagster_platform",
                repository_name="__repository__", job_name="platform_graphrun_job", auth_token="",
                timeout_s=5.0, verify_tls=True)
    base.update(kw)
    return DagsterConfig(**base)


@pytest.fixture()
def graph():
    eng = _engine()
    SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    db = SF()
    ids = _seed_single_node_graph(db)
    reg = _registry_with_adapter(db, ids["version_id"])
    db.commit()
    yield {"db": db, "ids": ids, "reg": reg}
    db.close()
    eng.dispose()


def _submit(graph, fake: FakeDagster):
    svc = GraphOrchestrationService(graph["db"], graph["reg"])
    return svc, svc.submit_graph(graph["ids"]["version_id"], executor=DagsterExecutor(_cfg(), fake))


# ---------------------------------------------------------------------------
# Selection and configuration
# ---------------------------------------------------------------------------

class TestSelection:
    def test_default_is_still_in_process(self):
        assert isinstance(select_executor(None), InProcessExecutor)

    def test_explicit_dagster_with_valid_config(self, monkeypatch):
        monkeypatch.setattr(settings, "DAGSTER_ENABLED", True)
        monkeypatch.setattr(settings, "DAGSTER_GRAPHQL_URL", "http://dagster.example/graphql")
        ex = select_executor("dagster")
        assert ex.name == "dagster" and ex.is_external is True

    def test_explicit_dagster_unconfigured_never_falls_back(self, monkeypatch):
        monkeypatch.setattr(settings, "DAGSTER_ENABLED", False)
        monkeypatch.setattr(settings, "DAGSTER_GRAPHQL_URL", "")
        with pytest.raises(DagsterConfigError) as exc:
            select_executor("dagster")
        assert "DAGSTER_ENABLED" in str(exc.value) and "DAGSTER_GRAPHQL_URL" in str(exc.value)

    def test_token_only_sent_when_configured(self):
        assert _cfg().auth_header() == {}
        assert _cfg(auth_token="TESTONLY").auth_header() == {"Authorization": "Bearer TESTONLY"}


# ---------------------------------------------------------------------------
# Submit, correlation, status
# ---------------------------------------------------------------------------

class TestSubmit:
    def test_launch_payload_is_minimal_and_correlated(self, graph):
        fake = FakeDagster()
        _svc, go = _submit(graph, fake)
        (launch,) = fake.launched
        assert launch["selector"] == {"repositoryLocationName": "dagster_platform",
                                      "repositoryName": "__repository__", "jobName": "platform_graphrun_job"}
        assert launch["run_config"] == {"ops": {OP_NAME: {"config": {"graph_run_id": go.run_id}}}}
        assert launch["tags"] == {TAG_KEY: go.run_id}
        run = graph["db"].get(ExecutionRun, go.run_id)
        assert run.executor == "dagster" and run.status == "running"
        assert go.external_ref == "dg-1" == DagsterExecutor.read_external_ref(run)

    def test_launch_failure_propagates(self, graph):
        with pytest.raises(DagsterTransportError):
            _submit(graph, FakeDagster(fail_launch=True))

    @pytest.mark.parametrize("status, expected", [
        ("QUEUED", "running"), ("STARTED", "running"), ("SUCCESS", "succeeded"),
        ("FAILURE", "failed"), ("CANCELED", "failed"),
    ])
    def test_status_mapping(self, status, expected):
        assert DAGSTER_STATUS_TO_PLATFORM[status] == expected

    def test_reconcile_failure_before_callback(self, graph):
        fake = FakeDagster(status="FAILURE")
        svc, go = _submit(graph, fake)
        run = svc.reconcile_run(go.run_id, DagsterExecutor(_cfg(), fake))
        assert run.status == "failed" and "dg-1" in run.error_message

    def test_unknown_status_stays_running(self, graph):
        fake = FakeDagster(status="SOMETHING_NEW")
        svc, go = _submit(graph, fake)
        assert svc.reconcile_run(go.run_id, DagsterExecutor(_cfg(), fake)).status == "running"


# ---------------------------------------------------------------------------
# GraphQL client (httpx mock transport — no Dagster needed)
# ---------------------------------------------------------------------------

class TestGraphQLClient:
    @pytest.fixture()
    def mock(self, monkeypatch):
        seen: list[dict] = []
        replies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append({"url": str(request.url), "headers": dict(request.headers),
                         "body": json.loads(request.content)})
            return httpx.Response(200, json=replies.pop(0))

        real = httpx.Client
        monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        return seen, replies

    def test_launch_sends_selector_config_and_tags(self, mock):
        seen, replies = mock
        replies.append({"data": {"launchRun": {"__typename": "LaunchRunSuccess",
                                               "run": {"runId": "abc", "status": "QUEUED"}}}})
        t = HttpxDagsterTransport(_cfg(auth_token="TESTONLY"))
        assert t.launch_run(_cfg().selector(), run_config_for("r1"), {TAG_KEY: "r1"}) == "abc"
        body = seen[0]["body"]
        assert "launchRun" in body["query"]
        params = body["variables"]["executionParams"]
        assert params["selector"]["jobName"] == "platform_graphrun_job"
        assert params["runConfigData"] == run_config_for("r1")
        assert params["executionMetadata"]["tags"] == [{"key": TAG_KEY, "value": "r1"}]
        assert seen[0]["headers"]["authorization"] == "Bearer TESTONLY"

    def test_launch_refusal_is_an_error(self, mock):
        _seen, replies = mock
        replies.append({"data": {"launchRun": {"__typename": "PipelineNotFoundError", "message": "no job"}}})
        with pytest.raises(DagsterTransportError, match="PipelineNotFoundError"):
            HttpxDagsterTransport(_cfg()).launch_run(_cfg().selector(), {}, {})

    def test_status_query(self, mock):
        _seen, replies = mock
        replies.append({"data": {"runOrError": {"__typename": "Run", "status": "STARTED"}}})
        assert HttpxDagsterTransport(_cfg()).get_run_status("abc") == "STARTED"
        replies.append({"data": {"runOrError": {"__typename": "RunNotFoundError", "message": "gone"}}})
        with pytest.raises(DagsterTransportError):
            HttpxDagsterTransport(_cfg()).get_run_status("abc")

    def test_unreachable_does_not_leak(self, monkeypatch):
        def boom(request):
            raise httpx.ConnectError("refused")
        real = httpx.Client
        monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(boom), **kw))
        with pytest.raises(DagsterTransportError, match="unreachable: ConnectError"):
            HttpxDagsterTransport(_cfg(auth_token="SECRET")).get_run_status("x")


# ---------------------------------------------------------------------------
# API: graph execution through Dagster, hardened callback, reconcile
# ---------------------------------------------------------------------------

class TestApi:
    @pytest.fixture()
    def ctx(self, monkeypatch):
        from fastapi.testclient import TestClient
        from backend.app.api.deps import get_adapter_registry
        from backend.app.api.routers import executions as exec_router
        from backend.app.main import api

        eng = _engine()
        SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        db = SF()
        ids = _seed_single_node_graph(db)
        reg = _registry_with_adapter(db, ids["version_id"])
        airflow_run = ExecutionRun(run_kind="graph", executor="airflow", status="requested",
                                   subgraph_json=json.dumps([ids["version_id"]]), input_snapshot="{}",
                                   audit_json=json.dumps({"dagster_run_id": "dg-1"}))
        db.add(airflow_run)
        db.flush()
        airflow_id = airflow_run.id
        out_name = db.get(Dataset, ids["out_id"]).name
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

        fake = FakeDagster()
        real_select = exec_router.select_executor
        monkeypatch.setattr(exec_router, "select_executor",
                            lambda name: DagsterExecutor(_cfg(), fake) if name == "dagster" else real_select(name))
        api.dependency_overrides[get_db] = _get_db
        api.dependency_overrides[get_adapter_registry] = lambda: reg
        with TestClient(api) as client:
            yield {"c": client, "ids": ids, "fake": fake, "airflow_id": airflow_id, "out_name": out_name, "SF": SF}
        api.dependency_overrides.clear()
        db.close()
        eng.dispose()

    def _launch(self, ctx) -> dict:
        r = ctx["c"].post("/api/graph-executions",
                          json={"target_version_id": ctx["ids"]["version_id"], "executor": "dagster"})
        assert r.status_code == 201, r.text
        return r.json()

    def test_full_round_trip(self, ctx):
        body = self._launch(ctx)
        assert body["executor"] == "dagster" and body["status"] == "running" and body["external_ref"] == "dg-1"
        cb = ctx["c"].post(f"/api/executions/{body['graph_run_id']}/dagster-callback",
                           json={"dagster_run_id": "dg-1"})
        assert cb.status_code == 200, cb.text
        out = cb.json()
        assert out["success"] is True and out["status"] == "succeeded" and out["recorded_result_ids"]
        assert out["written_datasets"] == [ctx["out_name"]]
        rec = ctx["c"].post(f"/api/executions/{body['graph_run_id']}/reconcile").json()
        assert rec["executor"] == "dagster" and rec["status"] == "succeeded"

    def test_re_callback_is_idempotent(self, ctx):
        rid = self._launch(ctx)["graph_run_id"]
        first = ctx["c"].post(f"/api/executions/{rid}/dagster-callback", json={"dagster_run_id": "dg-1"}).json()
        second = ctx["c"].post(f"/api/executions/{rid}/dagster-callback", json={"dagster_run_id": "dg-1"}).json()
        assert second["already_terminal"] is True
        assert first["recorded_result_ids"] == second["recorded_result_ids"]
        assert first["written_datasets"] == second["written_datasets"] == [ctx["out_name"]]

    def test_wrong_correlation_id_409(self, ctx):
        rid = self._launch(ctx)["graph_run_id"]
        r = ctx["c"].post(f"/api/executions/{rid}/dagster-callback", json={"dagster_run_id": "dg-OTHER"})
        assert r.status_code == 409

    def test_airflow_run_cannot_be_carried_out_by_dagster_callback(self, ctx):
        r = ctx["c"].post(f"/api/executions/{ctx['airflow_id']}/dagster-callback", json={"dagster_run_id": "dg-1"})
        assert r.status_code == 409 and "Dagster" in r.json()["detail"]

    def test_unknown_run_404(self, ctx):
        r = ctx["c"].post("/api/executions/nope/dagster-callback", json={"dagster_run_id": "dg-1"})
        assert r.status_code == 404

    def test_unconfigured_dagster_is_503(self, ctx, monkeypatch):
        from backend.app.api.routers import executions as exec_router
        from backend.app.execution import factory
        monkeypatch.setattr(exec_router, "select_executor", factory.select_executor)
        monkeypatch.setattr(settings, "DAGSTER_ENABLED", False)
        r = ctx["c"].post("/api/graph-executions",
                          json={"target_version_id": ctx["ids"]["version_id"], "executor": "dagster"})
        assert r.status_code == 503 and "DAGSTER_ENABLED" in r.json()["detail"]


def test_callback_is_service_only():
    assert required_role("POST", "/api/executions/{run_id}/dagster-callback") == "service"


# ---------------------------------------------------------------------------
# The Dagster code location itself — only where Dagster is installed
# ---------------------------------------------------------------------------

class TestDefinitions:
    def test_job_and_federation_assets(self, tmp_path, monkeypatch):
        pytest.importorskip("dagster")
        import importlib

        fmap = {"models": [{"version_id": "v1", "name": "Fuel cost"}],
                "datasets": [{"id": "a", "name": "fuel_price_input", "role": "source"},
                             {"id": "b", "name": "fuel_cost_output", "role": "model_output"}],
                "reads": [{"version_id": "v1", "dataset_id": "a"}],
                "writes": [{"version_id": "v1", "dataset_id": "b"}]}
        path = tmp_path / "federation_map.json"
        path.write_text(json.dumps(fmap), encoding="utf-8")
        monkeypatch.setenv("PLATFORM_FEDERATION_MAP", str(path))
        mod = importlib.reload(importlib.import_module("dagster_platform.definitions"))
        assert mod.defs.resolve_job_def("platform_graphrun_job").name == "platform_graphrun_job"
        specs = {s.key.to_user_string(): s for s in mod.federation_asset_specs(path)}
        assert [d.asset_key.to_user_string() for d in specs["federation/fuel_cost_output"].deps] == \
            ["federation/fuel_price_input"]
