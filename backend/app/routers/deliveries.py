"""Private fulfilment planning and receipt-code handover confirmation."""
from __future__ import annotations

import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, require_verified_phone
from app.models import (
    DeliveryMethod,
    DeliveryOrder,
    DeliveryStatus,
    Earning,
    FoundId,
    FoundIdStatus,
    Match,
    MatchStatus,
    Transaction,
    TransactionStatus,
    User,
    utcnow,
)
from app.notifications import notify
from app.schemas import (
    DeliveryConfirmRequest,
    DeliveryOut,
    DeliveryQuoteOut,
    DeliveryQuoteRequest,
)

router = APIRouter(prefix="/api/deliveries", tags=["deliveries"])


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _quote(*, pickup: str, destination: str, method: str) -> tuple[str | None, int, str | None]:
    if method == DeliveryMethod.MEETUP:
        return None, 0, f"Agree on a staffed public location near {destination}"
    combined = f"{pickup} {destination}".lower()
    # A transparent sandbox quote rule. Real integrations replace this function,
    # not the payment/escrow lifecycle around it.
    provider = "Posta Kenya" if any(word in combined for word in ("county", "town", "nairobi")) else "Super Metro Parcel"
    fee = 15000 if "nairobi" in combined else 30000
    return provider, fee, None


def _out(
    order: DeliveryOrder, *, include_code: bool = False, include_pickup: bool = False
) -> DeliveryOut:
    return DeliveryOut(
        id=order.id,
        method=order.method,
        provider=order.provider,
        pickup_address=order.pickup_address if include_pickup else "Private pickup location",
        destination_address=order.destination_address,
        suggested_public_location=order.suggested_public_location,
        fee=order.fee,
        currency=order.currency,
        status=order.status,
        tracking_reference=order.tracking_reference,
        delivered_at=order.delivered_at,
        handover_code=order.handover_code if include_code else None,
    )


async def _match_for_owner(
    db: AsyncSession, match_id: uuid.UUID, user: User
) -> tuple[Match, FoundId]:
    match = await db.get(Match, match_id)
    if match is None or match.seeker_user_id != user.id:
        raise HTTPException(status_code=404, detail="Claim not found")
    found = await db.get(FoundId, match.found_id_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return match, found


@router.put("/{match_id}/quote", response_model=DeliveryQuoteOut)
async def quote_delivery(
    match_id: uuid.UUID,
    payload: DeliveryQuoteRequest,
    user: User = Depends(require_verified_phone),
    db: AsyncSession = Depends(get_db),
) -> DeliveryQuoteOut:
    match, found = await _match_for_owner(db, match_id, user)
    if match.status != MatchStatus.AWAITING_PAYMENT:
        raise HTTPException(status_code=409, detail="Choose delivery after the claim is approved")
    if not found.pickup_address:
        raise HTTPException(status_code=409, detail="The finder has not provided a pickup location")

    provider, fee, public_location = _quote(
        pickup=found.pickup_address,
        destination=payload.destination_address.strip(),
        method=payload.method,
    )
    order = await db.scalar(select(DeliveryOrder).where(DeliveryOrder.match_id == match.id))
    if order is None:
        order = DeliveryOrder(
            match_id=match.id,
            method=payload.method,
            provider=provider,
            pickup_address=found.pickup_address,
            destination_address=payload.destination_address.strip(),
            suggested_public_location=public_location,
            fee=fee,
            currency=settings.payment_currency,
            status=DeliveryStatus.QUOTED,
        )
        db.add(order)
    elif order.status in {DeliveryStatus.QUOTED, DeliveryStatus.DRAFT}:
        order.method = payload.method
        order.provider = provider
        order.destination_address = payload.destination_address.strip()
        order.suggested_public_location = public_location
        order.fee = fee
        order.status = DeliveryStatus.QUOTED
    else:
        raise HTTPException(status_code=409, detail="Delivery has already been paid for")
    await db.commit()
    await db.refresh(order)
    return _out(order)


@router.get("/{match_id}", response_model=DeliveryOut)
async def get_delivery(
    match_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOut:
    match = await db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Delivery not found")
    found = await db.get(FoundId, match.found_id_id)
    if found is None or user.id not in {match.seeker_user_id, found.finder_user_id}:
        raise HTTPException(status_code=404, detail="Delivery not found")
    order = await db.scalar(select(DeliveryOrder).where(DeliveryOrder.match_id == match.id))
    if order is None:
        raise HTTPException(status_code=404, detail="Delivery has not been planned")
    # Only the document owner can see their receipt code; finders enter it but
    # must never retrieve it from the API.
    return _out(
        order,
        include_pickup=user.id == found.finder_user_id,
        include_code=user.id == match.seeker_user_id and order.status in {
        DeliveryStatus.READY_FOR_HANDOVER,
        DeliveryStatus.IN_TRANSIT,
        },
    )


@router.post("/{match_id}/dispatch", response_model=DeliveryOut)
async def dispatch_delivery(
    match_id: uuid.UUID,
    user: User = Depends(require_verified_phone),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOut:
    match = await db.get(Match, match_id)
    found = await db.get(FoundId, match.found_id_id) if match else None
    if match is None or found is None or found.finder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Delivery not found")
    order = await db.scalar(select(DeliveryOrder).where(DeliveryOrder.match_id == match.id))
    if order is None or order.status != DeliveryStatus.READY_FOR_HANDOVER:
        raise HTTPException(status_code=409, detail="Delivery is not ready to dispatch")
    order.status = DeliveryStatus.IN_TRANSIT
    order.dispatched_at = utcnow()
    order.tracking_reference = f"SEEK-{order.id.hex[:10].upper()}"
    await db.commit()
    await db.refresh(order)
    return _out(order)


@router.post("/{match_id}/confirm", response_model=DeliveryOut)
async def confirm_handover(
    match_id: uuid.UUID,
    payload: DeliveryConfirmRequest,
    user: User = Depends(require_verified_phone),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOut:
    match = await db.get(Match, match_id)
    found = await db.get(FoundId, match.found_id_id) if match else None
    if match is None or found is None or found.finder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Delivery not found")
    order = await db.scalar(select(DeliveryOrder).where(DeliveryOrder.match_id == match.id))
    if order is None or order.status not in {DeliveryStatus.READY_FOR_HANDOVER, DeliveryStatus.IN_TRANSIT}:
        raise HTTPException(status_code=409, detail="Delivery cannot be confirmed yet")
    if not order.handover_code_hash or not secrets.compare_digest(
        order.handover_code_hash, _hash(payload.code.strip())
    ):
        raise HTTPException(status_code=400, detail="The receipt code is incorrect")

    txn = await db.scalar(select(Transaction).where(Transaction.match_id == match.id))
    if txn is None or txn.status != TransactionStatus.PAID:
        raise HTTPException(status_code=409, detail="Payment has not been confirmed")
    order.status = DeliveryStatus.DELIVERED
    order.delivered_at = utcnow()
    match.status = MatchStatus.COMPLETED
    found.status = FoundIdStatus.RETURNED
    existing_earning = await db.scalar(select(Earning).where(Earning.transaction_id == txn.id))
    if existing_earning is None:
        db.add(Earning(
            finder_user_id=txn.finder_user_id,
            transaction_id=txn.id,
            match_id=match.id,
            found_id_id=found.id,
            amount=txn.finder_share,
            currency=txn.currency,
        ))
    owner = await db.get(User, match.seeker_user_id)
    if owner:
        await notify(
            db,
            user_id=owner.id,
            email=owner.email,
            kind="document_received",
            title="Document receipt confirmed",
            body="Your receipt code was accepted. The finder can now withdraw their reward.",
            match_id=match.id,
        )
    await db.commit()
    await db.refresh(order)
    return _out(order)
