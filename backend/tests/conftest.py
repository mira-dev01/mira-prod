import os

os.environ["DATABASE_URL"] = "postgresql+asyncpg://mira:mira@localhost:5432/mira_test"
os.environ["ENVIRONMENT"] = "test"
os.environ["VAPI_WEBHOOK_SECRET"] = "test-secret"
os.environ["EXOTEL_WEBHOOK_TOKEN"] = "test-token"

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.security import create_access_token, hash_password
from app.database import AsyncSessionLocal, Base, engine, get_db
from app.main import app
from app.models.property import Property
from app.models.user import User


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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session):
    user = User(
        email=f"host-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("testpass123"),
        name="Test Host",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_headers(test_user):
    token = create_access_token(test_user.id)
    return {"Authorization": f"Bearer {token}"}


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
