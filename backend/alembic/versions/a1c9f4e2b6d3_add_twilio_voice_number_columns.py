"""add twilio voice number columns

Revision ID: a1c9f4e2b6d3
Revises: a1c4e8f7b2d3
Create Date: 2026-08-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1c9f4e2b6d3'
down_revision: Union[str, None] = 'a1c4e8f7b2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Twilio equivalents of properties.exophone / users.lead_exophone --
    # additive, independent columns so Twilio call routing can be tested
    # without touching any Exotel column/route/pipeline code.
    op.add_column('properties', sa.Column('twilio_number', sa.String(length=32), nullable=True))
    op.create_index(op.f('ix_properties_twilio_number'), 'properties', ['twilio_number'], unique=True)

    op.add_column('users', sa.Column('twilio_lead_number', sa.String(length=32), nullable=True))
    op.create_index(op.f('ix_users_twilio_lead_number'), 'users', ['twilio_lead_number'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_twilio_lead_number'), table_name='users')
    op.drop_column('users', 'twilio_lead_number')

    op.drop_index(op.f('ix_properties_twilio_number'), table_name='properties')
    op.drop_column('properties', 'twilio_number')
