"""D15 — PostgreSQL / Shared MVP Database tests.

Tests dual-backend support:
  - SQLite engine creation (always runs)
  - PostgreSQL engine creation and full CRUD (requires TEST_DATABASE_URL)
  - Alembic migration infrastructure
  - Security: no credentials leak in health endpoint
  - Configuration: DATABASE_URL environment-driven

PostgreSQL tests are skipped when TEST_DATABASE_URL is not set.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.persistence.database import (
    Base,
    _build_engine,
    _is_sqlite,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from backend.app.config.settings import _normalize_db_url

_PG_URL = _normalize_db_url(os.environ.get("TEST_DATABASE_URL", ""))
_HAS_PG = _PG_URL.startswith("postgresql")

requires_pg = pytest.mark.skipif(
    not _HAS_PG,
    reason="TEST_DATABASE_URL not set or not PostgreSQL",
)


def _sqlite_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return eng


# ---------------------------------------------------------------------------
# 1. Configuration tests (always run)
# ---------------------------------------------------------------------------

class TestD15Configuration:
    def test_is_sqlite_detects_sqlite(self):
        assert _is_sqlite("sqlite:///./test.db") is True
        assert _is_sqlite("sqlite:///:memory:") is True

    def test_is_sqlite_detects_postgresql(self):
        assert _is_sqlite("postgresql+psycopg://u:p@h/d") is False
        assert _is_sqlite("postgresql://u:p@h/d") is False

    def test_build_engine_sqlite(self):
        eng = _build_engine("sqlite:///:memory:")
        assert eng is not None
        assert "sqlite" in str(eng.url)

    def test_database_url_from_settings(self):
        from backend.app.config.settings import DATABASE_URL
        assert DATABASE_URL is not None
        assert len(DATABASE_URL) > 0


# ---------------------------------------------------------------------------
# 2. SQLite schema tests (always run)
# ---------------------------------------------------------------------------

class TestD15SQLiteSchema:
    def test_all_tables_created(self):
        eng = _sqlite_engine()
        inspector = inspect(eng)
        tables = set(inspector.get_table_names())
        expected = {
            "model", "model_version", "dataset", "data_contract",
            "dependency", "execution_run", "execution_step",
            "result", "lineage_edge", "change_event",
        }
        assert expected.issubset(tables)

    def test_model_table_columns(self):
        eng = _sqlite_engine()
        inspector = inspect(eng)
        cols = {c["name"] for c in inspector.get_columns("model")}
        assert {"id", "name", "owner", "status", "model_type", "created_at"}.issubset(cols)

    def test_crud_on_sqlite(self):
        eng = _sqlite_engine()
        SF = sessionmaker(bind=eng)
        from backend.app.persistence.database import Model
        db = SF()
        m = Model(name="test_model", owner="test", model_type="synthetic", status="active")
        db.add(m)
        db.commit()
        fetched = db.query(Model).filter_by(name="test_model").first()
        assert fetched is not None
        assert fetched.owner == "test"
        db.close()


# ---------------------------------------------------------------------------
# 3. Alembic infrastructure tests (always run)
# ---------------------------------------------------------------------------

class TestD15Alembic:
    def test_alembic_ini_exists(self):
        from pathlib import Path
        ini = Path(__file__).resolve().parent.parent / "alembic.ini"
        assert ini.exists()

    def test_alembic_env_importable(self):
        import importlib
        spec = importlib.util.find_spec("alembic")
        assert spec is not None

    def test_baseline_migration_exists(self):
        from pathlib import Path
        versions_dir = Path(__file__).resolve().parent.parent / "alembic" / "versions"
        assert versions_dir.exists()
        migrations = list(versions_dir.glob("*.py"))
        assert len(migrations) >= 1
        baseline = versions_dir / "001_baseline_schema.py"
        assert baseline.exists()


# ---------------------------------------------------------------------------
# 4. Security tests (always run)
# ---------------------------------------------------------------------------

class TestD15Security:
    def test_gitignore_has_env(self):
        from pathlib import Path
        gitignore = Path(__file__).resolve().parent.parent / ".gitignore"
        content = gitignore.read_text(encoding="utf-8")
        assert ".env" in content

    def test_env_example_has_no_real_credentials(self):
        from pathlib import Path
        env_example = Path(__file__).resolve().parent.parent / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        assert "USER:PASSWORD" in content or "platform:platform" not in content.split("\n")[0]
        for line in content.splitlines():
            if line.startswith("#"):
                continue
            if "DATABASE_URL" in line and "postgresql" in line:
                pytest.fail(f"Uncommented PostgreSQL URL in .env.example: {line}")

    def test_health_does_not_expose_credentials(self):
        from fastapi.testclient import TestClient
        from backend.app.main import api
        with TestClient(api) as client:
            res = client.get("/health")
            body = res.json()
            assert "password" not in str(body).lower()
            assert "psycopg" not in str(body).lower()
            if body.get("db") != "ok":
                assert body["db"] == "error: connection failed"


# ---------------------------------------------------------------------------
# 5. Engine builder tests (always run)
# ---------------------------------------------------------------------------

class TestD15EngineBuilder:
    def test_sqlite_engine_works(self):
        eng = _build_engine("sqlite:///:memory:")
        assert eng is not None
        assert "sqlite" in str(eng.url)

    def test_build_engine_returns_engine(self):
        eng = _build_engine("sqlite:///:memory:")
        with eng.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1


# ---------------------------------------------------------------------------
# 6. PostgreSQL tests (skipped when TEST_DATABASE_URL not set)
# ---------------------------------------------------------------------------

@requires_pg
class TestD15PostgreSQL:
    @pytest.fixture(autouse=True)
    def pg_engine(self):
        # Each test runs inside a transaction that is rolled back, so a shared database
        # (e.g. the Supabase MVP instance) is never modified or dropped by the suite.
        self.engine = create_engine(
            _PG_URL, echo=False,
            connect_args={"prepare_threshold": None},
        )
        conn = self.engine.connect()
        trans = conn.begin()
        Base.metadata.create_all(bind=conn)
        self.conn = conn
        self.SF = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
        yield
        trans.rollback()
        conn.close()
        self.engine.dispose()

    def test_tables_created_on_pg(self):
        # D26 fix: inspect through the connection that holds the (uncommitted, transactional)
        # DDL. Inspecting via the engine opened a second connection that cannot see it; the
        # test had never actually run before because no disposable PostgreSQL was available.
        inspector = inspect(self.conn)
        tables = set(inspector.get_table_names())
        expected = {
            "model", "model_version", "dataset", "data_contract",
            "dependency", "execution_run", "execution_step",
            "result", "lineage_edge", "change_event",
        }
        assert expected.issubset(tables)

    def test_crud_model_on_pg(self):
        from backend.app.persistence.database import Model
        db = self.SF()
        m = Model(name="pg_test", owner="test", model_type="synthetic", status="active")
        db.add(m)
        db.commit()
        fetched = db.query(Model).filter_by(name="pg_test").first()
        assert fetched is not None
        assert fetched.id is not None
        assert len(fetched.id) == 36
        db.close()

    def test_crud_full_chain_on_pg(self):
        from backend.app.persistence.database import Dataset, Dependency, Model, ModelVersion
        db = self.SF()

        m1 = Model(name="Producer", owner="test", model_type="synthetic", status="active")
        m2 = Model(name="Consumer", owner="test", model_type="synthetic", status="active")
        db.add_all([m1, m2])
        db.flush()

        v1 = ModelVersion(model_id=m1.id, semver="1.0.0", is_active=True)
        v2 = ModelVersion(model_id=m2.id, semver="1.0.0", is_active=True)
        db.add_all([v1, v2])
        db.flush()

        ds = Dataset(name="test_output")
        db.add(ds)
        db.flush()

        dep = Dependency(
            producer_version_id=v1.id,
            output_dataset_id=ds.id,
            consumer_version_id=v2.id,
            input_dataset_id=ds.id,
        )
        db.add(dep)
        db.commit()

        deps = db.query(Dependency).filter_by(consumer_version_id=v2.id).all()
        assert len(deps) == 1
        assert deps[0].producer_version_id == v1.id
        db.close()

    def test_foreign_keys_enforced_on_pg(self):
        from backend.app.persistence.database import ModelVersion
        db = self.SF()
        v = ModelVersion(model_id="nonexistent", semver="1.0.0", is_active=True)
        db.add(v)
        with pytest.raises(Exception):
            db.commit()
        db.rollback()
        db.close()

    def test_unique_constraints_on_pg(self):
        from backend.app.persistence.database import Model
        db = self.SF()
        m1 = Model(name="UniqueTest", owner="test", model_type="synthetic", status="active")
        db.add(m1)
        db.commit()
        m2 = Model(name="UniqueTest", owner="test", model_type="synthetic", status="active")
        db.add(m2)
        with pytest.raises(Exception):
            db.commit()
        db.rollback()
        db.close()

    def test_seed_on_pg(self):
        from backend.app.ui.demo_seed import seed_demo_data
        db = self.SF()
        config = seed_demo_data(db)
        db.commit()
        assert len(config["models"]) == 4
        assert config["terminal_version_id"] is not None
        assert config["fuel_price_version_id"] is not None
        db.close()

    def test_select_1_on_pg(self):
        with self.engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
