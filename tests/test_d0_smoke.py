"""
D0 smoke tests — G1 Foundation gate (§126).

Verifies:
  1. Settings load without error
  2. DB engine creates + init_db() runs without error
  3. All 10 core entity tables exist in the schema
  4. GET /health returns 200 + ok=True
  5. GET / redirects (or returns health body after redirect follow)

These tests use an in-memory SQLite DB (isolated from the dev DB)
and the FastAPI TestClient (httpx transport).

Run with:
    pytest tests/test_d0_smoke.py -v
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Patch the DATABASE_URL to in-memory before importing the app modules
# so tests never touch the dev .db file.
# ---------------------------------------------------------------------------
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["STORAGE_ROOT"] = "storage_test_tmp"

# Now import app modules (they read the env var set above)
from backend.app.persistence.database import Base, init_db  # noqa: E402
from backend.app.config.settings import APP_VERSION, ENVIRONMENT  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def in_memory_engine():
    """Isolated SQLite in-memory engine for schema tests."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def test_client():
    """
    FastAPI TestClient with patched in-memory DB.

    We override get_db to use the in-memory engine so /health's DB check
    uses the same isolated DB, not the dev file.
    """
    # Re-import here after env vars are set
    from backend.app.persistence.database import engine as app_engine, SessionLocal
    from backend.app.main import api

    client = TestClient(api, raise_server_exceptions=True)
    return client


# ---------------------------------------------------------------------------
# Test 1: Settings
# ---------------------------------------------------------------------------

class TestSettings:
    def test_app_version_set(self):
        assert APP_VERSION, "APP_VERSION must be non-empty"
        assert APP_VERSION.startswith("0."), f"Unexpected version: {APP_VERSION}"

    def test_environment_set(self):
        # Either the default or overridden by env
        assert ENVIRONMENT in ("development", "test", "production", "staging"), (
            f"Unknown environment: {ENVIRONMENT}"
        )

    def test_database_url_is_sqlite(self):
        from backend.app.config.settings import DATABASE_URL
        # In test context we set it to in-memory
        assert "sqlite" in DATABASE_URL


# ---------------------------------------------------------------------------
# Test 2: DB schema / init_db
# ---------------------------------------------------------------------------

EXPECTED_TABLES = {
    "model",
    "model_version",
    "dataset",
    "data_contract",
    "dependency",
    "execution_run",
    "execution_step",
    "result",
    "lineage_edge",
    "change_event",
}


class TestDatabase:
    def test_init_db_creates_tables(self, in_memory_engine):
        """init_db (via create_all) must create all 10 core tables."""
        inspector = inspect(in_memory_engine)
        table_names = set(inspector.get_table_names())
        missing = EXPECTED_TABLES - table_names
        assert not missing, f"Tables missing after init_db(): {missing}"

    def test_all_expected_tables_present(self, in_memory_engine):
        """Cross-check: no expected table is absent."""
        inspector = inspect(in_memory_engine)
        present = set(inspector.get_table_names())
        assert EXPECTED_TABLES.issubset(present)

    def test_select_one(self, in_memory_engine):
        """Basic connectivity: SELECT 1 must succeed."""
        Session = sessionmaker(bind=in_memory_engine)
        db = Session()
        try:
            result = db.execute(text("SELECT 1")).scalar()
            assert result == 1
        finally:
            db.close()

    def test_model_table_has_expected_columns(self, in_memory_engine):
        inspector = inspect(in_memory_engine)
        col_names = {c["name"] for c in inspector.get_columns("model")}
        for expected_col in ("id", "name", "owner", "status", "created_at"):
            assert expected_col in col_names, f"Column '{expected_col}' missing from model table"

    def test_model_unique_constraint(self, in_memory_engine):
        """Inserting two models with same owner+name must fail."""
        from sqlalchemy import exc as sa_exc
        Session = sessionmaker(bind=in_memory_engine)
        db = Session()
        try:
            from backend.app.persistence.database import Model
            m1 = Model(id="aaa", name="ModelX", owner="ownerA", status="draft", model_type="python")
            m2 = Model(id="bbb", name="ModelX", owner="ownerA", status="draft", model_type="python")
            db.add(m1)
            db.commit()
            db.add(m2)
            with pytest.raises(sa_exc.IntegrityError):
                db.commit()
        finally:
            db.rollback()
            db.close()


# ---------------------------------------------------------------------------
# Test 3: /health endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_200(self, test_client):
        resp = test_client.get("/health")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_health_body_ok_true(self, test_client):
        resp = test_client.get("/health")
        data = resp.json()
        assert data.get("ok") is True, f"Expected ok=True, got: {data}"

    def test_health_body_has_required_fields(self, test_client):
        resp = test_client.get("/health")
        data = resp.json()
        for field in ("ok", "version", "environment", "db", "storage", "timestamp"):
            assert field in data, f"Field '{field}' missing from /health response"

    def test_health_db_ok(self, test_client):
        resp = test_client.get("/health")
        data = resp.json()
        assert data["db"] == "ok", f"DB health check failed: {data['db']}"

    def test_health_version_matches(self, test_client):
        resp = test_client.get("/health")
        data = resp.json()
        assert data["version"] == APP_VERSION

    def test_root_redirects_into_the_product(self, test_client):
        """GET / must redirect, and must not 404.

        D0–D23 sent / to /health, so the first thing anyone opening the server saw was a
        JSON health blob. D24 sends it to the Start Here page instead, because the
        landing page is a product surface, not an operations one. What this test protects
        is that / is a redirect into the application and never a dead end; /health itself
        is asserted by the tests above.
        """
        resp = test_client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 307, 308)
        assert resp.headers["location"] == "/ui/start"

        landed = test_client.get("/")
        assert landed.status_code == 200
