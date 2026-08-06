"""Finder flow and the alert-driven no-match flow, including notifications."""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_post_found_id_returns_full_record_to_finder(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    found = await post_found(finder, id_number="AB123456", holder_name="Jane Alice Doe")
    # The finder sees unredacted values; only seekers get previews.
    assert found["id_number"] == "AB123456"
    assert found["holder_name"] == "Jane Alice Doe"
    assert found["status"] == "posted"


async def test_post_found_id_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/found-ids",
        data={
            "document_type": "national_id",
            "holder_name": "Jane Doe",
            "id_number": "AB123456",
        },
    )
    assert response.status_code == 401


async def test_too_short_id_number_is_rejected(
    client: httpx.AsyncClient, make_user
) -> None:
    finder = await make_user()
    response = await client.post(
        "/api/found-ids",
        data={
            "document_type": "national_id",
            "holder_name": "Jane Doe",
            "id_number": "--",
            "run_ocr": "false",
        },
        headers=finder.headers,
    )
    assert response.status_code in (400, 422)


async def test_finder_can_list_own_postings(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    other = await make_user()
    await post_found(finder, id_number="AB123456")
    await post_found(other, id_number="CD654321")

    response = await client.get("/api/found-ids/mine", headers=finder.headers)
    body = response.json()
    assert len(body) == 1
    assert body[0]["id_number"] == "AB123456"


async def test_seeker_gets_redacted_view_of_found_record(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    seeker = await make_user()
    found = await post_found(finder, id_number="AB123456", holder_name="Jane Alice Doe")

    response = await client.get(f"/api/found-ids/{found['id']}", headers=seeker.headers)
    body = response.json()
    assert "id_number_redacted" in body
    assert "id_number" not in body
    assert "AB123456" not in response.text


# --- lost alerts / no-match flow -------------------------------------------


async def test_register_lost_alert(client: httpx.AsyncClient, make_user) -> None:
    seeker = await make_user()
    response = await client.post(
        "/api/lost-alerts",
        json={"holder_name": "Jane Alice Doe", "id_number": "AB123456"},
        headers=seeker.headers,
    )
    assert response.status_code == 201
    assert response.json()["status"] == "active"


async def test_alert_fires_when_matching_id_is_posted_later(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """The core no-match flow: alert first, document posted afterwards."""
    seeker = await make_user()
    finder = await make_user()

    await client.post(
        "/api/lost-alerts",
        json={"holder_name": "Jane Alice Doe", "id_number": "AB123456"},
        headers=seeker.headers,
    )
    await post_found(finder, id_number="AB123456", holder_name="Jane Alice Doe")

    alerts = await client.get("/api/lost-alerts", headers=seeker.headers)
    assert alerts.json()[0]["status"] == "matched"

    matches = await client.get("/api/matches/mine", headers=seeker.headers)
    body = matches.json()
    assert len(body) == 1
    assert body[0]["match"]["source"] == "alert_auto"


async def test_alert_notification_is_created_and_redacted(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    seeker = await make_user()
    finder = await make_user()

    await client.post(
        "/api/lost-alerts",
        json={"holder_name": "Jane Alice Doe", "id_number": "AB123456"},
        headers=seeker.headers,
    )
    await post_found(finder, id_number="AB123456")

    response = await client.get("/api/notifications", headers=seeker.headers)
    notifications = response.json()
    assert len(notifications) == 1
    assert notifications[0]["kind"] == "alert_matched"
    # Even the notification body must not carry the full ID number.
    assert "AB123456" not in notifications[0]["body"]
    assert "56" in notifications[0]["body"]

    unread = await client.get("/api/notifications/unread-count", headers=seeker.headers)
    assert unread.json()["unread"] == 1


async def test_alert_does_not_fire_on_different_id(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    seeker = await make_user()
    finder = await make_user()

    await client.post(
        "/api/lost-alerts",
        json={"holder_name": "Jane Alice Doe", "id_number": "AB123456"},
        headers=seeker.headers,
    )
    await post_found(finder, id_number="AB123457")

    matches = await client.get("/api/matches/mine", headers=seeker.headers)
    assert matches.json() == []
    notifications = await client.get("/api/notifications", headers=seeker.headers)
    assert notifications.json() == []


async def test_alert_matches_existing_found_id_immediately(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """Document posted first, alert registered afterwards."""
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, id_number="AB123456")

    response = await client.post(
        "/api/lost-alerts",
        json={"holder_name": "Jane Alice Doe", "id_number": "AB123456"},
        headers=seeker.headers,
    )
    assert response.json()["status"] == "matched"

    matches = await client.get("/api/matches/mine", headers=seeker.headers)
    assert len(matches.json()) == 1


async def test_alert_ignores_finders_own_posting(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    user = await make_user()
    await client.post(
        "/api/lost-alerts",
        json={"holder_name": "Jane Alice Doe", "id_number": "AB123456"},
        headers=user.headers,
    )
    await post_found(user, id_number="AB123456")

    matches = await client.get("/api/matches/mine", headers=user.headers)
    assert matches.json() == []


async def test_duplicate_alert_is_idempotent(
    client: httpx.AsyncClient, make_user
) -> None:
    seeker = await make_user()
    payload = {"holder_name": "Jane Alice Doe", "id_number": "AB123456"}
    first = await client.post("/api/lost-alerts", json=payload, headers=seeker.headers)
    second = await client.post("/api/lost-alerts", json=payload, headers=seeker.headers)
    assert first.json()["id"] == second.json()["id"]

    listing = await client.get("/api/lost-alerts", headers=seeker.headers)
    assert len(listing.json()) == 1


async def test_alert_normalizes_id_number(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    seeker = await make_user()
    finder = await make_user()
    await client.post(
        "/api/lost-alerts",
        json={"holder_name": "Jane Alice Doe", "id_number": "ab-123 456"},
        headers=seeker.headers,
    )
    await post_found(finder, id_number="AB123456")

    matches = await client.get("/api/matches/mine", headers=seeker.headers)
    assert len(matches.json()) == 1


async def test_cancelled_alert_does_not_fire(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    seeker = await make_user()
    finder = await make_user()
    created = await client.post(
        "/api/lost-alerts",
        json={"holder_name": "Jane Alice Doe", "id_number": "AB123456"},
        headers=seeker.headers,
    )
    alert_id = created.json()["id"]
    await client.delete(f"/api/lost-alerts/{alert_id}", headers=seeker.headers)

    await post_found(finder, id_number="AB123456")

    matches = await client.get("/api/matches/mine", headers=seeker.headers)
    assert matches.json() == []


async def test_alert_notifies_multiple_waiting_seekers(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    seeker_a = await make_user()
    seeker_b = await make_user()
    finder = await make_user()

    for seeker in (seeker_a, seeker_b):
        await client.post(
            "/api/lost-alerts",
            json={"holder_name": "Jane Alice Doe", "id_number": "AB123456"},
            headers=seeker.headers,
        )
    await post_found(finder, id_number="AB123456")

    for seeker in (seeker_a, seeker_b):
        matches = await client.get("/api/matches/mine", headers=seeker.headers)
        assert len(matches.json()) == 1


async def test_user_cannot_cancel_another_users_alert(
    client: httpx.AsyncClient, make_user
) -> None:
    owner = await make_user()
    attacker = await make_user()
    created = await client.post(
        "/api/lost-alerts",
        json={"holder_name": "Jane Alice Doe", "id_number": "AB123456"},
        headers=owner.headers,
    )
    response = await client.delete(
        f"/api/lost-alerts/{created.json()['id']}", headers=attacker.headers
    )
    assert response.status_code == 404
