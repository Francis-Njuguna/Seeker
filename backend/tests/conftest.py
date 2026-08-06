"""Pytest fixtures: isolated DB schema per test, HTTP client, helper factories."""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest_asyncio

# Configure the app for testing before any app module imports settings.
os.environ["SEEKER_TESTING"] = "1"
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://seeker:seeker@db:5432/seeker_test"
)
os.environ.setdefault("UPLOAD_DIR", "/tmp/seeker-test-uploads")
os.environ.setdefault("JWT_SECRET", "test-secret")

import httpx  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from app.database import Base, engine, get_db  # noqa: E402
from app.deps import search_limiter  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def fresh_schema() -> AsyncGenerator[None, None]:
    """Drop and recreate every table between tests.

    Matching is stateful (alerts fire once, matches are idempotent), so tests
    must not inherit rows from each other.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    search_limiter.reset()
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """HTTP client bound to the app in-process; schema is managed above."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


class UserSession:
    """A registered user plus its auth header."""

    def __init__(self, data: dict) -> None:
        self.id = data["user"]["id"]
        self.email = data["user"]["email"]
        self.display_name = data["user"]["display_name"]
        self.token = data["access_token"]

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@pytest_asyncio.fixture
async def make_user(client: httpx.AsyncClient):
    counter = {"n": 0}

    async def _make(email: str | None = None, name: str | None = None) -> UserSession:
        counter["n"] += 1
        n = counter["n"]
        response = await client.post(
            "/api/auth/register",
            json={
                "email": email or f"user{n}@example.com",
                "password": "correct-horse-battery",
                "display_name": name or f"User {n}",
            },
        )
        assert response.status_code == 201, response.text
        return UserSession(response.json())

    return _make


@pytest_asyncio.fixture
async def post_found(client: httpx.AsyncClient):
    """Post a found ID without an image (skips OCR, which needs a real photo)."""

    async def _post(
        user: UserSession,
        *,
        id_number: str = "AB123456",
        holder_name: str = "Jane Alice Doe",
        document_type: str = "national_id",
        found_location: str | None = "Central Station",
    ) -> dict:
        response = await client.post(
            "/api/found-ids",
            data={
                "document_type": document_type,
                "holder_name": holder_name,
                "id_number": id_number,
                "found_location": found_location or "",
                "run_ocr": "false",
            },
            headers=user.headers,
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _post
