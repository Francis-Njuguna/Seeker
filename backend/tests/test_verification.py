"""Claimant-side document verification, and image access after approval.

The comparison unit tests run real Tesseract over rendered cards (see
test_ocr.py for why that is a fair test of the parsing logic). The HTTP tests
cover the gate itself: who may verify, how often, and what approval unlocks.
"""
from __future__ import annotations

import io

import httpx

from app.verification import (
    PASS_THRESHOLD,
    compare_against_record,
    _name_tokens_match,
)
from tests.test_ocr import render_card

# No module-level asyncio mark: this file mixes sync comparison tests with async
# HTTP ones, and pytest.ini already runs in asyncio auto mode.

CARD_LINES = [
    "REPUBLIC OF EXAMPLE",
    "NATIONAL IDENTITY CARD",
    "SURNAME: DOE",
    "GIVEN NAMES: JANE ALICE",
    "ID NO: AB123456",
]

STUDENT_CARD_LINES = [
    "EXAMPLE UNIVERSITY",
    "STUDENT ID CARD",
    "NAME: JANE ALICE DOE",
    "REG NO: SC123456",
]


def _png(lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    render_card(lines).save(buffer, format="PNG")
    return buffer.getvalue()


# --- name comparison --------------------------------------------------------


def test_name_match_ignores_token_order() -> None:
    assert _name_tokens_match("DOE JANE", "Jane Doe")


def test_name_match_tolerates_a_dropped_middle_name() -> None:
    """OCR often misses a middle name depending on card layout."""
    assert _name_tokens_match("Jane Doe", "Jane Alice Doe")


def test_name_match_rejects_a_different_person() -> None:
    assert not _name_tokens_match("John Smith", "Jane Alice Doe")


def test_name_match_rejects_empty_extraction() -> None:
    assert not _name_tokens_match("", "Jane Alice Doe")


# --- comparison against the record -----------------------------------------


def test_matching_document_passes() -> None:
    outcome = compare_against_record(
        render_card(CARD_LINES),
        expected_id_number="AB123456",
        expected_holder_name="Jane Alice Doe",
    )
    assert outcome.passed
    assert outcome.id_number_matched
    assert outcome.score >= PASS_THRESHOLD


def test_wrong_id_number_fails_even_when_the_name_matches() -> None:
    """Names collide; the number is what decides. Name-only must not pass."""
    outcome = compare_against_record(
        render_card(CARD_LINES),
        expected_id_number="ZZ999999",
        expected_holder_name="Jane Alice Doe",
    )
    assert not outcome.passed
    assert not outcome.id_number_matched


def test_unrelated_document_fails() -> None:
    outcome = compare_against_record(
        render_card(["SHOPPING LIST", "MILK", "BREAD"]),
        expected_id_number="AB123456",
        expected_holder_name="Jane Alice Doe",
    )
    assert not outcome.passed
    assert outcome.score == 0.0
    assert outcome.readable


def test_unreadable_document_is_inconclusive() -> None:
    outcome = compare_against_record(
        render_card([]),
        expected_id_number="AB123456",
        expected_holder_name="Jane Alice Doe",
    )
    assert not outcome.passed
    assert not outcome.readable


def test_student_id_verifies_against_its_own_number() -> None:
    outcome = compare_against_record(
        render_card(STUDENT_CARD_LINES),
        expected_id_number="SC123456",
        expected_holder_name="Jane Alice Doe",
    )
    assert outcome.passed
    assert outcome.document_type == "student_id"


# --- HTTP flow --------------------------------------------------------------


async def _matched_pair(client, make_user, post_found, id_number="AB123456"):
    finder = await make_user()
    seeker = await make_user()
    found = await post_found(finder, id_number=id_number)
    search = await client.post(
        "/api/search", json={"id_number": id_number}, headers=seeker.headers
    )
    return finder, seeker, found, search.json()["match_ids"][0]


async def _verify(client, match_id, seeker, lines=CARD_LINES):
    return await client.post(
        f"/api/matches/{match_id}/verify-identity",
        files={"image": ("id.png", _png(lines), "image/png")},
        headers=seeker.headers,
    )


async def test_verification_lets_a_seeker_claim_without_the_number(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """The whole point: upload your own copy instead of typing the number."""
    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)

    verified = await _verify(client, match_id, seeker)
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified"] is True

    claim = await client.post(
        f"/api/matches/{match_id}/claim", json={}, headers=seeker.headers
    )
    assert claim.status_code == 200, claim.text
    body = claim.json()
    assert body["status"] == "claim_requested"
    assert body["verification_method"] == "document_image"


async def test_claim_without_number_or_verification_is_refused(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    response = await client.post(
        f"/api/matches/{match_id}/claim", json={}, headers=seeker.headers
    )
    assert response.status_code == 403


async def test_failed_verification_does_not_unlock_a_claim(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)

    failed = await _verify(client, match_id, seeker, ["SHOPPING LIST", "MILK"])
    assert failed.json()["verified"] is False

    response = await client.post(
        f"/api/matches/{match_id}/claim", json={}, headers=seeker.headers
    )
    assert response.status_code == 403


async def test_typed_number_still_works_and_is_recorded_as_such(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    response = await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456"},
        headers=seeker.headers,
    )
    assert response.json()["verification_method"] == "id_number_typed"


async def test_stranger_cannot_verify_someone_elses_match(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, _, _, match_id = await _matched_pair(client, make_user, post_found)
    attacker = await make_user()
    response = await _verify(client, match_id, attacker)
    assert response.status_code == 404


async def test_verification_attempts_are_capped(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """Without a cap, a claimant could retry until OCR misread one into a pass."""
    from app.verification import MAX_ATTEMPTS

    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)

    for _ in range(MAX_ATTEMPTS):
        response = await _verify(client, match_id, seeker, ["SHOPPING LIST", "MILK"])
        assert response.status_code == 200

    blocked = await _verify(client, match_id, seeker, ["SHOPPING LIST", "MILK"])
    assert blocked.status_code == 429


async def test_verification_rejects_a_non_image_upload(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, seeker, _, match_id = await _matched_pair(client, make_user, post_found)
    response = await client.post(
        f"/api/matches/{match_id}/verify-identity",
        files={"image": ("x.png", b"not an image", "image/png")},
        headers=seeker.headers,
    )
    assert response.status_code == 400


async def test_verification_does_not_apply_after_the_claim_is_approved(
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
    response = await _verify(client, match_id, seeker)
    assert response.status_code == 409
