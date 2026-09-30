"""D25 product metadata

Additive only. Two nullable presentation columns so the product can show business-readable
names without hard-coding them in the UI:

  - dataset.display_name   human label for a dataset ("Port assumptions"); NULL → the UI
                           derives one from dataset.name
  - model.card_json        model-library card (purpose, formula, domain, provider, units,
                           provenance, calibration status…) as JSON; NULL → no card

Revisions 001–005 are not touched. No existing data is rewritten.

Revision ID: 006
Revises: 005
Create Date: 2026-09-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dataset") as batch:
        batch.add_column(sa.Column("display_name", sa.String(255), nullable=True))
    with op.batch_alter_table("model") as batch:
        batch.add_column(sa.Column("card_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model") as batch:
        batch.drop_column("card_json")
    with op.batch_alter_table("dataset") as batch:
        batch.drop_column("display_name")
