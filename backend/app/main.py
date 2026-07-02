import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    analytics,
    auth,
    bookings,
    calls,
    faq,
    guests,
    leads,
    notifications,
    pricing,
    properties,
    technicians,
    voice,
)
from app.api.v1.webhooks import exotel
from app.config import settings
from app.database import AsyncSessionLocal
from app.services.calendar_service import sync_all_properties

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _scheduled_ical_sync() -> None:
    async with AsyncSessionLocal() as db:
        results = await sync_all_properties(db)
        if results:
            logger.info("iCal sync complete: %s", results)


async def _warmup_llm() -> None:
    # OpenRouter (and Groq) cold-starts its routing to the model backend on
    # the first request after idle -- on Render this adds 5-8s to the first
    # real call. Fire a 1-token ping at startup so the route is warm before
    # any caller arrives. Failures are non-fatal; the app still starts.
    try:
        if settings.llm_provider == "openrouter" and settings.openrouter_api_key:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=settings.openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
            )
            extra = {"reasoning_effort": "low"} if "gpt-oss" in settings.openrouter_model else {}
            await client.chat.completions.create(
                model=settings.openrouter_model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                extra_body=extra,
            )
            await client.close()
            logger.info("LLM warmup complete (openrouter/%s)", settings.openrouter_model)
        elif settings.llm_provider == "groq" and settings.groq_api_key:
            from groq import AsyncGroq
            client = AsyncGroq(api_key=settings.groq_api_key)
            await client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                extra_body={"reasoning_effort": "low"},
            )
            await client.close()
            logger.info("LLM warmup complete (groq/%s)", settings.groq_model)
    except Exception as e:
        logger.warning("LLM warmup failed (non-fatal): %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(_scheduled_ical_sync, "interval", minutes=settings.ical_sync_interval_minutes, id="ical_sync")
    # Keep LLM route warm every 4 minutes so demo calls with gaps don't hit cold-start latency.
    scheduler.add_job(_warmup_llm, "interval", minutes=4, id="llm_warmup_periodic")
    scheduler.start()
    asyncio.create_task(_scheduled_ical_sync())  # kick off one sync immediately, don't block startup on it
    asyncio.create_task(_warmup_llm())           # pre-warm LLM route so first caller doesn't wait 8s
    logger.info("MIRA backend started (env=%s)", settings.environment)
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="MIRA API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
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
app.include_router(voice.router, prefix=API_PREFIX)
app.include_router(exotel.router, prefix=API_PREFIX)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "environment": settings.environment}
