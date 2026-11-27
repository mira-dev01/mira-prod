"""Read-only: what a call costs, split by provider, from the usage Mira already records.

In-call usage (LLM tokens, Sarvam STT seconds / TTS characters) comes from service_usage_events, one row per call and
model; Exotel minutes come from call_sessions durations (the same data Exotel bills on), each call capped at
MAX_CALL_MINUTES so a session that never closed cannot swamp the average. Prices are the admin panel's rate card
(app/services/admin_monitor_service.ACCOUNTS: starting estimates, editable there; check them against invoices).

Writes nothing. Point it at the database you want to measure (production is where the real calls are):

    DATABASE_URL=<read-only production url> python -m scripts.cost_per_call_report [--days 30]
"""

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.database import AsyncSessionLocal
from app.services.admin_monitor_service import ACCOUNTS, DEFAULT_USD_INR, SERVICE_TO_ACCOUNT

MAX_CALL_MINUTES = 30
IN_CALL_SERVICES = ("groq", "openrouter", "anthropic", "sarvam_stt", "sarvam_tts", "twilio_voice")


def inr(account: str, model: str | None, unit: str, quantity: float) -> float:
    cfg = ACCOUNTS[account]
    prices = cfg["unit_prices"]
    price = prices.get(f"{model}:{unit}", prices.get(unit))
    if price is None:
        return 0.0
    return quantity * price * (DEFAULT_USD_INR if cfg["currency"] == "USD" else 1.0)


async def main(days: int) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with AsyncSessionLocal() as db:
        calls = (
            await db.execute(
                text(
                    "select id, extract(epoch from ended_at - started_at) from call_sessions "
                    "where started_at >= :s and ended_at is not null"
                ),
                {"s": since},
            )
        ).all()
        events = (
            await db.execute(
                text(
                    "select call_session_id, service, unit, model, quantity from service_usage_events "
                    "where created_at >= :s and call_session_id is not null"
                ),
                {"s": since},
            )
        ).all()

    minutes_by_call = {cid: min(float(secs or 0) / 60, MAX_CALL_MINUTES) for cid, secs in calls}
    n = len(minutes_by_call)
    if not n:
        print(f"No finished calls in the last {days} days in this database.")
        return
    total_minutes = sum(minutes_by_call.values())
    cost: dict[str, float] = defaultdict(float)
    cost["exotel"] = inr("exotel", None, "minutes", total_minutes)
    metered_calls = set()
    for cid, service, unit, model, quantity in events:
        if service not in IN_CALL_SERVICES or cid not in minutes_by_call:
            continue
        metered_calls.add(cid)
        quantity = float(quantity)
        cost[SERVICE_TO_ACCOUNT.get(service, service)] += inr(SERVICE_TO_ACCOUNT.get(service, service), model, unit, quantity)

    total = sum(cost.values())
    print(f"Last {days} days: {n} finished calls, {total_minutes:.0f} billable minutes ({total_minutes / n:.1f} min/call)")
    print(f"Calls with in-call usage rows: {len(metered_calls)} of {n}")
    print(f"{'provider':<14}{'INR total':>12}{'INR/call':>11}{'INR/min':>10}{'share':>8}")
    for account, value in sorted(cost.items(), key=lambda kv: -kv[1]):
        print(f"{account:<14}{value:>12.1f}{value / n:>11.2f}{value / total_minutes:>10.2f}{100 * value / total:>7.0f}%")
    print(f"{'TOTAL':<14}{total:>12.1f}{total / n:>11.2f}{total / total_minutes:>10.2f}")
    if len(metered_calls) < n:
        print("\nNote: some calls have no usage rows (made before metering, or on another path); LLM/STT/TTS are understated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    asyncio.run(main(parser.parse_args().days))
