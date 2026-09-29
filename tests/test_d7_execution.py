"""
D7 Model Execution service tests — AC-01 through AC-13.

Uses real SQLite persistence and the deterministic D6 SyntheticAdapter.
Per-test session isolation via rollback fixture.
AdapterRegistry is constructed fresh per test (in-memory).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d7_tmp")

from backend.app.persistence.database import Base, Model, ModelVersion  # noqa: E402
from backend.app.adapters.synthetic import SyntheticAdapter  # noqa: E402
from backend.app.services.adapter_registry import AdapterRegistry  # noqa: E402
from backend.app.services.execution import (  # noqa: E402
    ModelExecutionService,
    ExecutionOutcome,
    ExecutionVersionNotFoundError,
    ExecutionAdapterNotFoundError,
    ExecutionRunNotFoundError,
    ExecutionStepNotFoundError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def SessionFactory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture
def db(SessionFactory):
    session = SessionFactory()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def adapter_registry(db):
    return AdapterRegistry(db)


@pytest.fixture
def svc(db, adapter_registry):
    return ModelExecutionService(db, adapter_registry)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_version(db, model_name: str, owner: str = "test",
                  semver: str = "1.0.0") -> ModelVersion:
    model = Model(name=model_name, owner=owner, model_type="python")
    db.add(model)
    db.flush()
    version = ModelVersion(model_id=model.id, semver=semver, is_active=True)
    db.add(version)
    db.flush()
    return version


def _register_synthetic(adapter_registry, version, adapter_id: str,
                         scalar: float = 2.0) -> SyntheticAdapter:
    adapter = SyntheticAdapter(
        adapter_id=adapter_id,
        version_id=version.id,
        scalar=scalar,
    )
    adapter_registry.register_adapter(adapter)
    return adapter


# ---------------------------------------------------------------------------
# AC-02/AC-03/AC-04: Successful execution — run and step created
# ---------------------------------------------------------------------------

class TestSuccessfulExecution:
    def test_execute_returns_outcome(self, svc, db, adapter_registry):
        version = _make_version(db, "ExecModel1")
        _register_synthetic(adapter_registry, version, "exec-adapter-1", scalar=3.0)
        outcome = svc.execute_model(version.id, {"fuel": 100.0})
        assert isinstance(outcome, ExecutionOutcome)

    def test_execute_status_succeeded(self, svc, db, adapter_registry):
        version = _make_version(db, "ExecModel2")
        _register_synthetic(adapter_registry, version, "exec-adapter-2", scalar=2.0)
        outcome = svc.execute_model(version.id, {"x": 5.0})
        assert outcome.status == "succeeded"

    def test_execute_outputs_present(self, svc, db, adapter_registry):
        version = _make_version(db, "ExecModel3")
        _register_synthetic(adapter_registry, version, "exec-adapter-3", scalar=4.0)
        outcome = svc.execute_model(version.id, {"price": 50.0})
        assert outcome.outputs is not None
        assert outcome.outputs["price"] == pytest.approx(200.0)

    def test_execute_no_error_on_success(self, svc, db, adapter_registry):
        version = _make_version(db, "ExecModel4")
        _register_synthetic(adapter_registry, version, "exec-adapter-4")
        outcome = svc.execute_model(version.id, {"v": 1.0})
        assert outcome.error is None

    def test_execution_run_persisted(self, svc, db, adapter_registry):
        version = _make_version(db, "ExecModel5")
        _register_synthetic(adapter_registry, version, "exec-adapter-5")
        outcome = svc.execute_model(version.id, {})
        run = svc.get_execution_run(outcome.run_id)
        assert run.id == outcome.run_id
        assert run.status == "succeeded"

    def test_execution_step_persisted(self, svc, db, adapter_registry):
        version = _make_version(db, "ExecModel6")
        _register_synthetic(adapter_registry, version, "exec-adapter-6")
        outcome = svc.execute_model(version.id, {})
        step = svc.get_execution_step(outcome.step_id)
        assert step.id == outcome.step_id
        assert step.status == "succeeded"
        assert step.model_version_id == version.id

    def test_step_linked_to_run(self, svc, db, adapter_registry):
        version = _make_version(db, "ExecModel7")
        _register_synthetic(adapter_registry, version, "exec-adapter-7")
        outcome = svc.execute_model(version.id, {})
        step = svc.get_execution_step(outcome.step_id)
        assert step.run_id == outcome.run_id

    def test_run_has_input_snapshot(self, svc, db, adapter_registry):
        import json
        version = _make_version(db, "ExecModel8")
        _register_synthetic(adapter_registry, version, "exec-adapter-8")
        outcome = svc.execute_model(version.id, {"fuel_price": 99.0})
        run = svc.get_execution_run(outcome.run_id)
        snap = json.loads(run.input_snapshot)
        assert snap["fuel_price"] == pytest.approx(99.0)


# ---------------------------------------------------------------------------
# AC-06: Adapter resolved before invocation
# ---------------------------------------------------------------------------

class TestAdapterResolution:
    def test_adapter_outputs_match_scalar(self, svc, db, adapter_registry):
        version = _make_version(db, "ResModel1")
        _register_synthetic(adapter_registry, version, "res-adapter-1", scalar=5.0)
        outcome = svc.execute_model(version.id, {"cost": 10.0})
        assert outcome.outputs["cost"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# AC-05: running state recorded (check timestamps set)
# ---------------------------------------------------------------------------

class TestRunningState:
    def test_run_has_started_at(self, svc, db, adapter_registry):
        version = _make_version(db, "RunState1")
        _register_synthetic(adapter_registry, version, "run-adapter-1")
        outcome = svc.execute_model(version.id, {})
        run = svc.get_execution_run(outcome.run_id)
        assert run.started_at is not None

    def test_run_has_finished_at(self, svc, db, adapter_registry):
        version = _make_version(db, "RunState2")
        _register_synthetic(adapter_registry, version, "run-adapter-2")
        outcome = svc.execute_model(version.id, {})
        run = svc.get_execution_run(outcome.run_id)
        assert run.finished_at is not None

    def test_step_has_finished_at(self, svc, db, adapter_registry):
        version = _make_version(db, "RunState3")
        _register_synthetic(adapter_registry, version, "run-adapter-3")
        outcome = svc.execute_model(version.id, {})
        step = svc.get_execution_step(outcome.step_id)
        assert step.finished_at is not None


# ---------------------------------------------------------------------------
# AC-07 (failure path): failed adapter → failed state
# ---------------------------------------------------------------------------

class FailingAdapter(SyntheticAdapter):
    """Adapter that always raises on invoke."""

    def invoke(self, inputs):
        raise RuntimeError("synthetic failure")


class TestFailureHandling:
    def test_adapter_failure_produces_failed_status(self, db, adapter_registry):
        version = _make_version(db, "FailModel1")
        failing = FailingAdapter(adapter_id="fail-adapter-1", version_id=version.id)
        adapter_registry.register_adapter(failing)
        svc = ModelExecutionService(db, adapter_registry)
        outcome = svc.execute_model(version.id, {"x": 1.0})
        assert outcome.status == "failed"

    def test_adapter_failure_sets_error(self, db, adapter_registry):
        version = _make_version(db, "FailModel2")
        failing = FailingAdapter(adapter_id="fail-adapter-2", version_id=version.id)
        adapter_registry.register_adapter(failing)
        svc = ModelExecutionService(db, adapter_registry)
        outcome = svc.execute_model(version.id, {})
        assert outcome.error is not None
        assert "synthetic failure" in outcome.error

    def test_adapter_failure_outputs_none(self, db, adapter_registry):
        version = _make_version(db, "FailModel3")
        failing = FailingAdapter(adapter_id="fail-adapter-3", version_id=version.id)
        adapter_registry.register_adapter(failing)
        svc = ModelExecutionService(db, adapter_registry)
        outcome = svc.execute_model(version.id, {})
        assert outcome.outputs is None

    def test_run_persisted_as_failed(self, db, adapter_registry):
        version = _make_version(db, "FailModel4")
        failing = FailingAdapter(adapter_id="fail-adapter-4", version_id=version.id)
        adapter_registry.register_adapter(failing)
        svc = ModelExecutionService(db, adapter_registry)
        outcome = svc.execute_model(version.id, {})
        run = svc.get_execution_run(outcome.run_id)
        assert run.status == "failed"

    def test_step_persisted_as_failed(self, db, adapter_registry):
        version = _make_version(db, "FailModel5")
        failing = FailingAdapter(adapter_id="fail-adapter-5", version_id=version.id)
        adapter_registry.register_adapter(failing)
        svc = ModelExecutionService(db, adapter_registry)
        outcome = svc.execute_model(version.id, {})
        step = svc.get_execution_step(outcome.step_id)
        assert step.status == "failed"
        assert step.error_message is not None


# ---------------------------------------------------------------------------
# AC-08: Missing version/adapter rejected
# ---------------------------------------------------------------------------

class TestMissingDependencies:
    def test_missing_version_raises(self, svc, db):
        with pytest.raises(ExecutionVersionNotFoundError):
            svc.execute_model("nonexistent-version-id", {})

    def test_missing_adapter_raises(self, svc, db):
        version = _make_version(db, "NoAdapterModel")
        with pytest.raises(ExecutionAdapterNotFoundError):
            svc.execute_model(version.id, {})


# ---------------------------------------------------------------------------
# AC-10: Execution history lookup
# ---------------------------------------------------------------------------

class TestExecutionHistory:
    def test_list_recent_runs_includes_completed(self, svc, db, adapter_registry):
        version = _make_version(db, "HistoryModel1")
        _register_synthetic(adapter_registry, version, "hist-adapter-1")
        outcome = svc.execute_model(version.id, {"val": 7.0})
        runs = svc.list_recent_runs(limit=50)
        assert any(r.id == outcome.run_id for r in runs)

    def test_list_execution_steps_for_run(self, svc, db, adapter_registry):
        version = _make_version(db, "HistoryModel2")
        _register_synthetic(adapter_registry, version, "hist-adapter-2")
        outcome = svc.execute_model(version.id, {})
        steps = svc.list_execution_steps(outcome.run_id)
        assert len(steps) == 1
        assert steps[0].id == outcome.step_id

    def test_get_run_not_found_raises(self, svc, db):
        with pytest.raises(ExecutionRunNotFoundError):
            svc.get_execution_run("ghost-run-id")

    def test_get_step_not_found_raises(self, svc, db):
        with pytest.raises(ExecutionStepNotFoundError):
            svc.get_execution_step("ghost-step-id")


# ---------------------------------------------------------------------------
# AC-11/AC-12: /health still green
# ---------------------------------------------------------------------------

class TestHealthStillGreen:
    def test_health_200_ok(self):
        from fastapi.testclient import TestClient
        from backend.app.main import api

        client = TestClient(api, raise_server_exceptions=True)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("db") == "ok"
