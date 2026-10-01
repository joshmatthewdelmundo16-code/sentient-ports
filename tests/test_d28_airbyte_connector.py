"""D28 — Airbyte connector.

Airbyte itself is not run here (it needs Docker/Kubernetes). What is tested is everything the
platform owns: reading tables in the exact layout Airbyte's Postgres destination writes
(``_airbyte_raw_id``, ``_airbyte_extracted_at``, ``_airbyte_meta``, then the stream's columns),
sending the newest synced values through the governed path, the organization stream-prefix
isolation, the refusals, the poll scheduler, and the Airbyte public-API client against a fake.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import JSON, Column, DateTime, Float, MetaData, String, Table, select

from backend.app.config import settings
from backend.app.connectors import airbyte
from backend.app.connectors.base import ConnectorError
from backend.app.connectors.scheduler import run_due_once
from backend.app.persistence.database import ChangeEvent, ConnectorSource, IngestionRun, Organization
from tests.d27_support import network_client

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def net():
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "AIRBYTE_STAGING_SCHEMA", "")        # SQLite has no schemas
    with network_client() as n:
        yield n
    mp.undo()


@pytest.fixture(autouse=True)
def _no_schema(monkeypatch):
    monkeypatch.setattr(settings, "AIRBYTE_STAGING_SCHEMA", "")


def _airbyte_table(net, name: str, columns: list[Column], rows: list[dict]) -> None:
    """Create a table shaped like an Airbyte Postgres destination (typed, v2) table."""
    db = net.SF()
    md = MetaData()
    t = Table(name, md,
              Column("_airbyte_raw_id", String(36), primary_key=True),
              Column("_airbyte_extracted_at", DateTime(timezone=True), nullable=False),
              Column("_airbyte_meta", JSON),
              *columns)
    md.drop_all(db.connection(), tables=[t])
    md.create_all(db.connection(), tables=[t])
    for i, row in enumerate(rows):
        db.connection().execute(t.insert().values(
            _airbyte_raw_id=f"raw-{name}-{i}", _airbyte_meta={"changes": []}, **row))
    db.commit()
    db.close()


def _dataset(net, name: str = "planning_inputs", org: str = "port-northbay") -> dict:
    net.login("demo@platform.example")
    return next(d for d in net.get("/api/catalog", org).json() if d["name"] == name)


def _define(net, config: dict, name: str, org: str = "port-northbay"):
    ds = _dataset(net, org=org)
    return net.post("/api/connectors", org, json={"name": name, "kind": "airbyte",
                                                   "target_dataset_id": ds["id"], "config": config})


def _current(net) -> dict:
    return _dataset(net)["current_value"] if "current_value" in _dataset(net) else None


# ---------------------------------------------------------------------------

class TestDefinition:
    def test_kind_is_listed_and_configurable(self, net):
        net.login("demo@platform.example")
        body = net.get("/api/connectors/kinds", "port-northbay").json()
        assert body["kinds"]["airbyte"]["status"] == "implemented"
        assert "airbyte" in body["configurable"]

    @pytest.mark.parametrize("config, fragment", [
        ({}, "stream_table"),
        ({"stream_table": "Planning; DROP TABLE x"}, "stream_table"),
        ({"stream_table": "port_eastmouth__planning"}, "port_northbay__"),   # another org's prefix
        ({"stream_table": "port_northbay__p", "layout": "pivot"}, "layout"),
        ({"stream_table": "port_northbay__p", "fields": {"demand_teu": "Demand TEU"}}, "fields"),
    ])
    def test_invalid_definitions_are_refused(self, net, config, fragment):
        r = _define(net, config, "bad")
        assert r.status_code == 422 and fragment in r.json()["detail"]

    def test_prefix_is_per_organization(self, net):
        db = net.SF()
        east = db.execute(select(Organization).where(Organization.org_key == "port-eastmouth")).scalar_one()
        north = db.execute(select(Organization).where(Organization.org_key == "port-northbay")).scalar_one()
        airbyte.validate_config(db, north.id, {"stream_table": "port_northbay__planning"})
        with pytest.raises(ConnectorError, match="port_eastmouth__"):
            airbyte.validate_config(db, east.id, {"stream_table": "port_northbay__planning"})
        db.close()


class TestWideLayout:
    def test_newest_row_is_ingested_and_propagates(self, net):
        _airbyte_table(net, "port_northbay__plan_wide",
                       [Column("demand_teu", Float), Column("cranes_per_berth", Float), Column("notes", String)],
                       [{"_airbyte_extracted_at": T0, "demand_teu": 1_100_000, "cranes_per_berth": 3, "notes": "old"},
                        {"_airbyte_extracted_at": T0 + timedelta(hours=1), "demand_teu": 1_750_000,
                         "cranes_per_berth": 5, "notes": "new"}])
        r = _define(net, {"stream_table": "port_northbay__plan_wide"}, "Planning (Airbyte, wide)")
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        net.login("analyst@northbay.example")
        out = net.post(f"/api/connectors/{sid}/airbyte/ingest").json()
        assert out["status"] == "ingested" and out["graph_run_id"], out
        db = net.SF()
        run = db.get(IngestionRun, out["ingestion_id"])
        ev = db.get(ChangeEvent, run.change_event_id)
        db.close()
        value = json.loads(ev.new_value_json)
        assert run.connector_kind == "airbyte" and ev.source_type == "airbyte"
        assert value["demand_teu"] == 1_750_000.0 and value["cranes_per_berth"] == 5.0
        assert value["berths"] == 4.0                       # untouched fields merged, not dropped
        assert "port_northbay__plan_wide@2026-09-01" in ev.source_ref
        again = net.post(f"/api/connectors/{sid}/airbyte/ingest").json()
        assert again["status"] == "unchanged"                # re-reading the same sync is a no-op

    def test_explicit_column_mapping(self, net):
        _airbyte_table(net, "port_northbay__plan_mapped", [Column("teu_demand", Float)],
                       [{"_airbyte_extracted_at": T0, "teu_demand": 1_234_000}])
        sid = _define(net, {"stream_table": "port_northbay__plan_mapped",
                            "fields": {"demand_teu": "teu_demand"}}, "Planning (Airbyte, mapped)").json()["id"]
        out = net.post(f"/api/connectors/{sid}/airbyte/ingest", "port-northbay").json()
        assert out["status"] == "ingested", out


class TestFieldValueLayout:
    def test_excel_style_sheet_newest_value_per_field(self, net):
        # e.g. an Excel sheet with columns field | value, synced by Airbyte's file/SharePoint source.
        _airbyte_table(net, "port_northbay__assumptions_sheet", [Column("field", String), Column("value", String)],
                       [{"_airbyte_extracted_at": T0, "field": "demand_teu", "value": "1500000"},
                        {"_airbyte_extracted_at": T0 + timedelta(minutes=5), "field": "demand_teu", "value": "1650000"},
                        {"_airbyte_extracted_at": T0, "field": "berths", "value": "6"}])
        sid = _define(net, {"stream_table": "port_northbay__assumptions_sheet", "layout": "field_value"},
                      "Assumptions sheet (Airbyte)").json()["id"]
        out = net.post(f"/api/connectors/{sid}/airbyte/ingest", "port-northbay").json()
        assert out["status"] == "ingested", out
        db = net.SF()
        ev = db.get(ChangeEvent, db.get(IngestionRun, out["ingestion_id"]).change_event_id)
        db.close()
        value = json.loads(ev.new_value_json)
        assert value["demand_teu"] == 1_650_000.0 and value["berths"] == 6.0


class TestRefusals:
    def _hash(self, net):
        return _dataset(net)["value_hash"]

    @pytest.mark.parametrize("table, columns, rows, fragment", [
        ("port_northbay__r_unknown", [Column("field", String), Column("value", String)],
         [{"_airbyte_extracted_at": T0, "field": "not_a_field", "value": "1"}], "not a field"),
        ("port_northbay__r_badvalue", [Column("field", String), Column("value", String)],
         [{"_airbyte_extracted_at": T0, "field": "demand_teu", "value": "lots"}], "rejected"),
        ("port_northbay__r_negative", [Column("field", String), Column("value", String)],
         [{"_airbyte_extracted_at": T0, "field": "demand_teu", "value": "-5"}], "demand_teu"),
        ("port_northbay__r_empty", [Column("field", String), Column("value", String)], [], "empty"),
    ])
    def test_bad_synced_data_is_rejected_and_writes_nothing(self, net, table, columns, rows, fragment):
        _airbyte_table(net, table, columns, rows)
        sid = _define(net, {"stream_table": table, "layout": "field_value"}, f"refuse {table}").json()["id"]
        before = self._hash(net)
        out = net.post(f"/api/connectors/{sid}/airbyte/ingest", "port-northbay").json()
        assert out["status"] == "rejected" and fragment in out["error"], out
        assert self._hash(net) == before

    def test_table_not_synced_yet(self, net):
        sid = _define(net, {"stream_table": "port_northbay__never_synced"}, "not yet").json()["id"]
        out = net.post(f"/api/connectors/{sid}/airbyte/ingest", "port-northbay").json()
        assert out["status"] == "rejected" and "not synced" in out["error"]

    def test_non_airbyte_table_is_refused(self, net):
        db = net.SF()
        md = MetaData()
        t = Table("port_northbay__plain", md, Column("demand_teu", Float))
        md.create_all(db.connection(), tables=[t])
        db.connection().execute(t.insert().values(demand_teu=1.0))
        db.commit()
        db.close()
        sid = _define(net, {"stream_table": "port_northbay__plain"}, "plain").json()["id"]
        out = net.post(f"/api/connectors/{sid}/airbyte/ingest", "port-northbay").json()
        assert out["status"] == "rejected" and "_airbyte_extracted_at" in out["error"]

    def test_other_kinds_cannot_use_airbyte_routes(self, net):
        net.login("demo@platform.example")
        csv_source = next(s for s in net.get("/api/connectors", "port-northbay").json() if s["kind"] == "csv_upload")
        assert net.post(f"/api/connectors/{csv_source['id']}/airbyte/ingest", "port-northbay").status_code == 409


class FakeAirbyte:
    def __init__(self):
        self.calls: list[tuple] = []

    def post(self, path, body, headers):
        self.calls.append(("POST", path, body, headers))
        if path == "/v1/applications/token":
            return {"access_token": "issued-token"}
        return {"jobId": 42, "status": "pending", "jobType": "sync"}

    def get(self, path, headers):
        self.calls.append(("GET", path, None, headers))
        return {"jobId": 42, "status": "succeeded", "rowsSynced": 3}


class TestSyncTrigger:
    def test_sync_needs_a_connection_id(self, net):
        sid = _define(net, {"stream_table": "port_northbay__plan_wide"}, "no conn").json()["id"]
        assert net.post(f"/api/connectors/{sid}/airbyte/sync", "port-northbay").status_code == 409

    def test_unconfigured_api_is_503(self, net, monkeypatch):
        monkeypatch.setattr(settings, "AIRBYTE_API_URL", "")
        sid = _define(net, {"stream_table": "port_northbay__plan_wide",
                            "connection_id": "3f1c9a52-8d7e-4b1a-9c11-0a2b3c4d5e6f"}, "conn unconfigured").json()["id"]
        r = net.post(f"/api/connectors/{sid}/airbyte/sync", "port-northbay")
        assert r.status_code == 503 and "AIRBYTE_API_URL" in r.json()["detail"]

    def test_trigger_and_follow_with_client_credentials(self, net, monkeypatch):
        fake = FakeAirbyte()
        monkeypatch.setattr(settings, "AIRBYTE_API_TOKEN", "")
        monkeypatch.setattr(settings, "AIRBYTE_CLIENT_ID", "client")
        monkeypatch.setattr(settings, "AIRBYTE_CLIENT_SECRET", "TESTONLY")
        monkeypatch.setattr(airbyte.AirbyteClient, "from_env", classmethod(lambda cls: cls(fake)))
        conn = "3f1c9a52-8d7e-4b1a-9c11-0a2b3c4d5e6f"
        sid = _define(net, {"stream_table": "port_northbay__plan_wide", "connection_id": conn}, "conn ok").json()["id"]
        out = net.post(f"/api/connectors/{sid}/airbyte/sync", "port-northbay").json()
        assert out == {"job_id": 42, "airbyte_status": "pending", "status": "running"}
        job = net.get(f"/api/connectors/{sid}/airbyte/jobs/42", "port-northbay").json()
        assert job["status"] == "succeeded" and job["rows_synced"] == 3
        token_call, sync_call = fake.calls[0], fake.calls[1]
        assert token_call[1] == "/v1/applications/token"
        assert sync_call[2] == {"connectionId": conn, "jobType": "sync"}
        assert sync_call[3] == {"Authorization": "Bearer issued-token"}


class TestScheduler:
    def test_scheduler_ingests_due_airbyte_sources(self, net):
        _airbyte_table(net, "port_northbay__scheduled", [Column("demand_teu", Float)],
                       [{"_airbyte_extracted_at": T0, "demand_teu": 1_888_000}])
        sid = _define(net, {"stream_table": "port_northbay__scheduled"}, "scheduled").json()["id"]
        run_due_once(net.SF)
        db = net.SF()
        src = db.get(ConnectorSource, sid)
        db.close()
        assert src.last_status == "ingested"
