"""Gazetteer: a local copy of India's place names (GeoNames), used to turn a
place a guest names ("Kasol", "around Nainital") into a point on the map and to
tell which state/district a point is in. Read-only reference data loaded by
scripts/load_gazetteer.py -- nothing on the live-call path writes to it, and a
lookup never calls an external service.

Deliberately plain tables (no UUID/timestamp mixins): rows are keyed by the
GeoNames id so a reload is idempotent, and there is no per-row lifecycle.
"""

from sqlalchemy import BigInteger, Float, ForeignKey, Index, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AdminRegion(Base):
    """A state ('IN.11' = Himachal Pradesh) or district ('IN.11.026' = Kullu)."""

    __tablename__ = "admin_regions"

    code: Mapped[str] = mapped_column(String(24), primary_key=True)
    level: Mapped[int] = mapped_column(SmallInteger)  # 1 = state, 2 = district
    name: Mapped[str] = mapped_column(String(120))


class Place(Base):
    __tablename__ = "places"
    __table_args__ = (Index("ix_places_lat_lng", "lat", "lng"),)

    geoname_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(120))
    # app.services.geo.text.normalize(name): lowercase, ASCII-folded, punctuation
    # stripped. The exact-match key.
    name_norm: Mapped[str] = mapped_column(String(120), index=True)
    # app.services.geo.text.skeleton(name): a consonant skeleton, so speech-to-text
    # spellings of the same word ("kasaul"/"kasol") share a key.
    name_key: Mapped[str] = mapped_column(String(40), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    admin1_code: Mapped[str | None] = mapped_column(String(8))
    admin2_code: Mapped[str | None] = mapped_column(String(24))
    population: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class PlaceAlias(Base):
    """An alternate spelling of a place ("Nainital" for GeoNames' "Naini Tal")."""

    __tablename__ = "place_aliases"

    place_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("places.geoname_id", ondelete="CASCADE"), primary_key=True
    )
    alias_norm: Mapped[str] = mapped_column(String(120), primary_key=True, index=True)
    alias_key: Mapped[str] = mapped_column(String(40), index=True)
