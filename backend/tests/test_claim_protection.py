"""Claim protection: the lock, the race, and the bypass attempts.

These are the tests for the rule the whole product rests on — once a claim
starts, the document leaves public search and no one else can take it — so they
are written as adversaries rather than as happy paths. Every one of them
described real behaviour of this codebase before ``app/claims.py`` existed:

* a third party could find a document under active claim, by ID and by name;
* that third party could then claim it, giving one document two claimants;
* the direct-fetch route handed out the preview regardless of the claim;
* a claimant could reach ``/claim`` with no proof of any kind.

Run: docker exec seeker-backend-1 python -m pytest tests/test_claim_protection.py -v
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.config import settings
from app.models import (
    PUBLICLY_SEARCHABLE_FOUND_STATUSES,
    FoundId,
    FoundIdStatus,
    Match,
    MatchStatus,
    utcnow,
)


async def _start_claim(client, seeker, id_number):
    """Search, then take the lock. Returns the match id."""
    found = await client.post(
        "/api/search", json={"id_number": id_number}, headers=seeker.headers
    )
    match_id = found.json()["match_ids"][0]
    started = await client.post(
        f"/api/matches/{match_id}/start-claim", headers=seeker.headers
    )
    assert started.status_code == 200, started.text
    return match_id


# --- the document leaves public search (§2, §34) ----------------------------


async def test_a_claimed_document_disappears_from_id_search(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    claimant = await make_user()
    stranger = await make_user()
    await post_found(finder, id_number="LOCK001", holder_name="Jane Alice Doe")

    # The stranger can find it before the claim starts. Asserted so a later
    # found=False cannot be mistaken for the search being broken.
    before = await client.post(
        "/api/search", json={"id_number": "LOCK001"}, headers=stranger.headers
    )
    assert before.json()["found"] is True

    await _start_claim(client, claimant, "LOCK001")

    after = await client.post(
        "/api/search", json={"id_number": "LOCK001"}, headers=stranger.headers
    )
    body = after.json()
    # Reported exactly as a document that was never posted. A distinct "being
    # claimed" reply would confirm the document exists to a stranger.
    assert body["found"] is False
    assert body["results"] == []
    assert body["match_ids"] == []


async def test_a_claimed_document_disappears_from_name_search(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """Name search is a separate query and was a separate leak."""
    finder = await make_user()
    claimant = await make_user()
    stranger = await make_user()
    await post_found(finder, id_number="LOCK002", holder_name="Peter Kamau Njoroge")

    before = await client.post(
        "/api/search", json={"name": "Peter Kamau Njoroge"}, headers=stranger.headers
    )
    assert before.json()["found"] is True

    await _start_claim(client, claimant, "LOCK002")

    after = await client.post(
        "/api/search", json={"name": "Peter Kamau Njoroge"}, headers=stranger.headers
    )
    assert after.json()["found"] is False
    assert after.json()["results"] == []


async def test_a_claimed_document_is_not_reachable_by_direct_fetch(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """Hiding it from search is worthless if the UUID still works.

    A seeker who searched before the claim started keeps the id, so this route
    has to enforce the same rule — otherwise they can poll it and watch someone
    else's claim progress.
    """
    finder = await make_user()
    claimant = await make_user()
    stranger = await make_user()
    posted = await post_found(finder, id_number="LOCK003", holder_name="Mary Wanjiku")
    document_id = posted["id"]

    # The stranger searches first, so they legitimately hold the id.
    early = await client.post(
        "/api/search", json={"id_number": "LOCK003"}, headers=stranger.headers
    )
    assert early.json()["found"] is True
    reachable = await client.get(
        f"/api/found-ids/{document_id}", headers=stranger.headers
    )
    assert reachable.status_code == 200

    await _start_claim(client, claimant, "LOCK003")

    direct = await client.get(
        f"/api/found-ids/{document_id}", headers=stranger.headers
    )
    # 404, not 403: "forbidden" on a specific UUID confirms the document is
    # there, which is the fact being withheld.
    assert direct.status_code == 404

    blurred = await client.get(
        f"/api/found-ids/{document_id}/image/blurred", headers=stranger.headers
    )
    assert blurred.status_code == 404


# --- the claimant keeps access (§5) ----------------------------------------


async def test_the_claimant_keeps_access_while_the_document_is_hidden(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """Hiding is from the public, not from the person doing the work.

    The claimant has to read the OCR fields and submit a selfie after the lock
    is taken. If the lock cut off their own access the flow could never finish.
    """
    finder = await make_user()
    claimant = await make_user()
    posted = await post_found(finder, id_number="LOCK004", holder_name="Asha Omar")
    match_id = await _start_claim(client, claimant, "LOCK004")

    mine = await client.get("/api/matches/mine", headers=claimant.headers)
    assert mine.status_code == 200
    rows = [row for row in mine.json() if row["match"]["id"] == match_id]
    assert len(rows) == 1, "the claimant lost sight of their own claim"
    assert rows[0]["match"]["status"] == "claim_pending"
    assert rows[0]["preview"]["id"] == posted["id"]

    direct = await client.get(
        f"/api/found-ids/{posted['id']}", headers=claimant.headers
    )
    assert direct.status_code == 200


async def test_the_finder_keeps_access_while_their_document_is_claimed(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    claimant = await make_user()
    posted = await post_found(finder, id_number="LOCK005", holder_name="Ken Mutiso")
    await _start_claim(client, claimant, "LOCK005")

    direct = await client.get(f"/api/found-ids/{posted['id']}", headers=finder.headers)
    assert direct.status_code == 200
    # The finder posted it; they see it in full, not redacted.
    assert direct.json()["id_number"] == "LOCK005"


# --- one document, one claimant (§7) --------------------------------------


async def test_a_second_seeker_cannot_start_a_claim_on_a_locked_document(
    client: httpx.AsyncClient, make_user, post_found, db_session
) -> None:
    """The headline defect: two claimants on one document.

    The rival is handed a match row directly, bypassing search entirely, because
    search is only one of the ways to arrive at ``start-claim`` and the lock has
    to hold regardless of how the caller got there.
    """
    finder = await make_user()
    claimant = await make_user()
    rival = await make_user()
    posted = await post_found(finder, id_number="LOCK006", holder_name="Grace Njeri")

    await _start_claim(client, claimant, "LOCK006")

    # Fabricate the rival's match the way a pre-claim search would have.
    rival_match = Match(
        found_id_id=posted["id"],
        seeker_user_id=rival.id,
        source="search",
        matched_on="id_number",
        status=MatchStatus.MATCHED,
    )
    db_session.add(rival_match)
    await db_session.commit()

    blocked = await client.post(
        f"/api/matches/{rival_match.id}/start-claim", headers=rival.headers
    )
    assert blocked.status_code == 409, blocked.text
    assert "another" in blocked.json()["detail"].lower()

    # And the one-step path, with the correct ID number, must also be refused.
    # Knowing the number is proof of ownership, not a way past the lock.
    also_blocked = await client.post(
        f"/api/matches/{rival_match.id}/claim",
        json={"id_number": "LOCK006"},
        headers=rival.headers,
    )
    assert also_blocked.status_code == 409, also_blocked.text

    # Exactly one match holds the lock.
    holders = (
        await db_session.execute(
            select(Match)
            .where(Match.found_id_id == posted["id"])
            .where(Match.status.in_(["claim_pending", "claim_requested"]))
        )
    ).scalars().all()
    assert len(holders) == 1
    assert str(holders[0].seeker_user_id) == str(claimant.id)


async def test_two_simultaneous_claims_produce_exactly_one_winner(
    client: httpx.AsyncClient, make_user, post_found, db_session
) -> None:
    """§36: the race condition, run as a race.

    Both requests are fired with ``asyncio.gather`` so they interleave inside the
    server. The read-then-write shape this replaced would let both read
    ``posted`` and both write ``claim_requested``; the conditional UPDATE means
    the loser's WHERE clause matches nothing and it reports rowcount 0.

    Asserting "one of each" rather than "the first one wins" is deliberate —
    which request wins is genuinely nondeterministic, and a test that expected a
    particular winner would be flaky for the wrong reason.
    """
    finder = await make_user()
    a = await make_user()
    b = await make_user()
    posted = await post_found(finder, id_number="RACE001", holder_name="Sam Kiprotich")

    # Both seekers get a match row before either tries to lock.
    match_a = Match(
        found_id_id=posted["id"],
        seeker_user_id=a.id,
        source="search",
        matched_on="id_number",
        status=MatchStatus.MATCHED,
    )
    match_b = Match(
        found_id_id=posted["id"],
        seeker_user_id=b.id,
        source="search",
        matched_on="id_number",
        status=MatchStatus.MATCHED,
    )
    db_session.add_all([match_a, match_b])
    await db_session.commit()

    first, second = await asyncio.gather(
        client.post(f"/api/matches/{match_a.id}/start-claim", headers=a.headers),
        client.post(f"/api/matches/{match_b.id}/start-claim", headers=b.headers),
    )

    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 409], (
        f"expected exactly one winner, got {codes}: "
        f"{first.text[:200]} | {second.text[:200]}"
    )

    # The database agrees with the HTTP responses.
    holders = (
        await db_session.execute(
            select(Match)
            .where(Match.found_id_id == posted["id"])
            .where(Match.status == MatchStatus.CLAIM_PENDING)
        )
    ).scalars().all()
    assert len(holders) == 1

    document = await db_session.get(FoundId, posted["id"])
    await db_session.refresh(document)
    assert document.status == FoundIdStatus.CLAIM_REQUESTED
    assert document.status not in PUBLICLY_SEARCHABLE_FOUND_STATUSES


# --- proof cannot be bypassed (§24, §37) ----------------------------------


async def test_taking_the_lock_is_not_proof_of_ownership(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """§24: the lock buys time to prove, not the document.

    Starting a claim requires nothing — that is the point, since proving takes
    several requests. Finishing one requires proof, checked server-side against
    stored state.
    """
    finder = await make_user()
    claimant = await make_user()
    await post_found(finder, id_number="PROOF001", holder_name="Lucy Achieng")
    match_id = await _start_claim(client, claimant, "PROOF001")

    naked = await client.post(
        f"/api/matches/{match_id}/claim", json={}, headers=claimant.headers
    )
    assert naked.status_code == 403
    assert "prove ownership" in naked.json()["detail"].lower()

    wrong = await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "NOTTHERIGHTNUMBER"},
        headers=claimant.headers,
    )
    assert wrong.status_code == 403


async def test_a_client_asserted_verification_flag_is_ignored(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """§37: the frontend does not get a vote.

    ``ClaimRequest`` has no verification field, so these keys are dropped by
    Pydantic rather than trusted. The test exists because "trust the client"
    bugs are introduced by well-meaning additions to a schema, and this pins the
    behaviour so such an addition fails here.
    """
    finder = await make_user()
    attacker = await make_user()
    await post_found(finder, id_number="PROOF002", holder_name="David Otieno")
    match_id = await _start_claim(client, attacker, "PROOF002")

    forged = await client.post(
        f"/api/matches/{match_id}/claim",
        json={
            "verification_status": "verified",
            "face_verification_status": "verified",
            "verified": True,
            "face_verified": True,
            "status": "claimed",
        },
        headers=attacker.headers,
    )
    assert forged.status_code == 403, forged.text


async def test_another_users_match_is_not_claimable(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """Holding a match id is not authorisation to act on it."""
    finder = await make_user()
    claimant = await make_user()
    attacker = await make_user()
    await post_found(finder, id_number="PROOF003", holder_name="Ali Hassan")
    match_id = await _start_claim(client, claimant, "PROOF003")

    for path, kwargs in (
        ("start-claim", {}),
        ("claim", {"json": {"id_number": "PROOF003"}}),
        ("abandon", {}),
    ):
        response = await client.post(
            f"/api/matches/{match_id}/{path}", headers=attacker.headers, **kwargs
        )
        # 404 rather than 403 throughout: confirming the match exists would
        # confirm the document exists.
        assert response.status_code == 404, f"{path} -> {response.status_code}"


# --- a failed claim keeps the document hidden (§26) ------------------------


async def test_a_failed_selfie_does_not_return_the_document_to_search(
    client: httpx.AsyncClient, make_user, post_found, db_session
) -> None:
    """§26: a suspicious failure must not hand the document to the next person.

    The failure state is written directly rather than by submitting a mismatched
    selfie — the genuine end-to-end version of this, with real photographs of
    two different people, is in ``test_face_integration.py``. This test is about
    what the *lock* does afterwards, which is nothing: it stays held, and the
    match is flagged for a human.
    """
    finder = await make_user()
    claimant = await make_user()
    stranger = await make_user()
    posted = await post_found(finder, id_number="FAIL001", holder_name="Njeri Kariuki")
    match_id = await _start_claim(client, claimant, "FAIL001")

    await db_session.execute(
        update(Match)
        .where(Match.id == match_id)
        .values(
            face_verification_status="failed",
            face_attempts=1,
            flagged_for_review=True,
            flag_reason="Selfie did not match the photo on the posted document.",
        )
    )
    await db_session.commit()

    still_hidden = await client.post(
        "/api/search", json={"id_number": "FAIL001"}, headers=stranger.headers
    )
    assert still_hidden.json()["found"] is False

    document = await db_session.get(FoundId, posted["id"])
    await db_session.refresh(document)
    assert document.status == FoundIdStatus.CLAIM_REQUESTED


# --- expiry returns it to circulation (§6, §27) ----------------------------


async def test_an_abandoned_claim_expires_and_the_document_returns(
    client: httpx.AsyncClient, make_user, post_found, db_session
) -> None:
    """§27: enforced server-side, on read, with no scheduler required.

    The clock is wound back in the database rather than slept through, so the
    test costs milliseconds instead of the configured timeout.
    """
    finder = await make_user()
    claimant = await make_user()
    stranger = await make_user()
    posted = await post_found(finder, id_number="EXP001", holder_name="Brian Mwangi")
    match_id = await _start_claim(client, claimant, "EXP001")

    hidden = await client.post(
        "/api/search", json={"id_number": "EXP001"}, headers=stranger.headers
    )
    assert hidden.json()["found"] is False

    stale = utcnow() - timedelta(hours=settings.claim_timeout_hours + 1)
    await db_session.execute(
        update(Match).where(Match.id == match_id).values(claim_started_at=stale)
    )
    await db_session.commit()

    # The stranger's own search triggers the sweep.
    recovered = await client.post(
        "/api/search", json={"id_number": "EXP001"}, headers=stranger.headers
    )
    assert recovered.json()["found"] is True, "the document never came back"

    document = await db_session.get(FoundId, posted["id"])
    await db_session.refresh(document)
    assert document.status in PUBLICLY_SEARCHABLE_FOUND_STATUSES

    expired = await db_session.get(Match, match_id)
    await db_session.refresh(expired)
    assert expired.status == MatchStatus.EXPIRED
    # The reason is on the record rather than inferred from the status alone.
    assert expired.claim_expired_at is not None


async def test_a_live_claim_is_not_expired_by_the_sweep(
    client: httpx.AsyncClient, make_user, post_found, db_session
) -> None:
    """The sweep must not be greedy: a claim inside its window survives."""
    finder = await make_user()
    claimant = await make_user()
    stranger = await make_user()
    await post_found(finder, id_number="EXP002", holder_name="Faith Chebet")
    match_id = await _start_claim(client, claimant, "EXP002")

    recent = utcnow() - timedelta(hours=max(1, settings.claim_timeout_hours - 1))
    await db_session.execute(
        update(Match).where(Match.id == match_id).values(claim_started_at=recent)
    )
    await db_session.commit()

    for _ in range(3):
        probe = await client.post(
            "/api/search", json={"id_number": "EXP002"}, headers=stranger.headers
        )
        assert probe.json()["found"] is False

    live = await db_session.get(Match, match_id)
    await db_session.refresh(live)
    assert live.status == MatchStatus.CLAIM_PENDING


async def test_abandoning_a_claim_releases_the_document_immediately(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """§6: an honest claimant should not have to wait out the timeout."""
    finder = await make_user()
    claimant = await make_user()
    stranger = await make_user()
    await post_found(finder, id_number="EXP003", holder_name="Ruth Wafula")
    match_id = await _start_claim(client, claimant, "EXP003")

    dropped = await client.post(
        f"/api/matches/{match_id}/abandon", headers=claimant.headers
    )
    assert dropped.status_code == 200, dropped.text
    assert dropped.json()["status"] == "expired"

    back = await client.post(
        "/api/search", json={"id_number": "EXP003"}, headers=stranger.headers
    )
    assert back.json()["found"] is True

    # And the next person can genuinely take it.
    rival_match = back.json()["match_ids"][0]
    theirs = await client.post(
        f"/api/matches/{rival_match}/start-claim", headers=stranger.headers
    )
    assert theirs.status_code == 200, theirs.text


async def test_start_claim_is_idempotent_for_the_holder(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """A refreshed browser is not a conflict."""
    finder = await make_user()
    claimant = await make_user()
    await post_found(finder, id_number="IDEM001", holder_name="Tom Barasa")
    match_id = await _start_claim(client, claimant, "IDEM001")

    again = await client.post(
        f"/api/matches/{match_id}/start-claim", headers=claimant.headers
    )
    assert again.status_code == 200, again.text
    assert again.json()["match"]["status"] == "claim_pending"


# --- no parallel status systems (§35) -------------------------------------


async def test_document_and_match_status_never_disagree(
    client: httpx.AsyncClient, make_user, post_found, db_session
) -> None:
    """§35: one source of truth, checked at each step of the ladder.

    The invariant: a document is out of public search if and only if some match
    on it holds the lock. Two independent status fields will drift unless
    something asserts they move together.
    """
    finder = await make_user()
    claimant = await make_user()
    posted = await post_found(finder, id_number="SYNC001", holder_name="Eve Nasimiyu")

    async def assert_consistent(label: str) -> None:
        document = await db_session.get(FoundId, posted["id"])
        await db_session.refresh(document)
        holders = (
            await db_session.execute(
                select(Match)
                .where(Match.found_id_id == posted["id"])
                .where(
                    Match.status.in_(
                        [
                            MatchStatus.CLAIM_PENDING,
                            MatchStatus.CLAIM_REQUESTED,
                            MatchStatus.AWAITING_PAYMENT,
                            MatchStatus.CLAIMED,
                            MatchStatus.COMPLETED,
                        ]
                    )
                )
            )
        ).scalars().all()
        searchable = document.status in PUBLICLY_SEARCHABLE_FOUND_STATUSES
        assert searchable == (len(holders) == 0), (
            f"{label}: document={document.status} but {len(holders)} match(es) "
            f"hold the lock"
        )
        assert len(holders) <= 1, f"{label}: {len(holders)} simultaneous lock holders"

    await assert_consistent("after posting")

    match_id = await _start_claim(client, claimant, "SYNC001")
    await assert_consistent("after start-claim")

    claimed = await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "SYNC001"},
        headers=claimant.headers,
    )
    assert claimed.status_code == 200, claimed.text
    await assert_consistent("after claim submitted")

    approved = await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=finder.headers,
    )
    assert approved.status_code == 200, approved.text
    await assert_consistent("after finder approval")


async def test_a_declined_claim_returns_the_document_to_search(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """The finder deciding "not you" genuinely frees the document."""
    finder = await make_user()
    claimant = await make_user()
    stranger = await make_user()
    await post_found(finder, id_number="SYNC002", holder_name="Paul Kimani")
    match_id = await _start_claim(client, claimant, "SYNC002")

    await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "SYNC002"},
        headers=claimant.headers,
    )
    declined = await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": False, "note": "Different person."},
        headers=finder.headers,
    )
    assert declined.status_code == 200, declined.text

    back = await client.post(
        "/api/search", json={"id_number": "SYNC002"}, headers=stranger.headers
    )
    assert back.json()["found"] is True


# --- has_reference_face (§18) ---------------------------------------------


async def test_the_preview_says_whether_a_selfie_check_is_possible(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """§18: a boolean, so the UI does not offer a step that can only fail.

    ``post_found`` posts without an image, so there is no face on record and the
    flag must be false. The true case needs a real photograph and lives in
    ``test_face_integration.py``.
    """
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, id_number="FACE001", holder_name="Zawadi Juma")

    result = await client.post(
        "/api/search", json={"id_number": "FACE001"}, headers=seeker.headers
    )
    preview = result.json()["results"][0]
    assert preview["has_reference_face"] is False
    # And never the embedding itself, under any key.
    assert "embedding" not in result.text.lower()
