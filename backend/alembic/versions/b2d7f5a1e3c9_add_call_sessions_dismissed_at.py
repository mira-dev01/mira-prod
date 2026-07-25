"""add call_sessions.dismissed_at

Revision ID: b2d7f5a1e3c9
Revises: a1c9e6f4d2b7
Create Date: 2026-07-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2d7f5a1e3c9'
down_revision: Union[str, None] = 'a1c9e6f4d2b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "call_sessions",
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("call_sessions", "dismissed_at")
