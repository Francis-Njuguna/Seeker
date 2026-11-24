"""Seeker flow: exact-match search returns redacted previews, never raw fields."""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_search_by_exact_id_number_finds_document(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, id_number="AB123456", holder_name="Jane Alice Doe")

    response = await client.post(
        "/api/search", json={"id_number": "AB123456"}, headers=seeker.headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert body["matched_on"] == "id_number"
    assert len(body["results"]) == 1
    assert len(body["match_ids"]) == 1


async def test_search_preview_is_redacted(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, id_number="AB123456", holder_name="Jane Alice Doe")

    response = await client.post(
        "/api/search", json={"id_number": "AB123456"}, headers=seeker.headers
    )
    preview = response.json()["results"][0]

    assert preview["id_number_redacted"] == "******56"
    assert preview["holder_name_redacted"] == "J*** A**** D**"
    # The raw fields must not appear anywhere in the payload.
    raw = response.text
    assert "AB123456" not in raw
    assert "Jane" not in raw
    assert "id_number" not in preview
    assert "holder_name" not in preview


async def test_search_normalizes_formatting_differences(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, id_number="AB123456")

    for variant in ("ab123456", "AB-123-456", "  ab 123 456  "):
        response = await client.post(
            "/api/search", json={"id_number": variant}, headers=seeker.headers
        )
        assert response.json()["found"] is True, f"failed for {variant!r}"


async def test_search_does_not_fuzzy_match(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """One character off must not match. Exact-only is a hard requirement."""
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, id_number="AB123456")

    for near_miss in ("AB123457", "AB12345", "AB1234567", "AC123456"):
        response = await client.post(
            "/api/search", json={"id_number": near_miss}, headers=seeker.headers
        )
        assert response.json()["found"] is False, f"unexpectedly matched {near_miss!r}"


async def test_search_by_exact_name_finds_document(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, holder_name="Jane Alice Doe")

    response = await client.post(
        "/api/search",
        json={"name": "jane alice doe", "date_of_birth": "1995-04-12"},
        headers=seeker.headers,
    )
    body = response.json()
    assert body["found"] is True
    assert body["matched_on"] == "name_and_date_of_birth"
    # Name+DOB is specific enough to open a claimable match. (The old
    # name-only search deliberately returned a teaser with no match id.)
    assert len(body["match_ids"]) == 1


async def test_partial_name_does_not_match(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, holder_name="Jane Alice Doe")

    response = await client.post(
        "/api/search",
        json={"name": "Jane Doe", "date_of_birth": "1995-04-12"},
        headers=seeker.headers,
    )
    assert response.json()["found"] is False


async def test_no_match_suggests_registering_an_alert(
    client: httpx.AsyncClient, make_user
) -> None:
    seeker = await make_user()
    response = await client.post(
        "/api/search", json={"id_number": "ZZ999999"}, headers=seeker.headers
    )
    body = response.json()
    assert body["found"] is False
    assert body["results"] == []
    assert "alert" in body["suggestion"].lower()


async def test_search_requires_a_query_field(
    client: httpx.AsyncClient, make_user
) -> None:
    seeker = await make_user()
    response = await client.post("/api/search", json={}, headers=seeker.headers)
    assert response.status_code == 400


async def test_search_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/search", json={"id_number": "AB123456"})
    assert response.status_code == 401


async def test_repeated_search_reuses_one_match(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, id_number="AB123456")

    first = await client.post(
        "/api/search", json={"id_number": "AB123456"}, headers=seeker.headers
    )
    second = await client.post(
        "/api/search", json={"id_number": "AB123456"}, headers=seeker.headers
    )
    assert first.json()["match_ids"] == second.json()["match_ids"]

    matches = await client.get("/api/matches/mine", headers=seeker.headers)
    assert len(matches.json()) == 1


async def test_search_is_rate_limited(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """ID-number search is an enumeration oracle, so it must be throttled."""
    seeker = await make_user()
    statuses = set()
    for i in range(40):
        response = await client.post(
            "/api/search", json={"id_number": f"ZZ{i:06d}"}, headers=seeker.headers
        )
        statuses.add(response.status_code)
    assert 429 in statuses
