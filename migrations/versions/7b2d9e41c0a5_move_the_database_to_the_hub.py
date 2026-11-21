"""Move the database to the hub (decision of 2026-11-20)

- hub_messages goes: the hub keeps the records and nothing is resent, so no ids need remembering.
- controls.fingerprint may be null until the enclave computes fingerprints.
- open_runs.phase loses `sent`: the hub runs its own runs.

Revision ID: 7b2d9e41c0a5
Revises: 13e4a5c322a3
Create Date: 2026-11-21 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7b2d9e41c0a5"
down_revision: Union[str, Sequence[str], None] = "13e4a5c322a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("hub_messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_hub_messages_received_at"))
    op.drop_table("hub_messages")

    with op.batch_alter_table("controls", schema=None) as batch_op:
        batch_op.alter_column("fingerprint", existing_type=sa.LargeBinary(), nullable=True)

    op.execute("UPDATE open_runs SET phase = 'running' WHERE phase = 'sent'")
    with op.batch_alter_table("open_runs", schema=None) as batch_op:
        batch_op.drop_constraint("ck_phase", type_="check")
        batch_op.create_check_constraint("ck_phase", "phase IN ('waiting_answer', 'running')")


def downgrade() -> None:
    with op.batch_alter_table("open_runs", schema=None) as batch_op:
        batch_op.drop_constraint("ck_phase", type_="check")
        batch_op.create_check_constraint("ck_phase", "phase IN ('waiting_answer', 'sent', 'running')")

    with op.batch_alter_table("controls", schema=None) as batch_op:
        batch_op.alter_column("fingerprint", existing_type=sa.LargeBinary(), nullable=False)

    op.create_table(
        "hub_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("received_at", sa.String(length=24), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("hub_messages", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_hub_messages_received_at"), ["received_at"], unique=False)
