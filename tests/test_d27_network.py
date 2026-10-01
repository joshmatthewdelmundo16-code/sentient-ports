"""D27 — port network, hub views, governed sharing across organizations, collaboration cases.

The invariant under test: data crosses organizations ONLY through an active, unexpired
approval by an active participant for this audience (a named organization, the whole network,
or an open case this organization belongs to). Everything else is invisible — and asking for
it explicitly is denied and audited.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.network.service import INDICATORS, NetworkService
from backend.app.persistence.database import (
    ApprovedOutput,
    AuditEvent,
    CollaborationCase,
    ConnectorSource,
    Dataset,
    MasterPlan,
    OptimizationStudy,
    Participant,
)
from backend.app.security.tenancy import tenant_scope
from tests.d27_support import network_client

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@pytest.fixture(scope="module")
def net():
    with network_client() as n:
        yield n


def _indicator(hub: dict, member: str, key: str) -> dict:
    m = next(x for x in hub["members"] if x["name"] == member)
    return next(c for c in m["indicators"] if c["indicator"] == key)


class TestHierarchy:
    def test_tree_is_port_regional_national_network(self, net):
        net.login("demo@platform.example")
        roots = net.get("/api/network/hierarchy", "port-northbay").json()
        network = next(r for r in roots if r["name"] == "Demo port network")
        national = network["children"][0]
        assert national["kind"] == "national_hub"
        regions = {c["name"]: c for c in national["children"]}
        assert {p["name"] for p in regions["Northern regional hub"]["children"]} == {"Northbay port", "Eastmouth port"}
        assert all(r["synthetic"] for r in roots)


class TestHubView:
    def test_regional_hub_sees_only_approved_values(self, net):
        net.login("hub@north.example")
        hub = net.get("/api/network/hub").json()
        assert {m["name"] for m in hub["members"]} == {"Northbay port", "Eastmouth port"}
        assert _indicator(hub, "Northbay port", "annual_teu") == {**_indicator(hub, "Northbay port", "annual_teu"),
                                                                    "shared": True, "value": 20_000_000.0}
        # Eastmouth never approved emissions; Northbay's fuel-cost approval was revoked.
        assert _indicator(hub, "Eastmouth port", "annual_emissions_tco2")["shared"] is False
        assert _indicator(hub, "Eastmouth port", "annual_emissions_tco2")["value"] is None
        assert _indicator(hub, "Northbay port", "annual_fuel_cost_usd")["shared"] is False

    def test_aggregates_report_coverage_and_never_impute(self, net):
        net.login("hub@north.example")
        agg = {a["key"]: a for a in net.get("/api/network/hub").json()["aggregates"]}
        assert agg["annual_teu"]["value"] == 31_700_000.0 and (agg["annual_teu"]["reporting"], agg["annual_teu"]["in_scope"]) == (2, 2)
        assert agg["annual_emissions_tco2"]["value"] == 62_280.0 and agg["annual_emissions_tco2"]["reporting"] == 1
        assert agg["annual_fuel_cost_usd"]["value"] is None and agg["annual_fuel_cost_usd"]["reporting"] == 0

    def test_hub_aggregate_flows_into_the_hub_federation_with_paired_intensity(self, net):
        net.login("hub@north.example")
        perf = next(d for d in net.get("/api/catalog").json() if d["name"] == "network_performance_output")
        # Intensity uses only ports that shared BOTH figures (Northbay), not 62,280 / 31.7M.
        assert perf["value"]["emissions_intensity_tco2_per_teu"] == pytest.approx(62_280 / 20_000_000)
        assert perf["value"]["emissions_coverage_pct"] == 50.0
        src = next(d for d in net.get("/api/catalog").json() if d["name"] == "network_indicators")
        assert src["current_source"]["source_type"] == "network_share"

    def test_national_hub_sees_regional_aggregates_not_port_privates(self, net):
        net.login("exec@national.example")
        hub = net.get("/api/network/hub").json()
        north = next(h for h in hub["sub_hubs"] if h["name"] == "Northern regional hub")
        fields = {s["field"] for s in north["shared"]}
        assert fields == {"network_throughput_teu", "emissions_intensity_tco2_per_teu", "teu_coverage_pct"}
        # Port values reach the national hub only where a port approved them for it (or the
        # whole network): Northbay's network-wide berth utilization, nothing else.
        seen = {(m["name"], c["indicator"]) for m in hub["members"] for c in m["indicators"] if c["shared"]}
        assert seen == {("Northbay port", "berth_utilization_pct")}

    def test_whole_network_means_the_providers_network_only(self, net):
        net.login("demo@platform.example")
        sandbox = net.get("/api/network/shared-with-me", "engine-sandbox").json()
        assert sandbox == []                     # a separate tree: sees nothing of the port network

    def test_materialize_requires_the_hub_scope_and_its_pack(self, net):
        net.login("demo@platform.example")
        r = net.post("/api/network/hub/materialize", "port-northbay")
        assert r.status_code in (403, 409)       # Northbay has no hub pack (and is a port)


class TestExposure:
    def test_unshared_field_is_denied_and_audited(self, net):
        net.login("hub@north.example")
        r = net.get(f"/api/network/exposure?provider={net.orgs['port-northbay']}"
                    "&dataset=decision_summary_output&field=fuel_cost_per_teu")
        assert r.status_code == 403
        db = net.SF()
        denied = db.execute(select(AuditEvent).where(AuditEvent.action == "exposure.denied")).scalars().all()
        db.close()
        assert any(e.organization_id == net.orgs["demo-north-hub"] for e in denied)

    def test_shared_field_is_granted_with_its_purpose(self, net):
        net.login("hub@north.example")
        r = net.get(f"/api/network/exposure?provider={net.orgs['port-northbay']}"
                    "&dataset=decision_summary_output&field=annual_teu").json()
        assert r["value"] == 20_000_000.0 and r["purpose"] == "Regional capacity planning"
        assert r["audience"] == "you"

    def test_private_records_of_another_organization_stay_invisible(self, net):
        net.login("hub@north.example")
        text = json.dumps([net.get(p).json() for p in ("/api/network/hub", "/api/network/shared-with-me")])
        db = net.SF()
        private = {d.id for d in db.execute(select(Dataset).where(
            Dataset.organization_id == net.orgs["port-northbay"], Dataset.name == "port_assumptions")).scalars()}
        db.close()
        assert private and not (private & set(UUID.findall(text)))
        assert "bunker_price" not in text


class TestExpiryAndRevocation:
    def test_expired_and_revoked_approvals_disappear(self, net):
        db = net.SF()
        org = net.orgs["port-southreach"]
        with tenant_scope(db, org):
            a = db.execute(select(ApprovedOutput).where(ApprovedOutput.field_name == "annual_emissions_tco2")).scalars().first()
            a.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        db.close()
        net.login("demo@platform.example")
        south = NetworkService(net.SF()).shared_with(net.orgs["demo-south-hub"])
        assert "annual_emissions_tco2" not in {v.field for v in south}
        assert "annual_teu" in {v.field for v in south}


class TestCases:
    def test_members_see_the_case_and_its_shared_scenario_result(self, net):
        net.login("analyst@eastmouth.example")
        cases = net.get("/api/cases").json()
        case = next(c for c in cases if c["title"] == "Shared berth capacity study 2030")
        detail = net.get(f"/api/cases/{case['id']}").json()
        (out,) = [o for o in detail["shared_outputs"] if o["field"] == "annual_fuel_cost_usd"]
        assert out["value_source"] == "run" and out["value"] == 15_000_000.0     # the scenario, not today's 12.0M
        assert detail["my_role"] == "participant"

    def test_non_members_cannot_see_a_case(self, net):
        net.login("analyst@southreach.example")
        assert all(c["title"] != "Shared berth capacity study 2030" for c in net.get("/api/cases").json())
        case_id = net.seeded["case_id"]
        assert net.get(f"/api/cases/{case_id}").status_code == 404
        # And the case-limited value is not in its shared data either.
        assert all(v["audience"] != "case" for v in net.get("/api/network/shared-with-me").json())

    def test_case_lifecycle_share_then_close(self, net):
        net.login("hub@north.example")
        created = net.post("/api/cases", json={
            "title": "Joint emissions review", "purpose": "Compare in-port emissions methods.",
            "member_organization_ids": [net.orgs["port-northbay"]]})
        assert created.status_code == 201, created.text
        case_id = created.json()["id"]
        assert created.json()["members"][0]["role"] == "host"
        net.login("approver@northbay.example")
        part = next(p for p in net.get("/api/participants").json() if p["status"] == "active")
        summary = next(d for d in net.get("/api/catalog").json() if d["name"] == "decision_summary_output")
        r = net.post(f"/api/cases/{case_id}/outputs", json={
            "participant_id": part["id"], "dataset_id": summary["id"], "field_name": "emissions_per_teu",
            "purpose": "Method comparison"})
        assert r.status_code == 201, r.text
        net.login("hub@north.example")
        detail = net.get(f"/api/cases/{case_id}").json()
        assert [o["field"] for o in detail["shared_outputs"]] == ["emissions_per_teu"]
        # Not the host → cannot close.
        net.login("approver@northbay.example")
        assert net.post(f"/api/cases/{case_id}/close").status_code == 404
        net.login("hub@north.example")
        closed = net.post(f"/api/cases/{case_id}/close").json()
        assert closed["status"] == "closed" and closed["shared_outputs"] == []
        assert "emissions_per_teu" not in {v["field"] for v in net.get("/api/network/shared-with-me").json()
                                           if v["case_id"] == case_id}
        actions = {e["action"] for e in net.get(f"/api/cases/{case_id}").json()["audit"]}
        assert {"case.created", "case.output_shared", "case.closed"} <= actions

    def test_sharing_into_a_case_you_are_not_in_is_refused(self, net):
        net.login("analyst@southreach.example")
        part = net.get("/api/participants").json()[0]
        summary = next(d for d in net.get("/api/catalog").json() if d["name"] == "decision_summary_output")
        r = net.post(f"/api/cases/{net.seeded['case_id']}/outputs", json={
            "participant_id": part["id"], "dataset_id": summary["id"], "field_name": "annual_teu",
            "purpose": "Trying to join uninvited"})
        assert r.status_code == 422

    def test_same_field_can_go_to_a_hub_and_into_a_case(self, net):
        db = net.SF()
        rows = db.execute(select(ApprovedOutput).where(
            ApprovedOutput.organization_id == net.orgs["port-northbay"],
            ApprovedOutput.field_name == "annual_fuel_cost_usd", ApprovedOutput.status == "active")).scalars().all()
        db.close()
        assert any(r.collaboration_case_id for r in rows)


class TestD27Isolation:
    def test_plans_studies_connectors_and_cases_are_private(self, net):
        db = net.SF()
        nb = net.orgs["port-northbay"]
        ids = {
            "plan": [p.id for p in db.execute(select(MasterPlan).where(MasterPlan.organization_id == nb)).scalars()],
            "study": [s.id for s in db.execute(select(OptimizationStudy).where(OptimizationStudy.organization_id == nb)).scalars()],
            "source": [s.id for s in db.execute(select(ConnectorSource).where(ConnectorSource.organization_id == nb)).scalars()],
        }
        db.close()
        assert all(ids.values())
        net.login("analyst@eastmouth.example")
        for pid in ids["plan"]:
            assert net.get(f"/api/plans/{pid}").status_code == 404
            assert net.post(f"/api/plans/{pid}/evaluate").status_code == 404
        for sid in ids["study"]:
            assert net.get(f"/api/optimization/{sid}").status_code == 404
            assert net.post(f"/api/optimization/{sid}/run").status_code == 404
        for cid in ids["source"]:
            assert net.post(f"/api/connectors/{cid}/simulate").status_code == 404
        listing = json.dumps([net.get(p).json() for p in ("/api/plans", "/api/optimization", "/api/connectors",
                                                           "/api/model-library")])
        leaked = set(UUID.findall(listing)) & {i for v in ids.values() for i in v}
        assert leaked == set()
