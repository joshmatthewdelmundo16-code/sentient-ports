"""Shared fixtures for the D27 tests: one fully seeded network per test module."""

from __future__ import annotations

import contextlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event as sa_event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.deps import get_db
from backend.app.config import settings
from backend.app.main import api
from backend.app.persistence.database import Base, Organization
from backend.app.security import auth as auth_module
from backend.app.ui.network_seed import DEMO_PASSWORD, seed_network_demo
from tests.demo_support import db_override


def make_network():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    SF = sessionmaker(bind=eng)
    db = SF()
    seeded = seed_network_demo(db, extended=True)
    db.commit()
    db.close()
    return eng, SF, seeded


class Net:
    """A client plus helpers: sign in as a demo account, act in a scope."""

    def __init__(self, client: TestClient, SF, seeded):
        self.client, self.SF, self.seeded = client, SF, seeded
        db = SF()
        self.orgs = {o.org_key: o.id for o in db.execute(select(Organization)).scalars()}
        db.close()

    def login(self, email: str) -> None:
        for limiter in (auth_module.login_ip_limiter, auth_module.login_account_limiter, auth_module.write_limiter):
            limiter.reset()
        self.client.cookies.clear()
        r = self.client.post("/api/auth/login", json={"email": email, "password": DEMO_PASSWORD})
        assert r.status_code == 200, r.text

    def h(self, org_key: str | None = None) -> dict[str, str]:
        out = {"X-CSRF-Token": self.client.cookies.get("sp_csrf") or ""}
        if org_key:
            out["X-Scope-Org"] = self.orgs[org_key]
        return out

    def get(self, path: str, org_key: str | None = None, **kw):
        return self.client.get(path, headers=self.h(org_key), **kw)

    def post(self, path: str, org_key: str | None = None, **kw):
        return self.client.post(path, headers=self.h(org_key), **kw)


@contextlib.contextmanager
def network_client():
    eng, SF, seeded = make_network()
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "AUTH_MODE", "required")
    api.dependency_overrides[get_db] = db_override(SF)
    try:
        with TestClient(api) as client:
            yield Net(client, SF, seeded)
    finally:
        api.dependency_overrides.clear()
        mp.undo()
        eng.dispose()
