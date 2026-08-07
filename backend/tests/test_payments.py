"""Payments: the webhook is what releases the record, and nothing else is.

The mock provider is used throughout — that is the sandbox mode the flow ships
with, and its checkout endpoint routes through the same ``apply_payment_result``
the real callback does, so these tests cover the production release path.
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _approved_claim(client, make_user, post_found, id_number="AB123456"):
    """Get a match all the way to awaiting_payment. Returns the whole cast."""
    finder = await make_user()
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
    decision = await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=finder.headers,
    )
    assert decision.json()["status"] == "awaiting_payment"
    return finder, seeker, found, match_id


# --- the split ------------------------------------------------------------


def test_split_halves_the_fee_and_sums_to_the_total() -> None:
    from app.config import settings
    from app.payments import compute_split

    platform, finder = compute_split(settings.payment_amount_minor)
    assert platform + finder == settings.payment_amount_minor
    assert platform == 10000  # 100.00 KES
    assert finder == 10000


def test_split_gives_an_odd_remainder_to_the_finder() -> None:
    """Integer division must not quietly lose a cent."""
    from app.payments import compute_split

    platform, finder = compute_split(20001)
    assert platform + finder == 20001
    assert finder == 10001


def test_amount_is_two_hundred_kes_in_minor_units() -> None:
    from app.config import settings

    assert settings.payment_amount_minor == 20000
    assert settings.payment_currency == "KES"


# --- initiation -----------------------------------------------------------


async def test_initiate_returns_a_checkout_url_and_the_split(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, seeker, _, match_id = await _approved_claim(client, make_user, post_found)

    response = await client.post(
        f"/api/payments/{match_id}/initiate",
        json={"phone": "+254700000000"},
        headers=seeker.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["amount"] == 20000
    assert body["platform_share"] == 10000
    assert body["finder_share"] == 10000
    assert body["currency"] == "KES"
    assert body["reference"]
    assert body["checkout_url"]


async def test_initiate_is_idempotent_so_a_double_click_cannot_double_charge(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, seeker, _, match_id = await _approved_claim(client, make_user, post_found)

    first = await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=seeker.headers
    )
    second = await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=seeker.headers
    )
    assert first.json()["transaction_id"] == second.json()["transaction_id"]
    assert first.json()["reference"] == second.json()["reference"]


async def test_initiate_rejects_a_claim_that_was_never_approved(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, id_number="AB123456")
    search = await client.post(
        "/api/search", json={"id_number": "AB123456"}, headers=seeker.headers
    )
    match_id = search.json()["match_ids"][0]

    response = await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=seeker.headers
    )
    assert response.status_code == 409


async def test_stranger_cannot_initiate_payment_on_someone_elses_match(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, _, _, match_id = await _approved_claim(client, make_user, post_found)
    attacker = await make_user()

    response = await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=attacker.headers
    )
    assert response.status_code == 404


async def test_initiate_refuses_a_second_payment_once_paid(
    client: httpx.AsyncClient, make_user, post_found, pay_for
) -> None:
    _, seeker, _, match_id = await _approved_claim(client, make_user, post_found)
    await pay_for(match_id, seeker)

    response = await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=seeker.headers
    )
    assert response.status_code == 409


# --- the webhook: the gate ------------------------------------------------


async def test_webhook_advances_the_match_to_claimed(
    client: httpx.AsyncClient, make_user, post_found, pay_for
) -> None:
    _, seeker, _, match_id = await _approved_claim(client, make_user, post_found)
    result = await pay_for(match_id, seeker)

    assert result["webhook"]["status"] == "paid"
    status = await client.get(f"/api/payments/{match_id}", headers=seeker.headers)
    assert status.json()["status"] == "paid"
    assert status.json()["paid_at"] is not None


async def test_record_stays_hidden_until_the_webhook_confirms_payment(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """The requirement in one test: approval + initiation are not enough.

    Only the provider callback releases the details, so a seeker who reaches
    checkout and stops there learns nothing they did not already know.
    """
    _, seeker, found, match_id = await _approved_claim(client, make_user, post_found)

    approved = await client.get(f"/api/found-ids/{found['id']}", headers=seeker.headers)
    assert "id_number" not in approved.json()

    started = await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=seeker.headers
    )
    reference = started.json()["reference"]

    # Checkout opened but never completed: still redacted.
    mid = await client.get(f"/api/found-ids/{found['id']}", headers=seeker.headers)
    assert "id_number" not in mid.json()

    await client.post(
        "/api/payments/webhook", json={"reference": reference, "status": "paid"}
    )

    after = await client.get(f"/api/found-ids/{found['id']}", headers=seeker.headers)
    assert after.json()["id_number"] == "AB123456"


async def test_webhook_replay_is_idempotent(
    client: httpx.AsyncClient, make_user, post_found, promote_admin
) -> None:
    """Providers retry. A second delivery must not pay the finder twice."""
    _, seeker, _, match_id = await _approved_claim(client, make_user, post_found)
    started = await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=seeker.headers
    )
    reference = started.json()["reference"]
    payload = {"reference": reference, "status": "paid"}

    first = await client.post("/api/payments/webhook", json=payload)
    second = await client.post("/api/payments/webhook", json=payload)
    third = await client.post("/api/payments/webhook", json=payload)

    assert first.json()["status"] == "paid"
    assert second.json()["status"] == "already_paid"
    assert third.json()["status"] == "already_paid"

    admin = await make_user()
    await promote_admin(admin)
    stats = await client.get("/api/admin/stats", headers=admin.headers)
    # One payment, credited once.
    assert stats.json()["paid_transactions"] == 1
    assert stats.json()["platform_revenue"] == 10000
    assert stats.json()["finder_payouts"] == 10000


async def test_webhook_writes_exactly_one_earning_row(
    client: httpx.AsyncClient, make_user, post_found, db_session
) -> None:
    from sqlalchemy import func, select

    from app.models import Earning

    _, seeker, _, match_id = await _approved_claim(client, make_user, post_found)
    started = await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=seeker.headers
    )
    payload = {"reference": started.json()["reference"], "status": "paid"}
    await client.post("/api/payments/webhook", json=payload)
    await client.post("/api/payments/webhook", json=payload)

    count = await db_session.scalar(select(func.count()).select_from(Earning))
    assert count == 1

    earning = await db_session.scalar(select(Earning))
    assert earning.amount == 10000


async def test_webhook_ignores_an_unknown_reference(
    client: httpx.AsyncClient
) -> None:
    """200, not 404: a 4xx makes the provider retry a dead reference forever."""
    response = await client.post(
        "/api/payments/webhook", json={"reference": "MOCK-nope", "status": "paid"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


async def test_webhook_rejects_a_payload_with_no_reference(
    client: httpx.AsyncClient
) -> None:
    response = await client.post("/api/payments/webhook", json={"status": "paid"})
    assert response.status_code == 400


async def test_failed_payment_leaves_the_record_locked(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    _, seeker, found, match_id = await _approved_claim(client, make_user, post_found)
    started = await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=seeker.headers
    )

    webhook = await client.post(
        "/api/payments/webhook",
        json={"reference": started.json()["reference"], "status": "failed"},
    )
    assert webhook.json()["status"] == "failed"

    still_hidden = await client.get(
        f"/api/found-ids/{found['id']}", headers=seeker.headers
    )
    assert "id_number" not in still_hidden.json()


async def test_pending_status_does_not_release_the_record(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """An intermediate provider status is not a payment."""
    _, seeker, found, match_id = await _approved_claim(client, make_user, post_found)
    started = await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=seeker.headers
    )

    webhook = await client.post(
        "/api/payments/webhook",
        json={"reference": started.json()["reference"], "status": "processing"},
    )
    assert webhook.json()["status"] == "pending"

    record = await client.get(f"/api/found-ids/{found['id']}", headers=seeker.headers)
    assert "id_number" not in record.json()


async def test_mock_checkout_completes_the_payment(
    client: httpx.AsyncClient, make_user, post_found
) -> None:
    """The sandbox checkout page stands in for the customer paying."""
    _, seeker, found, match_id = await _approved_claim(client, make_user, post_found)
    started = await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=seeker.headers
    )

    response = await client.post(f"/api/payments/mock/{started.json()['reference']}")
    assert response.status_code == 200
    assert response.json()["status"] == "paid"

    after = await client.get(f"/api/found-ids/{found['id']}", headers=seeker.headers)
    assert after.json()["id_number"] == "AB123456"


async def test_payment_notifies_both_parties(
    client: httpx.AsyncClient, make_user, post_found, pay_for
) -> None:
    finder, seeker, _, match_id = await _approved_claim(client, make_user, post_found)
    await pay_for(match_id, seeker)

    seeker_notes = await client.get("/api/notifications", headers=seeker.headers)
    finder_notes = await client.get("/api/notifications", headers=finder.headers)
    assert "payment_confirmed" in [n["kind"] for n in seeker_notes.json()]
    assert "claim_paid" in [n["kind"] for n in finder_notes.json()]


async def test_payment_status_is_seeker_only(
    client: httpx.AsyncClient, make_user, post_found, pay_for
) -> None:
    finder, seeker, _, match_id = await _approved_claim(client, make_user, post_found)
    await pay_for(match_id, seeker)

    # Even the finder, who is party to the match, does not read the payer's
    # payment record here — earnings is the finder's view.
    response = await client.get(f"/api/payments/{match_id}", headers=finder.headers)
    assert response.status_code == 404


async def test_status_classification_covers_provider_vocabulary() -> None:
    from app.payments import classify_status

    for word in ("paid", "success", "successful", "completed", "settled", "PAID"):
        assert classify_status(word) == "paid"
    for word in ("failed", "cancelled", "canceled", "declined", "expired"):
        assert classify_status(word) == "failed"
    for word in ("pending", "processing", "", "who-knows"):
        assert classify_status(word) == "pending"
