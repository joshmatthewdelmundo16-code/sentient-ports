"""baseline schema

Revision ID: 001
Revises:
Create Date: 2026-09-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("model_type", sa.String(64), nullable=False, server_default="python"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("owner", "name", name="uq_model_owner_name"),
    )

    op.create_table(
        "model_version",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("model.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("semver", sa.String(32), nullable=False),
        sa.Column("inputs_spec", sa.Text(), nullable=True),
        sa.Column("outputs_spec", sa.Text(), nullable=True),
        sa.Column("execution_entrypoint", sa.String(512), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("model_id", "semver", name="uq_version_model_semver"),
    )

    op.create_table(
        "dataset",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("current_value", sa.Text(), nullable=True),
        sa.Column("current_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "data_contract",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("schema_json", sa.Text(), nullable=False),
        sa.Column("semver", sa.String(32), nullable=False, server_default="1.0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "dependency",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("producer_version_id", sa.String(36), sa.ForeignKey("model_version.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("output_dataset_id", sa.String(36), sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("consumer_version_id", sa.String(36), sa.ForeignKey("model_version.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("input_dataset_id", sa.String(36), sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("dependency_kind", sa.String(32), nullable=False, server_default="execution"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("producer_version_id", "consumer_version_id", "input_dataset_id", name="uq_dep_edge"),
    )

    op.create_table(
        "execution_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scenario_id", sa.String(36), nullable=True),
        sa.Column("subgraph_json", sa.Text(), nullable=True),
        sa.Column("input_snapshot", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="requested", index=True),
        sa.Column("triggered_by", sa.String(128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("audit_json", sa.Text(), nullable=True),
    )

    op.create_table(
        "execution_step",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("execution_run.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("model_version_id", sa.String(36), sa.ForeignKey("model_version.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("step_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(32), nullable=False, server_default="validating"),
        sa.Column("input_snapshot", sa.Text(), nullable=True),
        sa.Column("output_fingerprint", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "result",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("execution_run.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("step_id", sa.String(36), sa.ForeignKey("execution_step.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("model_version_id", sa.String(36), nullable=True),
        sa.Column("scenario_id", sa.String(36), nullable=True),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("value_numeric", sa.Float(), nullable=True),
        sa.Column("value_hash", sa.String(64), nullable=True),
        sa.Column("input_snapshot", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("step_id", "dataset_id", name="uq_result_step_dataset"),
    )

    op.create_table(
        "lineage_edge",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("execution_run.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("step_id", sa.String(36), sa.ForeignKey("execution_step.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_result_id", sa.String(36), sa.ForeignKey("result.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_result_id", sa.String(36), sa.ForeignKey("result.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "change_event",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("execution_run.id", ondelete="SET NULL"), nullable=True),
        sa.Column("old_value_json", sa.Text(), nullable=True),
        sa.Column("old_hash", sa.String(64), nullable=True),
        sa.Column("new_value_json", sa.Text(), nullable=True),
        sa.Column("new_hash", sa.String(64), nullable=True),
        sa.Column("triggered_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, index=True),
    )


def downgrade() -> None:
    op.drop_table("change_event")
    op.drop_table("lineage_edge")
    op.drop_table("result")
    op.drop_table("execution_step")
    op.drop_table("execution_run")
    op.drop_table("dependency")
    op.drop_table("data_contract")
    op.drop_table("dataset")
    op.drop_table("model_version")
    op.drop_table("model")
