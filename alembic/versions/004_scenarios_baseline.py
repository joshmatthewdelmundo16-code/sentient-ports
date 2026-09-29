"""D19 scenarios & baseline

Adds three additive tables for the scenario/baseline domain:
  - baseline          named authoritative input-state identity + its exact GraphRun
  - scenario          named scenario derived from a baseline + its exact GraphRun
  - scenario_override  explicit, typed dataset-field overrides (generic, JSON value)

Scenario/baseline runs continue to use the existing execution_run.scenario_id and
result.scenario_id columns (from revision 001) for run/result association — no ALTER is
performed on any existing table. Additive only. Does not touch 001, 002 or 003.

Revision ID: 004
Revises: 003
Create Date: 2026-09-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "baseline",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("target_version_id", sa.String(36),
                  sa.ForeignKey("model_version.id", ondelete="SET NULL"), nullable=True),
        sa.Column("baseline_run_id", sa.String(36),
                  sa.ForeignKey("execution_run.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("name", name="uq_baseline_name"),
    )

    op.create_table(
        "scenario",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("baseline_id", sa.String(36),
                  sa.ForeignKey("baseline.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("target_version_id", sa.String(36),
                  sa.ForeignKey("model_version.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scenario_run_id", sa.String(36),
                  sa.ForeignKey("execution_run.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("baseline_id", "name", name="uq_scenario_baseline_name"),
    )

    op.create_table(
        "scenario_override",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scenario_id", sa.String(36),
                  sa.ForeignKey("scenario.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("dataset_id", sa.String(36),
                  sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("field_name", sa.String(255), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("scenario_id", "dataset_id", "field_name",
                            name="uq_scenario_override"),
    )


def downgrade() -> None:
    op.drop_table("scenario_override")
    op.drop_table("scenario")
    op.drop_table("baseline")
