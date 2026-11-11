"""leads.custom_details: the host's own "also ask for" details

Revision ID: c5e9a2d7b4f1
Revises: b7d3a1c9e5f2
Create Date: 2026-11-10 00:00:00.000000

Additive only. Every existing lead gets an empty object, and nothing reads the column until a host
adds a custom detail on the Agent page.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c5e9a2d7b4f1'
down_revision: Union[str, None] = 'b7d3a1c9e5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'leads',
        sa.Column('custom_details', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('leads', 'custom_details')
