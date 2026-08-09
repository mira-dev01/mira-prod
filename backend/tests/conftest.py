import os

os.environ["DATABASE_URL"] = "postgresql+asyncpg://mira:mira@localhost:5432/mira_test"
os.environ["ENVIRONMENT"] = "test"
os.environ["EXOTEL_WEBHOOK_TOKEN"] = "test-token"
os.environ["TWILIO_WHATSAPP_WEBHOOK_TOKEN"] = "test-token"
# Real Redis, same "no mocking, hit the real dependency" convention as
# DATABASE_URL above -- CallCoordinator's lease mechanism is now
# correctness-bearing Redis, not Postgres (see app/services/
# call_coordinator.py), so a fake/mocked client would not actually exercise
# the atomic Lua scripts this suite needs to prove correct. Requires a local
# `redis-server` running on the default port before running this suite,
# same requirement DATABASE_URL already has for Postgres.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import uuid

import pytest_asyncio
from fastapi import Depends, HTTPException, Request, status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.database import AsyncSessionLocal, Base, engine, get_db
from app.integrations import redis_client
from app.main import app
from app.models.call_session import CallSession
from app.models.property import Property
from app.models.user import User

# Test-only stand-in for a Clerk session JWT -- real verification needs a
# live network call to Clerk's JWKS endpoint, impractical (and pointless)
# for a fast local test suite. Tests instead pass the user's own id as the
# bearer token; this override looks them up directly, skipping JWT/Clerk
# entirely, but still lets different tests authenticate as different users
# (needed by the ownership/cross-user tests) the same way real tokens did.
async def _override_get_current_user(request: Request, db=Depends(get_db)) -> User:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    token = auth_header.removeprefix("Bearer ")
    try:
        user_id = uuid.UUID(token)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables():
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    yield


@pytest_asyncio.fixture(autouse=True)
async def _clean_call_leases():
    # Only call_lease:* keys, not FLUSHDB -- scoped the same way
    # _clean_tables above only deletes rows from this app's own tables,
    # never wipes the whole database. Runs before AND after each test: a
    # crashed prior test run could leave stale keys with a real (short) TTL
    # that would otherwise bleed into the next test's assertions.
    client = redis_client.get_client()
    if client is not None:
        keys = await client.keys("call_lease:*")
        if keys:
            await client.delete(*keys)
    yield
    if client is not None:
        keys = await client.keys("call_lease:*")
        if keys:
            await client.delete(*keys)


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    async def _override_get_db():
        async with AsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session):
    user = User(
        email=f"host-{uuid.uuid4().hex[:8]}@example.com",
        clerk_user_id=f"user_{uuid.uuid4().hex[:16]}",
        name="Test Host",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def auth_headers_for(user: User) -> dict:
    return {"Authorization": f"Bearer {user.id}"}


@pytest_asyncio.fixture
async def auth_headers(test_user):
    return auth_headers_for(test_user)


@pytest_asyncio.fixture
async def test_property(db_session, test_user):
    property_ = Property(
        user_id=test_user.id,
        name="Test Villa",
        city="Goa",
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}",
        base_price=4000,
        house_rules="No smoking.",
        faq=[{"question": "Is wifi free?", "answer": "Yes."}],
        amenities=["WiFi", "AC"],
        max_guests=4,
    )
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)
    return property_


@pytest_asyncio.fixture
async def test_call_session(db_session, test_property, test_user):
    session = CallSession(
        exotel_call_id=f"call-{uuid.uuid4().hex[:8]}",
        user_id=test_user.id,
        property_id=test_property.id,
        caller_number="+919999999999",
        status="in_progress",
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session
