"""booking bill and test services: properties.pricing_terms, payment_requests.details, simulator tables

Revision ID: d7a2c4f8b1e3
Revises: c5e9a2d7b4f1
Create Date: 2026-11-10 00:00:00.000000

Additive only. Every existing property keeps pricing_terms NULL (the flat base_price quote) and every
existing payment request keeps details NULL. The simulator tables stay empty unless SIMULATOR_ENABLED is on.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd7a2c4f8b1e3'
down_revision: Union[str, None] = 'c5e9a2d7b4f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('pricing_terms', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('payment_requests', sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table(
        'sim_payment_links',
        sa.Column('id', sa.String(length=48), primary_key=True),
        sa.Column('amount_paise', sa.Integer(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='created', nullable=False),
        sa.Column('last_attempt', sa.String(length=16), nullable=True),
        sa.Column('payment_id', sa.String(length=48), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_table(
        'simulator_flags',
        sa.Column('key', sa.String(length=32), primary_key=True),
        sa.Column('enabled', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_table(
        'sim_messages',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('to_phone', sa.String(length=32), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('kind', sa.String(length=12), server_default='text', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('sim_messages')
    op.drop_table('simulator_flags')
    op.drop_table('sim_payment_links')
    op.drop_column('payment_requests', 'details')
    op.drop_column('properties', 'pricing_terms')
