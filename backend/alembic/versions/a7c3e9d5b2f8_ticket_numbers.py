"""Tickets get a short running number (TK-1033 on the dashboard).

Revision ID: a7c3e9d5b2f8
Revises: e4b8c2f6a1d3
"""

import sqlalchemy as sa
from alembic import op

revision = "a7c3e9d5b2f8"
down_revision = "e4b8c2f6a1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS ticket_number_seq START 1001")
    op.add_column("tickets", sa.Column("number", sa.Integer(), nullable=True))
    # The tickets that exist are numbered oldest first, so the numbers read in the order they were raised.
    op.execute(
        """
        UPDATE tickets SET number = numbered.n
        FROM (SELECT id, 1000 + row_number() OVER (ORDER BY created_at, id) AS n FROM tickets) numbered
        WHERE tickets.id = numbered.id
        """
    )
    # New tickets carry on after the last one numbered above (the first is 1001 when there are none yet).
    op.execute("SELECT setval('ticket_number_seq', COALESCE((SELECT MAX(number) FROM tickets), 1000))")
    op.alter_column("tickets", "number", nullable=False, server_default=sa.text("nextval('ticket_number_seq')"))
    op.create_unique_constraint("uq_tickets_number", "tickets", ["number"])


def downgrade() -> None:
    op.drop_constraint("uq_tickets_number", "tickets", type_="unique")
    op.drop_column("tickets", "number")
    op.execute("DROP SEQUENCE IF EXISTS ticket_number_seq")
