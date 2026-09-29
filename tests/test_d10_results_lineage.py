"""
D10 Results & Lineage tests — server-authoritative since D16.

Results are recorded by the execution service for every declared output dataset of a
successful step; callers never supply a version→dataset mapping. Verifies that:
- single-model execution persists a Result for its declared output (AC-01..04)
- results can be retrieved by id and by run (AC-05)
- graph execution records results for every successful node with a declared output (AC-06)
- lineage edges connect dependency-linked results inside one GraphRun (AC-07)
- independent branches do NOT receive cross-branch lineage (AC-08)
- D9 propagation produces new result records under one run (AC-09)
- failed execution records no result (AC-10)
- retrieval is deterministic (AC-11)

Real SQLite, per-test rollback isolation, no mocks.
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d10_tmp")

from backend.app.adapters.base import ModelAdapter  # noqa: E402
from backend.app.adapters.synthetic import SyntheticAdapter  # noqa: E402
from backend.app.persistence.database import (  # noqa: E402
    Base, Dataset, Dependency, Model, ModelVersion,
)
from backend.app.services.adapter_registry import AdapterRegistry  # noqa: E402
from backend.app.services.change_propagation import ChangePropagationService  # noqa: E402
from backend.app.services.execution import ModelExecutionService  # noqa: E402
from backend.app.services.federation import FederationService  # noqa: E402
from backend.app.services.orchestration import GraphOrchestrationService  # noqa: E402
from backend.app.services.results_lineage import (  # noqa: E402
    ResultNotFoundError,
    ResultsLineageService,
)


# ---------------------------------------------------------------------------
# Module-scoped engine + per-test session rollback
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
def registry(db):
    return AdapterRegistry(db)


@pytest.fixture
def exec_svc(db, registry):
    return ModelExecutionService(db, registry)


@pytest.fixture
def orch_svc(db, registry):
    return GraphOrchestrationService(db, registry)


@pytest.fixture
def prop_svc(db, registry):
    return ChangePropagationService(db, registry)


@pytest.fixture
def rl_svc(db):
    return ResultsLineageService(db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_counter = {"n": 0}


def _uniq(prefix: str) -> str:
    _counter["n"] += 1
    return f"{prefix}-{_counter['n']}"


def _make_version(db, name: str, semver: str = "1.0.0") -> ModelVersion:
    m = Model(name=_uniq(name), owner="test", model_type="python")
    db.add(m)
    db.flush()
    v = ModelVersion(model_id=m.id, semver=semver, is_active=True)
    db.add(v)
    db.flush()
    return v


def _make_dataset(db, name: str) -> Dataset:
    d = Dataset(name=_uniq(name))
    db.add(d)
    db.flush()
    return d


def _register(registry, version: ModelVersion, scalar: float = 2.0) -> None:
    registry.register_adapter(SyntheticAdapter(
        adapter_id=_uniq("adapter"), version_id=version.id, scalar=scalar,
    ))


def _link(db, producer: ModelVersion, consumer: ModelVersion, dataset: Dataset) -> None:
    db.add(Dependency(
        producer_version_id=producer.id,
        consumer_version_id=consumer.id,
        output_dataset_id=dataset.id,
        input_dataset_id=dataset.id,
    ))
    db.flush()


def _declare_output(db, version: ModelVersion, dataset: Dataset) -> None:
    FederationService(db).bind_output(version.id, dataset.id)


class _BrokenAdapter(ModelAdapter):
    def __init__(self, version_id: str) -> None:
        self._vid = version_id

    @property
    def adapter_id(self): return f"broken-{self._vid}"

    @property
    def version_id(self): return self._vid

    def invoke(self, inputs): raise RuntimeError("intentional failure")


# ---------------------------------------------------------------------------
# AC-01..04: single execution result persisted for the declared output
# ---------------------------------------------------------------------------

class TestSingleExecutionResult:
    def test_result_recorded_for_declared_output(self, exec_svc, rl_svc, db, registry):
        version = _make_version(db, "SingleModel")
        dataset = _make_dataset(db, "SingleDS")
        _declare_output(db, version, dataset)
        _register(registry, version, scalar=3.0)

        outcome = exec_svc.execute_model(version.id, {"x": 10.0})

        assert outcome.status == "succeeded"
        assert len(outcome.result_ids) == 1
        result = rl_svc.get_result(outcome.result_ids[0])
        assert result.run_id == outcome.run_id
        assert result.step_id == outcome.step_id
        assert result.dataset_id == dataset.id
        assert result.model_version_id == version.id
        assert json.loads(result.value_json) == {"x": 30.0}

    def test_no_declared_output_means_no_result(self, exec_svc, rl_svc, db, registry):
        version = _make_version(db, "NoOutputModel")
        _register(registry, version, scalar=1.0)
        outcome = exec_svc.execute_model(version.id, {"x": 1.0})
        assert outcome.status == "succeeded"
        assert outcome.result_ids == ()
        assert rl_svc.list_results_for_run(outcome.run_id) == []

    def test_failed_execution_records_no_result(self, exec_svc, rl_svc, db, registry):
        """AC-10: a failed execution must not be recorded as a result."""
        version = _make_version(db, "FailModel")
        dataset = _make_dataset(db, "FailDS")
        _declare_output(db, version, dataset)
        registry.register_adapter(_BrokenAdapter(version.id))

        outcome = exec_svc.execute_model(version.id, {"x": 1.0})

        assert outcome.status == "failed"
        assert "intentional failure" in outcome.error
        assert rl_svc.list_results_for_run(outcome.run_id) == []

    def test_single_execution_does_not_publish_dataset(self, exec_svc, db, registry):
        version = _make_version(db, "NoPublishModel")
        dataset = _make_dataset(db, "NoPublishDS")
        _declare_output(db, version, dataset)
        _register(registry, version, scalar=2.0)
        exec_svc.execute_model(version.id, {"x": 5.0})
        db.refresh(dataset)
        assert dataset.current_value is None


# ---------------------------------------------------------------------------
# AC-05: Result retrieval
# ---------------------------------------------------------------------------

class TestResultRetrieval:
    def test_get_result_by_id(self, exec_svc, rl_svc, db, registry):
        version = _make_version(db, "GetModel")
        dataset = _make_dataset(db, "GetDS")
        _declare_output(db, version, dataset)
        _register(registry, version, scalar=1.0)
        outcome = exec_svc.execute_model(version.id, {"v": 42.0})
        fetched = rl_svc.get_result(outcome.result_ids[0])
        assert fetched.id == outcome.result_ids[0]

    def test_get_result_not_found_raises(self, rl_svc):
        with pytest.raises(ResultNotFoundError):
            rl_svc.get_result("does-not-exist")

    def test_list_results_for_run(self, exec_svc, rl_svc, db, registry):
        version = _make_version(db, "ListModel")
        dataset = _make_dataset(db, "ListDS")
        _declare_output(db, version, dataset)
        _register(registry, version, scalar=2.0)
        outcome = exec_svc.execute_model(version.id, {"y": 7.0})
        results = rl_svc.list_results_for_run(outcome.run_id)
        assert [r.id for r in results] == list(outcome.result_ids)


# ---------------------------------------------------------------------------
# AC-06: Graph execution records results for successful nodes
# ---------------------------------------------------------------------------

class TestGraphExecutionResults:
    def test_linear_chain_results_created(self, orch_svc, rl_svc, db, registry):
        """A→B→C with C declaring a terminal output: three results in one run."""
        va, vb, vc = (_make_version(db, n) for n in ("A", "B", "C"))
        ds_ab, ds_bc, ds_c = (_make_dataset(db, n) for n in ("DS_AB", "DS_BC", "DS_C"))
        _link(db, va, vb, ds_ab)
        _link(db, vb, vc, ds_bc)
        _declare_output(db, vc, ds_c)
        _register(registry, va, scalar=2.0)
        _register(registry, vb, scalar=3.0)
        _register(registry, vc, scalar=4.0)

        go = orch_svc.execute_graph(vc.id, {"fuel": 10.0}, triggered_by="test")

        assert go.success
        results = rl_svc.list_results_for_run(go.run_id)
        assert len(results) == 3
        by_version = {r.model_version_id: json.loads(r.value_json) for r in results}
        assert by_version == {
            va.id: {"fuel": 20.0}, vb.id: {"fuel": 60.0}, vc.id: {"fuel": 240.0},
        }

    def test_failed_upstream_records_no_results(self, orch_svc, rl_svc, db, registry):
        va = _make_version(db, "FailA")
        vb = _make_version(db, "FailB")
        ds_ab = _make_dataset(db, "DS_FailAB")
        _link(db, va, vb, ds_ab)
        registry.register_adapter(_BrokenAdapter(va.id))
        _register(registry, vb, scalar=1.0)

        go = orch_svc.execute_graph(vb.id, {"x": 1.0}, triggered_by="test")

        assert not go.success
        assert go.result_ids == ()
        assert rl_svc.list_results_for_run(go.run_id) == []

    def test_node_without_declared_output_has_no_result(self, orch_svc, rl_svc, db, registry):
        va = _make_version(db, "MapA")
        vb = _make_version(db, "MapB")
        ds_ab = _make_dataset(db, "DS_MapAB")
        _link(db, va, vb, ds_ab)          # A declares ds_ab (via the edge); B declares nothing
        _register(registry, va, scalar=1.0)
        _register(registry, vb, scalar=1.0)

        go = orch_svc.execute_graph(vb.id, {"x": 5.0}, triggered_by="test")

        results = rl_svc.list_results_for_run(go.run_id)
        assert [r.model_version_id for r in results] == [va.id]


# ---------------------------------------------------------------------------
# AC-07: Lineage edges for dependency-linked nodes, inside the GraphRun
# ---------------------------------------------------------------------------

class TestLineageCreation:
    def _two_node(self, db, registry):
        va = _make_version(db, "LinA")
        vb = _make_version(db, "LinB")
        ds_ab = _make_dataset(db, "DS_LinAB")
        ds_b = _make_dataset(db, "DS_LinB")
        _link(db, va, vb, ds_ab)
        _declare_output(db, vb, ds_b)
        _register(registry, va, scalar=2.0)
        _register(registry, vb, scalar=3.0)
        return va, vb, ds_ab, ds_b

    def test_lineage_edge_created_for_dependency(self, orch_svc, rl_svc, db, registry):
        va, vb, ds_ab, ds_b = self._two_node(db, registry)
        go = orch_svc.execute_graph(vb.id, {"x": 5.0}, triggered_by="test")
        by_version = {r.model_version_id: r for r in rl_svc.list_results_for_run(go.run_id)}
        ra, rb = by_version[va.id], by_version[vb.id]

        edges = rl_svc.list_lineage_from_result(ra.id)
        assert len(edges) == 1
        edge = edges[0]
        assert edge.target_result_id == rb.id
        assert edge.source_dataset_id == ds_ab.id
        assert edge.target_dataset_id == ds_b.id
        assert edge.run_id == go.run_id
        assert edge.step_id == go.step_outcomes[vb.id].step_id

    def test_lineage_listed_for_graph_run(self, orch_svc, rl_svc, db, registry):
        va, vb, *_ = self._two_node(db, registry)
        go = orch_svc.execute_graph(vb.id, {"x": 1.0}, triggered_by="test")
        edges = rl_svc.list_lineage_for_run(go.run_id)
        assert len(edges) == 1

    def test_reverse_lineage_lookup(self, orch_svc, rl_svc, db, registry):
        va, vb, *_ = self._two_node(db, registry)
        go = orch_svc.execute_graph(vb.id, {"x": 4.0}, triggered_by="test")
        rb = next(r for r in rl_svc.list_results_for_run(go.run_id) if r.model_version_id == vb.id)
        back = rl_svc.list_lineage_to_result(rb.id)
        assert len(back) == 1 and back[0].target_result_id == rb.id


# ---------------------------------------------------------------------------
# AC-08: Branch lineage isolation
# ---------------------------------------------------------------------------

class TestBranchLineageIsolation:
    def test_independent_branches_no_cross_lineage(self, orch_svc, rl_svc, db, registry):
        """root → left and root → right: left results never link into right results."""
        vroot, vleft, vright = (_make_version(db, n) for n in ("Root", "Left", "Right"))
        ds_root = _make_dataset(db, "DS_Root")
        ds_left_out = _make_dataset(db, "DS_LeftOut")
        ds_right_out = _make_dataset(db, "DS_RightOut")
        fed = FederationService(db)
        fed.connect(producer_version_id=vroot.id, dataset_id=ds_root.id, consumer_version_id=vleft.id)
        fed.connect(producer_version_id=vroot.id, dataset_id=ds_root.id, consumer_version_id=vright.id)
        _declare_output(db, vleft, ds_left_out)
        _declare_output(db, vright, ds_right_out)
        _register(registry, vroot, scalar=2.0)
        _register(registry, vleft, scalar=3.0)
        _register(registry, vright, scalar=5.0)

        go_left = orch_svc.execute_graph(vleft.id, {"x": 1.0}, triggered_by="test")
        go_right = orch_svc.execute_graph(vright.id, {"x": 1.0}, triggered_by="test")

        right_ids = {r.id for r in rl_svc.list_results_for_run(go_right.run_id)}
        for r in rl_svc.list_results_for_run(go_left.run_id):
            for e in rl_svc.list_lineage_from_result(r.id):
                assert e.target_result_id not in right_ids


# ---------------------------------------------------------------------------
# AC-09: propagation produces new results under one run
# ---------------------------------------------------------------------------

class TestPropagationResults:
    def _chain(self, db, registry):
        va = _make_version(db, "PropA")
        vb = _make_version(db, "PropB")
        ds_ab = _make_dataset(db, "DS_PropAB")
        ds_b = _make_dataset(db, "DS_PropB")
        ds_trigger = _make_dataset(db, "DS_Trigger")
        _link(db, va, vb, ds_ab)
        _declare_output(db, vb, ds_b)
        _register(registry, va, scalar=2.0)
        _register(registry, vb, scalar=3.0)
        return va, vb, ds_trigger

    def test_propagation_produces_results_in_one_run(self, prop_svc, rl_svc, db, registry):
        va, vb, ds_trigger = self._chain(db, registry)
        event = prop_svc.record_change(dataset_id=ds_trigger.id, source_version_id=va.id)
        pr = prop_svc.propagate(event.id, {"fuel": 120.0})

        assert pr.success
        results = rl_svc.list_results_for_run(pr.run_id)
        assert len(results) == 2
        assert {r.run_id for r in results} == {pr.run_id}

    def test_second_propagation_produces_new_results(self, prop_svc, rl_svc, db, registry):
        va, vb, ds_trigger = self._chain(db, registry)
        ev1 = prop_svc.record_change(dataset_id=ds_trigger.id, source_version_id=va.id)
        pr1 = prop_svc.propagate(ev1.id, {"fuel": 100.0})
        ev2 = prop_svc.record_change(dataset_id=ds_trigger.id, source_version_id=va.id)
        pr2 = prop_svc.propagate(ev2.id, {"fuel": 120.0})

        ids1 = {r.id for r in rl_svc.list_results_for_run(pr1.run_id)}
        ids2 = {r.id for r in rl_svc.list_results_for_run(pr2.run_id)}
        assert ids1 and ids2 and ids1.isdisjoint(ids2)


# ---------------------------------------------------------------------------
# AC-11: Deterministic retrieval order
# ---------------------------------------------------------------------------

class TestDeterministicRetrieval:
    def test_list_results_for_run_stable_order(self, orch_svc, rl_svc, db, registry):
        va = _make_version(db, "StableA")
        vb = _make_version(db, "StableB")
        ds_ab = _make_dataset(db, "DS_StableAB")
        ds_b = _make_dataset(db, "DS_StableB")
        _link(db, va, vb, ds_ab)
        _declare_output(db, vb, ds_b)
        _register(registry, va, scalar=1.0)
        _register(registry, vb, scalar=1.0)
        go = orch_svc.execute_graph(vb.id, {"z": 99.0}, triggered_by="test")

        a = [r.id for r in rl_svc.list_results_for_run(go.run_id)]
        b = [r.id for r in rl_svc.list_results_for_run(go.run_id)]
        assert a == b and len(a) == 2
        edges_a = [e.id for e in rl_svc.list_lineage_for_run(go.run_id)]
        edges_b = [e.id for e in rl_svc.list_lineage_for_run(go.run_id)]
        assert edges_a == edges_b and len(edges_a) == 1
