"""D26 lock the platform tables away from Supabase's auto-generated Data API

Why
---
Supabase publishes every table in the `public` schema through PostgREST
(https://<project>.supabase.co/rest/v1/<table>). Its `anon` key is public by design (it is
meant to ship inside browser apps), so a table without Row Level Security is readable — and
often writable — by anyone who has that key, completely bypassing this application's
authentication, organization isolation and governance.

This platform never uses the Data API: the browser talks only to FastAPI, and FastAPI talks
to PostgreSQL directly. So the safe posture is:

  1. ENABLE ROW LEVEL SECURITY on every platform table, with NO policies. PostgREST's roles
     (anon, authenticated) then see nothing. The application connects as the table owner
     (Supabase's `postgres`), and owners bypass RLS, so the application is unaffected.
  2. REVOKE ALL on those tables from `anon` and `authenticated`, when those roles exist.

PostgreSQL only; a no-op on SQLite. Harmless on a non-Supabase PostgreSQL (the REVOKE is
skipped when the roles do not exist). If you ever run the application as a NON-owner role,
give that role BYPASSRLS or add explicit policies.

Revision ID: 008
Revises: 007
Create Date: 2026-09-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLATFORM_TABLES = (
    "model", "model_version", "model_io_binding", "dataset", "data_contract", "dependency",
    "execution_run", "execution_step", "result", "lineage_edge", "change_event",
    "ingestion_run", "baseline", "scenario", "scenario_override", "participant",
    "approved_output", "organization", "app_user", "membership", "user_session",
    "audit_event", "alembic_version",
)


def lock_tables(tables: tuple[str, ...]) -> None:
    """Enable RLS and revoke Data-API roles on `tables` (PostgreSQL only). Reused by later
    migrations for the tables they create."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    roles = set(bind.execute(sa.text(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")).scalars())
    for table in tables:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        for role in sorted(roles):
            op.execute(f'REVOKE ALL ON TABLE "{table}" FROM {role}')


def upgrade() -> None:
    lock_tables(PLATFORM_TABLES)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Privileges are not re-granted on purpose: restoring Data-API access is a deliberate
    # decision for an operator, not something a rollback should do silently.
    for table in PLATFORM_TABLES:
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
