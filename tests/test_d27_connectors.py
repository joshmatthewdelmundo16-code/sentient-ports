"""D27 — connectors (CSV, REST, signed webhook, telemetry, simulator) and live refresh.

Every connector must end in the governed path (contract → dataset → change event →
propagation → IngestionRun), and the security properties are tested as refusals: SSRF
guards, secret-variable restriction, webhook signature/replay, idempotency, simulated labels,
and organization scoping of the live stream.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from backend.app.config import settings
from backend.app.connectors import sources as src
from backend.app.connectors.base import ConnectorError
from backend.app.persistence.database import (
    AuditEvent,
    ChangeEvent,
    ConnectorSource,
    Dataset,
    IngestionRun,
    TelemetryReading,
)
from backend.app.security.tenancy import tenant_scope
from tests.d27_support import network_client


@pytest.fixture(scope="module")
def net():
    with network_client() as n:
        yield n


def _source(net, name: str) -> ConnectorSource:
    db = net.SF()
    s = db.execute(select(ConnectorSource).where(ConnectorSource.name == name)).scalars().first()
    db.close()
    return s


def _define(net, kind: str, name: str, dataset_name: str, config: dict, org: str = "port-northbay") -> str:
    net.login("demo@platform.example")
    ds = next(d for d in net.get("/api/catalog", org).json() if d["name"] == dataset_name)
    r = net.post("/api/connectors", org, json={"name": name, "kind": kind, "target_dataset_id": ds["id"], "config": config})
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestKindsAndDefinition:
    def test_kinds_are_honestly_labelled(self, net):
        net.login("demo@platform.example")
        kinds = net.get("/api/connectors/kinds", "port-northbay").json()["kinds"]
        assert kinds["database"]["status"] == "not_implemented"
        assert kinds["object_storage"]["status"] == "external_dependency"
        assert kinds["simulated"]["status"] == "simulated"

    def test_only_admins_define_sources_and_only_configurable_kinds(self, net):
        net.login("analyst@northbay.example")
        assert net.post("/api/connectors", json={"name": "x", "kind": "csv_upload", "target_dataset_id": "x"}).status_code == 403
        net.login("demo@platform.example")
        ds = next(d for d in net.get("/api/catalog", "port-northbay").json() if d["name"] == "planning_inputs")
        r = net.post("/api/connectors", "port-northbay", json={"name": "db", "kind": "database", "target_dataset_id": ds["id"]})
        assert r.status_code == 422


class TestCsv:
    def test_valid_csv_changes_the_dataset_and_propagates(self, net):
        sid = _source(net, "Planning inputs (CSV)").id
        net.login("analyst@northbay.example")
        body = b"field,value\ndemand_teu,1600000\ncranes_per_berth,4\n"
        r = net.post(f"/api/connectors/{sid}/csv", files={"file": ("plan.csv", body, "text/csv")}).json()
        assert r["status"] == "ingested" and r["graph_run_id"]
        db = net.SF()
        run = db.get(IngestionRun, r["ingestion_id"])
        ev = db.get(ChangeEvent, run.change_event_id)
        db.close()
        assert run.connector_kind == "csv_upload" and ev.source_type == "csv"
        assert json.loads(ev.new_value_json)["demand_teu"] == 1_600_000.0
        assert json.loads(ev.new_value_json)["berths"] == 4.0          # untouched fields merged, not dropped

    @pytest.mark.parametrize("name, body, fragment", [
        ("x.txt", b"demand_teu,1\n", ".csv"),
        ("x.csv", b"nope,1\n", "not a field"),
        ("x.csv", b"demand_teu,lots\n", "demand_teu"),
        ("x.csv", b"demand_teu,1,2\n", "two columns"),
        ("x.csv", b"demand_teu,-5\n", "demand_teu"),
    ])
    def test_invalid_csv_is_rejected_and_writes_nothing(self, net, name, body, fragment):
        sid = _source(net, "Planning inputs (CSV)").id
        net.login("analyst@northbay.example")
        before = next(d for d in net.get("/api/catalog").json() if d["name"] == "planning_inputs")["value_hash"]
        r = net.post(f"/api/connectors/{sid}/csv", files={"file": (name, body, "text/csv")}).json()
        assert r["status"] == "rejected" and fragment in r["error"]
        after = next(d for d in net.get("/api/catalog").json() if d["name"] == "planning_inputs")["value_hash"]
        assert before == after


class TestRestSecurity:
    def test_url_guards(self, monkeypatch):
        monkeypatch.setattr(settings, "CONNECTOR_ALLOWED_HOSTS", ["localhost", "api.example.org"])
        monkeypatch.setattr(settings, "CONNECTOR_ALLOW_PRIVATE_HOSTS", False)
        with pytest.raises(ConnectorError, match="https"):
            src.check_url("http://api.example.org/x")
        with pytest.raises(ConnectorError, match="ALLOWED_HOSTS"):
            src.check_url("https://evil.example.org/x")
        with pytest.raises(ConnectorError, match="non-public"):
            src.check_url("https://localhost/x")                          # resolves to loopback
        with pytest.raises(ConnectorError, match="Credentials"):
            src.check_url("https://user:pw@api.example.org/x")

    def _mock_source(self, net, config):
        return _define(net, "rest_poll", f"REST {time.time_ns()}", "live_operations_inputs", config)

    def _poll(self, net, sid, handler, monkeypatch):
        monkeypatch.setattr(settings, "CONNECTOR_ALLOWED_HOSTS", ["localhost"])
        monkeypatch.setattr(settings, "CONNECTOR_ALLOW_PRIVATE_HOSTS", True)
        db = net.SF()
        s = db.get(ConnectorSource, sid)
        with tenant_scope(db, s.organization_id):
            run = src.poll_rest(db, s, transport=httpx.MockTransport(handler))
            db.commit()
            out = (run.status, run.error, run.graph_run_id)
        db.close()
        return out

    def test_poll_reads_json_paths_into_the_dataset(self, net, monkeypatch):
        sid = self._mock_source(net, {"url": "http://localhost/ports.json",
                                      "fields": {"berth_occupancy_pct": "data.0.occupancy"}})
        status, error, run = self._poll(net, sid, lambda req: httpx.Response(
            200, json={"data": [{"occupancy": 81.5}]}), monkeypatch)
        assert status == "ingested" and run, error
        db = net.SF()
        live = db.execute(select(Dataset).where(Dataset.name == "live_operations_output",
                                                Dataset.organization_id == net.orgs["port-northbay"])).scalar_one()
        db.close()
        assert json.loads(live.current_value)["congestion_alert"] is True   # 81.5% ≥ 75% threshold

    @pytest.mark.parametrize("response, fragment", [
        (httpx.Response(302, headers={"location": "http://169.254.169.254/"}), "HTTP 302"),
        (httpx.Response(200, text="<html>", headers={"content-type": "text/html"}), "JSON"),
        (httpx.Response(200, json={"other": 1}), "not found"),
        (httpx.Response(200, content=b"{" + b" " * (2 * 1024 * 1024) + b"}", headers={"content-type": "application/json"}), "1 MiB"),
    ])
    def test_unsafe_or_bad_responses_are_rejected(self, net, monkeypatch, response, fragment):
        sid = self._mock_source(net, {"url": "http://localhost/x.json", "fields": {"berth_occupancy_pct": "summary.pct"}})
        status, error, _ = self._poll(net, sid, lambda req: response, monkeypatch)
        assert status == "rejected" and fragment in error

    def test_secret_header_only_from_connector_secret_variables(self, net, monkeypatch):
        net.login("demo@platform.example")
        ds = next(d for d in net.get("/api/catalog", "port-northbay").json() if d["name"] == "live_operations_inputs")
        bad = net.post("/api/connectors", "port-northbay", json={
            "name": "exfil", "kind": "rest_poll", "target_dataset_id": ds["id"],
            "config": {"url": "https://x/", "fields": {"a": "b"}, "secret_env": "DATABASE_URL"}})
        assert bad.status_code == 422
        seen = {}
        monkeypatch.setenv("CONNECTOR_SECRET_PORTDATA", "s3cr3t")
        sid = self._mock_source(net, {"url": "http://localhost/x.json", "fields": {"berth_occupancy_pct": "v"},
                                      "secret_env": "CONNECTOR_SECRET_PORTDATA"})

        def handler(req):
            seen["auth"] = req.headers.get("authorization")
            return httpx.Response(200, json={"v": 50})
        status, _, _ = self._poll(net, sid, handler, monkeypatch)
        assert status == "ingested" and seen["auth"] == "Bearer s3cr3t"
        listed = next(s for s in net.get("/api/connectors", "port-northbay").json() if s["id"] == sid)
        assert listed["config"]["secret_env"] == "configured"          # the variable name is not echoed


class TestWebhook:
    def _setup(self, net, monkeypatch):
        monkeypatch.setattr(settings, "CONNECTOR_SIGNING_KEY", "test-master-key-please-rotate")
        return _source(net, "Terminal telemetry (webhook)").id

    def _send(self, net, sid, body: bytes, *, ts=None, delivery="d-1", sig=None):
        ts = str(ts if ts is not None else int(time.time()))
        net.client.cookies.clear()
        return net.client.post(f"/api/webhooks/{sid}", content=body, headers={
            "X-Platform-Timestamp": ts, "X-Platform-Delivery": delivery,
            "X-Platform-Signature": sig or src.sign(sid, ts, body), "Content-Type": "application/json"})

    def test_signed_delivery_is_ingested_without_a_session(self, net, monkeypatch):
        sid = self._setup(net, monkeypatch)
        body = json.dumps({"values": {"berth_occupancy_pct": 40.0}}).encode()
        r = self._send(net, sid, body, delivery="first")
        assert r.status_code == 200 and r.json()["status"] in ("ingested", "unchanged")
        again = self._send(net, sid, body, delivery="first")
        assert again.json()["ingestion_id"] == r.json()["ingestion_id"]          # replay is idempotent

    def test_bad_signature_stale_timestamp_and_disabled_key_are_refused(self, net, monkeypatch):
        sid = self._setup(net, monkeypatch)
        body = json.dumps({"values": {"berth_occupancy_pct": 41.0}}).encode()
        assert self._send(net, sid, body, delivery="x1", sig="sha256=" + "0" * 64).status_code == 401
        assert self._send(net, sid, body, delivery="x2", ts=int(time.time()) - 3600).status_code == 401
        tampered = self._send(net, sid, body.replace(b"41.0", b"99.0"), delivery="x3",
                              sig=src.sign(sid, str(int(time.time())), body))
        assert tampered.status_code == 401
        db = net.SF()
        assert db.execute(select(AuditEvent).where(AuditEvent.action == "webhook.rejected")).scalars().first()
        db.close()
        monkeypatch.setattr(settings, "CONNECTOR_SIGNING_KEY", "")
        assert self._send(net, sid, body, delivery="x4", sig="sha256=00").status_code == 401

    def test_unknown_source_is_404(self, net, monkeypatch):
        self._setup(net, monkeypatch)
        assert self._send(net, "nope", b"{}", sig="sha256=00").status_code == 404


class TestTelemetryAndSimulator:
    def test_readings_are_idempotent_and_windowed(self, net):
        sid = _define(net, "telemetry", "Terminal sensors", "live_operations_inputs", {
            "window_minutes": 60, "fields": {"berth_occupancy_pct": {"metric": "occ", "agg": "mean"}}})
        net.login("analyst@northbay.example")
        now = datetime.now(timezone.utc)
        batch = [{"metric": "occ", "value": v, "observed_at": (now - timedelta(minutes=i)).isoformat(), "key": f"k{i}"}
                 for i, v in enumerate((70.0, 80.0, 90.0))]
        r = net.post(f"/api/connectors/{sid}/readings", json={"readings": batch}).json()
        assert r["accepted"] == 3 and r["window"]["berth_occupancy_pct"] == pytest.approx(80.0)
        again = net.post(f"/api/connectors/{sid}/readings", json={"readings": batch}).json()
        assert again["accepted"] == 0 and again["duplicates"] == 3
        bad = net.post(f"/api/connectors/{sid}/readings", json={"readings": [{"metric": "other", "value": 1}]})
        assert bad.status_code == 422

    def test_simulated_feed_is_labelled_everywhere(self, net):
        sid = _source(net, "Berth sensor feed (simulated)").id
        net.login("analyst@northbay.example")
        r = net.post(f"/api/connectors/{sid}/simulate?minutes=20&seed=7").json()
        assert r["simulated"] is True and r["accepted"] > 0
        db = net.SF()
        assert all(t.simulated for t in db.execute(select(TelemetryReading).where(TelemetryReading.source_id == sid)).scalars())
        run = db.get(IngestionRun, r["ingestion_id"])
        assert run.connector_kind == "simulated"
        if run.change_event_id:
            assert db.get(ChangeEvent, run.change_event_id).source_type == "simulated"
        db.close()
        feed = net.get("/api/activity").json()
        assert any(i.get("via", {}).get("source_type") == "simulated" for i in feed if i["kind"] == "dataset_change")

    def test_only_a_simulated_source_can_simulate(self, net):
        sid = _source(net, "Planning inputs (CSV)").id
        net.login("analyst@northbay.example")
        assert net.post(f"/api/connectors/{sid}/simulate").status_code == 409


class TestLive:
    def test_poll_changes_is_scoped_to_one_organization(self, net):
        from backend.app.api.routers.live import poll_changes
        eng = net.SF.kw["bind"]
        since = datetime.now(timezone.utc) - timedelta(days=1)
        north, _ = poll_changes(eng, net.orgs["port-northbay"], since, False)
        east, _ = poll_changes(eng, net.orgs["port-eastmouth"], since, False)
        assert north and east and not ({e["id"] for e in north} & {e["id"] for e in east})

    def test_stream_emits_ready_and_ends(self, net, monkeypatch):
        monkeypatch.setattr(settings, "LIVE_STREAM_INTERVAL_S", 0.2)
        net.login("analyst@northbay.example")
        with net.client.stream("GET", f"/api/events/stream?max_seconds=1&scope={net.orgs['port-northbay']}") as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            text = "".join(r.iter_text())
        assert "event: ready" in text and "event: end" in text

    def test_stream_scope_is_checked_against_membership(self, net):
        net.login("analyst@northbay.example")
        r = net.client.get(f"/api/events/stream?max_seconds=1&scope={net.orgs['port-eastmouth']}")
        assert r.status_code == 403


class TestScheduler:
    def test_due_rest_sources_are_polled_in_their_own_scope(self, net, monkeypatch):
        from backend.app.connectors.scheduler import run_due_once
        monkeypatch.setattr(settings, "CONNECTOR_ALLOWED_HOSTS", [])      # nothing may be fetched
        polled = run_due_once(net.SF)
        assert polled >= 1
        s = _source(net, "Port authority open data (REST)")
        assert s.last_status == "rejected" and "ALLOWED_HOSTS" in s.last_error
