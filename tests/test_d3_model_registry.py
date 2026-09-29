"""
D3 Model Registry service tests — AC-01 through AC-12.

Uses real SQLite persistence (no mocks). Each test gets an isolated
per-test session via a rollback fixture.
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d3_tmp")

from backend.app.persistence.database import Base  # noqa: E402
from backend.app.services.model_registry import (  # noqa: E402
    ModelRegistry,
    ModelAlreadyExistsError,
    ModelNotFoundError,
    ModelVersionNotFoundError,
    InvalidParentModelError,
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
    return ModelRegistry(db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_model(registry, db, name="ShippingCost", owner="demo"):
    m = registry.register_model(name=name, owner=owner, model_type="python", status="active")
    db.commit()
    return m


# ---------------------------------------------------------------------------
# AC-02: Model registration
# ---------------------------------------------------------------------------

class TestModelRegistration:
    def test_register_new_model(self, registry, db):
        m = registry.register_model(
            name="TestModelA", owner="ownerA", model_type="python", status="draft"
        )
        db.commit()
        assert m.id is not None
        assert m.name == "TestModelA"
        assert m.owner == "ownerA"

    def test_register_with_description(self, registry, db):
        m = registry.register_model(
            name="TestModelDesc", owner="ownerDesc",
            model_type="python", description="A test model", status="active",
        )
        db.commit()
        assert m.description == "A test model"

    def test_register_default_status_is_draft(self, registry, db):
        m = registry.register_model(name="TestModelDraft", owner="ownerDraft")
        db.commit()
        assert m.status == "draft"


# ---------------------------------------------------------------------------
# AC-02 / AC-03: Model lookup and duplicate prevention
# ---------------------------------------------------------------------------

class TestModelLookup:
    def test_get_model_by_id(self, registry, db):
        m = _register_model(registry, db, name="LookupById", owner="ownerLookup")
        found = registry.get_model(m.id)
        assert found.id == m.id
        assert found.name == "LookupById"

    def test_get_model_by_name(self, registry, db):
        _register_model(registry, db, name="LookupByName", owner="ownerByName")
        found = registry.get_model_by_name(name="LookupByName", owner="ownerByName")
        assert found.name == "LookupByName"

    def test_list_models_includes_registered(self, registry, db):
        _register_model(registry, db, name="ListModelA", owner="ownerList")
        _register_model(registry, db, name="ListModelB", owner="ownerList")
        models = registry.list_models(limit=200)
        names = {m.name for m in models}
        assert "ListModelA" in names
        assert "ListModelB" in names

    def test_get_model_not_found(self, registry, db):
        with pytest.raises(ModelNotFoundError):
            registry.get_model("nonexistent-id-xyz")

    def test_get_model_by_name_not_found(self, registry, db):
        with pytest.raises(ModelNotFoundError):
            registry.get_model_by_name(name="Ghost", owner="nobody")

    def test_duplicate_registration_rejected(self, registry, db):
        registry.register_model(name="DupModel", owner="ownerDup", model_type="python")
        db.commit()
        with pytest.raises(ModelAlreadyExistsError):
            registry.register_model(name="DupModel", owner="ownerDup", model_type="python")

    def test_same_name_different_owner_allowed(self, registry, db):
        registry.register_model(name="SharedName", owner="ownerX", model_type="python")
        db.commit()
        m2 = registry.register_model(name="SharedName", owner="ownerY", model_type="python")
        db.commit()
        assert m2.owner == "ownerY"


# ---------------------------------------------------------------------------
# AC-04 / AC-05: Model version registration
# ---------------------------------------------------------------------------

class TestModelVersionRegistration:
    def test_register_version_for_existing_model(self, registry, db):
        m = _register_model(registry, db, name="VersionedModel", owner="ownerV")
        v = registry.register_model_version(
            model_id=m.id,
            semver="1.0.0",
            inputs_spec=json.dumps([{"name": "fuel_price", "type": "float"}]),
            outputs_spec=json.dumps([{"name": "shipping_cost", "type": "float"}]),
            execution_entrypoint="demo.models.shipping.compute",
            is_active=True,
        )
        db.commit()
        assert v.id is not None
        assert v.model_id == m.id
        assert v.semver == "1.0.0"
        assert v.is_active is True

    def test_register_version_invalid_parent_raises(self, registry, db):
        with pytest.raises(InvalidParentModelError):
            registry.register_model_version(
                model_id="nonexistent-model-id",
                semver="1.0.0",
            )

    def test_register_duplicate_version_rejected(self, registry, db):
        m = _register_model(registry, db, name="DupVersionModel", owner="ownerDupV")
        registry.register_model_version(model_id=m.id, semver="1.0.0")
        db.commit()
        with pytest.raises(ModelAlreadyExistsError):
            registry.register_model_version(model_id=m.id, semver="1.0.0")

    def test_multiple_versions_for_same_model(self, registry, db):
        m = _register_model(registry, db, name="MultiVersionModel", owner="ownerMV")
        registry.register_model_version(model_id=m.id, semver="1.0.0")
        registry.register_model_version(model_id=m.id, semver="2.0.0")
        db.commit()
        versions = registry.list_model_versions(m.id)
        assert len(versions) == 2


# ---------------------------------------------------------------------------
# AC-06: Model version lookup
# ---------------------------------------------------------------------------

class TestModelVersionLookup:
    def test_get_version_by_id(self, registry, db):
        m = _register_model(registry, db, name="VerLookupModel", owner="ownerVL")
        v = registry.register_model_version(model_id=m.id, semver="1.0.0")
        db.commit()
        found = registry.get_model_version(v.id)
        assert found.semver == "1.0.0"

    def test_get_version_by_model_and_semver(self, registry, db):
        m = _register_model(registry, db, name="SemverLookup", owner="ownerSL")
        registry.register_model_version(model_id=m.id, semver="3.0.0")
        db.commit()
        v = registry.get_model_version_by_semver(model_id=m.id, semver="3.0.0")
        assert v.model_id == m.id

    def test_list_versions_for_model(self, registry, db):
        m = _register_model(registry, db, name="ListVersionsModel", owner="ownerLVM")
        registry.register_model_version(model_id=m.id, semver="1.0.0")
        registry.register_model_version(model_id=m.id, semver="1.1.0")
        db.commit()
        versions = registry.list_model_versions(m.id)
        semvers = [v.semver for v in versions]
        assert "1.0.0" in semvers
        assert "1.1.0" in semvers

    def test_get_version_not_found(self, registry, db):
        with pytest.raises(ModelVersionNotFoundError):
            registry.get_model_version("ghost-version-id")

    def test_get_version_by_semver_not_found(self, registry, db):
        m = _register_model(registry, db, name="SemverNotFound", owner="ownerSNF")
        with pytest.raises(ModelVersionNotFoundError):
            registry.get_model_version_by_semver(model_id=m.id, semver="99.0.0")

    def test_list_versions_empty_for_new_model(self, registry, db):
        m = _register_model(registry, db, name="NoVersionsModel", owner="ownerNV")
        versions = registry.list_model_versions(m.id)
        assert versions == []


# ---------------------------------------------------------------------------
# AC-10: /health still green
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
