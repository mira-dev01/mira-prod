"""add guest memory fields to guest_profiles and lead.guest_profile_id

Revision ID: d4f7a91c3e5b
Revises: c8e1f4a02b7d
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd4f7a91c3e5b'
down_revision: Union[str, None] = 'c8e1f4a02b7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # phone alone was globally unique -- replace with (phone, host_id) so
    # the same phone number calling two different hosts on Mira gets two
    # independent profiles instead of colliding.
    op.drop_index(op.f('ix_guest_profiles_phone'), table_name='guest_profiles')

    op.add_column('guest_profiles', sa.Column('host_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('guest_profiles', sa.Column('last_property_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('guest_profiles', sa.Column('preferred_language', sa.String(length=32), nullable=True))
    op.add_column('guest_profiles', sa.Column('last_outcome', sa.String(length=64), nullable=True))
    op.add_column('guest_profiles', sa.Column('last_follow_up', sa.String(length=255), nullable=True))
    op.add_column('guest_profiles', sa.Column('last_call_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'guest_profiles',
        sa.Column('conversation_summaries', postgresql.JSONB(), nullable=False, server_default='[]'),
    )

    op.create_index('ix_guest_profiles_phone', 'guest_profiles', ['phone'], unique=False)
    op.create_index('ix_guest_profiles_host_id', 'guest_profiles', ['host_id'], unique=False)
    op.create_unique_constraint('uq_guest_profiles_phone_host', 'guest_profiles', ['phone', 'host_id'])
    op.create_foreign_key(
        'guest_profiles_host_id_fkey', 'guest_profiles', 'users', ['host_id'], ['id'], ondelete='CASCADE'
    )
    op.create_foreign_key(
        'guest_profiles_last_property_id_fkey',
        'guest_profiles',
        'properties',
        ['last_property_id'],
        ['id'],
        ondelete='SET NULL',
    )

    op.add_column('leads', sa.Column('guest_profile_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index('ix_leads_guest_profile_id', 'leads', ['guest_profile_id'], unique=False)
    op.create_foreign_key(
        'leads_guest_profile_id_fkey', 'leads', 'guest_profiles', ['guest_profile_id'], ['id'], ondelete='SET NULL'
    )


def downgrade() -> None:
    op.drop_constraint('leads_guest_profile_id_fkey', 'leads', type_='foreignkey')
    op.drop_index('ix_leads_guest_profile_id', table_name='leads')
    op.drop_column('leads', 'guest_profile_id')

    op.drop_constraint('guest_profiles_last_property_id_fkey', 'guest_profiles', type_='foreignkey')
    op.drop_constraint('guest_profiles_host_id_fkey', 'guest_profiles', type_='foreignkey')
    op.drop_constraint('uq_guest_profiles_phone_host', 'guest_profiles', type_='unique')
    op.drop_index('ix_guest_profiles_host_id', table_name='guest_profiles')
    op.drop_index('ix_guest_profiles_phone', table_name='guest_profiles')

    op.drop_column('guest_profiles', 'conversation_summaries')
    op.drop_column('guest_profiles', 'last_call_at')
    op.drop_column('guest_profiles', 'last_follow_up')
    op.drop_column('guest_profiles', 'last_outcome')
    op.drop_column('guest_profiles', 'preferred_language')
    op.drop_column('guest_profiles', 'last_property_id')
    op.drop_column('guest_profiles', 'host_id')

    op.create_index(op.f('ix_guest_profiles_phone'), 'guest_profiles', ['phone'], unique=True)
