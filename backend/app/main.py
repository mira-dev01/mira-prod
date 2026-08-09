import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger as _loguru_logger
from sqlalchemy import text

from app.api.v1 import (
    analytics,
    auth,
    bookings,
    calls,
    faq,
    guests,
    leads,
    negotiation_rules,
    notifications,
    pricing,
    properties,
    technicians,
    voice,
)
from app.api.v1.webhooks import exotel, whatsapp
from app.config import settings
from app.database import AsyncSessionLocal
from app.services.calendar_service import sync_all_properties
from app.services.smart_pricing_service import refresh_live_pricing_cache, refresh_smart_pricing

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _PropagateToStdlib(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        stdlib_logger = logging.getLogger(record.name)
        stdlib_logger.handle(record)


_loguru_logger.remove()
_loguru_logger.add(sys.stderr, level="DEBUG")
_loguru_logger.add(_PropagateToStdlib(), level="DEBUG", format="{message}")

scheduler = AsyncIOScheduler()


async def _scheduled_ical_sync() -> None:
    async with AsyncSessionLocal() as db:
        results = await sync_all_properties(db)
        if results:
            logger.info("iCal sync complete: %s", results)


async def _backfill_property_display_names() -> None:
    """One-shot, idempotent startup task: backfills display_name/
    spoken_name/property_type/etc for any property imported before that
    feature existed (raw_name/display_name still NULL) -- without this,
    those properties keep speaking their raw scraped Airbnb title verbatim
    on live calls indefinitely, since nothing else ever re-touches an
    existing property's name fields. Safe to run on every deploy: scoped
    to WHERE display_name IS NULL, so it's a no-op once every property has
    been backfilled once. Logged loudly on failure but never raised --
    must not block server startup."""
    try:
        async with AsyncSessionLocal() as db:
            count = await properties.backfill_missing_display_names(db)
        if count:
            logger.info("Backfilled display_name/spoken_name for %d propert%s", count, "y" if count == 1 else "ies")
    except Exception:
        logger.exception("Property display_name backfill failed -- will retry on next deploy/restart")


async def _scheduled_smart_pricing_refresh() -> None:
    async with AsyncSessionLocal() as db:
        await refresh_smart_pricing(db)


async def _scheduled_live_pricing_cache_refresh() -> None:
    async with AsyncSessionLocal() as db:
        await refresh_live_pricing_cache(db)


async def _check_db_health() -> None:
    # Neon (like most serverless Postgres) suspends its compute after a few
    # minutes of inactivity -- the first query after that wakes it back up,
    # which costs multiple seconds. That wake-up was landing squarely on the
    # first DB query of a voice pipeline (run_browser_lead_pipeline /
    # run_voice_pipeline's guest/session lookups), showing up as an
    # unexplained multi-second gap before the greeting with nothing logged
    # in between. A trivial periodic ping, well under Neon's autosuspend
    # window, keeps the connection warm so a real call never pays that cost.
    # Same rationale as _check_llm_health above, applied to the DB instead
    # of the LLM route. Failures are logged only -- never fatal.
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
    except Exception as e:
        logger.warning("DB keep-alive ping failed: %s", e)


# Populated by _check_llm_health, read by app/voice/pipeline.py's _build_llm()
# to pick the first healthy Groq model in settings.groq_models priority
# order. Keyed by model name -> {"ok": bool, "latency_s": float | None,
# "checked_at": iso str, "error": str | None}. Also exposed read-only via
# GET /api/v1/health/llm for a dashboard widget.
llm_health: dict[str, dict] = {}


async def _check_llm_health() -> None:
    # OpenRouter (and Groq) cold-starts its routing to the model backend on
    # the first request after idle -- on Render this adds 5-8s to the first
    # real call. Firing a 1-token ping keeps the route warm and, for Groq,
    # doubles as a per-model health/rate-limit check: a 429 (e.g. gpt-oss-120b
    # hitting its per-model rate limit under call load -- account is on a
    # paid Groq plan as of 2026-07-07, not free tier, but the limit isn't
    # unlimited) marks that model down in
    # llm_health so _build_llm() skips it and falls through to the next model
    # in settings.groq_models, instead of every call re-discovering the same
    # 429 via multi-second retry/backoff. Failures are non-fatal; the app
    # still starts and callers still get a response via the next model down
    # the chain (or OpenRouter, as the last resort).
    import time

    if settings.llm_provider == "groq" and settings.groq_api_key:
        from groq import AsyncGroq

        client = AsyncGroq(api_key=settings.groq_api_key)
        for model in settings.groq_models:
            # reasoning_effort is gpt-oss-specific -- other models (e.g.
            # llama-3.1-8b-instant) reject it with a 400, which would
            # otherwise permanently mark a perfectly healthy model down.
            extra_body = {"reasoning_effort": "low"} if "gpt-oss" in model else {}
            started = time.monotonic()
            try:
                await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                    extra_body=extra_body,
                )
                llm_health[model] = {
                    "ok": True,
                    "latency_s": round(time.monotonic() - started, 3),
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "error": None,
                }
                logger.info("LLM health OK (groq/%s, %.2fs)", model, llm_health[model]["latency_s"])
            except Exception as e:
                llm_health[model] = {
                    "ok": False,
                    "latency_s": None,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(e),
                }
                logger.warning("LLM health check failed for groq/%s (non-fatal): %s", model, e)
        await client.close()

    if settings.llm_provider == "openrouter" and settings.openrouter_api_key:
        from openai import AsyncOpenAI

        started = time.monotonic()
        client = AsyncOpenAI(api_key=settings.openrouter_api_key, base_url="https://openrouter.ai/api/v1")
        extra = {"reasoning_effort": "low"} if "gpt-oss" in settings.openrouter_model else {}
        key = f"openrouter/{settings.openrouter_model}"
        try:
            await client.chat.completions.create(
                model=settings.openrouter_model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                extra_body=extra,
            )
            llm_health[key] = {
                "ok": True,
                "latency_s": round(time.monotonic() - started, 3),
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "error": None,
            }
            logger.info("LLM health OK (%s, %.2fs)", key, llm_health[key]["latency_s"])
        except Exception as e:
            llm_health[key] = {
                "ok": False,
                "latency_s": None,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
            }
            logger.warning("LLM health check failed for %s (non-fatal): %s", key, e)
        await client.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(_scheduled_ical_sync, "interval", minutes=settings.ical_sync_interval_minutes, id="ical_sync")
    # Keep LLM routes warm + health-checked every 60s so demo calls with gaps
    # don't hit cold-start latency, and a rate-limited model (Groq support's
    # own guidance for a 429: switch models, or wait 24h for the per-model
    # reset -- see settings.groq_models) gets marked down and routed around
    # quickly. 60s (down from an initial 4 min) trades a modest amount of
    # extra background ping traffic for a much shorter window where a call
    # can still land on a model that's actually rate-limited.
    scheduler.add_job(_check_llm_health, "interval", seconds=60, id="llm_health_periodic")
    # Neon's default autosuspend is a few minutes of inactivity -- ping well
    # inside that window so the connection is always warm by the time a real
    # call needs it (see _check_db_health above).
    scheduler.add_job(_check_db_health, "interval", minutes=3, id="db_keepalive")
    # Once a day, "in the morning" -- 1:00 UTC is ~6:30am IST. Render runs in
    # UTC; adjust the hour here if the deploy target's timezone differs.
    scheduler.add_job(_scheduled_smart_pricing_refresh, "cron", hour=1, minute=0, id="smart_pricing_refresh")
    # Staggered 15 minutes after the job above -- separate concern (the
    # actual per-listing cache get_pricing/negotiate_rate consult, not the
    # city-wide reference number), kept as its own job rather than folded
    # into _scheduled_smart_pricing_refresh so a failure/slowdown in one
    # never affects the other.
    scheduler.add_job(
        _scheduled_live_pricing_cache_refresh, "cron", hour=1, minute=15, id="live_pricing_cache_refresh"
    )
    scheduler.start()
    asyncio.create_task(_scheduled_ical_sync())   # kick off one sync immediately, don't block startup on it
    asyncio.create_task(_check_llm_health())      # pre-warm + health-check LLM routes so first caller doesn't wait
    asyncio.create_task(_check_db_health())       # pre-warm the DB connection so the first caller doesn't wait
    asyncio.create_task(_backfill_property_display_names())  # self-heal any pre-existing NULL display_name rows
    logger.info("MIRA backend started (env=%s)", settings.environment)
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="MIRA API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(properties.router, prefix=API_PREFIX)
app.include_router(bookings.router, prefix=API_PREFIX)
app.include_router(calls.router, prefix=API_PREFIX)
app.include_router(guests.router, prefix=API_PREFIX)
app.include_router(technicians.router, prefix=API_PREFIX)
app.include_router(pricing.router, prefix=API_PREFIX)
app.include_router(analytics.router, prefix=API_PREFIX)
app.include_router(notifications.router, prefix=API_PREFIX)
app.include_router(leads.router, prefix=API_PREFIX)
app.include_router(faq.router, prefix=API_PREFIX)
app.include_router(negotiation_rules.router, prefix=API_PREFIX)
app.include_router(voice.router, prefix=API_PREFIX)
app.include_router(exotel.router, prefix=API_PREFIX)
app.include_router(whatsapp.router, prefix=API_PREFIX)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "environment": settings.environment}


@app.get(f"{API_PREFIX}/health/llm")
async def llm_health_status() -> dict:
    """Per-model health/latency from the last periodic check (see
    _check_llm_health above) -- what app/voice/pipeline.py's _build_llm() is
    actually choosing between right now. Empty until the first check runs
    (immediately at startup, then every 60 seconds)."""
    return {"models": llm_health}
