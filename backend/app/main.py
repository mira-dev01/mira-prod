import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import analytics, auth, bookings, calls, guests, notifications, pricing, properties, technicians
from app.api.v1.webhooks import exotel, vapi
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(_scheduled_ical_sync, "interval", minutes=settings.ical_sync_interval_minutes, id="ical_sync")
    scheduler.start()
    asyncio.create_task(_scheduled_ical_sync())  # kick off one sync immediately, don't block startup on it
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
app.include_router(vapi.router, prefix=API_PREFIX)
app.include_router(exotel.router, prefix=API_PREFIX)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "environment": settings.environment}
