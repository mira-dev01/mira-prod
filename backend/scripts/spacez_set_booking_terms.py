"""SPACEZ ONLY. Sets the Spacez villas' commercial terms (properties.pricing_terms: base guests, partner villa,
meal and add-on prices, villa profile ...), their weekend rate (the property's own "weekend rate differs"
toggle) and their knowledge-base facts from a JSON file, and optionally switches the host onto the TEST
payment provider with the booking rules the Spacez flow uses (pay in full or split half-and-half, held 10
minutes, a successful payment confirms the booking).

A villa with terms is quoted with the full bill (GST, extra guests, meals, both ways to pay); anything a
villa's terms do not state stays UNKNOWN and the agent says the team will check. See
app/services/spacez_pricing_terms.py for every key.

File format -- {"common_terms": {...merged into every villa...},
                "villas": [{"name": "Casa Blu" | "property_id": "...", "bedrooms": 4, "max_guests": 10,
                            "weekend_rate": 10000, "terms": {"base_guests": 8, ...}}]}.
A "name" matches a property whose name starts with it (case-insensitive) and must match exactly one.
"weekend_rate" turns the property's weekend-rate toggle ON at that price (omit it and the toggle is left as it
is); the weeknight rate is the property's own base price and is never touched here.
See scripts/data/spacez_booking_terms.json.

Dry-run by default. Run (from backend/):
    python -m scripts.spacez_set_booking_terms --user-email hello@spacez.dev --file scripts/data/spacez_booking_terms.json
    python -m scripts.spacez_set_booking_terms --user-email hello@spacez.dev --file scripts/data/spacez_booking_terms.json \\
        --payment-defaults --apply
--payment-defaults also sets the host's payment settings (full payment or a 50% split, dates held 10 minutes, a
paid link confirms the booking). It never touches the Razorpay key: the host saves that in Settings -> Billing.
Needs the d7a2c4f8b1e3 migration applied first (alembic upgrade head).
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
from app.schemas.agent_config import AgentConfig, BookingConfig, PaymentConfig
from app.services import agent_config_service
from app.services.property.chunking import sync_property_facts
from app.services.spacez_pricing_terms import PricingTermsConfig


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--payment-defaults", action="store_true")
    parser.add_argument("--apply", action="store_true", help="write the changes (default: preview only)")
    args = parser.parse_args()

    document = json.loads(args.file.read_text())
    entries, common = document["villas"], document.get("common_terms") or {}
    problems = 0
    changed: list[Property] = []
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == args.user_email))
        if user is None:
            print(f"No host with email {args.user_email}")
            return 2
        properties = list((await db.scalars(select(Property).where(Property.user_id == user.id))).all())
        for entry in entries:
            if entry.get("property_id"):
                matches = [p for p in properties if p.id == uuid.UUID(entry["property_id"])]
            else:
                wanted = entry["name"].lower()
                matches = [p for p in properties if p.name.lower().startswith(wanted)]
            label = entry.get("name") or entry.get("property_id")
            if len(matches) != 1:
                print(f"  !! {label}: matched {len(matches)} properties (need exactly 1)")
                problems += 1
                continue
            terms = PricingTermsConfig.model_validate({**common, **entry["terms"]}).model_dump(exclude_none=True, exclude_defaults=False)
            prop = matches[0]
            unknown = [k for k in ("meal_bundle_price", "pickup_fare") if k not in terms]
            weekend = entry.get("weekend_rate")
            print(f"  {prop.name[:44]:44s} weeknight ₹{prop.base_price}, weekend {'₹' + str(weekend) if weekend else 'unchanged'} -> terms {terms}")
            if not prop.base_price or float(prop.base_price) <= 0:
                print("      !! this property has no base price (the weeknight rate): the bill cannot be built until it is set")
            if unknown:
                print(f"      not on file (the agent will say the team will check): {', '.join(unknown)}")
            if args.apply:
                prop.pricing_terms = terms
                if weekend:
                    prop.weekend_rate_enabled, prop.weekend_price = True, weekend
                if entry.get("bedrooms"):
                    prop.bedroom_count = entry["bedrooms"]
                if entry.get("max_guests"):
                    prop.max_guests = entry["max_guests"]
                changed.append(prop)
        if args.payment_defaults:
            print("  payments: split 50%, dates held 10 minutes, a paid link confirms the booking (the Razorpay key is saved in Billing)")
            if args.apply:
                config = agent_config_service.get_config(user).model_dump()
                config["payment"] = PaymentConfig(kind="full", split_percent=50, hold_minutes=10).model_dump()
                config["booking"] = BookingConfig(confirm_by="payment").model_dump()
                user.agent_config = AgentConfig.model_validate(config).model_dump()
        if problems:
            print("Fix the entries above first; nothing written.")
            await db.rollback()
            return 1
        if args.apply:
            await db.commit()
            for prop in changed:  # the villa's profile and prices go into its knowledge base (what search_faq reads)
                print(f"  knowledge base: {prop.name[:44]} -> {await sync_property_facts(db, prop)} facts")
            print("Saved.")
        else:
            print("Preview only; add --apply to write.")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
