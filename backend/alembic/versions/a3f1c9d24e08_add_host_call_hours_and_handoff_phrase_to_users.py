"""add host call hours and handoff phrase to users

Revision ID: a3f1c9d24e08
Revises: f041738fce4c
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f1c9d24e08'
down_revision: Union[str, None] = 'f041738fce4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Purely additive: five new columns on users, nothing else touched, no
    # backfill of real data needed. See
    # documentation/host-call-hours-and-handoff.md.
    #
    # host_call_hours_enabled: master switch for the account-global host
    # call hours window. server_default 'false' is load-bearing -- it
    # backfills every existing user row to "disabled", which is exactly
    # today's behavior (Mira answers 24/7 when the FIXED_HOST_HOURS_* env
    # override is unset), so no existing account's calls change routing as
    # a result of this migration.
    op.add_column(
        'users',
        sa.Column('host_call_hours_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    # host_call_hours_start / _end: nullable "HH:MM" strings, same
    # representation as properties.call_handling_schedule_start/_end and
    # check_in_time/check_out_time. Null until a host sets a window;
    # app/schemas/user.py requires both when host_call_hours_enabled is
    # turned on. Overnight windows (start > end) are valid values.
    op.add_column('users', sa.Column('host_call_hours_start', sa.String(length=8), nullable=True))
    op.add_column('users', sa.Column('host_call_hours_end', sa.String(length=8), nullable=True))
    # host_call_hours_timezone: same column shape as the existing
    # User.timezone (String(64), default 'Asia/Kolkata'); its own column
    # rather than reusing User.timezone because this one gates live call
    # routing and is IANA-validated at the schema layer. server_default
    # backfills every existing row to 'Asia/Kolkata', matching today's
    # entire fleet.
    op.add_column(
        'users',
        sa.Column('host_call_hours_timezone', sa.String(length=64), nullable=False, server_default='Asia/Kolkata'),
    )

    # agent_handoff_phrase: nullable Text, same shape as the existing
    # agent_first_message/agent_persona/agent_escalation_phrase per-host
    # voice customization columns. Null means "use DEFAULT_HOST_HANDOFF_
    # PHRASE" -- no existing row needs a value.
    op.add_column('users', sa.Column('agent_handoff_phrase', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'agent_handoff_phrase')
    op.drop_column('users', 'host_call_hours_timezone')
    op.drop_column('users', 'host_call_hours_end')
    op.drop_column('users', 'host_call_hours_start')
    op.drop_column('users', 'host_call_hours_enabled')
