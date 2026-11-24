"""Finder earnings: what you posted, what paid out, and nothing anyone else's.

No admin rights are involved here — every query is scoped to the caller as
finder, so the interesting tests are the ones proving a finder cannot see
another finder's rows.
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _paid_claim(client, make_user, post_found, pay_for, *, finder=None,
                      id_number="AB123456"):
    """Take one document from posted to paid. Returns (finder, seeker, found, match).

    A meetup fulfilment is planned along the way: the webhook only issues a
    receipt code when a delivery plan exists, and only that code releases the
    finder's earning from escrow.
    """
    finder = finder or await make_user()
    seeker = await make_user()
    found = await post_found(finder, id_number=id_number)
    search = await client.post(
        "/api/search", json={"id_number": id_number}, headers=seeker.headers
    )
    match_id = search.json()["match_ids"][0]

    await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": id_number},
        headers=seeker.headers,
    )
    await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=finder.headers,
    )
    await pay_for(match_id, seeker, meetup_quote=True)
    return finder, seeker, found, match_id


async def _confirm_handover(client, finder, seeker, match_id):
    """Release escrow: the owner shares the receipt code, the finder confirms it.

    Payment alone leaves the finder's share held. Only after this step does the
    earning exist, and the document flips to ``returned``.
    """
    delivery = await client.get(f"/api/deliveries/{match_id}", headers=seeker.headers)
    assert delivery.status_code == 200, delivery.text
    code = delivery.json()["handover_code"]
    assert code, "no receipt code issued for the delivery"
    confirmed = await client.post(
        f"/api/deliveries/{match_id}/confirm",
        json={"code": code},
        headers=finder.headers,
    )
    assert confirmed.status_code == 200, confirmed.text


# --- summary ----------------------------------------------------------------


async def test_a_new_finder_is_owed_nothing(
    client: httpx.AsyncClient, make_user
) -> None:
    user = await make_user()
    body = (await client.get("/api/earnings/summary", headers=user.headers)).json()

    assert body["total_earned"] == 0
    assert body["pending"] == 0
    assert body["documents_posted"] == 0
    assert body["documents_claimed"] == 0
    assert body["currency"] == "KES"


async def test_earnings_require_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/earnings/summary")).status_code == 401
    assert (await client.get("/api/earnings/documents")).status_code == 401


async def test_a_paid_claim_credits_the_finder_half_the_fee(
    client: httpx.AsyncClient, make_user, post_found, pay_for
) -> None:
    finder, seeker, _, match_id = await _paid_claim(
        client, make_user, post_found, pay_for
    )
    # Escrow: the webhook holds the share; the receipt-code handover releases it.
    await _confirm_handover(client, finder, seeker, match_id)

    body = (await client.get("/api/earnings/summary", headers=finder.headers)).json()
    assert body["total_earned"] == 10000  # 100.00 KES
    assert body["documents_posted"] == 1
    assert body["documents_returned"] == 1
    assert body["pending"] == 0


async def test_earnings_accumulate_across_documents(
    client: httpx.AsyncClient, make_user, post_found, pay_for
) -> None:
    finder = await make_user()
    for number in ("AB111111", "AB222222", "AB333333"):
        _, seeker, _, match_id = await _paid_claim(
            client, make_user, post_found, pay_for, finder=finder, id_number=number
        )
        await _confirm_handover(client, finder, seeker, match_id)

    body = (await client.get("/api/earnings/summary", headers=finder.headers)).json()
    assert body["total_earned"] == 30000
    assert body["documents_posted"] == 3
    assert body["documents_returned"] == 3


async def test_approved_but_unpaid_shows_as_pending_not_earned(
    client: httpx.AsyncClient, make_user, post_found, promote_admin
) -> None:
    """Money that will arrive if the seeker finishes checkout, and will not if
    they abandon it. It must not be reported as earned."""
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, id_number="AB123456")
    search = await client.post(
        "/api/search", json={"id_number": "AB123456"}, headers=seeker.headers
    )
    match_id = search.json()["match_ids"][0]
    await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "AB123456"},
        headers=seeker.headers,
    )

    approved = await client.get("/api/earnings/summary", headers=finder.headers)
    assert approved.json()["total_earned"] == 0

    await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=finder.headers,
    )
    # A fallback claim parks for admin review; only an admin decision opens
    # checkout, so it counts as awaiting payment only after that.
    admin = await make_user()
    await promote_admin(admin)
    review = await client.post(
        f"/api/admin/claims/{match_id}/decision",
        json={"approve": True, "note": "Fixture: fallback claim accepted"},
        headers=admin.headers,
    )
    assert review.status_code == 200, review.text

    awaiting = await client.get("/api/earnings/summary", headers=finder.headers)
    assert awaiting.json()["claims_awaiting_payment"] == 1
    assert awaiting.json()["total_earned"] == 0

    await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=seeker.headers
    )
    initiated = await client.get("/api/earnings/summary", headers=finder.headers)
    assert initiated.json()["pending"] == 10000
    assert initiated.json()["total_earned"] == 0


async def test_pending_clears_at_payment_and_earning_awaits_handover(
    client: httpx.AsyncClient, make_user, post_found, pay_for
) -> None:
    finder, seeker, _, match_id = await _paid_claim(
        client, make_user, post_found, pay_for
    )

    # Paid: nothing is pending any more, but escrow means nothing is earned yet.
    paid = (await client.get("/api/earnings/summary", headers=finder.headers)).json()
    assert paid["pending"] == 0
    assert paid["total_earned"] == 0

    await _confirm_handover(client, finder, seeker, match_id)

    body = (await client.get("/api/earnings/summary", headers=finder.headers)).json()
    assert body["pending"] == 0
    assert body["total_earned"] == 10000


async def test_completed_return_is_counted_separately_from_claimed(
    client: httpx.AsyncClient, make_user, post_found, pay_for
) -> None:
    finder, seeker, _, match_id = await _paid_claim(
        client, make_user, post_found, pay_for
    )
    await _confirm_handover(client, finder, seeker, match_id)
    completed = await client.post(
        f"/api/matches/{match_id}/complete", headers=seeker.headers
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"

    body = (await client.get("/api/earnings/summary", headers=finder.headers)).json()
    assert body["documents_returned"] == 1
    assert body["total_earned"] == 10000


# --- per-document rows ------------------------------------------------------


async def test_document_rows_report_the_amount_earned_per_claim(
    client: httpx.AsyncClient, make_user, post_found, pay_for
) -> None:
    finder, seeker, found, match_id = await _paid_claim(
        client, make_user, post_found, pay_for
    )

    rows = (await client.get("/api/earnings/documents", headers=finder.headers)).json()
    assert len(rows) == 1
    row = rows[0]
    assert row["found_id"] == found["id"]
    assert row["match_id"] == match_id
    # Paid, but still held: the row shows the claim, not yet an earning.
    assert row["amount_earned"] == 0
    assert row["payment_status"] == "paid"
    assert row["match_status"] == "claimed"
    assert row["status"] == "claimed"
    assert row["paid_at"] is not None

    await _confirm_handover(client, finder, seeker, match_id)

    rows = (await client.get("/api/earnings/documents", headers=finder.headers)).json()
    row = rows[0]
    assert row["amount_earned"] == 10000
    assert row["match_status"] == "completed"
    assert row["status"] == "returned"


async def test_an_unclaimed_document_appears_with_no_match_and_no_earning(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    found = await post_found(finder, id_number="AB123456")

    rows = (await client.get("/api/earnings/documents", headers=finder.headers)).json()
    assert len(rows) == 1
    assert rows[0]["found_id"] == found["id"]
    assert rows[0]["match_id"] is None
    assert rows[0]["amount_earned"] == 0
    assert rows[0]["payment_status"] is None
    assert rows[0]["status"] == "posted"


async def test_earnings_report_keeps_the_id_number_redacted(
    client: httpx.AsyncClient, make_user, post_found, pay_for
) -> None:
    """A finder reading an earnings report has no need for the full number, even
    on a document they posted themselves."""
    finder, _, _, _ = await _paid_claim(client, make_user, post_found, pay_for)

    rows = (await client.get("/api/earnings/documents", headers=finder.headers)).json()
    row = rows[0]
    assert "AB123456" not in str(row)
    assert row["id_number_redacted"].endswith("56")
    assert "id_number" not in row
    assert row["holder_name_redacted"] != "Jane Alice Doe"


# --- scoping ----------------------------------------------------------------


async def test_a_finder_sees_only_their_own_earnings(
    client: httpx.AsyncClient, make_user, post_found, pay_for
) -> None:
    earner, earner_seeker, _, earner_match = await _paid_claim(
        client, make_user, post_found, pay_for, id_number="AB111111"
    )
    await _confirm_handover(client, earner, earner_seeker, earner_match)
    other, other_seeker, _, other_match = await _paid_claim(
        client, make_user, post_found, pay_for, id_number="AB222222"
    )
    await _confirm_handover(client, other, other_seeker, other_match)

    for finder in (earner, other):
        summary = (
            await client.get("/api/earnings/summary", headers=finder.headers)
        ).json()
        rows = (
            await client.get("/api/earnings/documents", headers=finder.headers)
        ).json()
        assert summary["total_earned"] == 10000
        assert summary["documents_posted"] == 1
        assert len(rows) == 1


async def test_a_seeker_who_paid_earns_nothing(
    client: httpx.AsyncClient, make_user, post_found, pay_for
) -> None:
    """The payer is not the payee. Paying a fee must not credit the payer."""
    _, seeker, _, _ = await _paid_claim(client, make_user, post_found, pay_for)

    summary = (await client.get("/api/earnings/summary", headers=seeker.headers)).json()
    rows = (await client.get("/api/earnings/documents", headers=seeker.headers)).json()
    assert summary["total_earned"] == 0
    assert summary["documents_posted"] == 0
    assert rows == []


async def test_the_platform_share_is_not_credited_to_the_finder(
    client: httpx.AsyncClient, make_user, post_found, pay_for, promote_admin
) -> None:
    """The two halves must not overlap: 100 to the finder, 100 to the platform."""
    finder, seeker, _, match_id = await _paid_claim(
        client, make_user, post_found, pay_for
    )
    await _confirm_handover(client, finder, seeker, match_id)

    admin = await make_user()
    await promote_admin(admin)
    stats = (await client.get("/api/admin/stats", headers=admin.headers)).json()
    earnings = (
        await client.get("/api/earnings/summary", headers=finder.headers)
    ).json()

    assert earnings["total_earned"] == stats["finder_payouts"] == 10000
    assert stats["platform_revenue"] == 10000
    assert earnings["total_earned"] + stats["platform_revenue"] == 20000
