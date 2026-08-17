"""Private, claim-scoped messaging between the finder and document owner."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import FoundId, Match, Message, User
from app.schemas import MessageCreate, MessageOut

router = APIRouter(prefix="/api/messages", tags=["messages"])


async def _participants(db: AsyncSession, match_id: uuid.UUID, user: User) -> Match:
    match = await db.get(Match, match_id)
    found = await db.get(FoundId, match.found_id_id) if match else None
    if match is None or found is None or user.id not in {match.seeker_user_id, found.finder_user_id}:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return match


@router.get("/{match_id}", response_model=list[MessageOut])
async def list_messages(
    match_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    await _participants(db, match_id, user)
    result = await db.execute(
        select(Message).where(Message.match_id == match_id).order_by(Message.created_at.asc())
    )
    return [MessageOut.model_validate(row) for row in result.scalars().all()]


@router.post("/{match_id}", response_model=MessageOut)
async def post_message(
    match_id: uuid.UUID,
    payload: MessageCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageOut:
    await _participants(db, match_id, user)
    message = Message(match_id=match_id, sender_user_id=user.id, body=payload.body.strip())
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return MessageOut.model_validate(message)
