"""Guarded D24 governance demo seed.

Why this exists
---------------
D22 built the governance model (participants, approved outputs, the exposed-output
boundary) and D23 built `/ui/governance` to display it — but nothing ever created a
participant, so the page rendered three empty tables. The capability existed and was
invisible.

Honesty constraints
-------------------
Every record written here is **synthetic demo metadata**, not a real data-sharing
agreement and not a record of any real organisation's consent. Each participant and each
approval carries `metadata.synthetic = true` and a `metadata.disclaimer` string, and the
governance UI renders that label next to the data. Nothing here represents a real approval
by a real party.

Safety
------
SQLite-only and idempotent, matching `port_decision_seed`. A shared PostgreSQL/Supabase
database is never written to by a startup.

The approvals deliberately exercise every branch of the exposed-output boundary so the
page teaches what the boundary does:
  * two active approvals on a **baseline** output dataset  → appear in Exposed Outputs
  * one approval held by an **inactive** participant       → never exposed
  * one **revoked** approval                               → never exposed
Scenario outputs are never approved here, because scenario results are never auto-approved.
"""

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from backend.app.persistence.dataset_repository import DatasetRepository
from backend.app.persistence.exceptions import NotFoundError
from backend.app.services.governance import (
    DuplicateApprovalError,
    DuplicateParticipantError,
    GovernanceService,
    ParticipantNotFoundError,
)

_DISCLAIMER = (
    "Synthetic demo record. Not a real Data Collaboration Agreement and not evidence "
    "that any real organisation approved anything."
)


def _meta(extra: dict | None = None) -> dict:
    meta = {"synthetic": True, "disclaimer": _DISCLAIMER, "seeded_by": "d24-governance-seed"}
    if extra:
        meta.update(extra)
    return meta


# participant_key, display name, status, description
_PARTICIPANTS = [
    (
        "port-authority",
        "Demo Port Authority",
        "active",
        "Operator of the synthetic port. Owns the assumptions and the decision summary.",
    ),
    (
        "terminal-operator",
        "Demo Terminal Operator",
        "active",
        "Runs the container terminal. Consumes throughput and berth-utilisation outputs.",
    ),
    (
        "environmental-regulator",
        "Demo Environmental Regulator",
        "active",
        "Receives emissions reporting only — never cost or commercial fields.",
    ),
    (
        "prospective-partner",
        "Demo Prospective Partner",
        "inactive",
        "Onboarding not complete. Holds an approval that must NOT be exposed while inactive.",
    ),
]

# participant_key, dataset name, field, purpose, revoked?
_APPROVALS = [
    ("environmental-regulator", "port_emissions_output", "annual_emissions_tco2",
     "Annual in-port CO2 reporting.", False),
    ("environmental-regulator", "port_emissions_output", "emissions_per_teu",
     "Emission intensity benchmarking.", False),
    ("terminal-operator", "throughput_output", "annual_teu",
     "Capacity planning against agreed throughput.", False),
    ("terminal-operator", "berth_utilization_output", "utilization_pct",
     "Berth congestion monitoring.", False),
    # Held by an inactive participant — proves the boundary filters on participant status.
    ("prospective-partner", "decision_summary_output", "annual_teu",
     "Due-diligence review (pending onboarding).", False),
    # Revoked — proves the boundary filters on approval status.
    ("terminal-operator", "port_fuel_cost_output", "annual_fuel_cost_usd",
     "Withdrawn: commercial fuel cost is no longer shared.", True),
]


def _tables_present(db: Session) -> bool:
    """True only when the D22 governance tables exist.

    Inspects the **session's own connection**, not the engine. `inspect(engine).has_table()`
    checks a connection out of the pool and returns it afterwards, and returning a
    connection rolls back whatever transaction it was carrying — which silently discards
    any uncommitted work the caller had already done in this session. That is not
    hypothetical: seeding the port domain and then the governance demo in one session lost
    all six datasets to exactly this.
    """
    try:
        insp = inspect(db.connection())
        return insp.has_table("participant") and insp.has_table("approved_output")
    except Exception:
        return False


def is_sqlite(db: Session) -> bool:
    try:
        return db.get_bind().dialect.name == "sqlite"
    except Exception:
        return False


def seed_governance_demo(db: Session) -> dict:
    """Idempotently ensure synthetic participants and approvals exist.

    Returns {} without writing when the database is not SQLite or the D22 tables are
    absent. Re-running is a no-op: participants are matched by key and approvals by
    (participant, dataset, field).
    """
    if not is_sqlite(db) or not _tables_present(db):
        return {}

    svc = GovernanceService(db)
    datasets = DatasetRepository(db)

    # Participants — reuse by key.
    by_key = {}
    for key, name, status, description in _PARTICIPANTS:
        try:
            participant = svc.get_participant_by_key(key)
        except ParticipantNotFoundError:
            try:
                participant = svc.register_participant(
                    participant_key=key, name=name, description=description,
                    status="active", metadata=_meta(),
                )
            except DuplicateParticipantError:  # concurrent start
                participant = svc.get_participant_by_key(key)
        by_key[key] = participant

    # An inactive participant cannot *create* an approval, so every participant is made
    # active for the approval pass and the declared statuses are applied afterwards —
    # which is also how it happens in life: access is withdrawn from a party that already
    # has approvals on file. Reactivating first is what makes a re-run idempotent, since
    # after the first run the prospective partner is already inactive.
    for participant in by_key.values():
        if participant.status != "active":
            svc.set_status(participant.id, "active")
    db.flush()

    # Existing approvals of *any* status, so a re-run neither duplicates an active
    # approval nor recreates one this seed previously revoked. `create_approval` alone is
    # not enough: its duplicate guard only looks at active approvals, so the revoked
    # demo approval would be recreated (and re-revoked) on every startup.
    existing = {
        (a.participant_id, a.dataset_id, a.field_name) for a in svc.list_approvals()
    }

    created = 0
    for key, dataset_name, field, purpose, revoke in _APPROVALS:
        participant = by_key.get(key)
        if participant is None:
            continue
        try:
            dataset = datasets.get_by_name(dataset_name)
        except NotFoundError:
            continue  # port domain not seeded yet — skip rather than fail startup
        if (participant.id, dataset.id, field) in existing:
            continue
        try:
            approval = svc.create_approval(
                participant_id=participant.id, dataset_id=dataset.id, field_name=field,
                purpose=purpose, metadata=_meta({"revoked_for_demo": revoke}),
            )
            created += 1
        except DuplicateApprovalError:
            continue  # created concurrently by another starting process
        if revoke:
            svc.revoke_approval(approval.id)

    # Now apply the declared statuses (deactivating the prospective partner).
    for key, _name, status, _description in _PARTICIPANTS:
        participant = by_key.get(key)
        if participant is not None and participant.status != status:
            svc.set_status(participant.id, status)

    db.flush()
    return {
        "participants": len(by_key),
        "approvals": created,
        "synthetic": True,
    }
