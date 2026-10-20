"""add stages to negotiation_rules

Revision ID: c4153fd289d2
Revises: 9fd5d655d830
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c4153fd289d2'
down_revision: Union[str, None] = '9fd5d655d830'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Purely additive: one new nullable column, no default, no backfill.
    # Phase 4D (generalized negotiation policy model). NULL for every
    # existing row -- negotiation_policy.py treats stages=NULL as "use
    # discount_percent exactly as today," so this migration changes zero
    # existing pricing/negotiation behavior for any host. Nullable JSONB
    # (not NOT NULL with a server_default) because there is no sensible
    # non-null default for an ordered stage list -- unlike
    # call_handling_mode's server_default='MIRA' (9fd5d655d830), "no
    # staged ladder configured" IS the correct, permanent state for every
    # host who never authors one, not a placeholder to backfill away from.
    op.add_column(
        'negotiation_rules',
        sa.Column('stages', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('negotiation_rules', 'stages')
