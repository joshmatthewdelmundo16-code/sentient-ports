"""Create an organization and/or a user with a role — D26.

A shared or production database never gets demo accounts, so the first administrator is
created here, by an operator, against DATABASE_URL (run it where the app's secrets live —
e.g. `fly ssh console`, `render shell`, or locally with the variable exported).

The password is read from a hidden prompt, or from the PLATFORM_NEW_USER_PASSWORD
environment variable for non-interactive use. It is never accepted as a command-line
argument (arguments end up in shell history and process listings) and never printed.

    python scripts/create_user.py --org-key port-rotterdam --org-name "Port of example" \\
        --org-kind port --email admin@example.org --name "Platform admin" --role admin

    python scripts/create_user.py --org-key port-rotterdam --email analyst@example.org \\
        --name "Analyst" --role analyst
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from backend.app.persistence.database import (  # noqa: E402
    AppUser,
    Membership,
    Organization,
    SessionLocal,
)
from backend.app.security.passwords import check_password_policy, hash_password  # noqa: E402

KINDS = ("port", "regional_hub", "national_hub", "network", "sandbox")
ROLES = ("viewer", "analyst", "approver", "admin")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org-key", required=True)
    ap.add_argument("--org-name", help="Required when the organization does not exist yet")
    ap.add_argument("--org-kind", choices=KINDS, default="port")
    ap.add_argument("--parent-key", help="Parent organization key (hub hierarchy)")
    ap.add_argument("--email", help="Omit to only create the organization")
    ap.add_argument("--name", help="Display name for a new user")
    ap.add_argument("--role", choices=ROLES, default="viewer")
    opts = ap.parse_args()

    db = SessionLocal()
    try:
        org = db.execute(select(Organization).where(Organization.org_key == opts.org_key)).scalars().first()
        if org is None:
            if not opts.org_name:
                sys.exit(f"Organization {opts.org_key!r} does not exist; pass --org-name to create it.")
            parent_id = None
            if opts.parent_key:
                parent = db.execute(select(Organization).where(Organization.org_key == opts.parent_key)).scalars().first()
                if parent is None:
                    sys.exit(f"Parent organization {opts.parent_key!r} not found.")
                parent_id = parent.id
            org = Organization(org_key=opts.org_key, name=opts.org_name, kind=opts.org_kind,
                               parent_id=parent_id, status="active")
            db.add(org)
            db.flush()
            print(f"Created organization {org.name} ({org.kind}).")

        if opts.email:
            email = opts.email.strip().lower()
            user = db.execute(select(AppUser).where(AppUser.email == email)).scalars().first()
            if user is None:
                password = os.environ.get("PLATFORM_NEW_USER_PASSWORD") or getpass.getpass("New password: ")
                reason = check_password_policy(password)
                if reason:
                    sys.exit(reason)
                if "PLATFORM_NEW_USER_PASSWORD" not in os.environ and getpass.getpass("Repeat password: ") != password:
                    sys.exit("The passwords do not match.")
                user = AppUser(email=email, display_name=opts.name or email,
                               password_hash=hash_password(password), status="active")
                db.add(user)
                db.flush()
                print(f"Created user {email}.")
            m = db.execute(select(Membership).where(Membership.user_id == user.id,
                                                    Membership.organization_id == org.id)).scalars().first()
            if m is None:
                has_default = db.execute(select(Membership).where(
                    Membership.user_id == user.id, Membership.is_default.is_(True))).scalars().first()
                db.add(Membership(user_id=user.id, organization_id=org.id, role=opts.role,
                                  is_default=has_default is None))
            else:
                m.role = opts.role
            print(f"{email} is {opts.role} in {org.name}.")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
