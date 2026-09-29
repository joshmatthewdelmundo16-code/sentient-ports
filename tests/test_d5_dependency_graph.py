"""
D5 Dependency Graph service tests — AC-01 through AC-14.

Uses real SQLite persistence (no mocks). Per-test rollback isolation.
Synthetic graphs:
  Acyclic:  A → B → C
  Cyclic:   A → B → C → A
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d5_tmp")

from backend.app.persistence.database import Base, Dataset, Model, ModelVersion  # noqa: E402
from backend.app.services.dependency_graph import (  # noqa: E402
    CycleDetectedError,
    CycleReport,
    DependencyDatasetNotFoundError,
    DependencyGraphService,
    DependencyNotFoundError,
    DuplicateDependencyError,
    ProducerVersionNotFoundError,
    ConsumerVersionNotFoundError,
    SelfDependencyError,
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
def svc(db):
    return DependencyGraphService(db)


# ---------------------------------------------------------------------------
# Helpers — create persisted model versions and datasets
# ---------------------------------------------------------------------------

def _make_version(db, model_name: str, owner: str = "demo") -> ModelVersion:
    m = Model(name=model_name, owner=owner, model_type="python", status="active")
    db.add(m)
    db.flush()
    v = ModelVersion(model_id=m.id, semver="1.0.0", is_active=True)
    db.add(v)
    db.flush()
    return v


def _make_dataset(db, name: str) -> Dataset:
    ds = Dataset(name=name, description="test")
    db.add(ds)
    db.flush()
    return ds


def _edge(svc, db, va, vb, ds):
    """Register a single dependency edge va → vb via ds."""
    dep = svc.register_dependency(
        producer_version_id=va.id,
        output_dataset_id=ds.id,
        consumer_version_id=vb.id,
        input_dataset_id=ds.id,
        dependency_kind="data",
    )
    db.commit()
    return dep


# ---------------------------------------------------------------------------
# AC-02: Dependency registration
# ---------------------------------------------------------------------------

class TestDependencyRegistration:
    def test_register_valid_dependency(self, svc, db):
        va = _make_version(db, "RegA1")
        vb = _make_version(db, "RegB1")
        ds = _make_dataset(db, "reg_ds_1")
        dep = _edge(svc, db, va, vb, ds)
        assert dep.id is not None
        assert dep.producer_version_id == va.id
        assert dep.consumer_version_id == vb.id

    def test_register_stores_dependency_kind(self, svc, db):
        va = _make_version(db, "KindA")
        vb = _make_version(db, "KindB")
        ds = _make_dataset(db, "kind_ds")
        dep = svc.register_dependency(
            producer_version_id=va.id,
            output_dataset_id=ds.id,
            consumer_version_id=vb.id,
            input_dataset_id=ds.id,
            dependency_kind="execution",
        )
        db.commit()
        assert dep.dependency_kind == "execution"


# ---------------------------------------------------------------------------
# AC-02: Lookup
# ---------------------------------------------------------------------------

class TestDependencyLookup:
    def test_get_by_id(self, svc, db):
        va = _make_version(db, "GetA")
        vb = _make_version(db, "GetB")
        ds = _make_dataset(db, "get_ds")
        dep = _edge(svc, db, va, vb, ds)
        found = svc.get_dependency(dep.id)
        assert found.id == dep.id

    def test_list_dependencies(self, svc, db):
        va = _make_version(db, "ListA")
        vb = _make_version(db, "ListB")
        ds = _make_dataset(db, "list_ds")
        _edge(svc, db, va, vb, ds)
        deps = svc.list_dependencies()
        assert len(deps) >= 1

    def test_list_by_producer(self, svc, db):
        va = _make_version(db, "ProdA")
        vb = _make_version(db, "ProdB")
        ds = _make_dataset(db, "prod_ds")
        _edge(svc, db, va, vb, ds)
        by_prod = svc.list_by_producer(va.id)
        assert any(d.producer_version_id == va.id for d in by_prod)

    def test_list_by_consumer(self, svc, db):
        va = _make_version(db, "ConsA")
        vb = _make_version(db, "ConsB")
        ds = _make_dataset(db, "cons_ds")
        _edge(svc, db, va, vb, ds)
        by_cons = svc.list_by_consumer(vb.id)
        assert any(d.consumer_version_id == vb.id for d in by_cons)

    def test_get_not_found(self, svc, db):
        with pytest.raises(DependencyNotFoundError):
            svc.get_dependency("ghost-dep-id")


# ---------------------------------------------------------------------------
# AC-03: Duplicate rejected
# ---------------------------------------------------------------------------

class TestDuplicateRejection:
    def test_duplicate_edge_rejected(self, svc, db):
        va = _make_version(db, "DupA")
        vb = _make_version(db, "DupB")
        ds = _make_dataset(db, "dup_ds")
        _edge(svc, db, va, vb, ds)
        with pytest.raises(DuplicateDependencyError):
            svc.register_dependency(
                producer_version_id=va.id,
                output_dataset_id=ds.id,
                consumer_version_id=vb.id,
                input_dataset_id=ds.id,
            )


# ---------------------------------------------------------------------------
# AC-04: Reference validation
# ---------------------------------------------------------------------------

class TestReferenceValidation:
    def test_nonexistent_producer_rejected(self, svc, db):
        vb = _make_version(db, "RefConsumer")
        ds = _make_dataset(db, "ref_ds_1")
        with pytest.raises(ProducerVersionNotFoundError):
            svc.register_dependency(
                producer_version_id="ghost-producer-id",
                output_dataset_id=ds.id,
                consumer_version_id=vb.id,
                input_dataset_id=ds.id,
            )

    def test_nonexistent_consumer_rejected(self, svc, db):
        va = _make_version(db, "RefProducer")
        ds = _make_dataset(db, "ref_ds_2")
        with pytest.raises(ConsumerVersionNotFoundError):
            svc.register_dependency(
                producer_version_id=va.id,
                output_dataset_id=ds.id,
                consumer_version_id="ghost-consumer-id",
                input_dataset_id=ds.id,
            )

    def test_nonexistent_dataset_rejected(self, svc, db):
        va = _make_version(db, "RefDsA")
        vb = _make_version(db, "RefDsB")
        with pytest.raises(DependencyDatasetNotFoundError):
            svc.register_dependency(
                producer_version_id=va.id,
                output_dataset_id="ghost-ds-id",
                consumer_version_id=vb.id,
                input_dataset_id="ghost-ds-id",
            )

    def test_self_dependency_rejected(self, svc, db):
        va = _make_version(db, "SelfDepA")
        ds = _make_dataset(db, "self_ds")
        with pytest.raises(SelfDependencyError):
            svc.register_dependency(
                producer_version_id=va.id,
                output_dataset_id=ds.id,
                consumer_version_id=va.id,
                input_dataset_id=ds.id,
            )


# ---------------------------------------------------------------------------
# AC-05: Upstream / downstream traversal
# AC-06 / AC-07 / AC-08: Cycle detection and topological ordering
#
# Isolated graphs are built in fresh sessions via a shared module-scoped
# engine so that earlier test data doesn't interfere. Each class builds
# its own isolated in-memory engine to avoid cross-test contamination.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="class")
def isolated_session():
    """Fresh in-memory engine per test class — keeps graph tests clean."""
    from sqlalchemy import create_engine as _ce
    eng = _ce("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    session = SF()
    yield session
    session.close()
    eng.dispose()


def _build_abc_chain(session):
    """Persist A→B→C acyclic chain; return (va, vb, vc, ds_ab, ds_bc)."""
    svc = DependencyGraphService(session)
    va = _make_version(session, "ChainA", owner="chain")
    vb = _make_version(session, "ChainB", owner="chain")
    vc = _make_version(session, "ChainC", owner="chain")
    ds_ab = _make_dataset(session, "chain_ds_ab")
    ds_bc = _make_dataset(session, "chain_ds_bc")
    svc.register_dependency(
        producer_version_id=va.id, output_dataset_id=ds_ab.id,
        consumer_version_id=vb.id, input_dataset_id=ds_ab.id,
    )
    svc.register_dependency(
        producer_version_id=vb.id, output_dataset_id=ds_bc.id,
        consumer_version_id=vc.id, input_dataset_id=ds_bc.id,
    )
    session.commit()
    return svc, va, vb, vc, ds_ab, ds_bc


class TestGraphTraversal:
    @pytest.fixture(scope="class")
    def chain(self, isolated_session):
        """Build the A→B→C chain once for the whole class."""
        return _build_abc_chain(isolated_session)

    def test_direct_downstream(self, chain):
        svc, va, vb, vc, _, _ = chain
        downstream = svc.get_downstream(va.id)
        assert vb.id in downstream
        assert vc.id in downstream

    def test_direct_upstream(self, chain):
        svc, va, vb, vc, _, _ = chain
        upstream = svc.get_upstream(vc.id)
        assert va.id in upstream
        assert vb.id in upstream

    def test_transitive_upstream(self, chain):
        svc, va, vb, vc, _, _ = chain
        upstream = svc.get_upstream(vc.id)
        assert va.id in upstream
        assert vb.id in upstream

    def test_transitive_downstream(self, chain):
        svc, va, vb, vc, _, _ = chain
        downstream = svc.get_downstream(va.id)
        assert vb.id in downstream
        assert vc.id in downstream

    def test_unknown_node_returns_empty(self, chain):
        svc, *_ = chain
        assert svc.get_upstream("unknown-id") == []
        assert svc.get_downstream("unknown-id") == []


class TestCycleDetection:
    @pytest.fixture(scope="class")
    def acyclic_engine_session(self):
        from sqlalchemy import create_engine as _ce
        eng = _ce("sqlite:///:memory:", connect_args={"check_same_thread": False})

        @sa_event.listens_for(eng, "connect")
        def _fk(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(bind=eng)
        SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SF()
        _build_abc_chain(session)
        yield session
        session.close()
        eng.dispose()

    def test_acyclic_graph_reported_acyclic(self, acyclic_engine_session):
        svc = DependencyGraphService(acyclic_engine_session)
        report = svc.detect_cycles()
        assert isinstance(report, CycleReport)
        assert report.has_cycle is False
        assert report.cycle_nodes == []

    def test_cyclic_graph_detected(self):
        # Build a fresh isolated engine with A→B→C→A
        from sqlalchemy import create_engine as _ce
        eng = _ce("sqlite:///:memory:", connect_args={"check_same_thread": False})

        @sa_event.listens_for(eng, "connect")
        def _fk(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(bind=eng)
        SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SF()
        try:
            svc = DependencyGraphService(session)
            va = _make_version(session, "CycA", owner="cyc")
            vb = _make_version(session, "CycB", owner="cyc")
            vc = _make_version(session, "CycC", owner="cyc")
            ds_ab = _make_dataset(session, "cyc_ds_ab")
            ds_bc = _make_dataset(session, "cyc_ds_bc")
            ds_ca = _make_dataset(session, "cyc_ds_ca")

            svc.register_dependency(
                producer_version_id=va.id, output_dataset_id=ds_ab.id,
                consumer_version_id=vb.id, input_dataset_id=ds_ab.id,
            )
            svc.register_dependency(
                producer_version_id=vb.id, output_dataset_id=ds_bc.id,
                consumer_version_id=vc.id, input_dataset_id=ds_bc.id,
            )
            svc.register_dependency(
                producer_version_id=vc.id, output_dataset_id=ds_ca.id,
                consumer_version_id=va.id, input_dataset_id=ds_ca.id,
            )
            session.commit()

            report = svc.detect_cycles()
            assert report.has_cycle is True
            assert len(report.cycle_nodes) >= 2
            # All reported nodes should belong to the A/B/C group
            valid_ids = {va.id, vb.id, vc.id}
            for node in report.cycle_nodes:
                assert node in valid_ids
        finally:
            session.close()
            eng.dispose()


class TestTopologicalOrder:
    def test_acyclic_topological_order(self):
        from sqlalchemy import create_engine as _ce
        eng = _ce("sqlite:///:memory:", connect_args={"check_same_thread": False})

        @sa_event.listens_for(eng, "connect")
        def _fk(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(bind=eng)
        SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SF()
        try:
            svc, va, vb, vc, _, _ = _build_abc_chain(session)
            order = svc.topological_order()
            assert isinstance(order, list)
            # A must come before B and C; B must come before C
            assert order.index(va.id) < order.index(vb.id)
            assert order.index(vb.id) < order.index(vc.id)
        finally:
            session.close()
            eng.dispose()

    def test_cyclic_graph_raises(self):
        from sqlalchemy import create_engine as _ce
        eng = _ce("sqlite:///:memory:", connect_args={"check_same_thread": False})

        @sa_event.listens_for(eng, "connect")
        def _fk(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(bind=eng)
        SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SF()
        try:
            svc = DependencyGraphService(session)
            va = _make_version(session, "TopoA", owner="topo")
            vb = _make_version(session, "TopoB", owner="topo")
            ds_ab = _make_dataset(session, "topo_ds_ab")
            ds_ba = _make_dataset(session, "topo_ds_ba")
            svc.register_dependency(
                producer_version_id=va.id, output_dataset_id=ds_ab.id,
                consumer_version_id=vb.id, input_dataset_id=ds_ab.id,
            )
            svc.register_dependency(
                producer_version_id=vb.id, output_dataset_id=ds_ba.id,
                consumer_version_id=va.id, input_dataset_id=ds_ba.id,
            )
            session.commit()

            with pytest.raises(CycleDetectedError) as exc_info:
                svc.topological_order()
            assert len(exc_info.value.cycle_nodes) >= 2
        finally:
            session.close()
            eng.dispose()

    def test_empty_graph_returns_empty(self):
        from sqlalchemy import create_engine as _ce
        eng = _ce("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=eng)
        SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SF()
        try:
            svc = DependencyGraphService(session)
            order = svc.topological_order()
            assert order == []
        finally:
            session.close()
            eng.dispose()


# ---------------------------------------------------------------------------
# AC-12: /health still green
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
