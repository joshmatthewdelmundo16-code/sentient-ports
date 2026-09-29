"""
D2 repository tests — AC-01 through AC-09.

All tests use an isolated in-memory SQLite session.
Tests cover:
  - create / get / list for all ten repositories
  - entity-specific lookup methods (AC-03)
  - not-found behavior
  - duplicate/constraint behavior
  - update_status helpers
  - /health still green (AC-07)
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d2_tmp")

from backend.app.persistence.database import (  # noqa: E402
    Base,
    ChangeEvent,
    DataContract,
    Dataset,
    Dependency,
    ExecutionRun,
    ExecutionStep,
    LineageEdge,
    Model,
    ModelVersion,
    Result,
)
from backend.app.persistence import (  # noqa: E402
    ChangeEventRepository,
    DataContractRepository,
    DatasetRepository,
    DependencyRepository,
    ExecutionRunRepository,
    ExecutionStepRepository,
    LineageRepository,
    ModelRepository,
    ModelVersionRepository,
    NotFoundError,
    DuplicateError,
    ResultRepository,
)


# ---------------------------------------------------------------------------
# Module-scoped engine + session factory
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
    """Per-test session that rolls back after each test for isolation."""
    session = SessionFactory()
    yield session
    session.rollback()
    session.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _model(db, name="ModelA", owner="demo", status="active"):
    m = Model(name=name, owner=owner, model_type="python", status=status)
    db.add(m)
    db.flush()
    return m


def _version(db, model_id, semver="1.0.0", active=True):
    v = ModelVersion(model_id=model_id, semver=semver, is_active=active)
    db.add(v)
    db.flush()
    return v


def _dataset(db, name="shipping_cost"):
    ds = Dataset(name=name, description="test dataset")
    db.add(ds)
    db.flush()
    return ds


def _run(db, status="requested"):
    r = ExecutionRun(status=status, triggered_by="test")
    db.add(r)
    db.flush()
    return r


def _step(db, run_id, ver_id, order=0, status="succeeded"):
    s = ExecutionStep(run_id=run_id, model_version_id=ver_id, step_order=order, status=status)
    db.add(s)
    db.flush()
    return s


def _result(db, run_id, step_id, dataset_id, value=1.0):
    r = Result(
        run_id=run_id,
        step_id=step_id,
        dataset_id=dataset_id,
        value_json=json.dumps({"v": value}),
        value_numeric=value,
    )
    db.add(r)
    db.flush()
    return r


# ---------------------------------------------------------------------------
# ModelRepository
# ---------------------------------------------------------------------------

class TestModelRepository:
    def test_create_and_get(self, db):
        repo = ModelRepository(db)
        m = repo.add(Model(name="M1", owner="ownerX", model_type="python", status="draft"))
        db.commit()
        reloaded = repo.get(m.id)
        assert reloaded.name == "M1"

    def test_get_by_name(self, db):
        repo = ModelRepository(db)
        repo.add(Model(name="FindMe", owner="ownerY", model_type="python", status="active"))
        db.commit()
        found = repo.get_by_name("FindMe", "ownerY")
        assert found.owner == "ownerY"

    def test_list(self, db):
        repo = ModelRepository(db)
        repo.add(Model(name="ListA", owner="ownerZ", model_type="python", status="active"))
        repo.add(Model(name="ListB", owner="ownerZ", model_type="python", status="active"))
        db.commit()
        items = repo.list(limit=50)
        names = {m.name for m in items}
        assert "ListA" in names and "ListB" in names

    def test_get_not_found(self, db):
        repo = ModelRepository(db)
        with pytest.raises(NotFoundError):
            repo.get("nonexistent-id")

    def test_get_by_name_not_found(self, db):
        repo = ModelRepository(db)
        with pytest.raises(NotFoundError):
            repo.get_by_name("Ghost", "nobody")

    def test_duplicate_raises(self, db):
        repo = ModelRepository(db)
        repo.add(Model(name="DupModel", owner="ownerDup", model_type="python", status="draft"))
        db.commit()
        with pytest.raises(DuplicateError):
            repo.add(Model(name="DupModel", owner="ownerDup", model_type="python", status="draft"))


# ---------------------------------------------------------------------------
# ModelVersionRepository
# ---------------------------------------------------------------------------

class TestModelVersionRepository:
    def test_create_and_get(self, db):
        m = _model(db, name="MVModel1", owner="o1")
        repo = ModelVersionRepository(db)
        v = repo.add(ModelVersion(model_id=m.id, semver="1.0.0", is_active=True))
        db.commit()
        assert repo.get(v.id).semver == "1.0.0"

    def test_list_by_model(self, db):
        m = _model(db, name="MVModel2", owner="o2")
        repo = ModelVersionRepository(db)
        repo.add(ModelVersion(model_id=m.id, semver="1.0.0", is_active=False))
        repo.add(ModelVersion(model_id=m.id, semver="2.0.0", is_active=True))
        db.commit()
        versions = repo.list_by_model(m.id)
        assert len(versions) == 2
        assert [v.semver for v in versions] == ["1.0.0", "2.0.0"]

    def test_get_by_model_and_semver(self, db):
        m = _model(db, name="MVModel3", owner="o3")
        repo = ModelVersionRepository(db)
        repo.add(ModelVersion(model_id=m.id, semver="3.0.0", is_active=False))
        db.commit()
        v = repo.get_by_model_and_semver(m.id, "3.0.0")
        assert v.model_id == m.id

    def test_get_active(self, db):
        m = _model(db, name="MVModel4", owner="o4")
        repo = ModelVersionRepository(db)
        repo.add(ModelVersion(model_id=m.id, semver="1.0.0", is_active=False))
        repo.add(ModelVersion(model_id=m.id, semver="2.0.0", is_active=True))
        db.commit()
        active = repo.get_active(m.id)
        assert active is not None
        assert active.semver == "2.0.0"

    def test_get_by_semver_not_found(self, db):
        m = _model(db, name="MVModel5", owner="o5")
        repo = ModelVersionRepository(db)
        with pytest.raises(NotFoundError):
            repo.get_by_model_and_semver(m.id, "99.0.0")


# ---------------------------------------------------------------------------
# DataContractRepository
# ---------------------------------------------------------------------------

class TestDataContractRepository:
    def test_create_and_get(self, db):
        ds = _dataset(db, name="dc_ds_1")
        repo = DataContractRepository(db)
        dc = repo.add(DataContract(
            dataset_id=ds.id,
            schema_json=json.dumps({"fields": [{"name": "x", "type": "float"}]}),
            semver="1.0.0",
        ))
        db.commit()
        assert repo.get(dc.id).dataset_id == ds.id

    def test_get_by_dataset_and_semver(self, db):
        ds = _dataset(db, name="dc_ds_2")
        repo = DataContractRepository(db)
        repo.add(DataContract(
            dataset_id=ds.id,
            schema_json=json.dumps({"fields": []}),
            semver="2.0.0",
        ))
        db.commit()
        dc = repo.get_by_dataset_and_semver(ds.id, "2.0.0")
        assert dc.semver == "2.0.0"

    def test_list_by_dataset(self, db):
        ds = _dataset(db, name="dc_ds_3")
        repo = DataContractRepository(db)
        repo.add(DataContract(dataset_id=ds.id, schema_json="{}", semver="1.0.0"))
        repo.add(DataContract(dataset_id=ds.id, schema_json="{}", semver="1.1.0"))
        db.commit()
        contracts = repo.list_by_dataset(ds.id)
        assert len(contracts) == 2

    def test_not_found(self, db):
        ds = _dataset(db, name="dc_ds_4")
        repo = DataContractRepository(db)
        with pytest.raises(NotFoundError):
            repo.get_by_dataset_and_semver(ds.id, "0.0.0")


# ---------------------------------------------------------------------------
# DatasetRepository
# ---------------------------------------------------------------------------

class TestDatasetRepository:
    def test_create_and_get(self, db):
        repo = DatasetRepository(db)
        ds = repo.add(Dataset(name="ds_create_1", description="test"))
        db.commit()
        assert repo.get(ds.id).name == "ds_create_1"

    def test_get_by_name(self, db):
        repo = DatasetRepository(db)
        repo.add(Dataset(name="ds_byname_1", description="test"))
        db.commit()
        found = repo.get_by_name("ds_byname_1")
        assert found is not None

    def test_list(self, db):
        repo = DatasetRepository(db)
        repo.add(Dataset(name="ds_list_1"))
        repo.add(Dataset(name="ds_list_2"))
        db.commit()
        names = {ds.name for ds in repo.list(limit=200)}
        assert "ds_list_1" in names and "ds_list_2" in names

    def test_not_found(self, db):
        repo = DatasetRepository(db)
        with pytest.raises(NotFoundError):
            repo.get_by_name("does_not_exist_xyz")


# ---------------------------------------------------------------------------
# DependencyRepository
# ---------------------------------------------------------------------------

class TestDependencyRepository:
    def _setup(self, db):
        ma = _model(db, name="DepModelA", owner="dep")
        mb = _model(db, name="DepModelB", owner="dep")
        va = _version(db, ma.id, "1.0.0")
        vb = _version(db, mb.id, "1.0.0")
        ds = _dataset(db, name="dep_ds_1")
        return va, vb, ds

    def test_create_and_list_by_producer(self, db):
        va, vb, ds = self._setup(db)
        repo = DependencyRepository(db)
        repo.add(Dependency(
            producer_version_id=va.id,
            output_dataset_id=ds.id,
            consumer_version_id=vb.id,
            input_dataset_id=ds.id,
            dependency_kind="data",
        ))
        db.commit()
        edges = repo.list_by_producer(va.id)
        assert len(edges) == 1
        assert edges[0].consumer_version_id == vb.id

    def test_list_by_consumer(self, db):
        ma = _model(db, name="DepModelC", owner="dep2")
        mb = _model(db, name="DepModelD", owner="dep2")
        va = _version(db, ma.id, "1.0.0")
        vb = _version(db, mb.id, "1.0.0")
        ds = _dataset(db, name="dep_ds_2")
        repo = DependencyRepository(db)
        repo.add(Dependency(
            producer_version_id=va.id,
            output_dataset_id=ds.id,
            consumer_version_id=vb.id,
            input_dataset_id=ds.id,
            dependency_kind="data",
        ))
        db.commit()
        edges = repo.list_by_consumer(vb.id)
        assert len(edges) == 1
        assert edges[0].producer_version_id == va.id


# ---------------------------------------------------------------------------
# ExecutionRunRepository
# ---------------------------------------------------------------------------

class TestExecutionRunRepository:
    def test_create_and_get(self, db):
        repo = ExecutionRunRepository(db)
        run = repo.add(ExecutionRun(status="requested", triggered_by="pytest"))
        db.commit()
        assert repo.get(run.id).status == "requested"

    def test_list_recent(self, db):
        repo = ExecutionRunRepository(db)
        repo.add(ExecutionRun(status="succeeded", triggered_by="t1"))
        repo.add(ExecutionRun(status="failed", triggered_by="t2"))
        db.commit()
        recent = repo.list_recent(limit=10)
        assert len(recent) >= 2

    def test_update_status(self, db):
        repo = ExecutionRunRepository(db)
        run = repo.add(ExecutionRun(status="requested", triggered_by="t3"))
        db.commit()
        updated = repo.update_status(run.id, "running")
        db.commit()
        assert updated.status == "running"
        assert repo.get(run.id).status == "running"

    def test_not_found(self, db):
        repo = ExecutionRunRepository(db)
        with pytest.raises(NotFoundError):
            repo.get("ghost-run-id")


# ---------------------------------------------------------------------------
# ExecutionStepRepository
# ---------------------------------------------------------------------------

class TestExecutionStepRepository:
    def test_create_and_list_by_run(self, db):
        m = _model(db, name="StepModel1", owner="step")
        v = _version(db, m.id)
        run = _run(db)
        repo = ExecutionStepRepository(db)
        repo.add(ExecutionStep(run_id=run.id, model_version_id=v.id, step_order=0, status="succeeded"))
        repo.add(ExecutionStep(run_id=run.id, model_version_id=v.id, step_order=1, status="succeeded"))
        db.commit()
        steps = repo.list_by_run(run.id)
        assert len(steps) == 2
        assert steps[0].step_order == 0

    def test_update_status(self, db):
        m = _model(db, name="StepModel2", owner="step")
        v = _version(db, m.id)
        run = _run(db)
        repo = ExecutionStepRepository(db)
        step = repo.add(ExecutionStep(run_id=run.id, model_version_id=v.id, step_order=0, status="validating"))
        db.commit()
        updated = repo.update_status(step.id, "running")
        db.commit()
        assert updated.status == "running"

    def test_not_found(self, db):
        repo = ExecutionStepRepository(db)
        with pytest.raises(NotFoundError):
            repo.get("ghost-step-id")


# ---------------------------------------------------------------------------
# ResultRepository
# ---------------------------------------------------------------------------

class TestResultRepository:
    def test_create_and_list_by_run(self, db):
        m = _model(db, name="ResModel1", owner="res")
        v = _version(db, m.id)
        ds = _dataset(db, name="res_ds_1")
        run = _run(db)
        step = _step(db, run.id, v.id)
        repo = ResultRepository(db)
        repo.add(Result(
            run_id=run.id, step_id=step.id, dataset_id=ds.id,
            value_json=json.dumps({"v": 42.0}), value_numeric=42.0,
        ))
        db.commit()
        results = repo.list_by_run(run.id)
        assert len(results) == 1
        assert results[0].value_numeric == 42.0

    def test_list_by_dataset(self, db):
        m = _model(db, name="ResModel2", owner="res")
        v = _version(db, m.id)
        ds = _dataset(db, name="res_ds_2")
        run1 = _run(db)
        run2 = _run(db)
        step1 = _step(db, run1.id, v.id, order=0)
        step2 = _step(db, run2.id, v.id, order=0)
        repo = ResultRepository(db)
        repo.add(Result(run_id=run1.id, step_id=step1.id, dataset_id=ds.id,
                        value_json=json.dumps({"v": 1.0}), value_numeric=1.0))
        repo.add(Result(run_id=run2.id, step_id=step2.id, dataset_id=ds.id,
                        value_json=json.dumps({"v": 2.0}), value_numeric=2.0))
        db.commit()
        results = repo.list_by_dataset(ds.id)
        assert len(results) == 2

    def test_not_found(self, db):
        repo = ResultRepository(db)
        with pytest.raises(NotFoundError):
            repo.get("ghost-result-id")


# ---------------------------------------------------------------------------
# LineageRepository
# ---------------------------------------------------------------------------

class TestLineageRepository:
    def test_create_and_list_by_run(self, db):
        m = _model(db, name="LinModel1", owner="lin")
        v = _version(db, m.id)
        ds_src = _dataset(db, name="lin_ds_src")
        ds_tgt = _dataset(db, name="lin_ds_tgt")
        run = _run(db)
        step = _step(db, run.id, v.id)
        res_src = _result(db, run.id, step.id, ds_src.id, value=500.0)
        res_tgt = _result(db, run.id, step.id, ds_tgt.id, value=700.0)
        repo = LineageRepository(db)
        edge = repo.add(LineageEdge(
            run_id=run.id, step_id=step.id,
            source_result_id=res_src.id, target_result_id=res_tgt.id,
        ))
        db.commit()

        by_run = repo.list_by_run(run.id)
        assert len(by_run) == 1

        by_src = repo.list_by_source(res_src.id)
        assert len(by_src) == 1
        assert by_src[0].target_result_id == res_tgt.id

        by_tgt = repo.list_by_target(res_tgt.id)
        assert len(by_tgt) == 1
        assert by_tgt[0].source_result_id == res_src.id

    def test_get(self, db):
        m = _model(db, name="LinModel2", owner="lin")
        v = _version(db, m.id)
        ds = _dataset(db, name="lin_ds_get")
        run = _run(db)
        step = _step(db, run.id, v.id)
        res = _result(db, run.id, step.id, ds.id, value=1.0)
        repo = LineageRepository(db)
        edge = repo.add(LineageEdge(run_id=run.id, step_id=step.id, source_result_id=res.id))
        db.commit()
        assert repo.get(edge.id) is not None

    def test_not_found(self, db):
        repo = LineageRepository(db)
        with pytest.raises(NotFoundError):
            repo.get("ghost-edge-id")


# ---------------------------------------------------------------------------
# ChangeEventRepository
# ---------------------------------------------------------------------------

class TestChangeEventRepository:
    def test_create_and_get(self, db):
        ds = _dataset(db, name="ce_ds_1")
        repo = ChangeEventRepository(db)
        evt = repo.add(ChangeEvent(
            dataset_id=ds.id,
            old_value_json=json.dumps({"fuel": 80}),
            new_value_json=json.dumps({"fuel": 100}),
            triggered_by="user",
        ))
        db.commit()
        assert repo.get(evt.id).dataset_id == ds.id

    def test_list_recent(self, db):
        ds = _dataset(db, name="ce_ds_2")
        repo = ChangeEventRepository(db)
        repo.add(ChangeEvent(dataset_id=ds.id, new_value_json="{}", triggered_by="a"))
        repo.add(ChangeEvent(dataset_id=ds.id, new_value_json="{}", triggered_by="b"))
        db.commit()
        events = repo.list_recent(limit=5)
        assert len(events) >= 2

    def test_list_by_dataset(self, db):
        ds = _dataset(db, name="ce_ds_3")
        repo = ChangeEventRepository(db)
        repo.add(ChangeEvent(dataset_id=ds.id, new_value_json=json.dumps({"v": 1}), triggered_by="x"))
        repo.add(ChangeEvent(dataset_id=ds.id, new_value_json=json.dumps({"v": 2}), triggered_by="y"))
        db.commit()
        events = repo.list_by_dataset(ds.id)
        assert len(events) == 2

    def test_not_found(self, db):
        repo = ChangeEventRepository(db)
        with pytest.raises(NotFoundError):
            repo.get("ghost-event-id")


# ---------------------------------------------------------------------------
# AC-07: /health still green
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
