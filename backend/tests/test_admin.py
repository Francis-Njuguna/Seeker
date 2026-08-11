"""Admin dashboard: the role gate first, then the numbers behind it.

Admin is the only role permitted to read unredacted holder names across the
platform, so most of this file is about who is refused.
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio

ADMIN_ROUTES = ["/api/admin/stats", "/api/admin/transactions",
                "/api/admin/claims", "/api/admin/flagged"]


async def _paid_claim(client, make_user, post_found, pay_for, id_number="AB123456"):
    """Run one document all the way through to a confirmed payment."""
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
    await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=finder.headers,
    )
    await pay_for(match_id, seeker)
    return finder, seeker, found, match_id


# --- the gate ---------------------------------------------------------------


@pytest.mark.parametrize("route", ADMIN_ROUTES)
async def test_ordinary_users_are_refused(
    client: httpx.AsyncClient, make_user, route: str
) -> None:
    user = await make_user()
    response = await client.get(route, headers=user.headers)
    assert response.status_code == 403


@pytest.mark.parametrize("route", ADMIN_ROUTES)
async def test_anonymous_callers_are_refused(
    client: httpx.AsyncClient, route: str
) -> None:
    response = await client.get(route)
    assert response.status_code == 401


@pytest.mark.parametrize("route", ADMIN_ROUTES)
async def test_admins_are_allowed(
    client: httpx.AsyncClient, make_user, promote_admin, route: str
) -> None:
    admin = await make_user()
    await promote_admin(admin)
    response = await client.get(route, headers=admin.headers)
    assert response.status_code == 200


async def test_role_defaults_to_seeker_and_is_not_self_service(
    client: httpx.AsyncClient, make_user
) -> None:
    """There is no endpoint that grants admin — the first one is promoted by SQL."""
    user = await make_user()
    me = await client.get("/api/auth/me", headers=user.headers)
    assert me.json()["role"] == "seeker"

    # Nothing accepts a role on the way in either.
    response = await client.post(
        "/api/auth/register",
        json={
            "email": "sneaky@example.com",
            "password": "correct-horse-battery",
            "display_name": "Sneaky",
            "role": "admin",
        },
    )
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "seeker"


# --- stats ------------------------------------------------------------------


async def test_stats_are_empty_on_a_fresh_platform(
    client: httpx.AsyncClient, make_user, promote_admin
) -> None:
    admin = await make_user()
    await promote_admin(admin)

    body = (await client.get("/api/admin/stats", headers=admin.headers)).json()
    assert body["total_users"] == 1
    assert body["total_found_ids"] == 0
    assert body["total_transactions"] == 0
    assert body["platform_revenue"] == 0
    assert body["currency"] == "KES"


async def test_revenue_counts_only_payments_the_webhook_confirmed(
    client: httpx.AsyncClient, make_user, post_found, pay_for, promote_admin
) -> None:
    """An initiated-but-unpaid checkout must not show up as revenue."""
    await _paid_claim(client, make_user, post_found, pay_for, id_number="AB111111")

    # A second claim that reaches checkout and stops there.
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, id_number="AB222222")
    search = await client.post(
        "/api/search", json={"id_number": "AB222222"}, headers=seeker.headers
    )
    unpaid_match = search.json()["match_ids"][0]
    await client.post(
        f"/api/matches/{unpaid_match}/claim",
        json={"id_number": "AB222222"},
        headers=seeker.headers,
    )
    await client.post(
        f"/api/matches/{unpaid_match}/decision",
        json={"approve": True},
        headers=finder.headers,
    )
    await client.post(
        f"/api/payments/{unpaid_match}/initiate", json={}, headers=seeker.headers
    )

    admin = await make_user()
    await promote_admin(admin)
    body = (await client.get("/api/admin/stats", headers=admin.headers)).json()

    assert body["total_transactions"] == 2
    assert body["paid_transactions"] == 1
    assert body["platform_revenue"] == 10000
    assert body["finder_payouts"] == 10000
    assert body["gross_volume"] == 20000


async def test_revenue_equals_the_sum_of_paid_platform_shares(
    client: httpx.AsyncClient, make_user, post_found, pay_for, promote_admin, db_session
) -> None:
    for number in ("AB111111", "AB222222", "AB333333"):
        await _paid_claim(client, make_user, post_found, pay_for, id_number=number)

    from sqlalchemy import func, select

    from app.models import Transaction, TransactionStatus

    expected = await db_session.scalar(
        select(func.sum(Transaction.platform_share)).where(
            Transaction.status == TransactionStatus.PAID
        )
    )

    admin = await make_user()
    await promote_admin(admin)
    body = (await client.get("/api/admin/stats", headers=admin.headers)).json()

    assert body["platform_revenue"] == expected == 30000
    assert body["gross_volume"] == body["platform_revenue"] + body["finder_payouts"]


async def test_stats_group_documents_and_matches_by_status(
    client: httpx.AsyncClient, make_user, post_found, pay_for, promote_admin
) -> None:
    await _paid_claim(client, make_user, post_found, pay_for, id_number="AB111111")
    spare_finder = await make_user()
    await post_found(spare_finder, id_number="AB999999")

    admin = await make_user()
    await promote_admin(admin)
    body = (await client.get("/api/admin/stats", headers=admin.headers)).json()

    assert body["found_ids_by_status"]["claimed"] == 1
    # "posted" is the FoundIdStatus a new document starts on (models.py), which
    # is also the default the column carries and what matching.py branches on.
    assert body["found_ids_by_status"]["posted"] == 1
    assert body["matches_by_status"]["claimed"] == 1


# --- the ledger -------------------------------------------------------------


async def test_transaction_ledger_carries_the_split_and_both_parties(
    client: httpx.AsyncClient, make_user, post_found, pay_for, promote_admin
) -> None:
    finder, seeker, _, match_id = await _paid_claim(
        client, make_user, post_found, pay_for
    )

    admin = await make_user()
    await promote_admin(admin)
    rows = (await client.get("/api/admin/transactions", headers=admin.headers)).json()

    assert len(rows) == 1
    row = rows[0]
    assert row["match_id"] == match_id
    assert row["status"] == "paid"
    assert row["provider"] == "mock"
    assert row["amount"] == 20000
    assert row["platform_share"] == 10000
    assert row["finder_share"] == 10000
    assert row["payer_email"] == seeker.email
    assert row["finder_email"] == finder.email
    assert row["holder_name"] == "Jane Alice Doe"
    assert row["paid_at"] is not None


async def test_ledger_shows_an_unpaid_transaction_without_a_paid_timestamp(
    client: httpx.AsyncClient, make_user, post_found, promote_admin
) -> None:
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
    await client.post(
        f"/api/matches/{match_id}/decision",
        json={"approve": True},
        headers=finder.headers,
    )
    await client.post(
        f"/api/payments/{match_id}/initiate", json={}, headers=seeker.headers
    )

    admin = await make_user()
    await promote_admin(admin)
    rows = (await client.get("/api/admin/transactions", headers=admin.headers)).json()

    assert rows[0]["status"] == "initiated"
    assert rows[0]["paid_at"] is None


# --- claims and the review queue -------------------------------------------


async def test_claims_list_records_how_identity_was_proven(
    client: httpx.AsyncClient, make_user, post_found, pay_for, promote_admin
) -> None:
    _, seeker, _, match_id = await _paid_claim(
        client, make_user, post_found, pay_for
    )

    admin = await make_user()
    await promote_admin(admin)
    rows = (await client.get("/api/admin/claims", headers=admin.headers)).json()

    assert len(rows) == 1
    assert rows[0]["match_id"] == match_id
    assert rows[0]["verification_method"] == "id_number_typed"
    assert rows[0]["status"] == "claimed"
    assert rows[0]["payment_status"] == "paid"
    assert rows[0]["seeker_email"] == seeker.email


async def test_the_review_queue_holds_only_flagged_claims(
    client: httpx.AsyncClient, make_user, post_found, pay_for, promote_admin, db_session
) -> None:
    """Everything lands in /claims; only a flagged one lands in /flagged."""
    import uuid

    from sqlalchemy import update

    from app.models import Match

    _, _, _, clean_match = await _paid_claim(
        client, make_user, post_found, pay_for, id_number="AB111111"
    )
    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, id_number="AB222222")
    search = await client.post(
        "/api/search", json={"id_number": "AB222222"}, headers=seeker.headers
    )
    flagged_match = search.json()["match_ids"][0]
    await db_session.execute(
        update(Match)
        .where(Match.id == uuid.UUID(flagged_match))
        .values(flagged_for_review=True, flag_reason="Selfie did not match.")
    )
    await db_session.commit()

    admin = await make_user()
    await promote_admin(admin)
    all_claims = (await client.get("/api/admin/claims", headers=admin.headers)).json()
    queue = (await client.get("/api/admin/flagged", headers=admin.headers)).json()

    assert {row["match_id"] for row in all_claims} == {clean_match, flagged_match}
    assert [row["match_id"] for row in queue] == [flagged_match]
    assert queue[0]["flag_reason"] == "Selfie did not match."


async def test_flagged_count_in_stats_matches_the_queue_length(
    client: httpx.AsyncClient, make_user, post_found, promote_admin, db_session
) -> None:
    import uuid

    from sqlalchemy import update

    from app.models import Match

    finder = await make_user()
    seeker = await make_user()
    await post_found(finder, id_number="AB123456")
    search = await client.post(
        "/api/search", json={"id_number": "AB123456"}, headers=seeker.headers
    )
    await db_session.execute(
        update(Match)
        .where(Match.id == uuid.UUID(search.json()["match_ids"][0]))
        .values(flagged_for_review=True, flag_reason="Low confidence.")
    )
    await db_session.commit()

    admin = await make_user()
    await promote_admin(admin)
    stats = (await client.get("/api/admin/stats", headers=admin.headers)).json()
    queue = (await client.get("/api/admin/flagged", headers=admin.headers)).json()

    assert stats["flagged_for_review"] == len(queue) == 1
