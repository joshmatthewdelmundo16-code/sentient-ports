"""D22 participants & approved outputs

Additive-only governance layer. Creates two new tables and adds two nullable owner
foreign keys to existing tables. Does NOT touch or rewrite revisions 001–004, and performs
no destructive change to existing data (all additions are nullable / new tables).

  - participant          federation actor: unique participant_key, name, status, metadata
  - approved_output      participant's approval of one dataset field for exposure, with a
                         partial unique index guaranteeing one ACTIVE approval per
                         (participant, dataset, field) on both SQLite and PostgreSQL
  - dataset.owner_participant_id         nullable FK → participant (SET NULL)
  - model_version.owner_participant_id   nullable FK → participant (SET NULL)

Revision ID: 005
Revises: 004
Create Date: 2026-09-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "participant",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("participant_key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("participant_key", name="uq_participant_key"),
    )
    op.create_index("ix_participant_status", "participant", ["status"])

    op.create_table(
        "approved_output",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("participant_id", sa.String(36),
                  sa.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", sa.String(36),
                  sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_name", sa.String(255), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_approved_output_participant_id", "approved_output", ["participant_id"])
    op.create_index("ix_approved_output_dataset_id", "approved_output", ["dataset_id"])
    op.create_index("ix_approved_output_status", "approved_output", ["status"])
    # Partial unique index: one ACTIVE approval per (participant, dataset, field).
    op.create_index(
        "uq_approved_output_active",
        "approved_output",
        ["participant_id", "dataset_id", "field_name"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )

    # Additive nullable owner references. Batch mode so SQLite can add the FK column.
    with op.batch_alter_table("model_version") as batch:
        batch.add_column(sa.Column("owner_participant_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_model_version_owner_participant", "participant",
            ["owner_participant_id"], ["id"], ondelete="SET NULL",
        )
    op.create_index("ix_model_version_owner_participant_id", "model_version",
                    ["owner_participant_id"])

    with op.batch_alter_table("dataset") as batch:
        batch.add_column(sa.Column("owner_participant_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_dataset_owner_participant", "participant",
            ["owner_participant_id"], ["id"], ondelete="SET NULL",
        )
    op.create_index("ix_dataset_owner_participant_id", "dataset", ["owner_participant_id"])


def downgrade() -> None:
    op.drop_index("ix_dataset_owner_participant_id", table_name="dataset")
    with op.batch_alter_table("dataset") as batch:
        batch.drop_constraint("fk_dataset_owner_participant", type_="foreignkey")
        batch.drop_column("owner_participant_id")

    op.drop_index("ix_model_version_owner_participant_id", table_name="model_version")
    with op.batch_alter_table("model_version") as batch:
        batch.drop_constraint("fk_model_version_owner_participant", type_="foreignkey")
        batch.drop_column("owner_participant_id")

    op.drop_index("uq_approved_output_active", table_name="approved_output")
    op.drop_index("ix_approved_output_status", table_name="approved_output")
    op.drop_index("ix_approved_output_dataset_id", table_name="approved_output")
    op.drop_index("ix_approved_output_participant_id", table_name="approved_output")
    op.drop_table("approved_output")

    op.drop_index("ix_participant_status", table_name="participant")
    op.drop_table("participant")
