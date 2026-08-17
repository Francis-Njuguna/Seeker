"""One end-to-end test for the public MVP recovery contract."""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_manual_claim_to_receipt_code_and_wallet(
    client: httpx.AsyncClient, make_user, promote_admin
) -> None:
    finder = await make_user()
    seeker = await make_user()
    admin = await make_user()
    await promote_admin(admin)

    posted = await client.post(
        "/api/found-ids",
        data={
            "document_type": "national_id",
            "holder_name": "Jane Alice Doe",
            "id_number": "12345678",
            "holder_date_of_birth": "1990-04-12",
            "found_location": "Nairobi CBD",
            "pickup_area": "Nairobi CBD",
            "pickup_address": "Posta House, Kenyatta Avenue, Nairobi",
            "run_ocr": "false",
        },
        headers=finder.headers,
    )
    assert posted.status_code == 201, posted.text

    searched = await client.post(
        "/api/search",
        json={"document_type": "national_id", "id_number": "12345678"},
        headers=seeker.headers,
    )
    assert searched.status_code == 200, searched.text
    assert searched.json()["found"] is True
    match_id = searched.json()["match_ids"][0]

    locked = await client.post(
        f"/api/matches/{match_id}/start-claim", headers=seeker.headers
    )
    assert locked.status_code == 200, locked.text

    claimed = await client.post(
        f"/api/matches/{match_id}/claim",
        json={"id_number": "12345678", "message": "I lost it in the CBD."},
        headers=seeker.headers,
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["admin_review_required"] is True

    finder_review = await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=finder.headers,
    )
    assert finder_review.status_code == 200, finder_review.text
    assert finder_review.json()["status"] == "claim_requested"

    admin_review = await client.post(
        f"/api/admin/claims/{match_id}/decision",
        json={"approve": True, "note": "Manual evidence accepted."},
        headers=admin.headers,
    )
    assert admin_review.status_code == 200, admin_review.text
    assert admin_review.json()["status"] == "awaiting_payment"

    quote = await client.put(
        f"/api/deliveries/{match_id}/quote",
        json={"method": "courier", "destination_address": "Kisumu Town, Main Post Office"},
        headers=seeker.headers,
    )
    assert quote.status_code == 200, quote.text
    delivery_fee = quote.json()["fee"]

    payment = await client.post(
        f"/api/payments/{match_id}/initiate",
        json={"phone": "+254700000000"},
        headers=seeker.headers,
    )
    assert payment.status_code == 200, payment.text
    assert payment.json()["amount"] == 20000 + delivery_fee
    assert payment.json()["finder_share"] == 10000

    paid = await client.post(f"/api/payments/mock/{payment.json()['reference']}")
    assert paid.status_code == 200, paid.text

    pending_wallet = await client.get("/api/wallet", headers=finder.headers)
    assert pending_wallet.status_code == 200, pending_wallet.text
    assert pending_wallet.json()["pending"] == 10000
    assert pending_wallet.json()["available"] == 0

    owner_delivery = await client.get(
        f"/api/deliveries/{match_id}", headers=seeker.headers
    )
    receipt_code = owner_delivery.json()["handover_code"]
    assert receipt_code and len(receipt_code) == 6

    dispatched = await client.post(
        f"/api/deliveries/{match_id}/dispatch", headers=finder.headers
    )
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json()["status"] == "in_transit"

    confirmed = await client.post(
        f"/api/deliveries/{match_id}/confirm",
        json={"code": receipt_code},
        headers=finder.headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "delivered"

    wallet = await client.get("/api/wallet", headers=finder.headers)
    assert wallet.json()["pending"] == 0
    assert wallet.json()["available"] == 10000

    withdrawal = await client.post(
        "/api/wallet/withdrawals",
        json={"amount": 10000, "phone_number": "+254711111111"},
        headers=finder.headers,
    )
    assert withdrawal.status_code == 200, withdrawal.text
    payload = withdrawal.json()
    assert payload["otp"]["sandbox_code"]

    payout = await client.post(
        f"/api/wallet/withdrawals/{payload['withdrawal']['id']}/confirm",
        json={
            "challenge_id": payload["otp"]["challenge_id"],
            "code": payload["otp"]["sandbox_code"],
        },
        headers=finder.headers,
    )
    assert payout.status_code == 200, payout.text
    assert payout.json()["status"] == "paid"
