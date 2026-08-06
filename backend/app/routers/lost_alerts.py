"""No-match flow: register a lost alert, get notified on a later match."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.matching import find_by_id_number, get_or_create_match
from app.models import (
    LostAlert,
    LostAlertStatus,
    MatchSource,
    User,
    utcnow,
)
from app.normalize import normalize_id_number, normalize_name
from app.schemas import LostAlertCreate, LostAlertOut

router = APIRouter(prefix="/api/lost-alerts", tags=["lost-alerts"])


@router.post("", response_model=LostAlertOut, status_code=status.HTTP_201_CREATED)
async def create_lost_alert(
    payload: LostAlertCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LostAlertOut:
    """Register a standing alert for a lost document.

    If a matching found record already exists, the match is created
    immediately, so a seeker who registers an alert never misses a document
    that was posted while they were away.
    """
    normalized_id = normalize_id_number(payload.id_number)
    if len(normalized_id) < 3:
        raise HTTPException(
            status_code=400,
            detail="ID number must contain at least 3 alphanumeric characters",
        )

    existing = await db.execute(
        select(LostAlert)
        .where(LostAlert.seeker_user_id == user.id)
        .where(LostAlert.id_number_normalized == normalized_id)
    )
    duplicate = existing.scalars().first()
    if duplicate is not None:
        # Re-activate rather than erroring: the user's intent is clear.
        if duplicate.status in (LostAlertStatus.CANCELLED, LostAlertStatus.RESOLVED):
            duplicate.status = LostAlertStatus.ACTIVE
            duplicate.updated_at = utcnow()
            await db.commit()
            await db.refresh(duplicate)
        return LostAlertOut.model_validate(duplicate)

    alert = LostAlert(
        seeker_user_id=user.id,
        document_type=(payload.document_type or None),
        holder_name=payload.holder_name,
        holder_name_normalized=normalize_name(payload.holder_name),
        id_number=payload.id_number,
        id_number_normalized=normalized_id,
        status=LostAlertStatus.ACTIVE,
    )
    db.add(alert)
    await db.flush()

    already_found = await find_by_id_number(db, payload.id_number)
    if already_found is not None and already_found.finder_user_id != user.id:
        await get_or_create_match(
            db,
            found_id=already_found,
            seeker_user_id=user.id,
            source=MatchSource.ALERT_AUTO,
            lost_alert_id=alert.id,
        )
        alert.status = LostAlertStatus.MATCHED
        alert.last_notified_at = utcnow()

    await db.commit()
    await db.refresh(alert)
    return LostAlertOut.model_validate(alert)


@router.get("", response_model=list[LostAlertOut])
async def list_my_alerts(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[LostAlertOut]:
    result = await db.execute(
        select(LostAlert)
        .where(LostAlert.seeker_user_id == user.id)
        .order_by(LostAlert.created_at.desc())
    )
    return [LostAlertOut.model_validate(a) for a in result.scalars().all()]


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_alert(
    alert_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    alert = await db.get(LostAlert, alert_id)
    if alert is None or alert.seeker_user_id != user.id:
        raise HTTPException(status_code=404, detail="Alert does not exist")
    alert.status = LostAlertStatus.CANCELLED
    await db.commit()
