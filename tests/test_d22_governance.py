"""D22 — Participants & Approved Outputs (governance) tests.

Real in-memory SQLite; no mocks. Covers participant CRUD/validation/uniqueness/inactive
behavior; approval creation/duplicate-prevention/revoke/expiry; ownership references and
legacy (owner-less) records; the read-only exposure view and its filtering; that scenario
outputs are NOT auto-approved; and that D21 execution remains intact.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d22_tmp")

from backend.app.adapters.synthetic import SyntheticAdapter  # noqa: E402
from backend.app.persistence.database import (  # noqa: E402
    ApprovedOutput,
    Base,
    DataContract,
    Dataset,
    Model,
    ModelVersion,
    get_db,
)
from backend.app.services.adapter_registry import AdapterRegistry  # noqa: E402
from backend.app.services.federation import FederationService  # noqa: E402
from backend.app.services.governance import (  # noqa: E402
    ApprovalValidationError,
    DuplicateApprovalError,
    DuplicateParticipantError,
    GovernanceService,
    ParticipantInactiveError,
    ParticipantNotFoundError,
    ParticipantValidationError,
)


_ctr = {"n": 0}


def _uid(prefix: str = "") -> str:
    _ctr["n"] += 1
    return f"{prefix}{_ctr['n']}"


def _engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    return eng


def _future(hours: int = 24) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _seed_dataset(db: Session, value: dict | None = None) -> str:
    ds = Dataset(name=_uid("DS-"),
                 current_value=json.dumps(value) if value is not None else None)
    db.add(ds)
    db.flush()
    return ds.id


@pytest.fixture()
def ctx():
    eng = _engine()
    SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    db = SF()
    svc = GovernanceService(db)
    yield {"db": db, "SF": SF, "svc": svc}
    db.close()
    eng.dispose()


# ---------------------------------------------------------------------------
# Participant CRUD / validation / uniqueness / inactive
# ---------------------------------------------------------------------------

class TestParticipants:
    def test_register_and_get(self, ctx):
        svc = ctx["svc"]
        p = svc.register_participant(participant_key="port-a", name="Port A",
                                     metadata={"region": "APAC"})
        assert p.id and p.status == "active"
        assert svc.get_participant(p.id).participant_key == "port-a"
        assert svc.get_participant_by_key("port-a").id == p.id

    def test_duplicate_key_rejected(self, ctx):
        svc = ctx["svc"]
        svc.register_participant(participant_key="dup", name="One")
        with pytest.raises(DuplicateParticipantError):
            svc.register_participant(participant_key="dup", name="Two")

    def test_missing_key_or_name_rejected(self, ctx):
        svc = ctx["svc"]
        with pytest.raises(ParticipantValidationError):
            svc.register_participant(participant_key="  ", name="x")
        with pytest.raises(ParticipantValidationError):
            svc.register_participant(participant_key="k", name="  ")

    def test_bad_status_rejected(self, ctx):
        with pytest.raises(ParticipantValidationError):
            ctx["svc"].register_participant(participant_key="k", name="n", status="banned")

    def test_unserializable_metadata_rejected(self, ctx):
        with pytest.raises(ParticipantValidationError):
            ctx["svc"].register_participant(participant_key="k", name="n",
                                            metadata={"bad": {1, 2, 3}})

    def test_get_unknown_raises(self, ctx):
        with pytest.raises(ParticipantNotFoundError):
            ctx["svc"].get_participant("nope")

    def test_update_fields(self, ctx):
        svc = ctx["svc"]
        p = svc.register_participant(participant_key="k", name="Old")
        updated = svc.update_participant(p.id, name="New", status="inactive",
                                        metadata={"x": 1})
        assert updated.name == "New" and updated.status == "inactive"
        assert json.loads(updated.meta_json) == {"x": 1}

    def test_list_filter_by_status(self, ctx):
        svc = ctx["svc"]
        svc.register_participant(participant_key="a", name="A")
        b = svc.register_participant(participant_key="b", name="B")
        svc.set_status(b.id, "inactive")
        assert {p.participant_key for p in svc.list_participants(status="active")} == {"a"}
        assert {p.participant_key for p in svc.list_participants(status="inactive")} == {"b"}


# ---------------------------------------------------------------------------
# Approvals: creation / duplicate / revoke / expiry / checks
# ---------------------------------------------------------------------------

class TestApprovals:
    def _participant_and_dataset(self, ctx, value=None):
        svc = ctx["svc"]
        p = svc.register_participant(participant_key=_uid("p-"), name="P")
        ds_id = _seed_dataset(ctx["db"], value)
        return p, ds_id

    def test_create_approval(self, ctx):
        p, ds = self._participant_and_dataset(ctx)
        a = ctx["svc"].create_approval(participant_id=p.id, dataset_id=ds,
                                       field_name="teu", purpose="quarterly report")
        assert a.status == "active" and a.field_name == "teu"

    def test_duplicate_active_rejected(self, ctx):
        p, ds = self._participant_and_dataset(ctx)
        ctx["svc"].create_approval(participant_id=p.id, dataset_id=ds, field_name="teu")
        with pytest.raises(DuplicateApprovalError):
            ctx["svc"].create_approval(participant_id=p.id, dataset_id=ds, field_name="teu")

    def test_revoke_then_reapprove_allowed(self, ctx):
        svc = ctx["svc"]
        p, ds = self._participant_and_dataset(ctx)
        a = svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="teu")
        svc.revoke_approval(a.id)
        assert svc.get_approval(a.id).status == "revoked"
        # Re-approval after revoke is allowed (partial unique index excludes revoked rows).
        a2 = svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="teu")
        assert a2.id != a.id and a2.status == "active"

    def test_inactive_participant_cannot_approve(self, ctx):
        svc = ctx["svc"]
        p, ds = self._participant_and_dataset(ctx)
        svc.set_status(p.id, "inactive")
        with pytest.raises(ParticipantInactiveError):
            svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="teu")

    def test_unknown_dataset_rejected(self, ctx):
        svc = ctx["svc"]
        p = svc.register_participant(participant_key=_uid("p-"), name="P")
        with pytest.raises(ApprovalValidationError):
            svc.create_approval(participant_id=p.id, dataset_id="nope", field_name="teu")

    def test_empty_field_rejected(self, ctx):
        p, ds = self._participant_and_dataset(ctx)
        with pytest.raises(ApprovalValidationError):
            ctx["svc"].create_approval(participant_id=p.id, dataset_id=ds, field_name="   ")

    def test_past_expiry_rejected(self, ctx):
        p, ds = self._participant_and_dataset(ctx)
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        with pytest.raises(ApprovalValidationError):
            ctx["svc"].create_approval(participant_id=p.id, dataset_id=ds,
                                       field_name="teu", expires_at=past)

    def test_is_approved_true(self, ctx):
        p, ds = self._participant_and_dataset(ctx)
        ctx["svc"].create_approval(participant_id=p.id, dataset_id=ds, field_name="teu")
        assert ctx["svc"].is_approved(p.id, ds, "teu") is True

    def test_is_approved_false_when_none(self, ctx):
        p, ds = self._participant_and_dataset(ctx)
        assert ctx["svc"].is_approved(p.id, ds, "teu") is False

    def test_is_approved_false_when_revoked(self, ctx):
        svc = ctx["svc"]
        p, ds = self._participant_and_dataset(ctx)
        a = svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="teu")
        svc.revoke_approval(a.id)
        assert svc.is_approved(p.id, ds, "teu") is False

    def test_is_approved_false_when_expired(self, ctx):
        svc = ctx["svc"]
        p, ds = self._participant_and_dataset(ctx)
        svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="teu",
                            expires_at=_future(1))
        # Check at a time after expiry.
        assert svc.is_approved(p.id, ds, "teu", at=_future(48)) is False

    def test_is_approved_false_when_participant_inactive(self, ctx):
        svc = ctx["svc"]
        p, ds = self._participant_and_dataset(ctx)
        svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="teu")
        svc.set_status(p.id, "inactive")
        assert svc.is_approved(p.id, ds, "teu") is False


# ---------------------------------------------------------------------------
# Ownership + legacy records
# ---------------------------------------------------------------------------

class TestOwnership:
    def test_legacy_dataset_has_no_owner(self, ctx):
        ds_id = _seed_dataset(ctx["db"], {"v": 1})
        assert ctx["db"].get(Dataset, ds_id).owner_participant_id is None

    def test_set_and_clear_dataset_owner(self, ctx):
        svc = ctx["svc"]
        p = svc.register_participant(participant_key=_uid("p-"), name="P")
        ds_id = _seed_dataset(ctx["db"], {"v": 1})
        svc.set_dataset_owner(ds_id, p.id)
        assert ctx["db"].get(Dataset, ds_id).owner_participant_id == p.id
        svc.set_dataset_owner(ds_id, None)
        assert ctx["db"].get(Dataset, ds_id).owner_participant_id is None

    def test_set_model_version_owner(self, ctx):
        svc = ctx["svc"]
        db = ctx["db"]
        p = svc.register_participant(participant_key=_uid("p-"), name="P")
        m = Model(name=_uid("M-"), owner="t", model_type="python", status="active")
        db.add(m)
        db.flush()
        v = ModelVersion(model_id=m.id, semver="1.0.0", is_active=True)
        db.add(v)
        db.flush()
        svc.set_model_version_owner(v.id, p.id)
        assert db.get(ModelVersion, v.id).owner_participant_id == p.id

    def test_set_owner_unknown_dataset_raises(self, ctx):
        svc = ctx["svc"]
        p = svc.register_participant(participant_key=_uid("p-"), name="P")
        with pytest.raises(ApprovalValidationError):
            svc.set_dataset_owner("nope", p.id)


# ---------------------------------------------------------------------------
# Read-only exposure view + filtering
# ---------------------------------------------------------------------------

class TestExposureView:
    def _setup(self, ctx, value):
        svc = ctx["svc"]
        p = svc.register_participant(participant_key=_uid("p-"), name="P")
        ds_id = _seed_dataset(ctx["db"], value)
        return svc, p, ds_id

    def test_exposes_only_approved_fields(self, ctx):
        svc, p, ds = self._setup(ctx, {"teu": 100, "secret": 999})
        svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="teu")
        view = svc.approved_outputs_view()
        assert len(view) == 1
        assert view[0].field_name == "teu" and view[0].value == 100
        assert view[0].value_present is True

    def test_absent_field_value_present_false(self, ctx):
        svc, p, ds = self._setup(ctx, {"other": 1})
        svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="teu")
        view = svc.approved_outputs_view()
        assert view[0].value is None and view[0].value_present is False

    def test_revoked_excluded(self, ctx):
        svc, p, ds = self._setup(ctx, {"teu": 1})
        a = svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="teu")
        svc.revoke_approval(a.id)
        assert svc.approved_outputs_view() == []

    def test_expired_excluded(self, ctx):
        svc, p, ds = self._setup(ctx, {"teu": 1})
        svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="teu",
                            expires_at=_future(1))
        assert svc.approved_outputs_view(at=_future(48)) == []

    def test_inactive_participant_excluded(self, ctx):
        svc, p, ds = self._setup(ctx, {"teu": 1})
        svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="teu")
        svc.set_status(p.id, "inactive")
        assert svc.approved_outputs_view() == []

    def test_filter_by_participant_and_dataset(self, ctx):
        svc = ctx["svc"]
        p1 = svc.register_participant(participant_key=_uid("p-"), name="P1")
        p2 = svc.register_participant(participant_key=_uid("p-"), name="P2")
        ds1 = _seed_dataset(ctx["db"], {"a": 1})
        ds2 = _seed_dataset(ctx["db"], {"b": 2})
        svc.create_approval(participant_id=p1.id, dataset_id=ds1, field_name="a")
        svc.create_approval(participant_id=p2.id, dataset_id=ds2, field_name="b")
        assert {e.participant_id for e in svc.approved_outputs_view(participant_id=p1.id)} == {p1.id}
        assert {e.dataset_id for e in svc.approved_outputs_view(dataset_id=ds2.__str__())} == {ds2}


# ---------------------------------------------------------------------------
# Scenario outputs are NOT auto-approved (D19 interplay)
# ---------------------------------------------------------------------------

class TestScenarioNotAutoApproved:
    def test_scenario_execution_creates_no_approvals(self, ctx):
        from backend.app.services.scenarios import ScenarioService

        db = ctx["db"]
        # Build a runnable single-node graph: source S (value) → V (×2) → output O.
        model = Model(name=_uid("M-"), owner="t", model_type="python", status="active")
        db.add(model)
        db.flush()
        version = ModelVersion(model_id=model.id, semver="1.0.0", is_active=True)
        db.add(version)
        db.flush()
        src = Dataset(name=_uid("S-"), current_value=json.dumps({"value": 10.0}))
        out = Dataset(name=_uid("O-"))
        db.add_all([src, out])
        db.flush()
        for dsid in (src.id, out.id):
            db.add(DataContract(dataset_id=dsid,
                                schema_json=json.dumps({"fields": [{"name": "value", "type": "float"}]}),
                                semver="1.0.0"))
        db.flush()
        fed = FederationService(db)
        fed.bind_input(version.id, src.id)
        fed.bind_output(version.id, out.id)
        db.flush()
        reg = AdapterRegistry(db)
        reg.register_adapter(SyntheticAdapter(_uid("adp-"), version.id, scalar=2.0))

        sc_svc = ScenarioService(db, reg)
        baseline = sc_svc.create_baseline(name=_uid("B-"), target_version_id=version.id)
        scenario = sc_svc.create_scenario(baseline_id=baseline.id, name=_uid("SC-"))
        sc_svc.set_override(scenario.id, src.id, "value", 25.0)
        outcome = sc_svc.execute_scenario(scenario.id)
        assert outcome.success is True

        # Governance must NOT have auto-approved anything from the scenario run.
        assert db.query(ApprovedOutput).count() == 0
        assert ctx["svc"].approved_outputs_view() == []


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    def test_counts(self, ctx):
        svc = ctx["svc"]
        p = svc.register_participant(participant_key=_uid("p-"), name="P")
        ds = _seed_dataset(ctx["db"], {"a": 1, "b": 2})
        svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="a")
        a2 = svc.create_approval(participant_id=p.id, dataset_id=ds, field_name="b")
        svc.revoke_approval(a2.id)
        s = svc.summary()
        assert s["participants"] == 1 and s["active_participants"] == 1
        assert s["approvals"] == 2 and s["active_approvals"] == 1
        assert s["effective_approvals"] == 1 and s["revoked_approvals"] == 1


# ---------------------------------------------------------------------------
# API layer (TestClient) — endpoints, status codes, exposure boundary
# ---------------------------------------------------------------------------

class TestGovernanceAPI:
    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        from backend.app.main import api

        eng = _engine()
        SF = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        db = SF()
        ds_id = _seed_dataset(db, {"teu": 100, "secret": 42})
        db.commit()

        def _get_db():
            s = SF()
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

        api.dependency_overrides[get_db] = _get_db
        with TestClient(api) as c:
            yield {"c": c, "ds_id": ds_id}
        api.dependency_overrides.clear()
        db.close()
        eng.dispose()

    def test_participant_lifecycle(self, client):
        c = client["c"]
        r = c.post("/api/participants", json={"participant_key": "port-x", "name": "Port X"})
        assert r.status_code == 201
        pid = r.json()["id"]
        assert c.get(f"/api/participants/{pid}").json()["participant_key"] == "port-x"
        assert c.patch(f"/api/participants/{pid}",
                       json={"status": "inactive"}).json()["status"] == "inactive"
        assert any(p["id"] == pid for p in c.get("/api/participants").json())

    def test_duplicate_participant_409(self, client):
        c = client["c"]
        c.post("/api/participants", json={"participant_key": "dupe", "name": "A"})
        r = c.post("/api/participants", json={"participant_key": "dupe", "name": "B"})
        assert r.status_code == 409

    def test_approval_flow_and_exposure(self, client):
        c, ds = client["c"], client["ds_id"]
        pid = c.post("/api/participants",
                     json={"participant_key": _uid("p-"), "name": "P"}).json()["id"]
        r = c.post("/api/approved-outputs",
                   json={"participant_id": pid, "dataset_id": ds, "field_name": "teu"})
        assert r.status_code == 201
        aid = r.json()["id"]
        # Duplicate active approval → 409.
        dup = c.post("/api/approved-outputs",
                     json={"participant_id": pid, "dataset_id": ds, "field_name": "teu"})
        assert dup.status_code == 409
        # Exposure view returns only the approved field with its value.
        exposed = c.get("/api/exposed-outputs").json()
        assert len(exposed) == 1 and exposed[0]["field_name"] == "teu"
        assert exposed[0]["value"] == 100
        # Revoke → exposure empty.
        assert c.post(f"/api/approved-outputs/{aid}/revoke").json()["status"] == "revoked"
        assert c.get("/api/exposed-outputs").json() == []

    def test_inactive_participant_approve_409(self, client):
        c, ds = client["c"], client["ds_id"]
        pid = c.post("/api/participants",
                     json={"participant_key": _uid("p-"), "name": "P",
                           "status": "inactive"}).json()["id"]
        r = c.post("/api/approved-outputs",
                   json={"participant_id": pid, "dataset_id": ds, "field_name": "teu"})
        assert r.status_code == 409

    def test_participant_not_found_404(self, client):
        assert client["c"].get("/api/participants/nope").status_code == 404

    def test_set_dataset_owner_204(self, client):
        c, ds = client["c"], client["ds_id"]
        pid = c.post("/api/participants",
                     json={"participant_key": _uid("p-"), "name": "P"}).json()["id"]
        assert c.put(f"/api/datasets/{ds}/owner", json={"participant_id": pid}).status_code == 204
        assert c.put(f"/api/datasets/{ds}/owner", json={"participant_id": None}).status_code == 204

    def test_summary_endpoint(self, client):
        assert "participants" in client["c"].get("/api/governance/summary").json()


# ---------------------------------------------------------------------------
# D21 regression — execution/executor still intact after governance changes
# ---------------------------------------------------------------------------

class TestD21RegressionIntact:
    def test_executor_selection_unchanged(self):
        from backend.app.execution.factory import select_executor
        assert select_executor(None).name == "in_process"
        assert select_executor("in_process").is_external is False

    def test_airflow_executor_importable_and_minimal_payload(self):
        # Confirms D22 did not couple to or disturb D21 internals.
        from backend.app.execution.airflow_executor import dag_run_id_for
        assert dag_run_id_for("abc") == "graphrun__abc"
