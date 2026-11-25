"""Add run_choices and the situation of each run (2026-11-25)

The resident's choice after a failure was kept only in memory; it now survives a restart. `runs` stays
append-only (L-02), so the choice has its own table. `situation_id` is added with ADD COLUMN, not a
batch copy, so the append-only triggers on `runs` stay as they are.

Revision ID: c5e81b3f9d27
Revises: a41f0c7d2b19
Create Date: 2026-11-25 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import backend.hub.db.models


# revision identifiers, used by Alembic.
revision: str = "c5e81b3f9d27"
down_revision: Union[str, Sequence[str], None] = "a41f0c7d2b19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("situation_id", sa.String(), nullable=True))
    op.add_column("open_runs", sa.Column("situation_id", sa.String(), nullable=True))
    op.create_table(
        "run_choices",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("choice", sa.String(), nullable=False),
        sa.Column("retry_run_id", sa.String(), nullable=True),
        sa.Column("chosen_at", backend.hub.db.models.UtcTime(length=24), nullable=False),
        sa.CheckConstraint("choice IN ('retry', 'leave')", name="ck_choice"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("retry_run_id"),
    )


def downgrade() -> None:
    op.drop_table("run_choices")
    with op.batch_alter_table("open_runs", schema=None) as batch_op:
        batch_op.drop_column("situation_id")
    # runs keeps the column: dropping it would copy the append-only table (L-02)
