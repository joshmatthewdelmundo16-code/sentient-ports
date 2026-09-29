"""Shared helpers for API/UI tests that exercise the real demo seed (D12–D16)."""

from __future__ import annotations

from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.pool import StaticPool

from backend.app.persistence.database import Base
from backend.app.ui.demo_seed import seed_demo_data


def make_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    return eng


def db_override(SF):
    def _get_db():
        db = SF()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    return _get_db


def seed_demo(SF) -> dict:
    """Seed via the production demo seed; returns the demo config."""
    db = SF()
    cfg = seed_demo_data(db)
    db.commit()
    db.close()
    return cfg


def run_body(cfg: dict, fuel_price: float) -> dict:
    return {
        "target_version_id": cfg["terminal_version_id"],
        "dataset_values": {cfg["input_dataset_id"]: {"value": fuel_price}},
    }


def propagate_body(cfg: dict, fuel_price: float) -> dict:
    return {"dataset_id": cfg["input_dataset_id"], "value": {"value": fuel_price}}


def version_ids(cfg: dict) -> list[str]:
    return [m["version_id"] for m in cfg["models"]]
