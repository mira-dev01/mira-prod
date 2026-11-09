from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.observability import db_deadlock

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle_seconds,
    future=True,
    connect_args={"ssl": True} if settings.database_requires_ssl else {},
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Deadlocks are reported (with the writes they rolled back) to the admins; this only
# observes -- see app/observability/db_deadlock.py.
db_deadlock.install(engine)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


def pool_stats() -> dict[str, int]:
    """Point-in-time connection-pool usage, for the periodic DB health check
    (app.main._check_db_health). Observation only."""
    pool = engine.pool
    capacity = settings.db_pool_size + settings.db_max_overflow
    return {"checked_out": pool.checkedout(), "overflow": max(pool.overflow(), 0), "capacity": capacity}
