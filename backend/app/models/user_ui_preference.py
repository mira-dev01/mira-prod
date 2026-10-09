import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class UserUiPreference(UUIDPkMixin, TimestampMixin, Base):
    """One layout preference document per user per `key` ("navigation",
    "overview_widgets"). Deliberately its own table: layout is independent
    of capability activation (host_capabilities), capability config,
    integration status and onboarding (host_onboarding) -- hiding a page or
    widget here never touches any of those.

    Scoped to the User row, which in this app is the host account itself
    (one Clerk user = one host; there is no team model), so per-user and
    per-host scope coincide today. If team members are ever added, this
    stays per-user while capabilities stay per-host.

    `revision` is an optimistic-concurrency counter: a save must name the
    revision it was based on, so two tabs/devices can't silently overwrite
    each other. `schema_version` describes the shape of `data`.
    """

    __tablename__ = "user_ui_preferences"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_ui_preferences_user_key"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
