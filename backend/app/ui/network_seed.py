"""Synthetic multi-port network demo — D26 (extended by D27).

Builds, in a LOCAL SQLite database only:

    Demo port network                         (network)
    └─ National executive hub                 (national hub)
       ├─ Northern regional hub               (regional hub)
       │  ├─ Northbay port                    (port private zone)
       │  └─ Eastmouth port                   (port private zone)
       └─ Southern regional hub               (regional hub)
          └─ Southreach port                  (port private zone)
    Engine sandbox                            (the D0–D16 fuel-price chain, kept for tests)

Each port gets its OWN private federation (the D17 port models, datasets and contracts),
its own synthetic assumptions, a baseline, and a scenario — all stamped with that port's
organization, so no other organization can see them. Each port's authority then approves a
few outputs for a specific hub (or the whole network); some deliberately are NOT shared,
one is revoked and one expires, so the governed boundary is visible rather than theoretical.

Honesty: every organization, participant, approval and account here is synthetic and says
so in its metadata. The numbers are illustrative, not real port data. Nothing here runs
against a shared database: the caller checks the dialect first and this module checks it
again.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.persistence.database import (
    AppUser,
    ApprovedOutput,
    Baseline,
    Dataset,
    Membership,
    Organization,
    Participant,
    Scenario,
)
from backend.app.security.passwords import hash_password
from backend.app.security.tenancy import tenant_scope
from backend.app.services.scenarios import ScenarioService
from backend.app.ui import port_seed
from backend.app.ui.port_seed import BUNKER_PRICE

SYNTHETIC = {"synthetic": True,
             "disclaimer": "Synthetic demonstration record — not a real organization, agreement or approval."}

DEMO_PASSWORD = "port-network-demo-2026"

# key, name, kind, parent key
ORGS: list[tuple[str, str, str, str | None]] = [
    ("demo-network", "Demo port network", "network", None),
    ("demo-national", "National executive hub", "national_hub", "demo-network"),
    ("demo-north-hub", "Northern regional hub", "regional_hub", "demo-national"),
    ("demo-south-hub", "Southern regional hub", "regional_hub", "demo-national"),
    ("port-northbay", "Northbay port", "port", "demo-north-hub"),
    ("port-eastmouth", "Eastmouth port", "port", "demo-north-hub"),
    ("port-southreach", "Southreach port", "port", "demo-south-hub"),
    ("engine-sandbox", "Engine sandbox", "sandbox", None),
]

# Synthetic starting assumptions per port (Northbay = the D17 golden set).
PORT_ASSUMPTIONS: dict[str, dict[str, float]] = {
    "port-northbay": dict(port_seed.DEFAULT_ASSUMPTIONS),
    "port-eastmouth": {**port_seed.DEFAULT_ASSUMPTIONS, "annual_vessel_calls": 6500.0,
                       "average_teu_per_call": 1800.0, "number_of_berths": 4.0,
                       "bunker_price": 615.0, "average_berth_hours_per_call": 2.4},
    "port-southreach": {**port_seed.DEFAULT_ASSUMPTIONS, "annual_vessel_calls": 4200.0,
                        "average_teu_per_call": 1500.0, "number_of_berths": 3.0,
                        "bunker_price": 590.0, "fuel_burned_in_port_per_call": 2.3},
}

# port key → (participant key, participant name)
AUTHORITIES = {
    "port-northbay": ("northbay-port-authority", "Northbay port authority"),
    "port-eastmouth": ("eastmouth-port-authority", "Eastmouth port authority"),
    "port-southreach": ("southreach-port-authority", "Southreach port authority"),
}

# port, field of decision_summary_output, audience org key (None = whole network),
# purpose, lifecycle ("active" | "revoked" | "expiring")
APPROVALS: list[tuple[str, str, str | None, str, str]] = [
    ("port-northbay", "annual_teu", "demo-north-hub", "Regional capacity planning", "active"),
    ("port-northbay", "annual_emissions_tco2", "demo-north-hub", "Regional emissions reporting", "active"),
    ("port-northbay", "berth_utilization_pct", None, "Network performance benchmarking", "active"),
    ("port-northbay", "annual_fuel_cost_usd", "demo-national", "National cost review", "revoked"),
    ("port-eastmouth", "annual_teu", "demo-north-hub", "Regional capacity planning", "active"),
    ("port-eastmouth", "berth_utilization_pct", "demo-north-hub", "Regional capacity planning", "expiring"),
    ("port-southreach", "annual_teu", "demo-south-hub", "Regional capacity planning", "active"),
    ("port-southreach", "annual_emissions_tco2", "demo-south-hub", "Regional emissions reporting", "active"),
]

# email, display name, [(org key, role, default?)]
ACCOUNTS: list[tuple[str, str, list[tuple[str, str, bool]]]] = [
    ("demo@platform.example", "Demo reviewer", [
        ("port-northbay", "admin", True), ("port-eastmouth", "analyst", False),
        ("demo-north-hub", "approver", False), ("demo-national", "analyst", False),
        ("demo-network", "viewer", False), ("engine-sandbox", "admin", False),
    ]),
    ("analyst@northbay.example", "Northbay analyst", [("port-northbay", "analyst", True)]),
    ("approver@northbay.example", "Northbay approver", [("port-northbay", "approver", True)]),
    ("viewer@northbay.example", "Northbay viewer", [("port-northbay", "viewer", True)]),
    ("analyst@eastmouth.example", "Eastmouth analyst", [("port-eastmouth", "approver", True)]),
    ("analyst@southreach.example", "Southreach analyst", [("port-southreach", "approver", True)]),
    ("hub@north.example", "Northern hub analyst", [("demo-north-hub", "approver", True)]),
    ("exec@national.example", "National executive", [("demo-national", "analyst", True)]),
]

DEMO_ACCOUNTS = [
    {"email": email, "role": roles[0][1],
     "org_name": next(n for k, n, _, _ in ORGS if k == roles[0][0]) +
                 (f" (+{len(roles) - 1} more)" if len(roles) > 1 else ""),
     "password": DEMO_PASSWORD}
    for email, _name, roles in ACCOUNTS
]


def _is_sqlite(db: Session) -> bool:
    try:
        return db.get_bind().dialect.name == "sqlite"
    except Exception:  # noqa: BLE001
        return False


def ensure_organizations(db: Session) -> dict[str, Organization]:
    by_key = {o.org_key: o for o in db.execute(select(Organization)).scalars()}
    for key, name, kind, parent in ORGS:
        org = by_key.get(key)
        if org is None:
            meta = dict(SYNTHETIC)
            if key == "port-northbay":
                meta["local_default"] = True
            org = Organization(org_key=key, name=name, kind=kind, status="active",
                               meta_json=json.dumps(meta))
            db.add(org)
            db.flush()
            by_key[key] = org
    for key, _name, _kind, parent in ORGS:
        if parent and by_key[key].parent_id is None:
            by_key[key].parent_id = by_key[parent].id
    db.flush()
    return by_key


def _seed_port(db: Session, org: Organization) -> dict[str, Any]:
    with tenant_scope(db, org.id):
        port = port_seed.seed_port_domain(db, PORT_ASSUMPTIONS[org.org_key], owner=org.org_key)
        svc = ScenarioService(db)
        baseline = db.execute(select(Baseline).where(Baseline.name == "Current operations")).scalars().first()
        if baseline is None:
            baseline = svc.create_baseline(
                name="Current operations",
                description=f"{org.name} today, from its own synthetic assumptions.",
                target_version_id=port["terminal_version_id"])
        if baseline.baseline_run_id is None:
            svc.execute_baseline(baseline.id, triggered_by="network-seed")
        scenario = db.execute(select(Scenario).where(Scenario.baseline_id == baseline.id,
                                                     Scenario.name == "Higher bunker price")).scalars().first()
        if scenario is None:
            scenario = svc.create_scenario(
                baseline_id=baseline.id, name="Higher bunker price",
                description="In-port bunker fuel 25% dearer than today.")
            current = PORT_ASSUMPTIONS[org.org_key][BUNKER_PRICE]
            svc.set_override(scenario.id, port["assumptions_dataset_id"], BUNKER_PRICE, round(current * 1.25, 2))
        if scenario.scenario_run_id is None:
            svc.execute_scenario(scenario.id, triggered_by="network-seed")
        db.flush()
        from backend.app.ui.port_decision_seed import build_decision_config
        return build_decision_config(port, baseline, scenario, svc)


def _seed_governance(db: Session, orgs: dict[str, Organization]) -> int:
    created = 0
    now = datetime.now(timezone.utc)
    for port_key, (pkey, pname) in AUTHORITIES.items():
        org = orgs[port_key]
        with tenant_scope(db, org.id):
            participant = db.execute(select(Participant).where(Participant.participant_key == pkey)).scalars().first()
            if participant is None:
                participant = Participant(participant_key=pkey, name=pname, status="active",
                                          description=f"Approves what {org.name} shares. Synthetic.",
                                          meta_json=json.dumps(SYNTHETIC))
                db.add(participant)
                db.flush()
            summary = db.execute(select(Dataset).where(Dataset.name == "decision_summary_output")).scalars().first()
            for p_key, field, audience, purpose, lifecycle in APPROVALS:
                if p_key != port_key or summary is None:
                    continue
                exists = db.execute(select(ApprovedOutput).where(
                    ApprovedOutput.participant_id == participant.id,
                    ApprovedOutput.dataset_id == summary.id,
                    ApprovedOutput.field_name == field)).scalars().first()
                if exists is not None:
                    continue
                db.add(ApprovedOutput(
                    participant_id=participant.id, dataset_id=summary.id, field_name=field,
                    purpose=purpose, status="revoked" if lifecycle == "revoked" else "active",
                    approved_at=now - timedelta(days=12),
                    expires_at=now + timedelta(days=45) if lifecycle == "expiring" else None,
                    revoked_at=now - timedelta(days=2) if lifecycle == "revoked" else None,
                    revocation_reason="Superseded by the national cost methodology (synthetic)." if lifecycle == "revoked" else None,
                    audience_organization_id=orgs[audience].id if audience else None,
                    meta_json=json.dumps(SYNTHETIC),
                ))
                created += 1
            db.flush()
    return created


def _seed_accounts(db: Session, orgs: dict[str, Organization]) -> int:
    created = 0
    for email, name, roles in ACCOUNTS:
        user = db.execute(select(AppUser).where(AppUser.email == email)).scalars().first()
        if user is None:
            user = AppUser(email=email, display_name=name, password_hash=hash_password(DEMO_PASSWORD),
                           status="active")
            db.add(user)
            db.flush()
            created += 1
        have = {m.organization_id for m in db.execute(select(Membership).where(Membership.user_id == user.id)).scalars()}
        for org_key, role, default in roles:
            if orgs[org_key].id not in have:
                db.add(Membership(user_id=user.id, organization_id=orgs[org_key].id, role=role, is_default=default))
    db.flush()
    return created


def seed_network_demo(db: Session, *, extended: bool = True) -> dict[str, Any]:
    """Idempotently build the demo network. Returns configs for the legacy pages.

    extended=False builds only the D26 part (organizations, accounts, port federations,
    approvals) — used by tests that do not need hubs, plans, connectors or cases."""
    if not _is_sqlite(db):
        return {}
    orgs = ensure_organizations(db)
    from backend.app.ui.demo_seed import seed_demo_data

    with tenant_scope(db, orgs["engine-sandbox"].id):
        sandbox = seed_demo_data(db)
    ports = {key: _seed_port(db, orgs[key]) for key in PORT_ASSUMPTIONS}
    approvals = _seed_governance(db, orgs)
    accounts = _seed_accounts(db, orgs)

    # D27 extends the demo (hub federations, collaboration case, planning packs). Imported
    # lazily so this module stays usable on its own.
    extra: dict[str, Any] = {}
    if extended:
        from backend.app.network.demo import seed_d27_demo
        extra = seed_d27_demo(db, orgs)
    db.flush()
    return {
        "organizations": {k: o.id for k, o in orgs.items()},
        "sandbox": sandbox,
        "ports": ports,
        "decision": ports["port-northbay"],
        "approvals_created": approvals,
        "accounts_created": accounts,
        **extra,
    }
