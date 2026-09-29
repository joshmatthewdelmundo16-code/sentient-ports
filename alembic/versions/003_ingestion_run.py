"""D18 ingestion run

Adds the ingestion_run table: a durable record of every Excel ingestion attempt,
including rejected ones (which write no dataset value and produce no ChangeEvent).
content_sha256 is the authoritative content identity of the uploaded workbook bytes.

Additive only. Does not touch 001 or 002.

Revision ID: 003
Revises: 002
Create Date: 2026-09-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ingestion_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_name", sa.String(512), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("mapping_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="received"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dataset_ids_json", sa.Text(), nullable=True),
        sa.Column("change_event_id", sa.String(36),
                  sa.ForeignKey("change_event.id", ondelete="SET NULL"), nullable=True),
        sa.Column("graph_run_id", sa.String(36),
                  sa.ForeignKey("execution_run.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, index=True),
    )


def downgrade() -> None:
    op.drop_table("ingestion_run")
