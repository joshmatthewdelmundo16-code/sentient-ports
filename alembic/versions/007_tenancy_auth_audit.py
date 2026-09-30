"""D26 tenancy, authentication, audit and governed sharing

Additive except for three uniqueness changes that multi-tenancy requires (each is a
widening, so no existing row can violate the new constraint):

  New tables
    organization     tenant / zone: port private zone, regional hub, national hub, network
    app_user         a person who signs in (scrypt password hash; no plaintext anywhere)
    membership       user ↔ organization with a role (viewer < analyst < approver < admin)
    user_session     server-side session: SHA-256 of the cookie token, CSRF token, expiry
    audit_event      append-only security and governance trail (incl. denials)

  New nullable column organization_id (FK organization) on every domain table, so every
  record belongs to exactly one organization and the server can enforce isolation.

  approved_output gains: audience_organization_id (who it is shared WITH; NULL = whole
  network, the D22 meaning), source_run_id (explicitly share one run's result — never
  implicit), approved_by_user_id, revoked_by_user_id, revocation_reason.

  Uniqueness (widened to be per organization):
    dataset.name            → (organization_id, name)
    baseline.name           → (organization_id, name)
    model (owner, name)     → (organization_id, owner, name)

  Backfill: if any domain row exists, one organization "default" is created and every
  existing row is assigned to it — nothing becomes invisible after the upgrade.

Revisions 001–006 are not touched.

Revision ID: 007
Revises: 006
Create Date: 2026-09-30
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = (
    "model", "model_version", "model_io_binding", "dataset", "data_contract", "dependency",
    "execution_run", "execution_step", "result", "lineage_edge", "change_event",
    "ingestion_run", "baseline", "scenario", "scenario_override", "participant",
    "approved_output",
)

_NAMING = {
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _drop_unique_on(table: str, columns: list[str], known_name: str | None) -> None:
    """Drop the unique constraint on exactly `columns`, whatever it is called.

    001 created dataset.name with an unnamed UNIQUE. PostgreSQL named it (normally
    dataset_name_key); SQLite left it anonymous. Reflection finds the real name on
    PostgreSQL; on SQLite, batch mode's naming convention gives the anonymous one a name
    it can be dropped by.
    """
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        name = known_name or f"uq_{table}_{columns[0]}"
        with op.batch_alter_table(table, naming_convention=_NAMING) as batch:
            batch.drop_constraint(name, type_="unique")
        return
    insp = sa.inspect(bind)
    for uc in insp.get_unique_constraints(table):
        if list(uc["column_names"]) == columns:
            op.drop_constraint(uc["name"], table, type_="unique")
    for ix in insp.get_indexes(table):
        if ix.get("unique") and list(ix["column_names"]) == columns:
            op.drop_index(ix["name"], table_name=table)


def upgrade() -> None:
    op.create_table(
        "organization",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="port"),
        sa.Column("parent_id", sa.String(36),
                  sa.ForeignKey("organization.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("org_key", name="uq_organization_key"),
    )
    op.create_index("ix_organization_parent_id", "organization", ["parent_id"])

    op.create_table(
        "app_user",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_app_user_email"),
    )

    op.create_table(
        "membership",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("organization_id", sa.String(36),
                  sa.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="viewer"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
    )
    op.create_index("ix_membership_user_id", "membership", ["user_id"])
    op.create_index("ix_membership_organization_id", "membership", ["organization_id"])

    op.create_table(
        "user_session",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("csrf_token", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_user_session_token_hash"),
    )
    op.create_index("ix_user_session_user_id", "user_session", ["user_id"])

    op.create_table(
        "audit_event",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("organization_id", sa.String(36),
                  sa.ForeignKey("organization.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_user_id", sa.String(36),
                  sa.ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_label", sa.String(320), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("detail_json", sa.Text(), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_audit_event_organization_id", "audit_event", ["organization_id"])
    op.create_index("ix_audit_event_occurred_at", "audit_event", ["occurred_at"])
    op.create_index("ix_audit_event_action", "audit_event", ["action"])

    # organization_id on every domain table.
    for table in TENANT_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("organization_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                f"fk_{table}_organization", "organization", ["organization_id"], ["id"],
            )
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])

    with op.batch_alter_table("approved_output") as batch:
        batch.add_column(sa.Column("audience_organization_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("source_run_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("approved_by_user_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("revoked_by_user_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("revocation_reason", sa.Text(), nullable=True))
        batch.create_foreign_key("fk_approved_output_audience", "organization",
                                 ["audience_organization_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_approved_output_source_run", "execution_run",
                                 ["source_run_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_approved_output_approved_by", "app_user",
                                 ["approved_by_user_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_approved_output_revoked_by", "app_user",
                                 ["revoked_by_user_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_approved_output_audience", "approved_output", ["audience_organization_id"])

    # Uniqueness widened to be per organization. Expression indexes over
    # coalesce(organization_id, '') so unowned rows keep their old guarantee (a composite
    # UNIQUE would treat every NULL organization as distinct).
    _drop_unique_on("dataset", ["name"], None)
    with op.batch_alter_table("baseline") as batch:
        batch.drop_constraint("uq_baseline_name", type_="unique")
    with op.batch_alter_table("model") as batch:
        batch.drop_constraint("uq_model_owner_name", type_="unique")
    org = sa.text("coalesce(organization_id, '')")
    op.create_index("uq_dataset_org_name", "dataset", [org, "name"], unique=True)
    op.create_index("uq_baseline_org_name", "baseline", [org, "name"], unique=True)
    op.create_index("uq_model_org_owner_name", "model", [org, "owner", "name"], unique=True)

    # Backfill: existing rows join one default organization so nothing becomes invisible.
    bind = op.get_bind()
    has_rows = any(
        bind.execute(sa.text(f"SELECT 1 FROM {t} LIMIT 1")).first() is not None
        for t in TENANT_TABLES
    )
    if has_rows:
        org_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        bind.execute(
            sa.text(
                "INSERT INTO organization (id, org_key, name, kind, status, metadata_json, "
                "created_at, updated_at) VALUES (:id, 'default', 'Default organization', "
                "'port', 'active', :meta, :now, :now)"
            ),
            {"id": org_id, "now": now,
             "meta": '{"created_by": "migration 007", "reason": "backfill of pre-tenancy rows"}'},
        )
        for t in TENANT_TABLES:
            bind.execute(sa.text(f"UPDATE {t} SET organization_id = :id WHERE organization_id IS NULL"),
                         {"id": org_id})


def downgrade() -> None:
    # Once two organizations own same-named records, global uniqueness cannot be restored
    # without deleting someone's data. Refuse with an explanation instead of failing halfway
    # through a table rebuild; roll back by restoring a pre-upgrade backup in that case.
    bind = op.get_bind()
    for table, cols in (("dataset", "name"), ("baseline", "name"), ("model", "owner, name")):
        dup = bind.execute(sa.text(
            f"SELECT COUNT(*) FROM (SELECT {cols} FROM {table} GROUP BY {cols} HAVING COUNT(*) > 1) d"
        )).scalar()
        if dup:
            raise RuntimeError(
                f"Cannot downgrade 007: {dup} {table} name(s) are now used by more than one "
                "organization, so they cannot be made globally unique again. Restore the "
                "pre-upgrade database backup instead (see DEPLOYMENT.md, 'Rollback').")

    op.drop_index("uq_model_org_owner_name", table_name="model")
    op.drop_index("uq_baseline_org_name", table_name="baseline")
    op.drop_index("uq_dataset_org_name", table_name="dataset")
    with op.batch_alter_table("model") as batch:
        batch.create_unique_constraint("uq_model_owner_name", ["owner", "name"])
    with op.batch_alter_table("baseline") as batch:
        batch.create_unique_constraint("uq_baseline_name", ["name"])
    with op.batch_alter_table("dataset") as batch:
        batch.create_unique_constraint("uq_dataset_name", ["name"])

    op.drop_index("ix_approved_output_audience", table_name="approved_output")
    with op.batch_alter_table("approved_output") as batch:
        for fk in ("fk_approved_output_revoked_by", "fk_approved_output_approved_by",
                   "fk_approved_output_source_run", "fk_approved_output_audience"):
            batch.drop_constraint(fk, type_="foreignkey")
        for col in ("revocation_reason", "revoked_by_user_id", "approved_by_user_id",
                    "source_run_id", "audience_organization_id"):
            batch.drop_column(col)

    for table in reversed(TENANT_TABLES):
        op.drop_index(f"ix_{table}_organization_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_organization", type_="foreignkey")
            batch.drop_column("organization_id")

    op.drop_index("ix_audit_event_action", table_name="audit_event")
    op.drop_index("ix_audit_event_occurred_at", table_name="audit_event")
    op.drop_index("ix_audit_event_organization_id", table_name="audit_event")
    op.drop_table("audit_event")
    op.drop_index("ix_user_session_user_id", table_name="user_session")
    op.drop_table("user_session")
    op.drop_index("ix_membership_organization_id", table_name="membership")
    op.drop_index("ix_membership_user_id", table_name="membership")
    op.drop_table("membership")
    op.drop_table("app_user")
    op.drop_index("ix_organization_parent_id", table_name="organization")
    op.drop_table("organization")
