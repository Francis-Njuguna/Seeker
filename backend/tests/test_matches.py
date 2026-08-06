"""Claim lifecycle and the status ladder that payments will later hook into."""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _matched_pair(client, make_user, post_found, id_number="AB123456"):
    """Finder posts, seeker searches, returns (finder, seeker, found, match_id)."""
    finder = await make_user()
    seeker = await make_user()
    found = await post_found(finder, id_number=id_number)
    search = await client.post(
        "/api/search", json={"id_number": id_number}, headers=seeker.headers
    )
    return finder, seeker, found, search.json()["match_ids"][0]


async def test_claim_requires_full_id_number(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """The preview shows only the last two chars, so a guess must fail."""
    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)

    response = await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "999956"},
        headers=seeker.headers,
    )
    assert response.status_code == 403


async def test_claim_succeeds_with_correct_id_number(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)

    response = await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456", "message": "Lost it on the train."},
        headers=seeker.headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "claim_requested"


async def test_claim_notifies_the_finder(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456"},
        headers=seeker.headers,
    )

    response = await client.get("/api/notifications", headers=finder.headers)
    kinds = [n["kind"] for n in response.json()]
    assert "claim_requested" in kinds


async def test_finder_sees_incoming_claim(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456"},
        headers=seeker.headers,
    )

    response = await client.get("/api/matches/incoming", headers=finder.headers)
    body = response.json()
    assert len(body) == 1
    assert body[0]["match"]["id"] == match_id


async def test_approval_advances_status_to_claimed(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder, seeker, found, match_id = await _matched_pair(client, make_user, post_found)
    await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456"},
        headers=seeker.headers,
    )
    response = await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=finder.headers,
    )
    assert response.json()["status"] == "claimed"

    # The found record tracks the same lifecycle.
    record = await client.get(f"/api/found-ids/{found['id']}", headers=finder.headers)
    assert record.json()["status"] == "claimed"


async def test_seeker_sees_full_record_only_after_approval(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder, seeker, found, match_id = await _matched_pair(client, make_user, post_found)

    before = await client.get(f"/api/found-ids/{found['id']}", headers=seeker.headers)
    assert "id_number" not in before.json()

    await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456"},
        headers=seeker.headers,
    )
    await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=finder.headers,
    )

    after = await client.get(f"/api/found-ids/{found['id']}", headers=seeker.headers)
    assert after.json()["id_number"] == "AB123456"


async def test_rejection_returns_document_to_the_pool(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder, seeker, found, match_id = await _matched_pair(client, make_user, post_found)
    await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456"},
        headers=seeker.headers,
    )
    response = await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": False, "note": "Photo does not match."},
        headers=finder.headers,
    )
    assert response.json()["status"] == "rejected"

    # Still discoverable by a different seeker.
    other = await make_user()
    search = await client.post(
        "/api/search", json={"id_number": "AB123456"}, headers=other.headers
    )
    assert search.json()["found"] is True


async def test_only_finder_can_decide_a_claim(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456"},
        headers=seeker.headers,
    )
    # The seeker must not be able to approve their own claim.
    response = await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=seeker.headers,
    )
    assert response.status_code == 404


async def test_stranger_cannot_claim_someone_elses_match(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, _, _, match_id = await _matched_pair(client, make_user, post_found)
    attacker = await make_user()
    response = await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456"},
        headers=attacker.headers,
    )
    assert response.status_code == 404


async def test_decision_requires_a_pending_claim(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder, _, _, match_id = await _matched_pair(client, make_user, post_found)
    response = await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=finder.headers,
    )
    assert response.status_code == 409


async def test_full_lifecycle_reaches_completed(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """matched -> claim_requested -> claimed -> completed."""
    finder, seeker, found, match_id = await _matched_pair(client, make_user, post_found)

    await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456"},
        headers=seeker.headers,
    )
    await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=finder.headers,
    )
    response = await client.post(
        f"/api/matches/{match_id}/complete", headers=seeker.headers
    )
    assert response.json()["status"] == "completed"

    record = await client.get(f"/api/found-ids/{found['id']}", headers=finder.headers)
    assert record.json()["status"] == "returned"


async def test_claimed_document_leaves_the_search_pool(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456"},
        headers=seeker.headers,
    )
    await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=finder.headers,
    )

    other = await make_user()
    search = await client.post(
        "/api/search", json={"id_number": "AB123456"}, headers=other.headers
    )
    assert search.json()["found"] is False


async def test_match_status_vocabulary_reserves_a_payment_state() -> None:
    """Payments can be added between claim_requested and claimed later."""
    from app.models import MatchStatus

    assert MatchStatus.AWAITING_PAYMENT == "awaiting_payment"
    ladder = [s.value for s in MatchStatus]
    assert ladder.index("claim_requested") < ladder.index("awaiting_payment")
    assert ladder.index("awaiting_payment") < ladder.index("claimed")
