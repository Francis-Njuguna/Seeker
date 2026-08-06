"""Match lifecycle: review a redacted preview, request a claim, approve it.

The status ladder is matched -> claim_requested -> claimed -> completed. A
payment step slots in at ``AWAITING_PAYMENT`` between claim_requested and
claimed without any schema change; nothing here charges anyone today.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, to_preview
from app.models import (
    FoundId,
    FoundIdStatus,
    LostAlert,
    LostAlertStatus,
    Match,
    MatchStatus,
    User,
    utcnow,
)
from app.normalize import normalize_id_number
from app.notifications import notify
from app.schemas import ClaimDecision, ClaimRequest, MatchOut, MatchWithPreview

router = APIRouter(prefix="/api/matches", tags=["matches"])


async def _load_match(db: AsyncSession, match_id: uuid.UUID) -> tuple[Match, FoundId]:
    match = await db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match does not exist")
    found = await db.get(FoundId, match.found_id_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Found record does not exist")
    return match, found


@router.get("/mine", response_model=list[MatchWithPreview])
async def list_my_matches(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[MatchWithPreview]:
    """Matches where the caller is the seeker, with redacted previews."""
    result = await db.execute(
        select(Match, FoundId)
        .join(FoundId, FoundId.id == Match.found_id_id)
        .where(Match.seeker_user_id == user.id)
        .order_by(Match.created_at.desc())
    )
    return [
        MatchWithPreview(match=MatchOut.model_validate(m), preview=to_preview(f))
        for m, f in result.all()
    ]


@router.get("/incoming", response_model=list[MatchWithPreview])
async def list_incoming_claims(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[MatchWithPreview]:
    """Claims awaiting the caller's decision as the finder."""
    result = await db.execute(
        select(Match, FoundId)
        .join(FoundId, FoundId.id == Match.found_id_id)
        .where(FoundId.finder_user_id == user.id)
        .where(Match.status == MatchStatus.CLAIM_REQUESTED)
        .order_by(Match.claim_requested_at.desc())
    )
    return [
        MatchWithPreview(match=MatchOut.model_validate(m), preview=to_preview(f))
        for m, f in result.all()
    ]


@router.post("/{match_id}/claim", response_model=MatchOut)
async def request_claim(
    match_id: uuid.UUID,
    payload: ClaimRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchOut:
    """Request a claim, proving ownership with the full ID number.

    The preview only shows the last two characters, so reproducing the whole
    number is evidence the seeker holds the real details rather than having
    stumbled onto the record.
    """
    match, found = await _load_match(db, match_id)
    if match.seeker_user_id != user.id:
        raise HTTPException(status_code=404, detail="Match does not exist")

    if match.status not in (MatchStatus.MATCHED, MatchStatus.REJECTED):
        raise HTTPException(
            status_code=409, detail=f"Claim cannot be started from status '{match.status}'"
        )

    if normalize_id_number(payload.id_number) != found.id_number_normalized:
        raise HTTPException(
            status_code=403, detail="The ID number does not match this document"
        )

    match.status = MatchStatus.CLAIM_REQUESTED
    match.claim_requested_at = utcnow()
    found.status = FoundIdStatus.CLAIM_REQUESTED

    finder = await db.get(User, found.finder_user_id)
    if finder is not None:
        await notify(
            db,
            user_id=finder.id,
            email=finder.email,
            kind="claim_requested",
            title="Someone is claiming a document you posted",
            body=(
                f"{user.display_name} verified the full ID number on the "
                f"{found.document_type.replace('_', ' ')} you posted.\n\n"
                + (f"Their message: {payload.message}\n\n" if payload.message else "")
                + "Open Seeker to approve or decline the claim."
            ),
            match_id=match.id,
        )

    await db.commit()
    await db.refresh(match)
    return MatchOut.model_validate(match)


@router.post("/{match_id}/decision", response_model=MatchOut)
async def decide_claim(
    match_id: uuid.UUID,
    payload: ClaimDecision,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchOut:
    """Finder approves or declines a claim.

    Approval is the point where the seeker gains access to the unblurred image
    and full record. A payment gate would sit immediately before this.
    """
    match, found = await _load_match(db, match_id)
    if found.finder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Match does not exist")
    if match.status != MatchStatus.CLAIM_REQUESTED:
        raise HTTPException(
            status_code=409, detail=f"No pending claim on this match (status '{match.status}')"
        )

    seeker = await db.get(User, match.seeker_user_id)

    if payload.approve:
        match.status = MatchStatus.CLAIMED
        match.claimed_at = utcnow()
        found.status = FoundIdStatus.CLAIMED

        if match.lost_alert_id:
            alert = await db.get(LostAlert, match.lost_alert_id)
            if alert is not None:
                alert.status = LostAlertStatus.RESOLVED

        title = "Your claim was approved"
        body = (
            "The finder approved your claim. You can now see the full record "
            "and the unblurred photo, and arrange handover.\n\n"
            + (f"Note from the finder: {payload.note}" if payload.note else "")
        )
        kind = "claim_approved"
    else:
        match.status = MatchStatus.REJECTED
        # Return the document to the pool so other seekers can still find it.
        found.status = FoundIdStatus.MATCHED
        title = "Your claim was declined"
        body = (
            "The finder declined your claim.\n\n"
            + (f"Reason given: {payload.note}" if payload.note else "")
        )
        kind = "claim_rejected"

    if seeker is not None:
        await notify(
            db,
            user_id=seeker.id,
            email=seeker.email,
            kind=kind,
            title=title,
            body=body,
            match_id=match.id,
        )

    await db.commit()
    await db.refresh(match)
    return MatchOut.model_validate(match)


@router.post("/{match_id}/complete", response_model=MatchOut)
async def complete_match(
    match_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchOut:
    """Mark a claimed document as physically handed over. Either party may call."""
    match, found = await _load_match(db, match_id)
    if user.id not in (match.seeker_user_id, found.finder_user_id):
        raise HTTPException(status_code=404, detail="Match does not exist")
    if match.status != MatchStatus.CLAIMED:
        raise HTTPException(
            status_code=409, detail="Only a claimed match can be completed"
        )

    match.status = MatchStatus.COMPLETED
    found.status = FoundIdStatus.RETURNED
    await db.commit()
    await db.refresh(match)
    return MatchOut.model_validate(match)
