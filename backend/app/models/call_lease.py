import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class CallLease(UUIDPkMixin, TimestampMixin, Base):
    """STAGED FOR REMOVAL -- NOT WRITTEN TO BY ANY PRODUCTION CODE PATH.

    As of the Redis migration (see app/services/call_coordinator.py's own
    module docstring), CallCoordinator's active-call ownership mechanism
    runs entirely on Redis -- a single key per (host, property) pair with a
    JSON value and native TTL expiry, not this table. This model, its
    table, and its migration (alembic/versions/6384600c83f2_add_call_leases.py)
    are kept in the repo ONLY as a deliberate staging step: nothing writes
    here anymore, and the table can be dropped in a later, separate cleanup
    phase once the Redis-backed implementation has been running in
    production long enough to trust. Do not add new writers to this table --
    if you need to persist something about call ownership, it belongs in
    Redis via app/integrations/redis_lease_client.py, not here.

    Everything below this point describes the PRE-MIGRATION design and is
    retained for historical/removal-planning context only.

    CallCoordinator's sole storage: one row per attempt to own a live call
    for a given (host, property) pair. Lead Agent calls are host-scoped, not
    property-scoped (see CallSession.property_id, which is nullable for the
    same reason) -- represented here by `property_id = NIL_PROPERTY_ID`
    rather than SQL NULL, specifically so the uniqueness index below works.
    Postgres unique indexes treat every NULL as distinct from every other
    NULL, so a plain nullable column would let two concurrent Lead Agent
    calls for the same host both "succeed" and never collide -- confirmed by
    a real failing test in test_call_coordinator.py before this was caught.
    CallCoordinator translates None <-> NIL_PROPERTY_ID at its own boundary
    (see acquire/is_busy) so no caller of CallCoordinator needs to know this
    sentinel exists.

    Correctness does not come from application logic checking "is there an
    active row" before inserting -- that check-then-insert has a race window
    under concurrent requests. It comes from `ix_call_leases_active_owner`, a
    partial unique index on (host_user_id, property_id) WHERE released_at IS
    NULL: Postgres itself rejects a second concurrent INSERT for the same
    owner while one active lease exists, atomically. CallCoordinator.acquire
    relies on catching that IntegrityError, not on a pre-check.

    Expiry is lazy, not swept: a row past expires_at simply stops counting as
    "active" the next time anyone queries for one (see CallCoordinator.
    is_busy / acquire), so a crashed holder that never calls release() cannot
    wedge a host/property as permanently busy -- see decision doc "Design a
    lease-based ownership mechanism" for why CallSession.status alone was
    rejected as the concurrency gate (a crashed pipeline leaves it stuck at
    in_progress forever, with nothing to reconcile it).
    """

    __tablename__ = "call_leases"
    __table_args__ = (
        Index(
            "ix_call_leases_active_owner",
            "host_user_id",
            "property_id",
            unique=True,
            postgresql_where="released_at IS NULL",
        ),
    )

    # Sentinel standing in for "no property, this is a Lead Agent call" --
    # see the class docstring for why SQL NULL can't be used here.
    NIL_PROPERTY_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

    host_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Intentionally NOT a ForeignKey to properties.id: NIL_PROPERTY_ID is a
    # valid, permanent value for this column that will never exist in
    # properties, which an FK constraint would reject.
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Free string, same discipline as Lead.status / Notification.channel --
    # today only "pipeline" is issued, by run_voice_pipeline. Future
    # consumers (human handoff, a second AI agent) are additional values,
    # not a new table or a new coordinator -- CallCoordinator.transfer()
    # re-points an active lease's holder_type/holder_ref atomically for
    # exactly this, without a release-then-acquire race window where a
    # third caller could grab the (host, property) pair mid-handoff.
    holder_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # Opaque to CallCoordinator -- today the caller passes exotel_call_id /
    # CallSession.id, whichever it already has at acquire time. Used only to
    # let the correct holder renew/release its own lease.
    holder_ref: Mapped[str] = mapped_column(String(255), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
