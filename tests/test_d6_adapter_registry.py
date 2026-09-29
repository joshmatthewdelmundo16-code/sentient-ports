"""
D6 Adapter Registry tests — AC-01 through AC-11.

Tests the adapter abstraction, SyntheticAdapter, and AdapterRegistry.
The registry is in-memory; model versions are persisted in real SQLite
so that version-existence validation is exercised against real data.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d6_tmp")

from backend.app.persistence.database import Base, Model, ModelVersion  # noqa: E402
from backend.app.adapters.base import ModelAdapter  # noqa: E402
from backend.app.adapters.synthetic import SyntheticAdapter  # noqa: E402
from backend.app.services.adapter_registry import (  # noqa: E402
    AdapterRegistry,
    AdapterNotFoundError,
    DuplicateAdapterError,
    AdapterVersionNotFoundError,
    InvalidAdapterError,
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
def registry(db):
    return AdapterRegistry(db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_version(db, model_name: str = "TestModel", owner: str = "test",
                  semver: str = "1.0.0") -> ModelVersion:
    model = Model(name=model_name, owner=owner, model_type="python")
    db.add(model)
    db.flush()
    version = ModelVersion(model_id=model.id, semver=semver)
    db.add(version)
    db.flush()
    return version


# ---------------------------------------------------------------------------
# AC-01: Adapter abstraction exists
# ---------------------------------------------------------------------------

class TestAdapterAbstraction:
    def test_synthetic_adapter_is_model_adapter(self, db):
        """SyntheticAdapter satisfies the ModelAdapter contract."""
        version = _make_version(db, "AbsModel", "abs", "1.0.0")
        adapter = SyntheticAdapter(
            adapter_id="abs-adapter-1",
            version_id=version.id,
            scalar=2.0,
        )
        assert isinstance(adapter, ModelAdapter)

    def test_adapter_has_stable_id(self, db):
        version = _make_version(db, "IdModel", "id", "1.0.0")
        adapter = SyntheticAdapter(adapter_id="id-adapter-1", version_id=version.id)
        assert adapter.adapter_id == "id-adapter-1"

    def test_adapter_declares_version_id(self, db):
        version = _make_version(db, "VerId", "ver", "1.0.0")
        adapter = SyntheticAdapter(adapter_id="ver-adapter-1", version_id=version.id)
        assert adapter.version_id == version.id


# ---------------------------------------------------------------------------
# AC-06: Synthetic adapter callable boundary
# ---------------------------------------------------------------------------

class TestSyntheticAdapter:
    def test_invoke_multiplies_numeric_inputs(self, db):
        version = _make_version(db, "SynModel", "syn", "1.0.0")
        adapter = SyntheticAdapter(adapter_id="syn-adapter-1", version_id=version.id, scalar=3.0)
        result = adapter.invoke({"fuel_price": 100.0, "quantity": 5})
        assert result["fuel_price"] == pytest.approx(300.0)
        assert result["quantity"] == pytest.approx(15.0)

    def test_invoke_passes_through_non_numeric(self, db):
        version = _make_version(db, "SynModel2", "syn2", "1.0.0")
        adapter = SyntheticAdapter(adapter_id="syn-adapter-2", version_id=version.id, scalar=2.0)
        result = adapter.invoke({"label": "port", "cost": 50.0})
        assert result["label"] == "port"
        assert result["cost"] == pytest.approx(100.0)

    def test_invoke_empty_inputs(self, db):
        version = _make_version(db, "SynModel3", "syn3", "1.0.0")
        adapter = SyntheticAdapter(adapter_id="syn-adapter-3", version_id=version.id)
        result = adapter.invoke({})
        assert result == {}

    def test_invoke_is_deterministic(self, db):
        version = _make_version(db, "SynModel4", "syn4", "1.0.0")
        adapter = SyntheticAdapter(adapter_id="syn-adapter-4", version_id=version.id, scalar=1.5)
        inputs = {"x": 10.0}
        r1 = adapter.invoke(inputs)
        r2 = adapter.invoke(inputs)
        assert r1 == r2


# ---------------------------------------------------------------------------
# AC-02/AC-03: Registration and resolution
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_adapter_succeeds(self, registry, db):
        version = _make_version(db, "RegModel", "reg", "1.0.0")
        adapter = SyntheticAdapter(adapter_id="reg-adapter-1", version_id=version.id)
        registered = registry.register_adapter(adapter)
        assert registered is adapter

    def test_resolve_adapter_by_version_id(self, registry, db):
        version = _make_version(db, "ResModel", "res", "1.0.0")
        adapter = SyntheticAdapter(adapter_id="res-adapter-1", version_id=version.id)
        registry.register_adapter(adapter)
        resolved = registry.resolve_adapter(version.id)
        assert resolved is adapter

    def test_get_adapter_by_id(self, registry, db):
        version = _make_version(db, "GetModel", "get", "1.0.0")
        adapter = SyntheticAdapter(adapter_id="get-adapter-1", version_id=version.id)
        registry.register_adapter(adapter)
        found = registry.get_adapter_by_id("get-adapter-1")
        assert found is adapter


# ---------------------------------------------------------------------------
# AC-05: Listing
# ---------------------------------------------------------------------------

class TestListing:
    def test_list_adapters_includes_registered(self, registry, db):
        version = _make_version(db, "ListModel", "list", "1.0.0")
        adapter = SyntheticAdapter(adapter_id="list-adapter-1", version_id=version.id)
        registry.register_adapter(adapter)
        adapters = registry.list_adapters()
        assert any(a.adapter_id == "list-adapter-1" for a in adapters)

    def test_list_adapters_empty_registry(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker as SM
        eng = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=eng)
        session = SM(bind=eng)()
        fresh_registry = AdapterRegistry(session)
        assert fresh_registry.list_adapters() == []
        session.close()
        eng.dispose()


# ---------------------------------------------------------------------------
# AC-04: Duplicate identity rejected
# ---------------------------------------------------------------------------

class TestDuplicateIdentity:
    def test_duplicate_adapter_id_rejected(self, registry, db):
        version = _make_version(db, "DupModel", "dup", "1.0.0")
        version2 = _make_version(db, "DupModel2", "dup2", "1.0.0")
        adapter1 = SyntheticAdapter(adapter_id="dup-adapter-1", version_id=version.id)
        adapter2 = SyntheticAdapter(adapter_id="dup-adapter-1", version_id=version2.id)
        registry.register_adapter(adapter1)
        with pytest.raises(DuplicateAdapterError):
            registry.register_adapter(adapter2)


# ---------------------------------------------------------------------------
# AC-05: Invalid / missing version rejected
# ---------------------------------------------------------------------------

class TestVersionValidation:
    def test_nonexistent_version_rejected(self, registry, db):
        adapter = SyntheticAdapter(
            adapter_id="ghost-adapter-1",
            version_id="nonexistent-version-id",
        )
        with pytest.raises(AdapterVersionNotFoundError):
            registry.register_adapter(adapter)

    def test_invalid_adapter_object_rejected(self, registry, db):
        with pytest.raises(InvalidAdapterError):
            registry.register_adapter("not-an-adapter")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Missing adapter behavior
# ---------------------------------------------------------------------------

class TestMissingAdapterBehavior:
    def test_resolve_missing_version_raises(self, registry, db):
        with pytest.raises(AdapterNotFoundError):
            registry.resolve_adapter("unregistered-version-id")

    def test_get_by_id_missing_raises(self, registry, db):
        with pytest.raises(AdapterNotFoundError):
            registry.get_adapter_by_id("nonexistent-adapter-id")


# ---------------------------------------------------------------------------
# AC-09/AC-10: D0–D5 regression + /health
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
