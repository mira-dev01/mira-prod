"""add call handling schedule to properties and handoff status to call sessions

Revision ID: 9fd5d655d830
Revises: 8f1c4b9e2a67
Create Date: 2026-08-11 15:00:41.278495

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9fd5d655d830'
down_revision: Union[str, None] = '8f1c4b9e2a67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Purely additive: four new columns on properties, one new column on
    # call_sessions. No data migration, no touched rows, no new tables, no
    # new indexes. Phase 1 of the Call Ownership Schedule feature -- storage
    # only, no routing/resolver logic reads these columns yet.
    #
    # call_handling_mode: one of "MIRA" / "HOST" / "SCHEDULED" (validated at
    # the Pydantic layer, see app/schemas/property.py -- no DB-level CHECK
    # constraint, matching this column's plain-string, no-enum-type
    # approach below). server_default "MIRA" is load-bearing, not just a
    # convenience default -- it guarantees every existing property row is
    # backfilled to "MIRA" by Postgres itself as part of this ALTER TABLE
    # (NOT NULL with a server_default backfills existing rows), so no
    # existing property's calls are ever at risk of silently routing to a
    # host who never configured anything. Plain String(16), matching this
    # codebase's existing convention of never using a DB enum type for
    # status-like columns (see User.status, CallSession.status, Lead.status).
    op.add_column(
        'properties',
        sa.Column('call_handling_mode', sa.String(length=16), nullable=False, server_default='MIRA'),
    )
    # call_handling_schedule_start / _end: nullable HH:MM strings, same
    # representation as the existing check_in_time/check_out_time columns.
    # No schedule configured is a valid, common state (call_handling_mode
    # alone is a complete configuration) -- these stay null until a host
    # explicitly sets a window. Overnight windows (start > end, e.g.
    # "22:00" -> "06:00") are valid values; nothing at this layer
    # interprets or rejects them.
    op.add_column('properties', sa.Column('call_handling_schedule_start', sa.String(length=8), nullable=True))
    op.add_column('properties', sa.Column('call_handling_schedule_end', sa.String(length=8), nullable=True))
    # timezone: same column definition as the existing User.timezone
    # (String(64), default "Asia/Kolkata") -- reused rather than inventing
    # a second timezone representation. server_default backfills every
    # existing property to "Asia/Kolkata", matching today's entire fleet
    # and User.timezone's own existing default, so no existing row is left
    # in an ambiguous state.
    op.add_column(
        'properties',
        sa.Column('timezone', sa.String(length=64), nullable=False, server_default='Asia/Kolkata'),
    )

    # handoff_status: nullable, no default. NULL is the genuine "not
    # applicable" state for every call that never has a takeover requested
    # -- including every existing row, which must remain NULL, not be
    # backfilled to some other value. Nothing writes a non-NULL value here
    # yet; that begins in the phase that implements the Take Call endpoint.
    op.add_column('call_sessions', sa.Column('handoff_status', sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column('call_sessions', 'handoff_status')
    op.drop_column('properties', 'timezone')
    op.drop_column('properties', 'call_handling_schedule_end')
    op.drop_column('properties', 'call_handling_schedule_start')
    op.drop_column('properties', 'call_handling_mode')
