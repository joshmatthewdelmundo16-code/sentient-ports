"""One-time Supabase / PostgreSQL demo seed.

Seeds the port domain federation, baseline, scenario, governance and a demo user
into a PostgreSQL database (e.g. Supabase). Designed to run ONCE before the client
demo, NOT on every startup.

Usage:
    # Set the Supabase connection string (session pooler / direct, NOT transaction pooler):
    export DATABASE_URL="postgresql+psycopg://postgres.xxx:PASSWORD@HOST:5432/postgres"
    # Set the demo user password:
    export PLATFORM_NEW_USER_PASSWORD="your-demo-password-here"

    # Run migrations first:
    cd platform
    alembic upgrade head

    # Then seed:
    python scripts/seed_supabase_demo.py

The script is idempotent: re-running it skips existing records.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.app.persistence.database import (  # noqa: E402
    AppUser,
    Membership,
    Organization,
    SessionLocal,
)
from backend.app.persistence.dataset_repository import DatasetRepository  # noqa: E402
from backend.app.persistence.exceptions import NotFoundError  # noqa: E402
from backend.app.persistence.scenario_repository import (  # noqa: E402
    BaselineRepository,
    ScenarioRepository,
)
from backend.app.security.passwords import hash_password  # noqa: E402
from backend.app.security.tenancy import tenant_scope  # noqa: E402
from backend.app.services.governance import (  # noqa: E402
    DuplicateApprovalError,
    DuplicateParticipantError,
    GovernanceService,
    ParticipantNotFoundError,
)
from backend.app.services.scenarios import ScenarioService  # noqa: E402
from backend.app.ui.port_seed import BUNKER_PRICE, seed_port_domain  # noqa: E402

# ── Configuration ────────────────────────────────────────────────────────────

ORG_KEY = "demo-port"
ORG_NAME = "Illustrative Port"
ORG_KIND = "port"

DEMO_EMAIL = "demo@sentientports.com"
DEMO_DISPLAY_NAME = "Demo Operator"
DEMO_ROLE = "admin"

BASELINE_NAME = "Current operations"
SCENARIO_NAME = "Higher bunker price"
SCENARIO_BUNKER_PRICE = 750.0

_DISCLAIMER = (
    "Synthetic demo record — illustrative scenario, not a real data-sharing agreement."
)


def _meta() -> dict:
    return {"synthetic": True, "disclaimer": _DISCLAIMER, "seeded_by": "supabase-demo-seed"}


# ── Steps ────────────────────────────────────────────────────────────────────

def ensure_org(db: Session) -> Organization:
    org = db.execute(
        select(Organization).where(Organization.org_key == ORG_KEY)
    ).scalars().first()
    if org is None:
        org = Organization(
            org_key=ORG_KEY, name=ORG_NAME, kind=ORG_KIND, status="active",
        )
        db.add(org)
        db.flush()
        print(f"  Created organization: {ORG_NAME}")
    else:
        print(f"  Organization exists: {ORG_NAME}")
    return org


def ensure_user(db: Session, org: Organization) -> AppUser | None:
    password = os.environ.get("PLATFORM_NEW_USER_PASSWORD")
    if not password:
        print("  PLATFORM_NEW_USER_PASSWORD not set — skipping user creation.")
        print("  Set it and re-run, or create a user later with scripts/create_user.py")
        return None

    user = db.execute(
        select(AppUser).where(AppUser.email == DEMO_EMAIL)
    ).scalars().first()
    if user is None:
        user = AppUser(
            email=DEMO_EMAIL,
            display_name=DEMO_DISPLAY_NAME,
            password_hash=hash_password(password),
            status="active",
        )
        db.add(user)
        db.flush()
        print(f"  Created user: {DEMO_EMAIL}")
    else:
        print(f"  User exists: {DEMO_EMAIL}")

    membership = db.execute(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == org.id,
        )
    ).scalars().first()
    if membership is None:
        db.add(Membership(
            user_id=user.id,
            organization_id=org.id,
            role=DEMO_ROLE,
            is_default=True,
        ))
        print(f"  Granted {DEMO_ROLE} role in {ORG_NAME}")
    return user


def seed_port_and_decision(db: Session) -> dict:
    """Seed port domain, baseline, and scenario — adapted from port_decision_seed."""
    port = seed_port_domain(db)
    svc = ScenarioService(db)
    baselines = BaselineRepository(db)
    scenarios = ScenarioRepository(db)

    try:
        baseline = baselines.get_by_name(BASELINE_NAME)
        print(f"  Baseline exists: {BASELINE_NAME}")
    except NotFoundError:
        baseline = svc.create_baseline(
            name=BASELINE_NAME,
            description="Illustrative port operating baseline.",
            target_version_id=port["terminal_version_id"],
        )
        print(f"  Created baseline: {BASELINE_NAME}")

    if baseline.baseline_run_id is None:
        svc.execute_baseline(baseline.id, triggered_by="supabase-demo-seed")
        print("  Executed baseline run")
    else:
        print("  Baseline already has a run")

    scenario = next(
        (s for s in scenarios.list_by_baseline(baseline.id) if s.name == SCENARIO_NAME),
        None,
    )
    if scenario is None:
        scenario = svc.create_scenario(
            baseline_id=baseline.id,
            name=SCENARIO_NAME,
            description="Tests a higher in-port bunker fuel price (+25%).",
        )
        svc.set_override(
            scenario.id, port["assumptions_dataset_id"], BUNKER_PRICE, SCENARIO_BUNKER_PRICE,
        )
        print(f"  Created scenario: {SCENARIO_NAME} (bunker {SCENARIO_BUNKER_PRICE})")
    else:
        print(f"  Scenario exists: {SCENARIO_NAME}")

    if scenario.scenario_run_id is None:
        svc.execute_scenario(scenario.id, triggered_by="supabase-demo-seed")
        print("  Executed scenario run")
    else:
        print("  Scenario already has a run")

    db.flush()
    return port


def seed_governance(db: Session) -> None:
    """Seed governance participants and approvals."""
    svc = GovernanceService(db)
    datasets = DatasetRepository(db)

    participants_spec = [
        ("port-authority", "Port Authority", "active",
         "Operator of the illustrative port."),
        ("terminal-operator", "Terminal Operator", "active",
         "Container terminal operator."),
        ("environmental-regulator", "Environmental Regulator", "active",
         "Emissions reporting recipient."),
    ]

    by_key = {}
    for key, name, status, desc in participants_spec:
        try:
            p = svc.get_participant_by_key(key)
        except ParticipantNotFoundError:
            try:
                p = svc.register_participant(
                    participant_key=key, name=name, description=desc,
                    status=status, metadata=_meta(),
                )
                print(f"  Created participant: {name}")
            except DuplicateParticipantError:
                p = svc.get_participant_by_key(key)
        by_key[key] = p

    approvals_spec = [
        ("environmental-regulator", "port_emissions_output", "annual_emissions_tco2",
         "Annual in-port CO2 reporting."),
        ("environmental-regulator", "port_emissions_output", "emissions_per_teu",
         "Emission intensity benchmarking."),
        ("terminal-operator", "throughput_output", "annual_teu",
         "Capacity planning."),
        ("terminal-operator", "berth_utilization_output", "utilization_pct",
         "Berth congestion monitoring."),
    ]

    for key, ds_name, field, purpose in approvals_spec:
        participant = by_key.get(key)
        if not participant:
            continue
        try:
            ds = datasets.get_by_name(ds_name)
        except NotFoundError:
            continue
        try:
            svc.create_approval(
                participant_id=participant.id, dataset_id=ds.id, field_name=field,
                purpose=purpose, metadata=_meta(),
            )
            print(f"  Approved: {field} → {participant.name}")
        except DuplicateApprovalError:
            pass

    db.flush()


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    from backend.app.config.settings import DATABASE_URL
    print(f"\nSupabase demo seed")
    print(f"Database: {DATABASE_URL[:40]}...")
    print()

    if DATABASE_URL.startswith("sqlite"):
        print("WARNING: This is a SQLite database. Use the normal app startup for SQLite.")
        print("This script is designed for PostgreSQL / Supabase.\n")

    db = SessionLocal()
    try:
        print("[1/4] Organization")
        org = ensure_org(db)

        # Scope all data to this organization
        tenant_scope(db, org.id)

        print("\n[2/4] User")
        ensure_user(db, org)

        print("\n[3/4] Port domain + baseline + scenario")
        seed_port_and_decision(db)

        print("\n[4/4] Governance")
        seed_governance(db)

        db.commit()
        print("\n✓ Supabase demo seed complete.")
        print(f"  Login: {DEMO_EMAIL}")
        print(f"  Organization: {ORG_NAME}")
        print(f"  Baseline: {BASELINE_NAME}")
        print(f"  Scenario: {SCENARIO_NAME} (bunker $600 → $750)")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
