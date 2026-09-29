"""
D4 Data Contract Manager service tests — AC-01 through AC-11.

Uses real SQLite persistence (no mocks). Per-test session isolation
via rollback fixture. Synthetic demo values only.
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d4_tmp")

from backend.app.persistence.database import Base, Dataset  # noqa: E402
from backend.app.services.data_contract_manager import (  # noqa: E402
    DataContractManager,
    ContractNotFoundError,
    DuplicateContractError,
    DatasetNotFoundError,
    InvalidContractError,
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
def manager(db):
    return DataContractManager(db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHEMA = json.dumps({"fields": [{"name": "shipping_cost", "type": "float", "unit": "USD"}]})


def _make_dataset(db, name="shipping_cost") -> Dataset:
    ds = Dataset(name=name, description="test dataset")
    db.add(ds)
    db.flush()
    return ds


# ---------------------------------------------------------------------------
# AC-02: Contract registration
# ---------------------------------------------------------------------------

class TestContractRegistration:
    def test_register_contract_successfully(self, manager, db):
        ds = _make_dataset(db, "reg_ds_1")
        contract = manager.register_contract(
            dataset_id=ds.id,
            schema_json=_SCHEMA,
            semver="1.0.0",
        )
        db.commit()
        assert contract.id is not None
        assert contract.dataset_id == ds.id
        assert contract.semver == "1.0.0"

    def test_register_contract_default_semver(self, manager, db):
        ds = _make_dataset(db, "reg_ds_default_semver")
        contract = manager.register_contract(dataset_id=ds.id, schema_json=_SCHEMA)
        db.commit()
        assert contract.semver == "1.0.0"

    def test_register_multiple_semvers_same_dataset(self, manager, db):
        ds = _make_dataset(db, "reg_ds_multi")
        manager.register_contract(dataset_id=ds.id, schema_json=_SCHEMA, semver="1.0.0")
        v2 = manager.register_contract(dataset_id=ds.id, schema_json=_SCHEMA, semver="2.0.0")
        db.commit()
        assert v2.semver == "2.0.0"


# ---------------------------------------------------------------------------
# AC-05: Contract lookup
# ---------------------------------------------------------------------------

class TestContractLookup:
    def test_get_contract_by_id(self, manager, db):
        ds = _make_dataset(db, "lookup_ds_1")
        c = manager.register_contract(dataset_id=ds.id, schema_json=_SCHEMA, semver="1.0.0")
        db.commit()
        found = manager.get_contract(c.id)
        assert found.id == c.id

    def test_get_contract_by_dataset_and_semver(self, manager, db):
        ds = _make_dataset(db, "lookup_ds_2")
        manager.register_contract(dataset_id=ds.id, schema_json=_SCHEMA, semver="3.0.0")
        db.commit()
        found = manager.get_contract_by_dataset_and_semver(dataset_id=ds.id, semver="3.0.0")
        assert found.dataset_id == ds.id
        assert found.semver == "3.0.0"

    def test_list_contracts_includes_registered(self, manager, db):
        ds = _make_dataset(db, "list_ds_1")
        manager.register_contract(dataset_id=ds.id, schema_json=_SCHEMA, semver="1.0.0")
        db.commit()
        contracts = manager.list_contracts(limit=200)
        assert any(c.dataset_id == ds.id for c in contracts)

    def test_list_contracts_by_dataset(self, manager, db):
        ds = _make_dataset(db, "list_ds_2")
        manager.register_contract(dataset_id=ds.id, schema_json=_SCHEMA, semver="1.0.0")
        manager.register_contract(dataset_id=ds.id, schema_json=_SCHEMA, semver="1.1.0")
        db.commit()
        contracts = manager.list_contracts_by_dataset(ds.id)
        assert len(contracts) == 2
        semvers = [c.semver for c in contracts]
        assert "1.0.0" in semvers and "1.1.0" in semvers

    def test_list_contracts_by_dataset_empty(self, manager, db):
        ds = _make_dataset(db, "list_ds_empty")
        contracts = manager.list_contracts_by_dataset(ds.id)
        assert contracts == []


# ---------------------------------------------------------------------------
# AC-03: Duplicate identity rejected
# ---------------------------------------------------------------------------

class TestDuplicateRejection:
    def test_duplicate_contract_rejected(self, manager, db):
        ds = _make_dataset(db, "dup_ds_1")
        manager.register_contract(dataset_id=ds.id, schema_json=_SCHEMA, semver="1.0.0")
        db.commit()
        with pytest.raises(DuplicateContractError):
            manager.register_contract(dataset_id=ds.id, schema_json=_SCHEMA, semver="1.0.0")


# ---------------------------------------------------------------------------
# AC-04: Parent dataset reference validated
# ---------------------------------------------------------------------------

class TestDatasetReferenceValidation:
    def test_nonexistent_dataset_rejected(self, manager, db):
        with pytest.raises(DatasetNotFoundError):
            manager.register_contract(
                dataset_id="nonexistent-dataset-id",
                schema_json=_SCHEMA,
                semver="1.0.0",
            )

    def test_valid_dataset_id_accepted(self, manager, db):
        ds = _make_dataset(db, "ref_ds_valid")
        contract = manager.register_contract(dataset_id=ds.id, schema_json=_SCHEMA)
        db.commit()
        assert contract.dataset_id == ds.id


# ---------------------------------------------------------------------------
# Missing contract behavior
# ---------------------------------------------------------------------------

class TestMissingContractBehavior:
    def test_get_contract_not_found_by_id(self, manager, db):
        with pytest.raises(ContractNotFoundError):
            manager.get_contract("ghost-contract-id")

    def test_get_contract_not_found_by_dataset_and_semver(self, manager, db):
        ds = _make_dataset(db, "missing_ds_1")
        with pytest.raises(ContractNotFoundError):
            manager.get_contract_by_dataset_and_semver(dataset_id=ds.id, semver="99.0.0")


# ---------------------------------------------------------------------------
# AC (InvalidContractError): schema_json structural validation
# ---------------------------------------------------------------------------

class TestSchemaJsonValidation:
    def test_empty_schema_json_rejected(self, manager, db):
        ds = _make_dataset(db, "invalid_ds_1")
        with pytest.raises(InvalidContractError):
            manager.register_contract(dataset_id=ds.id, schema_json="", semver="1.0.0")

    def test_whitespace_only_schema_json_rejected(self, manager, db):
        ds = _make_dataset(db, "invalid_ds_2")
        with pytest.raises(InvalidContractError):
            manager.register_contract(dataset_id=ds.id, schema_json="   ", semver="1.0.0")

    def test_non_json_schema_rejected(self, manager, db):
        ds = _make_dataset(db, "invalid_ds_3")
        with pytest.raises(InvalidContractError):
            manager.register_contract(
                dataset_id=ds.id, schema_json="not json at all", semver="1.0.0"
            )

    def test_valid_minimal_json_accepted(self, manager, db):
        ds = _make_dataset(db, "invalid_ds_4")
        contract = manager.register_contract(
            dataset_id=ds.id, schema_json=json.dumps({}), semver="1.0.0"
        )
        db.commit()
        assert contract.id is not None


# ---------------------------------------------------------------------------
# AC-09 / AC-11: /health still green
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
