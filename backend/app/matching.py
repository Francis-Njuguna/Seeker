"""Exact-match engine and match lifecycle.

Matching rule (deliberately narrow): a found document matches a lost alert if
and only if their normalized ID numbers are equal. Name is never used to
create a match automatically — names collide constantly, and a false positive
here hands a stranger a preview of someone's ID.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    FoundId,
    FoundIdStatus,
    LostAlert,
    LostAlertStatus,
    Match,
    MatchSource,
    MatchStatus,
    User,
    utcnow,
)
from app.normalize import normalize_id_number, redact_id_number
from app.notifications import notify

# Found records in these states are no longer available to new seekers.
CLOSED_FOUND_STATUSES = {FoundIdStatus.CLAIMED, FoundIdStatus.RETURNED, FoundIdStatus.ARCHIVED}


async def find_by_id_number(db: AsyncSession, id_number: str) -> FoundId | None:
    """Exact lookup on the normalized ID number."""
    normalized = normalize_id_number(id_number)
    if not normalized:
        return None
    result = await db.execute(
        select(FoundId)
        .where(FoundId.id_number_normalized == normalized)
        .where(FoundId.status.notin_(list(CLOSED_FOUND_STATUSES)))
        .order_by(FoundId.created_at.desc())
    )
    return result.scalars().first()


async def find_by_name(db: AsyncSession, name: str) -> list[FoundId]:
    """Exact lookup on the normalized full name.

    Returns every hit rather than one: names are not unique, so the seeker
    confirms via the redacted preview.
    """
    from app.normalize import normalize_name

    normalized = normalize_name(name)
    if not normalized:
        return []
    result = await db.execute(
        select(FoundId)
        .where(FoundId.holder_name_normalized == normalized)
        .where(FoundId.status.notin_(list(CLOSED_FOUND_STATUSES)))
        .order_by(FoundId.created_at.desc())
    )
    return list(result.scalars().all())


async def get_or_create_match(
    db: AsyncSession,
    *,
    found_id: FoundId,
    seeker_user_id: uuid.UUID,
    source: MatchSource,
    lost_alert_id: uuid.UUID | None = None,
    matched_on: str = "id_number",
) -> tuple[Match, bool]:
    """Idempotent per (found_id, seeker). Returns ``(match, created)``."""
    existing = await db.execute(
        select(Match)
        .where(Match.found_id_id == found_id.id)
        .where(Match.seeker_user_id == seeker_user_id)
    )
    match = existing.scalars().first()
    if match is not None:
        if lost_alert_id and match.lost_alert_id is None:
            match.lost_alert_id = lost_alert_id
        return match, False

    match = Match(
        found_id_id=found_id.id,
        lost_alert_id=lost_alert_id,
        seeker_user_id=seeker_user_id,
        source=source,
        matched_on=matched_on,
        status=MatchStatus.MATCHED,
    )
    db.add(match)

    if found_id.status == FoundIdStatus.POSTED:
        found_id.status = FoundIdStatus.MATCHED

    await db.flush()
    return match, True


async def match_new_found_id(db: AsyncSession, found_id: FoundId) -> list[Match]:
    """Fan a newly posted found document out to every waiting lost alert.

    This is what makes the no-match flow work: a seeker who registered an alert
    before the document was posted gets notified when it arrives.
    """
    result = await db.execute(
        select(LostAlert, User)
        .join(User, User.id == LostAlert.seeker_user_id)
        .where(LostAlert.id_number_normalized == found_id.id_number_normalized)
        .where(LostAlert.status == LostAlertStatus.ACTIVE)
    )
    rows = list(result.all())

    created: list[Match] = []
    for alert, seeker in rows:
        # The finder posting a document that matches their own alert is not a
        # match worth notifying about.
        if alert.seeker_user_id == found_id.finder_user_id:
            continue

        match, was_created = await get_or_create_match(
            db,
            found_id=found_id,
            seeker_user_id=alert.seeker_user_id,
            source=MatchSource.ALERT_AUTO,
            lost_alert_id=alert.id,
        )
        if not was_created:
            continue

        alert.status = LostAlertStatus.MATCHED
        alert.last_notified_at = utcnow()

        await notify(
            db,
            user_id=seeker.id,
            email=seeker.email,
            kind="alert_matched",
            title="A document matching your lost ID alert was found",
            body=(
                f"Someone posted a {found_id.document_type.replace('_', ' ')} "
                f"ending in {redact_id_number(found_id.id_number)}.\n\n"
                "Open Seeker to review the redacted preview and start a claim "
                "if it is yours."
            ),
            match_id=match.id,
        )
        created.append(match)

    return created
