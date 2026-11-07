"""Sets properties' map positions from a JSON file of known coordinates, and
derives each one's state/district from the gazetteer (scripts/load_gazetteer.py).

For a host whose own website or listing already publishes exact coordinates --
the authoritative source; the importer's text guess (property_location.
guess_point_from_text) only accepts unambiguous names and leaves the rest empty.

File format -- a list of {"airbnb_listing_id" | "property_id", "latitude",
"longitude", "note"?, "source"?}. See scripts/data/hushstays_locations.json.

Dry-run by default (prints what would change, writes nothing). Run (from backend/):
    python -m scripts.set_property_locations --user-email hello@hushstays.dev \\
        --file scripts/data/hushstays_locations.json            # preview
    python -m scripts.set_property_locations --user-email hello@hushstays.dev \\
        --file scripts/data/hushstays_locations.json --apply
Run the gazetteer loader first, or state/district stay empty (the point is still saved).
"""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from app.database import AsyncSessionLocal, engine
from app.models.property import Property
from app.models.user import User
from app.services.geo.property_location import set_property_point

_SOURCE = "host_website"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: preview only)")
    args = parser.parse_args()
    entries = json.loads(args.file.read_text(encoding="utf-8"))

    async with AsyncSessionLocal() as db:
        user_id = await db.scalar(select(User.id).where(User.email == args.user_email))
        if user_id is None:
            print(f"no user {args.user_email}", file=sys.stderr)
            return 1
        properties = list((await db.scalars(select(Property).where(Property.user_id == user_id))).all())
        by_airbnb = {p.airbnb_listing_id: p for p in properties if p.airbnb_listing_id}
        by_id = {p.id: p for p in properties}

        done, missed = 0, []
        for entry in entries:
            if entry.get("property_id"):
                prop = by_id.get(uuid.UUID(entry["property_id"]))
            else:
                prop = by_airbnb.get(str(entry.get("airbnb_listing_id")))
            if prop is None:
                missed.append(entry.get("airbnb_listing_id") or entry.get("property_id"))
                continue
            await set_property_point(db, prop, entry["latitude"], entry["longitude"], _SOURCE)
            done += 1
            print(f"{prop.name[:52]:54} {prop.state or '?':18} {prop.district or '?':16} ({prop.latitude}, {prop.longitude})")
        print(f"\n{done} properties located, {len(missed)} entries matched no property {missed or ''}")
        if args.apply:
            await db.commit()
            print("applied")
        else:
            await db.rollback()
            print("preview only -- re-run with --apply to write")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
