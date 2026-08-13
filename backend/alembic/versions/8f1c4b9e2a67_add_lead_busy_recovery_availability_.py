"""add lead busy recovery availability tracking

Revision ID: 8f1c4b9e2a67
Revises: 44fd2130051e
Create Date: 2026-08-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8f1c4b9e2a67'
# Originally written against 7a236ad1ffd1 (the head at the time), before
# 44fd2130051e (a no-op merge migration reconciling the shagun branch's
# saturday-minimum-stay/negotiation-engine heads back into main) landed on
# main first -- both ended up claiming 7a236ad1ffd1 as their parent,
# producing two divergent heads ("Multiple head revisions" -- alembic
# upgrade head refuses to run, which is what actually broke the Railway
# deploy: alembic upgrade head is the container's own startup command,
# see Dockerfile, so the container never got past that line and every
# healthcheck attempt failed against a process that never started).
# Re-pointed at 44fd2130051e (the real current head, which already
# contains 7a236ad1ffd1 as an ancestor) to collapse back to one line.
down_revision: Union[str, None] = '44fd2130051e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Purely additive: three new nullable columns on the existing leads
    # table, no data migration, no touched rows. Tracks whether Mira owes
    # this busy-recovery guest an "I'm available now" WhatsApp message --
    # deliberately separate from Lead.status (host-managed sales lifecycle:
    # open/contacted/booked/closed, see app/models/lead.py) and from
    # recovery_reason (WHY this lead needed recovery, unrelated to whether
    # the follow-up availability message has gone out).
    #
    # busy_recovery_availability_status: null for every non-busy-recovery
    # lead and forever. For a busy-recovery lead (recovery_reason=
    # "BUSY_CALL"), one of "pending" / "processing" / "notified" -- see
    # app/services/recovery_service.py's process_availability_recovery for
    # the state machine (an atomic UPDATE ... WHERE status IN (...) ...
    # RETURNING claim, not an application-level lock).
    op.add_column(
        'leads',
        sa.Column('busy_recovery_availability_status', sa.String(length=16), nullable=True),
    )
    # busy_recovery_at: when THIS busy call happened -- not reused from
    # Lead.created_at (a reused lead's created_at can predate a later busy
    # rejection; see recovery_service.py's upsert_lead reuse) or updated_at
    # (churns on unrelated edits: a host status change, a WhatsApp reply
    # updating next_follow_up). The one timestamp the availability
    # expiration window (process_availability_recovery's
    # AVAILABILITY_WINDOW) actually needs to be accurate against.
    op.add_column(
        'leads',
        sa.Column('busy_recovery_at', sa.DateTime(timezone=True), nullable=True),
    )
    # busy_recovery_claimed_at: set the moment a worker atomically claims
    # this row (pending -> processing), cleared back to null on notified.
    # Lets a crashed/killed worker's stuck "processing" row become
    # reclaimable after a short staleness threshold, instead of sitting
    # unprocessable forever -- see process_availability_recovery's claim
    # query, which treats a stale "processing" row as claimable exactly
    # like a "pending" one.
    op.add_column(
        'leads',
        sa.Column('busy_recovery_claimed_at', sa.DateTime(timezone=True), nullable=True),
    )
    # Indexed: process_availability_recovery's claim query filters on this
    # column for every lease-release event -- same "avoid a sequential scan
    # on every trigger" reasoning as the prior migration's notification
    # indexes. CONCURRENTLY needs to run outside the migration's
    # transaction.
    with op.get_context().autocommit_block():
        op.create_index(
            'ix_leads_busy_recovery_availability_status',
            'leads',
            ['busy_recovery_availability_status'],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            'ix_leads_busy_recovery_availability_status', table_name='leads', postgresql_concurrently=True
        )
    op.drop_column('leads', 'busy_recovery_claimed_at')
    op.drop_column('leads', 'busy_recovery_at')
    op.drop_column('leads', 'busy_recovery_availability_status')
