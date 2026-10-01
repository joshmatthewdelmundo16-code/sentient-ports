"""D27 collaboration cases, connectors, telemetry, master plans, optimization studies

Additive only.

  collaboration_case     a shared decision case hosted by one organization
  case_member            which organizations take part (host / participant / observer)
  connector_source       a configured data source: upload, CSV, REST poll, webhook,
                         telemetry, simulated feed or governed network share
  telemetry_reading      event/stream readings received from a source (idempotent per key)
  master_plan            a multi-period plan: horizon, time-dependent assumptions, investments
  plan_period            one evaluated period of a plan, pinned to the exact GraphRun
  optimization_study     decision variables, constraints, objectives and the result

  approved_output.collaboration_case_id   an approval limited to one case's members
  ingestion_run.connector_kind / connector_source_id   every connector run is an IngestionRun

New tables get organization_id (tenant) — except case_member, which by design spans
organizations and is only read through the collaboration service. Migration 008's Data-API
lockdown is applied to every new table.

Revision ID: 009
Revises: 008
Create Date: 2026-09-30
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = ("collaboration_case", "case_member", "connector_source", "telemetry_reading",
              "master_plan", "plan_period", "optimization_study")


def _lock(tables: tuple[str, ...]) -> None:
    spec = importlib.util.spec_from_file_location(
        "m008", Path(__file__).with_name("008_supabase_data_api_lockdown.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    mod.lock_tables(tables)


def _org() -> sa.Column:
    return sa.Column("organization_id", sa.String(36), sa.ForeignKey("organization.id"), nullable=True)


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def upgrade() -> None:
    op.create_table(
        "collaboration_case",
        sa.Column("id", sa.String(36), primary_key=True), _org(),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True),
        _ts("closes_at"), _ts("closed_at"), sa.Column("metadata_json", sa.Text(), nullable=True),
        _ts("created_at"), _ts("updated_at"),
    )
    op.create_table(
        "case_member",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("collaboration_case.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_organization_id", sa.String(36), sa.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="participant"),
        _ts("added_at"),
        sa.UniqueConstraint("case_id", "member_organization_id", name="uq_case_member"),
    )
    op.create_table(
        "connector_source",
        sa.Column("id", sa.String(36), primary_key=True), _org(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("target_dataset_id", sa.String(36), sa.ForeignKey("dataset.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("poll_interval_s", sa.Integer(), nullable=True),
        _ts("last_run_at"), sa.Column("last_status", sa.String(32), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True), _ts("created_at"), _ts("updated_at"),
    )
    op.create_table(
        "telemetry_reading",
        sa.Column("id", sa.String(36), primary_key=True), _org(),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("connector_source.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric", sa.String(128), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        _ts("observed_at"), _ts("received_at"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("source_id", "idempotency_key", name="uq_telemetry_source_key"),
    )
    op.create_table(
        "master_plan",
        sa.Column("id", sa.String(36), primary_key=True), _org(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("parent_plan_id", sa.String(36), sa.ForeignKey("master_plan.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_version_id", sa.String(36), sa.ForeignKey("model_version.id", ondelete="SET NULL"), nullable=True),
        sa.Column("inputs_dataset_id", sa.String(36), sa.ForeignKey("dataset.id", ondelete="SET NULL"), nullable=True),
        sa.Column("spec_json", sa.Text(), nullable=False),
        _ts("evaluated_at"), _ts("created_at"), _ts("updated_at"),
    )
    op.create_table(
        "plan_period",
        sa.Column("id", sa.String(36), primary_key=True), _org(),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("master_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("execution_run.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("inputs_json", sa.Text(), nullable=True),
        sa.Column("outputs_json", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("plan_id", "year", name="uq_plan_period_year"),
    )
    op.create_table(
        "optimization_study",
        sa.Column("id", sa.String(36), primary_key=True), _org(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("base_plan_id", sa.String(36), sa.ForeignKey("master_plan.id", ondelete="SET NULL"), nullable=True),
        sa.Column("spec_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("verification_plan_id", sa.String(36), sa.ForeignKey("master_plan.id", ondelete="SET NULL"), nullable=True),
        _ts("evaluated_at"), _ts("created_at"), _ts("updated_at"),
    )
    for t in NEW_TABLES:
        if t != "case_member":
            op.create_index(f"ix_{t}_organization_id", t, ["organization_id"])
    op.create_index("ix_case_member_case_id", "case_member", ["case_id"])
    op.create_index("ix_case_member_org", "case_member", ["member_organization_id"])
    op.create_index("ix_telemetry_reading_source", "telemetry_reading", ["source_id", "observed_at"])
    op.create_index("ix_plan_period_plan", "plan_period", ["plan_id"])

    with op.batch_alter_table("approved_output") as batch:
        batch.add_column(sa.Column("collaboration_case_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_approved_output_case", "collaboration_case",
                                 ["collaboration_case_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_approved_output_case", "approved_output", ["collaboration_case_id"])
    # One ACTIVE approval per exact sharing target, so one field can be shared with a hub and,
    # separately, into a case. (005's index is replaced here; 005 itself is not edited.)
    op.drop_index("uq_approved_output_active", table_name="approved_output")
    op.create_index(
        "uq_approved_output_active", "approved_output",
        ["participant_id", "dataset_id", "field_name",
         sa.text("coalesce(audience_organization_id, '')"),
         sa.text("coalesce(collaboration_case_id, '')"),
         sa.text("coalesce(source_run_id, '')")],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    with op.batch_alter_table("ingestion_run") as batch:
        batch.add_column(sa.Column("connector_kind", sa.String(32), nullable=True))
        batch.add_column(sa.Column("connector_source_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_ingestion_run_source", "connector_source",
                                 ["connector_source_id"], ["id"], ondelete="SET NULL")
    _lock(NEW_TABLES)


def downgrade() -> None:
    with op.batch_alter_table("ingestion_run") as batch:
        batch.drop_constraint("fk_ingestion_run_source", type_="foreignkey")
        batch.drop_column("connector_source_id")
        batch.drop_column("connector_kind")
    op.drop_index("uq_approved_output_active", table_name="approved_output")
    op.create_index("uq_approved_output_active", "approved_output",
                    ["participant_id", "dataset_id", "field_name"], unique=True,
                    sqlite_where=sa.text("status = 'active'"),
                    postgresql_where=sa.text("status = 'active'"))
    op.drop_index("ix_approved_output_case", table_name="approved_output")
    with op.batch_alter_table("approved_output") as batch:
        batch.drop_constraint("fk_approved_output_case", type_="foreignkey")
        batch.drop_column("collaboration_case_id")
    for t in reversed(NEW_TABLES):
        op.drop_table(t)
