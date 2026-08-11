"""Does a claim actually remove the document from public search?

Written before any fix, to establish the current behaviour rather than assume it.
Run: docker exec seeker-backend-1 python -m pytest tests/test_probe_claim_lock.py -v
"""
from __future__ import annotations

import httpx


async def test_probe_document_visibility_after_claim_starts(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    seeker = await make_user()
    other = await make_user()

    await post_found(finder, id_number="PROBE001", holder_name="Jane Alice Doe")

    # Seeker A finds it and claims it with the full ID number.
    first = await client.post(
        "/api/search", json={"id_number": "PROBE001"}, headers=seeker.headers
    )
    match_id = first.json()["match_ids"][0]
    claim = await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "PROBE001"},
        headers=seeker.headers,
    )
    print(f"\nCLAIM STATUS: {claim.status_code} {claim.text[:200]}")

    # Now a completely unrelated user searches for the same document.
    after = await client.post(
        "/api/search", json={"id_number": "PROBE001"}, headers=other.headers
    )
    body = after.json()
    print(f"THIRD-PARTY SEARCH AFTER CLAIM: found={body['found']!r}")
    print(f"  results={len(body.get('results') or [])}")
    print(f"  match_ids={body.get('match_ids')!r}")

    # And by name.
    by_name = await client.post(
        "/api/search", json={"name": "Jane Alice Doe"}, headers=other.headers
    )
    print(f"THIRD-PARTY NAME SEARCH AFTER CLAIM: found={by_name.json()['found']!r}")

    # Can the second user also start a claim on the same document?
    if body.get("match_ids"):
        rival = body["match_ids"][0]
        rival_claim = await client.post(
            f"/api/matches/{rival}/claim",
            json={"id_number": "PROBE001"},
            headers=other.headers,
        )
        print(f"SECOND CLAIMANT CLAIM: {rival_claim.status_code} {rival_claim.text[:200]}")
