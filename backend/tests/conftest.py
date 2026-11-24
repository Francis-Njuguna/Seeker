"""Pytest fixtures: isolated DB schema per test, HTTP client, helper factories."""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest_asyncio

# Configure the app for testing before any app module imports settings.
os.environ["SEEKER_TESTING"] = "1"

# ``fresh_schema`` below drops every table between tests, so pointing this at a
# real database destroys it. docker-compose sets DATABASE_URL to the *dev*
# database for the backend service, which a plain setdefault would silently
# inherit — so redirect to a sibling ``_test`` database instead of trusting it.
# Set TEST_DATABASE_URL to override explicitly.
_configured = os.environ.get(
    "TEST_DATABASE_URL",
    os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://seeker:seeker@db:5432/seeker"
    ),
)
if not _configured.rsplit("/", 1)[-1].endswith("_test"):
    _configured = f"{_configured}_test"
os.environ["DATABASE_URL"] = _configured
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
async def promote_admin(db_session: AsyncSession):
    """Promote a user to admin.

    Admin is deliberately not self-service, so there is no endpoint for this —
    tests reach into the DB exactly as an operator would with SQL.
    """
    from sqlalchemy import update

    from app.models import User, UserRole

    async def _promote(user: UserSession) -> None:
        await db_session.execute(
            update(User).where(User.id == user.id).values(role=UserRole.ADMIN)
        )
        await db_session.commit()

    return _promote


@pytest_asyncio.fixture
async def pay_for(client: httpx.AsyncClient, promote_admin):
    """Take an approved claim through payment: initiate, then fire the webhook.

    Mirrors what the mock provider's checkout page does, so tests exercise the
    real release path rather than setting the status directly.

    Fallback claims (typed number, selfie-only) require admin review after the
    finder approves, so a claim can legitimately be parked at CLAIM_REQUESTED
    when payment is requested. When that happens this fixture completes the
    admin review exactly as the admin console would, then retries — the tests
    below are about payment, not about the review ladder.
    """
    admin_headers: dict[str, str] | None = None

    async def _ensure_admin() -> dict[str, str]:
        nonlocal admin_headers
        if admin_headers is None:
            response = await client.post(
                "/api/auth/register",
                json={
                    "email": "pay-admin@example.com",
                    "password": "correct-horse-battery",
                    "display_name": "Pay Admin",
                },
            )
            assert response.status_code == 201, response.text
            admin = UserSession(response.json())
            await promote_admin(admin)
            admin_headers = admin.headers
        return admin_headers

    async def _pay(
        match_id: str, seeker: UserSession, *, meetup_quote: bool = False
    ) -> dict:
        """Pay an approved claim.

        ``meetup_quote`` plans a free meetup fulfilment first. Escrow tests need
        it because the payment webhook only issues a receipt code when a
        delivery plan exists, and only that code releases the finder's earning.
        """
        started = await client.post(
            f"/api/payments/{match_id}/initiate",
            json={"phone": "+254700000000"},
            headers=seeker.headers,
        )
        if started.status_code == 409 and "claim_requested" in started.text:
            # Fallback claim parked for admin review after finder approval.
            # Complete the review, then pay.
            review = await client.post(
                f"/api/admin/claims/{match_id}/decision",
                json={"approve": True, "note": "Fixture: fallback claim accepted"},
                headers=await _ensure_admin(),
            )
            assert review.status_code == 200, review.text
            started = await client.post(
                f"/api/payments/{match_id}/initiate",
                json={"phone": "+254700000000"},
                headers=seeker.headers,
            )
        assert started.status_code == 200, started.text

        if meetup_quote:
            # Quoted after initiation: meetup is free, so the split is unchanged,
            # and the webhook below will issue the receipt code.
            quoted = await client.put(
                f"/api/deliveries/{match_id}/quote",
                json={
                    "method": "meetup",
                    "destination_address": "Campus Security Office",
                },
                headers=seeker.headers,
            )
            assert quoted.status_code == 200, quoted.text

        reference = started.json()["reference"]

        webhook = await client.post(
            "/api/payments/webhook", json={"reference": reference, "status": "paid"}
        )
        assert webhook.status_code == 200, webhook.text
        return {"reference": reference, **started.json(), "webhook": webhook.json()}

    return _pay


@pytest_asyncio.fixture
async def post_found(client: httpx.AsyncClient):
    """Post a found ID without an image (skips OCR, which needs a real photo).

    A date of birth is included by default: name search now requires name+DOB
    to match on both the stored record and the query, so name-based tests need
    the stored DOB to exist. A pickup address is included too, because delivery
    quotes (and therefore escrow handover) require one.
    """

    async def _post(
        user: UserSession,
        *,
        id_number: str = "AB123456",
        holder_name: str = "Jane Alice Doe",
        holder_date_of_birth: str | None = "1995-04-12",
        document_type: str = "national_id",
        found_location: str | None = "Central Station",
        pickup_address: str | None = "Campus Security Office, Gate A",
    ) -> dict:
        response = await client.post(
            "/api/found-ids",
            data={
                "document_type": document_type,
                "holder_name": holder_name,
                "id_number": id_number,
                "holder_date_of_birth": holder_date_of_birth or "",
                "found_location": found_location or "",
                "pickup_address": pickup_address or "",
                "run_ocr": "false",
            },
            headers=user.headers,
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _post
