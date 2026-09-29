"""D16 federation integrity

- model_io_binding: explicit model-version ↔ dataset input/output declarations
- model_version.adapter_type / adapter_config: persisted execution behaviour
- execution_run becomes the GraphRun: run_kind, executor, target_version_id,
  trigger_type, error_message (pre-D16 rows are marked run_kind='legacy')
- change_event provenance: source_type, source_ref, source_version_id,
  produced_by_run_id, result_id
- lineage_edge dataset/change-event provenance: source_dataset_id, target_dataset_id,
  source_change_event_id

Revision ID: 002
Revises: 001
Create Date: 2026-09-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_io_binding",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_version_id", sa.String(36),
                  sa.ForeignKey("model_version.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", sa.String(36),
                  sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("field_map", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("model_version_id", "dataset_id", "direction", name="uq_io_binding"),
    )
    op.create_index("ix_model_io_binding_model_version_id", "model_io_binding", ["model_version_id"])
    op.create_index("ix_model_io_binding_dataset_id", "model_io_binding", ["dataset_id"])

    with op.batch_alter_table("model_version") as batch:
        batch.add_column(sa.Column("adapter_type", sa.String(64), nullable=True))
        batch.add_column(sa.Column("adapter_config", sa.Text(), nullable=True))

    with op.batch_alter_table("execution_run") as batch:
        batch.add_column(sa.Column("run_kind", sa.String(16), nullable=False, server_default="graph"))
        batch.add_column(sa.Column("executor", sa.String(32), nullable=False, server_default="in_process"))
        batch.add_column(sa.Column("target_version_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("trigger_type", sa.String(32), nullable=True))
        batch.add_column(sa.Column("error_message", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_execution_run_target_version", "model_version",
            ["target_version_id"], ["id"], ondelete="SET NULL",
        )
    # Every run that existed before D16 was a one-model run, not a GraphRun.
    op.execute("UPDATE execution_run SET run_kind = 'legacy'")

    with op.batch_alter_table("change_event") as batch:
        batch.add_column(sa.Column("source_type", sa.String(32), nullable=True))
        batch.add_column(sa.Column("source_ref", sa.Text(), nullable=True))
        batch.add_column(sa.Column("source_version_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("produced_by_run_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("result_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_change_event_source_version", "model_version",
            ["source_version_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_change_event_produced_by_run", "execution_run",
            ["produced_by_run_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_change_event_result", "result", ["result_id"], ["id"], ondelete="SET NULL",
        )

    with op.batch_alter_table("lineage_edge") as batch:
        batch.add_column(sa.Column("source_dataset_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("target_dataset_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("source_change_event_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_lineage_source_dataset", "dataset",
            ["source_dataset_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_lineage_target_dataset", "dataset",
            ["target_dataset_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_lineage_source_change_event", "change_event",
            ["source_change_event_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("lineage_edge") as batch:
        batch.drop_constraint("fk_lineage_source_change_event", type_="foreignkey")
        batch.drop_constraint("fk_lineage_target_dataset", type_="foreignkey")
        batch.drop_constraint("fk_lineage_source_dataset", type_="foreignkey")
        batch.drop_column("source_change_event_id")
        batch.drop_column("target_dataset_id")
        batch.drop_column("source_dataset_id")

    with op.batch_alter_table("change_event") as batch:
        batch.drop_constraint("fk_change_event_result", type_="foreignkey")
        batch.drop_constraint("fk_change_event_produced_by_run", type_="foreignkey")
        batch.drop_constraint("fk_change_event_source_version", type_="foreignkey")
        batch.drop_column("result_id")
        batch.drop_column("produced_by_run_id")
        batch.drop_column("source_version_id")
        batch.drop_column("source_ref")
        batch.drop_column("source_type")

    with op.batch_alter_table("execution_run") as batch:
        batch.drop_constraint("fk_execution_run_target_version", type_="foreignkey")
        batch.drop_column("error_message")
        batch.drop_column("trigger_type")
        batch.drop_column("target_version_id")
        batch.drop_column("executor")
        batch.drop_column("run_kind")

    with op.batch_alter_table("model_version") as batch:
        batch.drop_column("adapter_config")
        batch.drop_column("adapter_type")

    op.drop_index("ix_model_io_binding_dataset_id", table_name="model_io_binding")
    op.drop_index("ix_model_io_binding_model_version_id", table_name="model_io_binding")
    op.drop_table("model_io_binding")
