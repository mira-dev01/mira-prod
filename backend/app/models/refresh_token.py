import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class RefreshToken(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SHA-256 hex digest of the raw token, never the raw value -- same
    # reasoning as hashed_password on User; a DB read/backup leak shouldn't
    # hand out a usable refresh token.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set to the new token's id when this one is rotated out on use, so a
    # replayed old token (e.g. stolen and used after the legitimate client
    # already rotated it) can be detected and the whole chain revoked.
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
