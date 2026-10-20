"""add index on notification property_id

Revision ID: 054ea268d326
Revises: c22483e0853a
Create Date: 2026-08-07 09:42:13.313474

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '054ea268d326'
down_revision: Union[str, None] = 'c22483e0853a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: autogenerate also proposed dropping the refresh_tokens table, an
    # ix_host_discount_rules_host_id index, and a saturday_minimum_stay_enabled
    # column -- pre-existing drift unrelated to this migration (same pattern
    # already seen in every prior migration this session), left untouched here.
    #
    # Scale Readiness ("Phase 17") self-review: a plain CREATE INDEX takes a
    # SHARE lock for the whole build, blocking every INSERT/UPDATE/DELETE on
    # `notifications` until it finishes -- notably every escalate_to_host/
    # dispatch_technician/send_whatsapp/send_photos tool call, since all four
    # write this table. Harmless at today's table size (this exact migration
    # already ran against production with no observed impact), but shipping
    # a lock-blocking index migration is the opposite of what a "prepare for
    # significantly higher volume" phase should leave behind for the NEXT
    # migration like this, or for a fresh environment (e.g. the dormant
    # Render fallback, docs/architecture.md) applying this same history
    # against an already-populated table. CONCURRENTLY avoids the lock, at
    # the cost of needing to run outside the transaction Alembic wraps
    # migrations in by default -- autocommit_block() is Alembic's own
    # documented mechanism for exactly this (a Postgres DDL operation that
    # must run outside a transaction block), and works the same way against
    # the async engine's underlying sync-facing connection used here.
    with op.get_context().autocommit_block():
        op.create_index(
            op.f('ix_notifications_property_id'),
            'notifications',
            ['property_id'],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            op.f('ix_notifications_property_id'),
            table_name='notifications',
            postgresql_concurrently=True,
        )
