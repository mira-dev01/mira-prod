"""add call_leases

Revision ID: 6384600c83f2
Revises: 9c3f2a7e5d41
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '6384600c83f2'
down_revision: Union[str, None] = '9c3f2a7e5d41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'call_leases',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.Column('host_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        # Not SQL NULL for "no property" (Lead Agent calls) -- a plain
        # nullable column would defeat the uniqueness index below, since
        # Postgres treats every NULL as distinct from every other NULL.
        # CallLease.NIL_PROPERTY_ID (all-zero UUID) is the sentinel used
        # instead; deliberately no FK to properties.id since that sentinel
        # will never exist there.
        sa.Column('property_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('holder_type', sa.String(length=32), nullable=False),
        sa.Column('holder_ref', sa.String(length=255), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['host_user_id'], ['users.id'], ondelete='CASCADE'),
    )

    # Plain (non-unique) lookup index for renew()/release(), both keyed by
    # holder_ref -- created as part of table creation, no separate lock
    # concern (empty table at this point in the migration).
    op.create_index(op.f('ix_call_leases_holder_ref'), 'call_leases', ['holder_ref'], unique=False)

    # The correctness-bearing index: at most one row with released_at IS
    # NULL per (host_user_id, property_id). This is what CallCoordinator.
    # acquire()'s IntegrityError catch actually relies on -- not application
    # logic. Table is new/empty at migration time, so there's no pre-existing
    # data that could violate it and no concurrent write traffic yet to block
    # -- CONCURRENTLY (the pattern already established in
    # 054ea268d326_add_index_on_notification_property_id.py for a live table)
    # isn't needed here, but autocommit_block() is used anyway for
    # consistency with that migration and because a plain CREATE UNIQUE INDEX
    # takes a lock other DDL in the same transaction would otherwise inherit.
    with op.get_context().autocommit_block():
        op.create_index(
            'ix_call_leases_active_owner',
            'call_leases',
            ['host_user_id', 'property_id'],
            unique=True,
            postgresql_where=sa.text('released_at IS NULL'),
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index('ix_call_leases_active_owner', table_name='call_leases')
    op.drop_index(op.f('ix_call_leases_holder_ref'), table_name='call_leases')
    op.drop_table('call_leases')
