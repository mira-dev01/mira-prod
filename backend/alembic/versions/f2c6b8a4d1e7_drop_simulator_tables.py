"""drop the payment/WhatsApp simulator tables

Revision ID: f2c6b8a4d1e7
Revises: e8b3d5f1a2c4
Create Date: 2026-11-11 00:00:00.000000

The simulator (a pretend payment provider and a WhatsApp log) is gone: payments and WhatsApp now only ever
talk to the real Razorpay and Twilio. Its three tables held test data only and nothing reads them any more.
properties.pricing_terms and payment_requests.details (added by d7a2c4f8b1e3 next to them) are real booking
data and stay.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2c6b8a4d1e7'
down_revision: Union[str, None] = 'e8b3d5f1a2c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('sim_messages')
    op.drop_table('simulator_flags')
    op.drop_table('sim_payment_links')


def downgrade() -> None:
    # Same shape d7a2c4f8b1e3 created, so a rollback leaves the schema as it was (the tables come back empty).
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
