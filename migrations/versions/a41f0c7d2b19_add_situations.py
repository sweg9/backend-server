"""Add situations: one trigger and what the hub planned and did for it (2026-11-24)

Revision ID: a41f0c7d2b19
Revises: 7b2d9e41c0a5
Create Date: 2026-11-24 22:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import backend.hub.db.models


# revision identifiers, used by Alembic.
revision: str = "a41f0c7d2b19"
down_revision: Union[str, Sequence[str], None] = "7b2d9e41c0a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUSES = "'received', 'planning', 'waiting_for_answer', 'running', 'done', 'needs_attention', 'suggested', 'dismissed', 'cancelled'"


def upgrade() -> None:
    op.create_table(
        "situations",
        sa.Column("situation_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("received_at", backend.hub.db.models.UtcTime(length=24), nullable=False),
        sa.Column("updated_at", backend.hub.db.models.UtcTime(length=24), nullable=False),
        sa.CheckConstraint("trigger IN ('sensor', 'time', 'routine')", name="ck_trigger"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_status"),
        sa.PrimaryKeyConstraint("situation_id"),
    )
    with op.batch_alter_table("situations", schema=None) as batch_op:
        batch_op.create_index("ix_situations_received_at", ["received_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("situations", schema=None) as batch_op:
        batch_op.drop_index("ix_situations_received_at")
    op.drop_table("situations")
