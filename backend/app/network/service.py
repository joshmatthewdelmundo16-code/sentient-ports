"""Port network: hierarchy, governed shared data, hub views — D27.

The ONLY way data crosses from one organization to another. Everything here reads other
organizations through `cross_org_session` (a separate, unscoped, read-only session) and
returns plain values, so the request session never holds another organization's objects.

What an organization can see of others is exactly this, and nothing more:

  an approval that is active, unexpired, held by an active participant, and
    · names this organization as its audience, or
    · names no audience and no case (the D22 "whole network" meaning) and the provider is
      in the same network, or
    · is limited to a collaboration case this organization is a member of (case open).

Private datasets, scenarios, runs and models of other organizations are never readable.
Hub aggregates are computed only from those shared values, with explicit coverage; a port
that did not share a figure is counted as missing, never imputed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from backend.app.persistence.database import (
    ApprovedOutput,
    CaseMember,
    CollaborationCase,
    DataContract,
    Dataset,
    Organization,
    Participant,
    Result,
)
from backend.app.product.labels import dataset_label, display_unit, field_label
from backend.app.product.util import iso
from backend.app.security.tenancy import cross_org_session, get_scope, tenant_scope
from backend.app.services.contract_validation import parse_schema

# The standard indicators a hub asks its members for. Hubs know these field NAMES (a shared
# network schema); they learn VALUES only where a member approved them.
INDICATORS: tuple[dict[str, str], ...] = (
    {"key": "annual_teu", "dataset": "decision_summary_output", "field": "annual_teu",
     "label": "Annual throughput", "unit": "TEU/year", "agg": "sum"},
    {"key": "annual_emissions_tco2", "dataset": "decision_summary_output", "field": "annual_emissions_tco2",
     "label": "Annual CO₂ emissions", "unit": "tCO2/year", "agg": "sum"},
    {"key": "berth_utilization_pct", "dataset": "decision_summary_output", "field": "berth_utilization_pct",
     "label": "Berth utilization", "unit": "%", "agg": "mean"},
    {"key": "annual_fuel_cost_usd", "dataset": "decision_summary_output", "field": "annual_fuel_cost_usd",
     "label": "Annual fuel cost", "unit": "USD/year", "agg": "sum"},
)


class NetworkError(Exception):
    pass


class ExposureDenied(NetworkError):
    """The requested field is not approved for the requesting organization."""


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class SharedValue:
    approval_id: str
    provider_org_id: str
    provider_org_name: str
    provider_kind: str
    participant_name: str
    dataset_name: str
    dataset_label: str
    field: str
    field_label: str
    unit: str | None
    value: Any
    value_present: bool
    value_source: str               # "published" | "run"
    source_run_id: str | None
    purpose: str | None
    audience: str                   # "you" | "network" | "case"
    case_id: str | None
    approved_at: str | None
    expires_at: str | None


class NetworkService:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Hierarchy
    # ------------------------------------------------------------------

    @staticmethod
    def _orgs(s: Session) -> dict[str, Organization]:
        return {o.id: o for o in s.execute(select(Organization).where(Organization.status == "active")).scalars()}

    @staticmethod
    def _root(orgs: dict[str, Organization], org_id: str) -> str:
        seen = set()
        cur = org_id
        while orgs.get(cur) is not None and orgs[cur].parent_id and cur not in seen:
            seen.add(cur)
            cur = orgs[cur].parent_id
        return cur

    @staticmethod
    def _descendants(orgs: dict[str, Organization], org_id: str) -> list[str]:
        children: dict[str, list[str]] = {}
        for o in orgs.values():
            if o.parent_id:
                children.setdefault(o.parent_id, []).append(o.id)
        out: list[str] = []
        stack = list(children.get(org_id, []))
        while stack:
            c = stack.pop()
            out.append(c)
            stack.extend(children.get(c, []))
        return out

    def hierarchy(self) -> list[dict[str, Any]]:
        with cross_org_session(self._db) as s:
            orgs = self._orgs(s)
            def node(o: Organization) -> dict[str, Any]:
                kids = sorted((c for c in orgs.values() if c.parent_id == o.id), key=lambda c: c.name)
                meta = json.loads(o.meta_json) if o.meta_json else {}
                return {"id": o.id, "key": o.org_key, "name": o.name, "kind": o.kind,
                        "synthetic": bool(meta.get("synthetic")), "children": [node(k) for k in kids]}
            roots = sorted((o for o in orgs.values() if not o.parent_id), key=lambda o: o.name)
            return [node(r) for r in roots]

    # ------------------------------------------------------------------
    # What is shared with an organization
    # ------------------------------------------------------------------

    def _case_ids_for(self, s: Session, org_id: str) -> set[str]:
        rows = s.execute(
            select(CaseMember.case_id).join(CollaborationCase, CollaborationCase.id == CaseMember.case_id)
            .where(CaseMember.member_organization_id == org_id, CollaborationCase.status == "open")
        ).scalars()
        hosted = s.execute(select(CollaborationCase.id).where(
            CollaborationCase.organization_id == org_id, CollaborationCase.status == "open")).scalars()
        return set(rows) | set(hosted)

    def shared_with(self, consumer_org_id: str, *, case_id: str | None = None,
                    include_own: bool = False) -> list[SharedValue]:
        now = datetime.now(timezone.utc)
        with cross_org_session(self._db) as s:
            orgs = self._orgs(s)
            root = self._root(orgs, consumer_org_id)
            cases = self._case_ids_for(s, consumer_org_id)
            q = select(ApprovedOutput).where(ApprovedOutput.status == "active")
            if case_id is not None:
                if case_id not in cases:
                    return []
                q = q.where(ApprovedOutput.collaboration_case_id == case_id)
            else:
                q = q.where(or_(
                    ApprovedOutput.audience_organization_id == consumer_org_id,
                    (ApprovedOutput.audience_organization_id.is_(None) & ApprovedOutput.collaboration_case_id.is_(None)),
                    ApprovedOutput.collaboration_case_id.in_(cases) if cases else false(),
                ))
            out: list[SharedValue] = []
            contracts: dict[str, dict[str, Any]] = {}
            for a in s.execute(q).scalars():
                if not include_own and a.organization_id == consumer_org_id:
                    continue
                exp = _aware(a.expires_at)
                if exp is not None and exp <= now:
                    continue
                provider = orgs.get(a.organization_id or "")
                if provider is None:
                    continue
                if a.audience_organization_id is None and a.collaboration_case_id is None \
                        and self._root(orgs, provider.id) != root:
                    continue                     # "whole network" means the provider's network
                participant = s.get(Participant, a.participant_id)
                if participant is None or participant.status != "active":
                    continue
                ds = s.get(Dataset, a.dataset_id)
                if ds is None:
                    continue
                if ds.id not in contracts:
                    c = s.execute(select(DataContract).where(DataContract.dataset_id == ds.id)
                                  .order_by(DataContract.created_at.desc())).scalars().first()
                    fields = {}
                    if c is not None:
                        try:
                            fields = {f.name: f for f in parse_schema(json.loads(c.schema_json)).fields}
                        except (ValueError, TypeError):
                            fields = {}
                    contracts[ds.id] = fields
                spec = contracts[ds.id].get(a.field_name)
                value, present = None, False
                if a.source_run_id:
                    res = s.execute(select(Result).where(Result.run_id == a.source_run_id,
                                                         Result.dataset_id == ds.id)).scalars().first()
                    rec = json.loads(res.value_json) if res is not None else None
                else:
                    rec = json.loads(ds.current_value) if ds.current_value else None
                if isinstance(rec, dict) and a.field_name in rec:
                    value, present = rec[a.field_name], True
                out.append(SharedValue(
                    approval_id=a.id, provider_org_id=provider.id, provider_org_name=provider.name,
                    provider_kind=provider.kind, participant_name=participant.name,
                    dataset_name=ds.name, dataset_label=dataset_label(ds.name, ds.display_name),
                    field=a.field_name, field_label=field_label(a.field_name, getattr(spec, "label", None)),
                    unit=display_unit(getattr(spec, "unit", None)), value=value, value_present=present,
                    value_source="run" if a.source_run_id else "published", source_run_id=a.source_run_id,
                    purpose=a.purpose,
                    audience="case" if a.collaboration_case_id else ("you" if a.audience_organization_id else "network"),
                    case_id=a.collaboration_case_id, approved_at=iso(a.approved_at), expires_at=iso(a.expires_at),
                ))
            return out

    # ------------------------------------------------------------------
    # Hub view
    # ------------------------------------------------------------------

    def hub_overview(self, hub_org_id: str) -> dict[str, Any]:
        from types import SimpleNamespace

        with cross_org_session(self._db) as s:
            loaded = self._orgs(s)
            if hub_org_id not in loaded:
                raise NetworkError("Unknown organization.")
            member_ids = self._descendants(loaded, hub_org_id)
            # Plain copies: the cross-organization session expires its objects when it closes.
            orgs = {k: SimpleNamespace(id=o.id, name=o.name, kind=o.kind, parent_id=o.parent_id)
                    for k, o in loaded.items()}
        hub = orgs[hub_org_id]
        visible = [v for v in self.shared_with(hub_org_id) if v.value_source == "published"]
        ports = [orgs[m] for m in member_ids if orgs[m].kind == "port"]
        hubs = [orgs[m] for m in member_ids if orgs[m].kind != "port"]

        members = []
        for p in sorted(ports, key=lambda o: o.name):
            cells = []
            for ind in INDICATORS:
                hit = next((v for v in visible if v.provider_org_id == p.id and v.dataset_name == ind["dataset"]
                            and v.field == ind["field"]), None)
                cells.append({
                    "indicator": ind["key"], "shared": hit is not None and hit.value_present,
                    "value": hit.value if hit else None, "purpose": hit.purpose if hit else None,
                    "audience": hit.audience if hit else None, "approval_id": hit.approval_id if hit else None,
                    "expires_at": hit.expires_at if hit else None,
                })
            members.append({"id": p.id, "name": p.name, "kind": p.kind,
                            "parent_id": p.parent_id, "indicators": cells})

        aggregates = []
        for i, ind in enumerate(INDICATORS):
            vals = [m["indicators"][i]["value"] for m in members if m["indicators"][i]["shared"]
                    and isinstance(m["indicators"][i]["value"], (int, float))]
            total = sum(vals) if vals else None
            aggregates.append({
                **ind, "unit": display_unit(ind["unit"]),
                "value": (total if ind["agg"] == "sum" else (total / len(vals) if vals else None)),
                "reporting": len(vals), "in_scope": len(members),
            })
        sub_hubs = []
        for h in sorted(hubs, key=lambda o: o.name):
            shared = [asdict(v) for v in visible if v.provider_org_id == h.id]
            sub_hubs.append({"id": h.id, "name": h.name, "kind": h.kind, "shared": shared})
        other = [asdict(v) for v in visible if v.provider_org_id not in member_ids]
        return {
            "hub": {"id": hub.id, "name": hub.name, "kind": hub.kind},
            "indicators": list(INDICATORS),
            "members": members,
            "sub_hubs": sub_hubs,
            "aggregates": aggregates,
            "shared_from_outside": other,
        }

    def indicator_record(self, overview: dict[str, Any]) -> dict[str, Any]:
        """The hub's aggregate record, with paired sums so ratios never mix coverage."""
        by_key = {i["key"]: n for n, i in enumerate(overview["indicators"])}
        members = overview["members"]
        def shared(m, key):
            c = m["indicators"][by_key[key]]
            return c["shared"] and isinstance(c["value"], (int, float))
        teu = [m for m in members if shared(m, "annual_teu")]
        emis = [m for m in members if shared(m, "annual_emissions_tco2")]
        util = [m for m in members if shared(m, "berth_utilization_pct")]
        both = [m for m in emis if shared(m, "annual_teu")]
        val = lambda m, key: m["indicators"][by_key[key]]["value"]
        return {
            "total_annual_teu": float(sum(val(m, "annual_teu") for m in teu)),
            "total_annual_emissions_tco2": float(sum(val(m, "annual_emissions_tco2") for m in both)),
            "teu_where_emissions_reported": float(sum(val(m, "annual_teu") for m in both)),
            "mean_berth_utilization_pct": (float(sum(val(m, "berth_utilization_pct") for m in util)) / len(util)) if util else None,
            "ports_in_scope": float(len(members)),
            "ports_reporting_teu": float(len(teu)),
            "ports_reporting_emissions": float(len(both)),
        }

    def materialize(self, hub_org_id: str, *, triggered_by: str) -> dict[str, Any]:
        """Write the hub's aggregate of APPROVED values into its own dataset, then propagate.

        This is the governed-share connector: source = approved outputs, target = the hub's
        `network_indicators` dataset, through the ordinary contract / change-detection /
        propagation path. The request session must already be scoped to the hub.
        """
        from backend.app.services.adapter_registry import AdapterRegistry
        from backend.app.services.change_propagation import ChangePropagationService
        from backend.app.services.dataset_values import DatasetValueService

        scope = get_scope(self._db)
        if scope is None or scope.org_id != hub_org_id:
            raise NetworkError("Materialization must run inside the hub's own scope.")
        ds = self._db.execute(select(Dataset).where(Dataset.name == "network_indicators")).scalars().first()
        if ds is None:
            raise NetworkError("Install the “Hub network performance” model pack in this hub first.")
        overview = self.hub_overview(hub_org_id)
        record = self.indicator_record(overview)
        used = sorted({c["approval_id"] for m in overview["members"] for c in m["indicators"] if c["approval_id"]})
        missing = [(m["name"], c["indicator"]) for m in overview["members"] for c in m["indicators"] if not c["shared"]]
        write = DatasetValueService(self._db).write_external(
            ds.id, record, source_type="network_share",
            source_ref=f"approved-outputs:{len(used)}", triggered_by=triggered_by)
        run_id = None
        if write.changed:
            run_id = ChangePropagationService(self._db, AdapterRegistry(self._db)).propagate(write.event.id).run_id
        return {"record": record, "changed": write.changed, "run_id": run_id,
                "approvals_used": used, "not_shared": [{"member": a, "indicator": b} for a, b in missing]}

    # ------------------------------------------------------------------
    # Explicit exposure request (the audited "may I see this?" path)
    # ------------------------------------------------------------------

    def request_exposure(self, consumer_org_id: str, provider_org_id: str, dataset_name: str,
                         field: str) -> SharedValue:
        for v in self.shared_with(consumer_org_id):
            if v.provider_org_id == provider_org_id and v.dataset_name == dataset_name and v.field == field:
                return v
        raise ExposureDenied(
            "Not shared with you: there is no active, unexpired approval of that field for "
            "your organization, your network or one of your open cases.")


def install_hub_pack(db: Session, hub_org_id: str, owner: str) -> dict[str, Any]:
    from backend.app.library.packs import install_pack
    with tenant_scope(db, hub_org_id):
        return install_pack(db, "hub_network_performance", owner=owner)
