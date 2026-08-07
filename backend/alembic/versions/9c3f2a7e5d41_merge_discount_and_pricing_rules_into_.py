"""merge host_discount_rules and property_pricing_rules into negotiation_rules

Revision ID: 9c3f2a7e5d41
Revises: 054ea268d326
Create Date: 2026-08-07 15:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9c3f2a7e5d41'
down_revision: Union[str, None] = '054ea268d326'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# HostDiscountRule.trigger_type -> NegotiationRule.rule_type. Old trigger
# rows were implicitly host-wide (no property scoping existed on that
# table), so they migrate with property_ids=[] -- see NegotiationRule's
# docstring for why an empty list means "every property" for these types.
_TRIGGER_TO_RULE_TYPE = {
    "no_ask": "discount_no_ask",
    "guest_requests": "discount_guest_requests",
    "repeat_guest_same_host": "discount_repeat_guest",
}

# A host_discount_rules row whose trigger_type wasn't one of the three known
# built-ins becomes rule_type="custom" on upgrade -- the one NegotiationRule
# shape for "didn't fit a known type" -- which makes it indistinguishable
# from a genuinely native property_pricing_rules rule_type="custom" row
# (same rule_type, and both can legitimately have property_ids=[] -- a
# pending native custom pricing rule hasn't had properties picked yet
# either). downgrade() needs to route each "custom" row back to its ORIGINAL
# table, not just split by rule_type, so upgrade() tags migrated
# host_discount_rules rows with this prefix on their label and downgrade()
# strips it back off -- the one durable discriminator that survives the
# round trip regardless of property_ids/status at downgrade time.
_MIGRATED_TRIGGER_LABEL_PREFIX = "__migrated_host_discount_trigger__:"


def upgrade() -> None:
    op.create_table(
        'negotiation_rules',
        sa.Column('host_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('rule_type', sa.String(length=64), nullable=False),
        sa.Column('condition', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('discount_percent', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('label', sa.String(length=255), nullable=True),
        sa.Column('property_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('source', sa.String(length=16), server_default='ai_parsed', nullable=False),
        sa.Column('status', sa.String(length=32), server_default='pending_validation', nullable=False),
        sa.Column('raw_source_text', sa.Text(), nullable=True),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['host_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_negotiation_rules_host_id', 'negotiation_rules', ['host_id'], unique=False)

    connection = op.get_bind()

    # Carry over every existing host_discount_rules row, mapping
    # trigger_type -> the equivalent discount_* rule_type. A trigger_type
    # that isn't one of the three known built-ins (a host's own custom
    # wording, per that table's original comment) still needs a row, so it
    # maps to rule_type="custom" with its original wording preserved in
    # label -- the one shape NegotiationRule has for "didn't fit a known type".
    for row in connection.execute(sa.text(
        "SELECT id, host_id, trigger_type, discount_percent, source, status, raw_source_text, "
        "created_at, updated_at FROM host_discount_rules"
    )).mappings():
        rule_type = _TRIGGER_TO_RULE_TYPE.get(row["trigger_type"], "custom")
        label = None if rule_type != "custom" else _MIGRATED_TRIGGER_LABEL_PREFIX + row["trigger_type"]
        connection.execute(
            sa.text(
                "INSERT INTO negotiation_rules "
                "(id, host_id, rule_type, condition, discount_percent, label, property_ids, "
                "source, status, raw_source_text, created_at, updated_at) "
                "VALUES (:id, :host_id, :rule_type, '{}', :discount_percent, :label, '[]', "
                ":source, :status, :raw_source_text, :created_at, :updated_at)"
            ),
            {
                "id": row["id"],
                "host_id": row["host_id"],
                "rule_type": rule_type,
                "discount_percent": row["discount_percent"],
                "label": label,
                "source": row["source"],
                "status": row["status"],
                "raw_source_text": row["raw_source_text"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )

    # Carry over every existing property_pricing_rules row as-is -- same
    # columns, same meaning, just a new home table. IDs are preserved so
    # anything referencing an old rule id (none currently do, but future
    # data/logs might) keeps resolving to the same logical rule.
    for row in connection.execute(sa.text(
        "SELECT id, host_id, rule_type, condition, discount_percent, label, property_ids, "
        "source, status, raw_source_text, created_at, updated_at FROM property_pricing_rules"
    )).mappings():
        # asyncpg (unlike psycopg2) doesn't auto-adapt a Python dict/list to
        # JSONB through raw sa.text() -- SQLAlchemy's JSONB type machinery
        # that normally does this is bypassed entirely by textual SQL, so
        # condition/property_ids must be JSON-serialized to a string and
        # cast explicitly, or asyncpg raises "'dict' object has no attribute
        # 'encode'"/"'list' object has no attribute 'encode'" (confirmed
        # live against the actual asyncpg driver this app uses).
        params = dict(row)
        params["condition"] = json.dumps(params["condition"])
        params["property_ids"] = json.dumps(params["property_ids"])
        connection.execute(
            sa.text(
                "INSERT INTO negotiation_rules "
                "(id, host_id, rule_type, condition, discount_percent, label, property_ids, "
                "source, status, raw_source_text, created_at, updated_at) "
                "VALUES (:id, :host_id, :rule_type, CAST(:condition AS JSONB), :discount_percent, :label, "
                "CAST(:property_ids AS JSONB), :source, :status, :raw_source_text, :created_at, :updated_at)"
            ),
            params,
        )

    op.drop_table('property_pricing_rules')
    op.drop_index('ix_host_discount_rules_host_id', table_name='host_discount_rules')
    op.drop_table('host_discount_rules')


def downgrade() -> None:
    op.create_table(
        'host_discount_rules',
        sa.Column('host_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('trigger_type', sa.String(length=64), nullable=False),
        sa.Column('discount_percent', sa.Numeric(5, 2), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False, server_default='ai_parsed'),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='pending_validation'),
        sa.Column('raw_source_text', sa.Text(), nullable=True),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['host_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_host_discount_rules_host_id', 'host_discount_rules', ['host_id'], unique=False)

    op.create_table(
        'property_pricing_rules',
        sa.Column('host_id', sa.UUID(), nullable=False),
        sa.Column('rule_type', sa.String(length=64), nullable=False),
        sa.Column('condition', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('discount_percent', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('label', sa.String(length=255), nullable=True),
        sa.Column('property_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('source', sa.String(length=16), server_default='ai_parsed', nullable=False),
        sa.Column('status', sa.String(length=32), server_default='pending_validation', nullable=False),
        sa.Column('raw_source_text', sa.Text(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['host_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    connection = op.get_bind()
    _RULE_TYPE_TO_TRIGGER = {v: k for k, v in _TRIGGER_TO_RULE_TYPE.items()}

    # The three discount_* rule types always came from host_discount_rules --
    # unambiguous, route by rule_type alone.
    for row in connection.execute(sa.text(
        "SELECT id, host_id, rule_type, discount_percent, source, status, raw_source_text, "
        "created_at, updated_at FROM negotiation_rules WHERE rule_type IN "
        "('discount_no_ask', 'discount_guest_requests', 'discount_repeat_guest')"
    )).mappings():
        connection.execute(
            sa.text(
                "INSERT INTO host_discount_rules "
                "(id, host_id, trigger_type, discount_percent, source, status, raw_source_text, "
                "created_at, updated_at) "
                "VALUES (:id, :host_id, :trigger_type, :discount_percent, :source, :status, "
                ":raw_source_text, :created_at, :updated_at)"
            ),
            {
                "id": row["id"],
                "host_id": row["host_id"],
                "trigger_type": _RULE_TYPE_TO_TRIGGER[row["rule_type"]],
                "discount_percent": row["discount_percent"] or 0,
                "source": row["source"],
                "status": row["status"],
                "raw_source_text": row["raw_source_text"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )

    # rule_type="custom" is ambiguous -- it's the landing shape for BOTH a
    # host_discount_rules row with a non-standard trigger_type AND a
    # genuinely native property_pricing_rules custom rule. Only the former
    # carries the _MIGRATED_TRIGGER_LABEL_PREFIX tag upgrade() stamped onto
    # its label -- route those back into host_discount_rules with their
    # original trigger_type restored (label stripped, since that column
    # doesn't exist on host_discount_rules), everything else (including
    # every non-"custom" stay-pricing rule_type) into property_pricing_rules
    # untouched.
    prefix_len = len(_MIGRATED_TRIGGER_LABEL_PREFIX)
    for row in connection.execute(sa.text(
        "SELECT id, host_id, discount_percent, label, source, status, raw_source_text, "
        "created_at, updated_at FROM negotiation_rules WHERE rule_type = 'custom' "
        "AND left(label, :prefix_len) IS NOT DISTINCT FROM :prefix"
    ), {"prefix": _MIGRATED_TRIGGER_LABEL_PREFIX, "prefix_len": prefix_len}).mappings():
        connection.execute(
            sa.text(
                "INSERT INTO host_discount_rules "
                "(id, host_id, trigger_type, discount_percent, source, status, raw_source_text, "
                "created_at, updated_at) "
                "VALUES (:id, :host_id, :trigger_type, :discount_percent, :source, :status, "
                ":raw_source_text, :created_at, :updated_at)"
            ),
            {
                "id": row["id"],
                "host_id": row["host_id"],
                "trigger_type": row["label"][prefix_len:],
                "discount_percent": row["discount_percent"] or 0,
                "source": row["source"],
                "status": row["status"],
                "raw_source_text": row["raw_source_text"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )

    for row in connection.execute(sa.text(
        "SELECT id, host_id, rule_type, condition, discount_percent, label, property_ids, "
        "source, status, raw_source_text, created_at, updated_at FROM negotiation_rules WHERE rule_type NOT IN "
        "('discount_no_ask', 'discount_guest_requests', 'discount_repeat_guest') "
        "AND NOT (rule_type = 'custom' AND left(label, :prefix_len) IS NOT DISTINCT FROM :prefix)"
    ), {"prefix": _MIGRATED_TRIGGER_LABEL_PREFIX, "prefix_len": prefix_len}).mappings():
        # Same asyncpg JSONB-adaptation requirement as upgrade()'s
        # property_pricing_rules -> negotiation_rules copy above.
        params = dict(row)
        params["condition"] = json.dumps(params["condition"])
        params["property_ids"] = json.dumps(params["property_ids"])
        connection.execute(
            sa.text(
                "INSERT INTO property_pricing_rules "
                "(id, host_id, rule_type, condition, discount_percent, label, property_ids, "
                "source, status, raw_source_text, created_at, updated_at) "
                "VALUES (:id, :host_id, :rule_type, CAST(:condition AS JSONB), :discount_percent, :label, "
                "CAST(:property_ids AS JSONB), :source, :status, :raw_source_text, :created_at, :updated_at)"
            ),
            params,
        )

    op.drop_index('ix_negotiation_rules_host_id', table_name='negotiation_rules')
    op.drop_table('negotiation_rules')
