"""Registration, login, and token handling."""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_register_returns_token_and_user(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register",
        json={
            "email": "finder@example.com",
            "password": "correct-horse-battery",
            "display_name": "Finder One",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["access_token"]
    assert body["user"]["email"] == "finder@example.com"
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]


async def test_duplicate_email_is_rejected(client: httpx.AsyncClient, make_user) -> None:
    user = await make_user(email="dupe@example.com")
    response = await client.post(
        "/api/auth/register",
        json={
            "email": user.email,
            "password": "another-password-x",
            "display_name": "Impostor",
        },
    )
    assert response.status_code == 409


async def test_login_succeeds_with_correct_password(
    client: httpx.AsyncClient, make_user
) -> None:
    user = await make_user()
    response = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_login_fails_with_wrong_password(
    client: httpx.AsyncClient, make_user
) -> None:
    user = await make_user()
    response = await client.post(
        "/api/auth/login", json={"email": user.email, "password": "wrong-password"}
    )
    assert response.status_code == 401


async def test_login_does_not_reveal_whether_email_exists(
    client: httpx.AsyncClient, make_user
) -> None:
    user = await make_user()
    known = await client.post(
        "/api/auth/login", json={"email": user.email, "password": "wrong-password"}
    )
    unknown = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "wrong-password"},
    )
    assert known.status_code == unknown.status_code == 401
    assert known.json()["detail"] == unknown.json()["detail"]


async def test_protected_route_requires_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_protected_route_rejects_garbage_token(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


async def test_me_returns_current_user(client: httpx.AsyncClient, make_user) -> None:
    user = await make_user()
    response = await client.get("/api/auth/me", headers=user.headers)
    assert response.status_code == 200
    assert response.json()["id"] == user.id


async def test_short_password_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"email": "weak@example.com", "password": "short", "display_name": "Weak"},
    )
    assert response.status_code == 422
