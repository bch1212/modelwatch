"""Shared test fixtures — in-memory SQLite for fast tests."""

import os
import asyncio

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Generate a test encryption key before importing app modules
TEST_FERNET_KEY = Fernet.generate_key().decode()

os.environ.update({
    "DATABASE_URL": "sqlite+aiosqlite:///",
    "ENCRYPTION_KEY": TEST_FERNET_KEY,
    "STRIPE_SECRET_KEY": "sk_test_fake",
    "STRIPE_WEBHOOK_SECRET": "whsec_fake",
    "SENDGRID_API_KEY": "",
})

from app.config import get_settings  # noqa: E402
get_settings.cache_clear()

from app.models.database import Base, get_db  # noqa: E402
import app.models.database as db_module  # noqa: E402

test_engine = create_async_engine("sqlite+aiosqlite:///", echo=False)
test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

db_module._engine = test_engine
db_module._async_session = test_session_factory

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session():
    async with test_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    """Test client with DB override."""
    from httpx import AsyncClient, ASGITransport

    async def override_get_db():
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(client):
    """Test client with a signed-up workspace and auth header pre-set."""
    resp = await client.post("/api/auth/signup", json={
        "email": "test@example.com",
        "workspace_name": "Test WS",
    })
    assert resp.status_code == 201
    api_key = resp.json()["api_key"]
    assert api_key, "SendGrid disabled in tests, key should be returned in response"
    client.headers["Authorization"] = f"Bearer {api_key}"
    return client
