"""The claim lock: exclusive, atomic custody of a found document.

A document under claim must leave public search immediately and stay out of it
while the claimant works through OCR review and the selfie check. That is a
multi-request workflow, so the lock cannot be a side effect of the final step —
it is taken up front, by ``acquire``, and released only by an explicit outcome.

Why this is a separate module rather than a few lines in the router: the lock is
the security boundary of the whole product. Two people must never hold one
document, and the check that guarantees it is a single conditional UPDATE whose
correctness depends on details (row-level locking, EvalPlanQual re-evaluation)
worth stating once, in one place, rather than restating at every call site.

The atomicity argument
----------------------
``acquire`` issues:

    UPDATE found_ids SET status = 'claim_requested'
     WHERE id = :id AND status IN ('posted', 'matched')

and treats ``rowcount == 0`` as "someone else got there first".

Under PostgreSQL's default READ COMMITTED, two concurrent transactions running
this against the same row behave like so: the first takes a row lock and
updates. The second blocks on that lock; when the first commits, the second
re-evaluates its WHERE clause against the *new* row version (EvalPlanQual),
finds ``status`` is now ``claim_requested``, and matches nothing. It reports
rowcount 0 and is rejected. Exactly one winner, with no advisory lock, no
SERIALIZABLE retry loop, and no read-then-write window.

The read-then-write shape this replaces — check the status, then set it — has a
window between the two statements in which both requests see ``posted``. Both
proceed, and the document ends up with two claimants. There is no amount of
care in the surrounding code that closes that window; only the database can.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    LOCK_HOLDING_MATCH_STATUSES,
    FoundId,
    FoundIdStatus,
    Match,
    MatchStatus,
    utcnow,
)

logger = logging.getLogger(__name__)

# Statuses a document may be in for a claim to be startable. Anything else means
# the document is already spoken for, already returned, or withdrawn.
CLAIMABLE_FOUND_STATUSES: tuple[str, ...] = (
    FoundIdStatus.POSTED,
    FoundIdStatus.MATCHED,
)

# Match statuses from which a seeker may start a claim. REJECTED is included so a
# seeker whose earlier claim the finder declined can try again if the document
# comes back into circulation; EXPIRED likewise, for a claimant who let the clock
# run out and returned later to find it still unclaimed.
STARTABLE_MATCH_STATUSES: tuple[str, ...] = (
    MatchStatus.MATCHED,
    MatchStatus.REJECTED,
    MatchStatus.EXPIRED,
)


class ClaimConflictError(RuntimeError):
    """The document is not available for this claimant to lock."""


@dataclass(frozen=True)
class LockOutcome:
    acquired: bool
    reason: str | None = None


def claim_deadline(started_at):
    """When a lock taken at ``started_at`` lapses."""
    return started_at + timedelta(hours=settings.claim_timeout_hours)


async def active_lock_holder(
    db: AsyncSession, found_id: uuid.UUID, *, exclude_match: uuid.UUID | None = None
) -> Match | None:
    """The match currently holding this document, if any.

    Used for diagnosis and for the guard that stops one seeker starting a second
    claim behind another's back. It is *not* what makes ``acquire`` safe — a read
    cannot be. The conditional UPDATE is.
    """
    stmt = (
        select(Match)
        .where(Match.found_id_id == found_id)
        .where(Match.status.in_(list(LOCK_HOLDING_MATCH_STATUSES)))
    )
    if exclude_match is not None:
        stmt = stmt.where(Match.id != exclude_match)
    return (await db.execute(stmt)).scalars().first()


async def acquire(db: AsyncSession, *, match: Match, found: FoundId) -> LockOutcome:
    """Atomically move the document into claim-pending custody for this match.

    Returns ``LockOutcome(acquired=False, reason=...)`` rather than raising, so
    the caller decides the HTTP status. Does not commit: the caller commits the
    lock together with whatever else the request changes, so a failure part-way
    through cannot leave a document locked to a claim that was never recorded.
    """
    # Guard first for a clear error message. This does not carry the safety
    # property — the UPDATE below does — but without it a second claimant gets a
    # bare "unavailable" when the truthful answer is "someone else has it".
    holder = await active_lock_holder(db, found.id, exclude_match=match.id)
    if holder is not None:
        return LockOutcome(
            acquired=False,
            reason=(
                "Another seeker has an active claim on this document. "
                "It is unavailable until that claim is resolved."
            ),
        )

    result = await db.execute(
        update(FoundId)
        .where(FoundId.id == found.id)
        .where(FoundId.status.in_(CLAIMABLE_FOUND_STATUSES))
        .values(status=FoundIdStatus.CLAIM_REQUESTED, updated_at=utcnow())
    )

    if result.rowcount == 0:
        # Either a concurrent request won the race, or the document had already
        # moved on. Both mean the same thing to this caller.
        return LockOutcome(
            acquired=False,
            reason=(
                "This document is no longer available to claim. Another claim "
                "may have started, or it has already been returned."
            ),
        )

    # The raw UPDATE bypassed the ORM, so the instance in the identity map still
    # carries the old status. Refresh it before anything serialises the response.
    await db.refresh(found)
    return LockOutcome(acquired=True)


async def release(
    db: AsyncSession,
    *,
    found: FoundId,
    to_status: str = FoundIdStatus.POSTED,
) -> None:
    """Return a document to circulation.

    Only ever called for an outcome that genuinely frees the document — an
    expired or finder-rejected claim. A *failed selfie* must not come through
    here: see ``routers/matches.verify_face``, which leaves the lock in place on
    purpose so the next person in line cannot start claiming a document whose
    disputed claim is still open.
    """
    await db.execute(
        update(FoundId)
        .where(FoundId.id == found.id)
        .where(FoundId.status == FoundIdStatus.CLAIM_REQUESTED)
        .values(status=to_status, updated_at=utcnow())
    )
    await db.refresh(found)


async def expire_stale_claims(db: AsyncSession) -> int:
    """Release documents whose claimant walked away. Returns how many.

    Enforced here, server-side, rather than by a countdown in the browser: a
    client-side timer releases nothing, and a claimant who closes the tab would
    otherwise hold a document out of circulation forever.

    Called at the top of every public search (see ``routers/search``) and by the
    admin dashboard, which means the sweep runs whenever it could matter without
    the deployment needing a scheduler. A cron job calling
    ``POST /api/admin/expire-claims`` is the same code path for operators who
    want it on a timer.

    Attempts-exhausted claims are excluded on purpose. They are failures under
    investigation, not abandonment — §26: a document whose claim collapsed
    suspiciously should reach an admin, not the next claimant.
    """
    cutoff = utcnow() - timedelta(hours=settings.claim_timeout_hours)

    stale = (
        await db.execute(
            select(Match, FoundId)
            .join(FoundId, FoundId.id == Match.found_id_id)
            .where(Match.status == MatchStatus.CLAIM_PENDING)
            .where(
                or_(
                    and_(
                        Match.claim_started_at.is_not(None),
                        Match.claim_started_at < cutoff,
                    ),
                    # Rows written before claim_started_at existed fall back to
                    # created_at rather than never expiring.
                    and_(Match.claim_started_at.is_(None), Match.created_at < cutoff),
                )
            )
        )
    ).all()

    expired = 0
    for match, found in stale:
        # Re-assert the transition conditionally. Two workers sweeping at once
        # must not both count the same claim, and a claimant who completed the
        # claim microseconds ago must not have it yanked back.
        result = await db.execute(
            update(Match)
            .where(Match.id == match.id)
            .where(Match.status == MatchStatus.CLAIM_PENDING)
            .values(
                status=MatchStatus.EXPIRED,
                claim_expired_at=utcnow(),
                flagged_for_review=False,
                flag_reason=None,
                updated_at=utcnow(),
            )
        )
        if result.rowcount == 0:
            continue

        await release(db, found=found, to_status=FoundIdStatus.POSTED)
        expired += 1
        # Match id only. Nothing about the document or the person: an expiry log
        # line is operational, and holder names have no business in it.
        logger.info("claim lock expired and document returned (match=%s)", match.id)

    if expired:
        await db.commit()
    return expired


__all__ = [
    "CLAIMABLE_FOUND_STATUSES",
    "LOCK_HOLDING_MATCH_STATUSES",
    "STARTABLE_MATCH_STATUSES",
    "ClaimConflictError",
    "LockOutcome",
    "acquire",
    "active_lock_holder",
    "claim_deadline",
    "expire_stale_claims",
    "release",
]
