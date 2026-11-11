"""Sets villas' commercial terms (properties.pricing_terms: base guests, weekend rate, partner villa, meal
and add-on prices ...) from a JSON file, and optionally switches the host onto the TEST payment provider
with the booking rules the Spacez flow uses (pay in full or split half-and-half, held 10 minutes, a
successful payment confirms the booking).

A villa with terms is quoted with the full bill (GST, extra guests, meals, both ways to pay); anything a
villa's terms do not state stays UNKNOWN and the agent says the team will check. See
app/services/pricing_terms.py for every key.

File format -- {"villas": [{"name": "Casa Blu" | "property_id": "...", "terms": {"base_guests": 8, ...}}]}.
A "name" matches a property whose name starts with it (case-insensitive) and must match exactly one.
See scripts/data/spacez_booking_terms.json.

Dry-run by default. Run (from backend/):
    python -m scripts.set_booking_terms --user-email hello@spacez.dev --file scripts/data/spacez_booking_terms.json
    python -m scripts.set_booking_terms --user-email hello@spacez.dev --file scripts/data/spacez_booking_terms.json \\
        --simulated-payments --apply
--simulated-payments saves the key "sim_<host>" (no secret needed); it only works where SIMULATOR_ENABLED=true.
Needs the d7a2c4f8b1e3 migration applied first (alembic upgrade head).
"""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.database import AsyncSessionLocal, engine
from app.models.property import Property
from app.models.user import User
from app.schemas.agent_config import AgentConfig, BookingConfig, PaymentConfig
from app.services import agent_config_service
from app.services.pricing_terms import PricingTermsConfig


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--simulated-payments", action="store_true")
    parser.add_argument("--apply", action="store_true", help="write the changes (default: preview only)")
    args = parser.parse_args()

    entries = json.loads(args.file.read_text())["villas"]
    problems = 0
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
            terms = PricingTermsConfig.model_validate(entry["terms"]).model_dump(exclude_none=True, exclude_defaults=False)
            prop = matches[0]
            unknown = [k for k in ("weekend_rate", "meal_bundle_price", "pickup_fare") if k not in terms]
            print(f"  {prop.name[:44]:44s} base ₹{prop.base_price} -> terms {terms}")
            if unknown:
                print(f"      not on file (the agent will say the team will check): {', '.join(unknown)}")
            if args.apply:
                prop.pricing_terms = terms
        if args.simulated_payments:
            print("  payments: test provider key 'sim_host', split 50%, held 10 minutes, a successful payment confirms the booking")
            if args.apply:
                billing = dict(user.billing_config or {})
                billing["razorpay"] = {
                    "key_id": f"sim_{user.id.hex[:8]}",
                    "secret_enc": "sim",
                    "mode": "test",
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                }
                user.billing_config = billing
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
            print("Saved.")
        else:
            print("Preview only; add --apply to write.")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
